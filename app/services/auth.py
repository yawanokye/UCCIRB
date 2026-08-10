from __future__ import annotations

import hashlib
import hmac
import os
import time
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from ..config import SESSION_IDLE_MINUTES, SUPERADMIN_ALLOWED_IPS
from ..models import Role, User

# Argon2id for all new/reset passwords. Existing PBKDF2 hashes remain verifiable and
# are transparently upgraded after a successful login.
_argon2 = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)
PBKDF2_ROUNDS = 240_000


def hash_password(password: str) -> str:
    return _argon2.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    if encoded.startswith('$argon2'):
        try:
            return _argon2.verify(encoded, password)
        except (VerifyMismatchError, InvalidHashError, Exception):
            return False
    try:
        scheme, rounds, salt_hex, digest_hex = encoded.split('$', 3)
        if scheme != 'pbkdf2_sha256':
            return False
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except Exception:
        return False


def password_needs_rehash(encoded: str) -> bool:
    if encoded.startswith('pbkdf2_sha256$'):
        return True
    if encoded.startswith('$argon2'):
        try:
            return _argon2.check_needs_rehash(encoded)
        except Exception:
            return True
    return True


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for', '').split(',')[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else 'unknown'


def current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get('user_id')
    if not user_id:
        return None

    now = int(time.time())
    last_seen = int(request.session.get('last_seen', now))
    if now - last_seen > SESSION_IDLE_MINUTES * 60:
        request.session.clear()
        return None
    request.session['last_seen'] = now

    user = db.get(User, user_id)
    if not user or not user.active:
        request.session.clear()
        return None
    if user.role == Role.SUPERADMIN.value and SUPERADMIN_ALLOWED_IPS and _client_ip(request) not in SUPERADMIN_ALLOWED_IPS:
        request.session.clear()
        return None
    return user


def require_user(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail='Authentication required or session expired')
    return user


def require_roles(user: User, *roles: str):
    if user.role not in roles:
        raise HTTPException(status_code=403, detail='You do not have permission to perform this action')
