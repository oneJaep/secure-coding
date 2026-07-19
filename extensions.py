from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
socketio = SocketIO()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
