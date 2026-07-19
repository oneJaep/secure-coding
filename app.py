import os
import re
import secrets
import time
from collections import defaultdict
from datetime import timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, abort,
)
from flask_socketio import join_room, emit, disconnect
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db, socketio, csrf, limiter
from models import User, Product, Report, Message, Transaction, utcnow

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
os.makedirs(INSTANCE_DIR, exist_ok=True)
SECRET_KEY_FILE = os.path.join(INSTANCE_DIR, 'secret_key')

ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME')

USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{3,20}$')
PASSWORD_RE = re.compile(r'^(?=.*[A-Za-z])(?=.*\d).{8,72}$')

LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCK_MINUTES = 15
REPORT_THRESHOLD = 5
TRANSFER_MAX = 100_000_000
PRODUCT_PRICE_MAX = 100_000_000
CHAT_RATE_LIMIT = 5
CHAT_RATE_WINDOW = 10  # seconds


def get_secret_key():
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(SECRET_KEY_FILE, 'w') as f:
        f.write(key)
    os.chmod(SECRET_KEY_FILE, 0o600)
    return key


app = Flask(__name__)
app.config['SECRET_KEY'] = get_secret_key()
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'market.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') != 'development'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)

db.init_app(app)
csrf.init_app(app)
limiter.init_app(app)
socketio.init_app(app)


# ---------------------------------------------------------------------------
# request-level helpers
# ---------------------------------------------------------------------------

if os.environ.get('FORCE_HTTPS') == '1':
    @app.before_request
    def force_https():
        if request.headers.get('X-Forwarded-Proto', request.scheme) != 'https':
            return redirect(request.url.replace('http://', 'https://', 1), code=301)


@app.before_request
def load_current_user():
    g.user = None
    uid = session.get('user_id')
    if not uid:
        return
    user = db.session.get(User, uid)
    if user and user.is_active:
        g.user = user
    else:
        session.clear()


@app.context_processor
def inject_current_user():
    return dict(current_user=g.get('user'))


@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' https://cdnjs.cloudflare.com; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'"
    )
    return response


@app.errorhandler(403)
def forbidden(_e):
    return render_template('403.html'), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    app.logger.exception('unhandled server error: %s', e)
    return render_template('500.html'), 500


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash('로그인이 필요합니다.')
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def validate_product_fields(title, description, price_raw):
    if not (1 <= len(title) <= 200):
        return '상품명은 1~200자여야 합니다.'
    if not (1 <= len(description) <= 2000):
        return '상품 설명은 1~2000자여야 합니다.'
    if not re.match(r'^\d+$', price_raw or ''):
        return '가격은 숫자만 입력할 수 있습니다.'
    price = int(price_raw)
    if not (0 < price <= PRODUCT_PRICE_MAX):
        return f'가격은 1원 이상 {PRODUCT_PRICE_MAX:,}원 이하여야 합니다.'
    return None


