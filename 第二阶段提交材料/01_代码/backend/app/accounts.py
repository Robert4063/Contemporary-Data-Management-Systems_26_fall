"""User registration and session lifecycle endpoints."""
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pymongo.errors import DuplicateKeyError

from .models import Credentials
from .security import bearer, current_user, now, password_hash, password_ok, token_hash


router = APIRouter(tags=['账号与会话'])


@router.post('/api/auth/register', status_code=201)
def register(body: Credentials, request: Request):
    doc = {'username': body.username.lower(), 'password_hash': password_hash(body.password), 'created_at': now()}
    try:
        request.app.state.db.users.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, '用户名已被注册')
    return {'id': str(doc['_id']), 'username': doc['username']}

@router.post('/api/auth/login')
def login(body: Credentials, request: Request):
    db = request.app.state.db
    user = db.users.find_one({'username': body.username.lower()})
    # Perform a hash even for unknown users to reduce username timing differences.
    valid = password_ok(body.password, user['password_hash']) if user else bool(password_hash(body.password)) and False
    if not valid:
        raise HTTPException(401, '用户名或密码错误')
    token = secrets.token_urlsafe(32)
    expires = now() + timedelta(hours=request.app.state.settings.session_hours)
    db.sessions.insert_one({'user_id': user['_id'], 'token_hash': token_hash(token), 'expires_at': expires})
    return {'access_token': token, 'token_type': 'bearer', 'expires_at': expires}

@router.get('/api/auth/me')
def me(user=Depends(current_user)):
    return {'id': str(user['_id']), 'username': user['username']}

@router.post('/api/auth/logout', status_code=204)
def logout(request: Request, user=Depends(current_user), credentials=Depends(bearer)):
    request.app.state.db.sessions.delete_one({'token_hash': token_hash(credentials.credentials)})

