import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


def now():
    return datetime.now(timezone.utc)


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def password_ok(password, encoded):
    return hmac.compare_digest(password_hash(password, encoded.split('$')[2]), encoded)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not credentials:
        raise HTTPException(401, '请先登录', headers={'WWW-Authenticate': 'Bearer'})
    session = request.app.state.db.sessions.find_one({'token_hash': token_hash(credentials.credentials), 'expires_at': {'$gt': now()}})
    user = request.app.state.db.users.find_one({'_id': session['user_id']}) if session else None
    if not user:
        raise HTTPException(401, '登录已过期或无效', headers={'WWW-Authenticate': 'Bearer'})
    return user
