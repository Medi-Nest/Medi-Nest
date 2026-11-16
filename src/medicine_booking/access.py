"""Password hashing, expiring sessions and role authorization."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from .domain import AppError, new_id, utc_now, validate_text


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 260000).hex()
    return salt + ':' + digest


def verify_password(password, stored):
    return hmac.compare_digest(hash_password(password, stored.split(':')[0]), stored)


def require_role(actor, *roles):
    if not actor or actor['role'] not in roles:
        raise AppError('You do not have permission for this action.', 403)


def require_owner(actor, patient_id):
    if actor['role'] == 'PATIENT' and actor['id'] != patient_id:
        raise AppError('Record not found.', 404)


class AuthService:
    def __init__(self, database, session_hours=8):
        self.database = database
        self.session_hours = session_hours
        self.dummy_hash = hash_password(secrets.token_hex(16))

    def register(self, name, email, password):
        name = validate_text(name, 'name', 80)
        email = validate_text(email, 'email', 160).lower()
        if '@' not in email or ' ' in email or not isinstance(password, str) or not 10 <= len(password) <= 128:
            raise AppError('Enter a valid email and a password of 10–128 characters.')
        with self.database.transaction() as db:
            if db.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
                raise AppError('An account already uses this email.', 409)
            db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',
                       (new_id(), name, email, 'PATIENT', hash_password(password), utc_now()))

    def login(self, email, password):
        email = validate_text(email, 'email', 160).lower()
        if not isinstance(password,str) or not 1 <= len(password) <= 128:
            raise AppError('Email or password is incorrect.',401)
        with self.database.transaction() as db:
            user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            valid = verify_password(password, user['password'] if user else self.dummy_hash)
            if not user or not valid:
                raise AppError('Email or password is incorrect.', 401)
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(24)
            expires = (datetime.now(timezone.utc) + timedelta(hours=self.session_hours)).isoformat()
            db.execute('DELETE FROM sessions WHERE expires_at<=?', (utc_now(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), user['id'], csrf, expires))
            return token, csrf

    def get_session(self, token):
        with self.database.transaction() as db:
            user = db.execute('''SELECT u.id,u.name,u.email,u.role,s.csrf FROM sessions s
                JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?''',
                (hashlib.sha256(token.encode()).hexdigest(), utc_now())).fetchone()
            if not user:
                raise AppError('Please sign in to continue.', 401)
            return dict(user)

    def logout(self, token):
        with self.database.transaction() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
