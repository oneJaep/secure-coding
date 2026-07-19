import uuid
from datetime import datetime, timezone

from extensions import db


def new_id():
    return str(uuid.uuid4())


def utcnow():
    # SQLite/SQLAlchemy DateTime columns drop tzinfo on round-trip, so values read
    # back from the DB are naive. Store naive UTC everywhere to keep comparisons safe.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    username = db.Column(db.String(20), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    bio = db.Column(db.Text, nullable=False, default='')
    balance = db.Column(db.Integer, nullable=False, default=1_000_000)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)  # False == 휴면(정지) 계정
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    products = db.relationship('Product', backref='seller', lazy=True)


class Product(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    price = db.Column(db.Integer, nullable=False)
    seller_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='active')  # active | blocked
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Report(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    reporter_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    target_type = db.Column(db.String(10), nullable=False)  # user | product
    target_id = db.Column(db.String(36), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint('reporter_id', 'target_type', 'target_id', name='uq_report_once_per_target'),
    )

    reporter = db.relationship('User', foreign_keys=[reporter_id])


class Message(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    sender_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.String(300), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    sender = db.relationship('User', foreign_keys=[sender_id])
    receiver = db.relationship('User', foreign_keys=[receiver_id])


class Transaction(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    sender_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    sender = db.relationship('User', foreign_keys=[sender_id])
    receiver = db.relationship('User', foreign_keys=[receiver_id])