# ---------------------------------------------------------------------------
# 기본 / 인증
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    if g.user:
        return redirect(url_for('dashboard'))
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
@limiter.limit('20 per minute')
def register():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''

        if not USERNAME_RE.match(username):
            flash('사용자명은 3~20자의 영문, 숫자, _ 만 사용할 수 있습니다.')
            return redirect(url_for('register'))
        if not PASSWORD_RE.match(password):
            flash('비밀번호는 8자 이상이며 영문과 숫자를 포함해야 합니다.')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first() is not None:
            flash('이미 존재하는 사용자명입니다.')
            return redirect(url_for('register'))

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            is_admin=bool(ADMIN_USERNAME) and username == ADMIN_USERNAME,
        )
        db.session.add(user)
        db.session.commit()
        flash('회원가입이 완료되었습니다. 로그인 해주세요.')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        user = User.query.filter_by(username=username).first()
        now = utcnow()

        if user and user.locked_until and user.locked_until > now:
            wait_min = int((user.locked_until - now).total_seconds() // 60) + 1
            flash(f'로그인 시도가 너무 많습니다. {wait_min}분 후 다시 시도하세요.')
            return redirect(url_for('login'))

        if user and check_password_hash(user.password_hash, password):
            if not user.is_active:
                flash('신고 누적으로 휴면 처리된 계정입니다. 관리자에게 문의하세요.')
                return redirect(url_for('login'))
            user.failed_attempts = 0
            user.locked_until = None
            db.session.commit()
            session.clear()
            session['user_id'] = user.id
            session.permanent = True
            flash('로그인 성공!')
            return redirect(url_for('dashboard'))

        if user:
            user.failed_attempts += 1
            if user.failed_attempts >= LOGIN_MAX_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=LOGIN_LOCK_MINUTES)
                user.failed_attempts = 0
            db.session.commit()
        flash('아이디 또는 비밀번호가 올바르지 않습니다.')
        return redirect(url_for('login'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('로그아웃되었습니다.')
    return redirect(url_for('index'))


# ---------------------------------------------------------------------------
# 대시보드 / 프로필
# ---------------------------------------------------------------------------

@app.route('/dashboard')
@login_required
def dashboard():
    q = (request.args.get('q') or '').strip()[:100]
    query = Product.query.filter_by(status='active')
    if q:
        like = f'%{q}%'
        query = query.filter(db.or_(Product.title.ilike(like), Product.description.ilike(like)))
    products = query.order_by(Product.created_at.desc()).all()
    return render_template('dashboard.html', products=products, user=g.user, q=q)


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = g.user
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        if form_type == 'bio':
            bio = request.form.get('bio', '')
            if len(bio) > 500:
                flash('소개글은 500자 이하여야 합니다.')
                return redirect(url_for('profile'))
            user.bio = bio
            db.session.commit()
            flash('프로필이 업데이트되었습니다.')
        elif form_type == 'password':
            current_password = request.form.get('current_password') or ''
            new_password = request.form.get('new_password') or ''
            if not check_password_hash(user.password_hash, current_password):
                flash('현재 비밀번호가 올바르지 않습니다.')
                return redirect(url_for('profile'))
            if not PASSWORD_RE.match(new_password):
                flash('새 비밀번호는 8자 이상이며 영문과 숫자를 포함해야 합니다.')
                return redirect(url_for('profile'))
            user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash('비밀번호가 변경되었습니다.')
        return redirect(url_for('profile'))
    return render_template('profile.html', user=user)


@app.route('/user/<user_id>')
@login_required
def user_profile(user_id):
    profile_user = db.session.get(User, user_id)
    if not profile_user or not profile_user.is_active:
        flash('사용자를 찾을 수 없습니다.')
        return redirect(url_for('dashboard'))
    products = (Product.query
                .filter_by(seller_id=profile_user.id, status='active')
                .order_by(Product.created_at.desc()).all())
    return render_template('user_profile.html', profile_user=profile_user, products=products)


# ---------------------------------------------------------------------------
# 상품
# ---------------------------------------------------------------------------

@app.route('/product/new', methods=['GET', 'POST'])
@login_required
def new_product():
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        description = (request.form.get('description') or '').strip()
        price_raw = (request.form.get('price') or '').strip()
        error = validate_product_fields(title, description, price_raw)
        if error:
            flash(error)
            return redirect(url_for('new_product'))
        product = Product(title=title, description=description, price=int(price_raw), seller_id=g.user.id)
        db.session.add(product)
        db.session.commit()
        flash('상품이 등록되었습니다.')
        return redirect(url_for('dashboard'))
    return render_template('new_product.html')


@app.route('/product/<product_id>')
@login_required
def view_product(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        flash('상품을 찾을 수 없습니다.')
        return redirect(url_for('dashboard'))
    is_owner = product.seller_id == g.user.id
    if product.status == 'blocked' and not is_owner and not g.user.is_admin:
        flash('신고 누적으로 차단된 상품입니다.')
        return redirect(url_for('dashboard'))
    return render_template('view_product.html', product=product, seller=product.seller, is_owner=is_owner)


@app.route('/product/<product_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_product(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        flash('상품을 찾을 수 없습니다.')
        return redirect(url_for('my_products'))
    if product.seller_id != g.user.id:
        abort(403)
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        description = (request.form.get('description') or '').strip()
        price_raw = (request.form.get('price') or '').strip()
        error = validate_product_fields(title, description, price_raw)
        if error:
            flash(error)
            return redirect(url_for('edit_product', product_id=product_id))
        product.title = title
        product.description = description
        product.price = int(price_raw)
        db.session.commit()
        flash('상품 정보가 수정되었습니다.')
        return redirect(url_for('view_product', product_id=product_id))
    return render_template('edit_product.html', product=product)


@app.route('/product/<product_id>/delete', methods=['POST'])
@login_required
def delete_product(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        flash('상품을 찾을 수 없습니다.')
        return redirect(url_for('my_products'))
    if product.seller_id != g.user.id:
        abort(403)
    db.session.delete(product)
    db.session.commit()
    flash('상품이 삭제되었습니다.')
    return redirect(url_for('my_products'))


@app.route('/my/products')
@login_required
def my_products():
    products = (Product.query.filter_by(seller_id=g.user.id)
                .order_by(Product.created_at.desc()).all())
    return render_template('my_products.html', products=products)


# ---------------------------------------------------------------------------
# 신고
# ---------------------------------------------------------------------------

@app.route('/report', methods=['GET', 'POST'])
@login_required
def report():
    if request.method == 'POST':
        target_type = request.form.get('target_type')
        target_id = (request.form.get('target_id') or '').strip()
        reason = (request.form.get('reason') or '').strip()

        if target_type not in ('user', 'product'):
            flash('신고 대상 유형이 올바르지 않습니다.')
            return redirect(url_for('report'))
        if not (1 <= len(reason) <= 500):
            flash('신고 사유는 1~500자여야 합니다.')
            return redirect(url_for('report'))

        if target_type == 'user':
            target = db.session.get(User, target_id)
        else:
            target = db.session.get(Product, target_id)
        if not target:
            flash('신고 대상을 찾을 수 없습니다.')
            return redirect(url_for('report'))
        if target_type == 'user' and target_id == g.user.id:
            flash('자기 자신은 신고할 수 없습니다.')
            return redirect(url_for('report'))

        rep = Report(reporter_id=g.user.id, target_type=target_type, target_id=target_id, reason=reason)
        db.session.add(rep)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('이미 신고한 대상입니다.')
            return redirect(url_for('report'))

        count = Report.query.filter_by(target_type=target_type, target_id=target_id).count()
        if count >= REPORT_THRESHOLD:
            if target_type == 'product' and target.status != 'blocked':
                target.status = 'blocked'
                db.session.commit()
                app.logger.warning('AUDIT: product %s auto-blocked after %d reports', target_id, count)
            elif target_type == 'user' and target.is_active:
                target.is_active = False
                db.session.commit()
                app.logger.warning('AUDIT: user %s auto-suspended after %d reports', target_id, count)

        flash('신고가 접수되었습니다.')
        return redirect(url_for('dashboard'))

    return render_template(
        'report.html',
        target_type=request.args.get('target_type', ''),
        target_id=request.args.get('target_id', ''),
    )


# ---------------------------------------------------------------------------
# 송금
# ---------------------------------------------------------------------------

@app.route('/transfer', methods=['GET', 'POST'])
@login_required
def transfer():
    user = g.user
    if request.method == 'POST':
        target_username = (request.form.get('target_username') or '').strip()
        amount_raw = (request.form.get('amount') or '').strip()
        current_password = request.form.get('current_password') or ''

        if not check_password_hash(user.password_hash, current_password):
            flash('현재 비밀번호가 올바르지 않습니다.')
            return redirect(url_for('transfer'))
        if not re.match(r'^\d+$', amount_raw):
            flash('금액은 숫자만 입력할 수 있습니다.')
            return redirect(url_for('transfer'))
        amount = int(amount_raw)
        if not (0 < amount <= TRANSFER_MAX):
            flash('송금액이 올바르지 않습니다.')
            return redirect(url_for('transfer'))

        target = User.query.filter_by(username=target_username).first()
        if not target or not target.is_active:
            flash('대상 사용자를 찾을 수 없습니다.')
            return redirect(url_for('transfer'))
        if target.id == user.id:
            flash('본인에게는 송금할 수 없습니다.')
            return redirect(url_for('transfer'))
        if user.balance < amount:
            flash('잔액이 부족합니다.')
            return redirect(url_for('transfer'))

        user.balance -= amount
        target.balance += amount
        db.session.add(Transaction(sender_id=user.id, receiver_id=target.id, amount=amount))
        db.session.commit()
        flash(f'{target.username}님에게 {amount:,}원을 송금했습니다.')
        return redirect(url_for('transfer'))

    history = (Transaction.query
               .filter(db.or_(Transaction.sender_id == user.id, Transaction.receiver_id == user.id))
               .order_by(Transaction.created_at.desc())
               .limit(20).all())
    return render_template('transfer.html', user=user, history=history)


# ---------------------------------------------------------------------------
# 1:1 채팅
# ---------------------------------------------------------------------------

@app.route('/messages')
@login_required
def messages_inbox():
    uid = g.user.id
    msgs = (Message.query
            .filter(db.or_(Message.sender_id == uid, Message.receiver_id == uid))
            .order_by(Message.created_at.desc()).all())
    seen = set()
    partners = []
    for m in msgs:
        other_id = m.receiver_id if m.sender_id == uid else m.sender_id
        if other_id in seen:
            continue
        seen.add(other_id)
        other = m.receiver if m.sender_id == uid else m.sender
        if other:
            partners.append(other)
    return render_template('messages_inbox.html', partners=partners)


@app.route('/messages/<user_id>')
@login_required
def messages_room(user_id):
    other = db.session.get(User, user_id)
    if not other or other.id == g.user.id:
        flash('대화 상대를 찾을 수 없습니다.')
        return redirect(url_for('messages_inbox'))
    uid = g.user.id
    history = (Message.query.filter(
        db.or_(
            db.and_(Message.sender_id == uid, Message.receiver_id == other.id),
            db.and_(Message.sender_id == other.id, Message.receiver_id == uid),
        )
    ).order_by(Message.created_at.asc()).limit(200).all())
    return render_template('messages_room.html', other=other, history=history)


# ---------------------------------------------------------------------------
# 관리자
# ---------------------------------------------------------------------------

@app.route('/admin')
@admin_required
def admin_dashboard():
    stats = {
        'users': User.query.count(),
        'products': Product.query.count(),
        'reports': Report.query.count(),
        'blocked_products': Product.query.filter_by(status='blocked').count(),
        'suspended_users': User.query.filter_by(is_active=False).count(),
    }
    return render_template('admin/dashboard.html', stats=stats)


@app.route('/admin/users')
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)


@app.route('/admin/users/<user_id>/ban', methods=['POST'])
@admin_required
def admin_ban_user(user_id):
    target = db.session.get(User, user_id)
    if not target:
        abort(404)
    if target.id == g.user.id:
        flash('본인 계정은 정지할 수 없습니다.')
        return redirect(url_for('admin_users'))
    target.is_active = False
    db.session.commit()
    app.logger.warning('AUDIT: admin %s suspended user %s', g.user.username, target.username)
    flash(f'{target.username} 계정을 정지했습니다.')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<user_id>/unban', methods=['POST'])
@admin_required
def admin_unban_user(user_id):
    target = db.session.get(User, user_id)
    if not target:
        abort(404)
    target.is_active = True
    target.failed_attempts = 0
    target.locked_until = None
    db.session.commit()
    app.logger.warning('AUDIT: admin %s reinstated user %s', g.user.username, target.username)
    flash(f'{target.username} 계정 정지를 해제했습니다.')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<user_id>/toggle_admin', methods=['POST'])
@admin_required
def admin_toggle_admin(user_id):
    target = db.session.get(User, user_id)
    if not target:
        abort(404)
    if target.id == g.user.id:
        flash('본인의 관리자 권한은 변경할 수 없습니다.')
        return redirect(url_for('admin_users'))
    target.is_admin = not target.is_admin
    db.session.commit()
    app.logger.warning('AUDIT: admin %s set is_admin=%s for %s', g.user.username, target.is_admin, target.username)
    flash('관리자 권한이 변경되었습니다.')
    return redirect(url_for('admin_users'))


@app.route('/admin/products')
@admin_required
def admin_products():
    products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('admin/products.html', products=products)


@app.route('/admin/products/<product_id>/block', methods=['POST'])
@admin_required
def admin_block_product(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        abort(404)
    product.status = 'blocked'
    db.session.commit()
    app.logger.warning('AUDIT: admin %s blocked product %s', g.user.username, product.id)
    flash('상품을 차단했습니다.')
    return redirect(url_for('admin_products'))


@app.route('/admin/products/<product_id>/unblock', methods=['POST'])
@admin_required
def admin_unblock_product(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        abort(404)
    product.status = 'active'
    db.session.commit()
    app.logger.warning('AUDIT: admin %s unblocked product %s', g.user.username, product.id)
    flash('상품 차단을 해제했습니다.')
    return redirect(url_for('admin_products'))


@app.route('/admin/products/<product_id>/delete', methods=['POST'])
@admin_required
def admin_delete_product(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        abort(404)
    db.session.delete(product)
    db.session.commit()
    app.logger.warning('AUDIT: admin %s deleted product %s', g.user.username, product_id)
    flash('상품을 삭제했습니다.')
    return redirect(url_for('admin_products'))


@app.route('/admin/reports')
@admin_required
def admin_reports():
    reports = Report.query.order_by(Report.created_at.desc()).all()
    rows = []
    for r in reports:
        if r.target_type == 'user':
            target = db.session.get(User, r.target_id)
            label = target.username if target else '(삭제된 사용자)'
        else:
            target = db.session.get(Product, r.target_id)
            label = target.title if target else '(삭제된 상품)'
        rows.append({'report': r, 'target_label': label})
    return render_template('admin/reports.html', rows=rows)


# ---------------------------------------------------------------------------
# Socket.IO: 전체 채팅 + 1:1 채팅
# ---------------------------------------------------------------------------

_msg_times = defaultdict(list)


def check_rate_limit(key):
    now = time.time()
    times = _msg_times[key]
    while times and times[0] < now - CHAT_RATE_WINDOW:
        times.pop(0)
    if len(times) >= CHAT_RATE_LIMIT:
        return False
    times.append(now)
    return True


def dm_room_name(id_a, id_b):
    return 'dm_' + '_'.join(sorted([id_a, id_b]))


@socketio.on('connect')
def handle_connect():
    uid = session.get('user_id')
    if not uid:
        return False
    user = db.session.get(User, uid)
    if not user or not user.is_active:
        return False


@socketio.on('send_message')
def handle_send_message_event(data):
    uid = session.get('user_id')
    if not uid:
        disconnect()
        return
    user = db.session.get(User, uid)
    if not user or not user.is_active:
        disconnect()
        return
    if not isinstance(data, dict):
        return
    message = data.get('message')
    if not isinstance(message, str):
        return
    message = message.strip()
    if not message or len(message) > 300:
        emit('chat_error', {'error': '메시지 길이가 올바르지 않습니다.'})
        return
    if not check_rate_limit(uid):
        emit('chat_error', {'error': '메시지를 너무 빠르게 보내고 있습니다. 잠시 후 다시 시도하세요.'})
        return
    emit('message', {'username': user.username, 'message': message}, broadcast=True)


@socketio.on('join_dm')
def handle_join_dm(data):
    uid = session.get('user_id')
    if not uid:
        disconnect()
        return
    other_id = data.get('other_id') if isinstance(data, dict) else None
    if not other_id or not db.session.get(User, other_id):
        return
    join_room(dm_room_name(uid, other_id))


@socketio.on('dm_message')
def handle_dm_message(data):
    uid = session.get('user_id')
    if not uid:
        disconnect()
        return
    user = db.session.get(User, uid)
    if not user or not user.is_active:
        disconnect()
        return
    if not isinstance(data, dict):
        return
    other_id = data.get('other_id')
    message = data.get('message')
    other = db.session.get(User, other_id) if other_id else None
    if not other or not isinstance(message, str):
        return
    message = message.strip()
    if not message or len(message) > 300:
        emit('chat_error', {'error': '메시지 길이가 올바르지 않습니다.'})
        return
    if not check_rate_limit(uid):
        emit('chat_error', {'error': '메시지를 너무 빠르게 보내고 있습니다. 잠시 후 다시 시도하세요.'})
        return

    db.session.add(Message(sender_id=uid, receiver_id=other.id, content=message))
    db.session.commit()
    emit('dm_message', {
        'sender_id': uid,
        'sender_name': user.username,
        'other_id': other.id,
        'content': message,
    }, room=dm_room_name(uid, other.id))


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------

def init_db():
    with app.app_context():
        db.create_all()
    db_path = os.path.join(BASE_DIR, 'market.db')
    if os.path.exists(db_path):
        os.chmod(db_path, 0o600)


if __name__ == '__main__':
    init_db()
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    # Werkzeug's dev server is fine for this course project (local use / ngrok tunnel).
    # For a real deployment, run behind eventlet/gunicorn instead.
    socketio.run(app, debug=debug_mode, allow_unsafe_werkzeug=True)
