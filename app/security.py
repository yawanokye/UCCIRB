from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import (
    ALLOWED_HOSTS,
    LOGIN_LOCKOUT_MINUTES,
    LOGIN_MAX_FAILURES,
    RATE_LIMIT_POSTS_PER_MINUTE,
    RATE_LIMIT_SECURE_REVIEW_PER_MINUTE,
    SECRET_KEY,
    SUPERADMIN_ALLOWED_IPS,
)
from .database import get_db
from .models import SecurityEvent

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)
logger = logging.getLogger("ucc_irb.security")


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


def _digest(value: str) -> str:
    return hmac.new(SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def ip_hash(request: Request) -> str:
    return _digest(client_ip(request))


def email_hash(email: str) -> str:
    return _digest(email.strip().lower())


def ua_hash(request: Request) -> str:
    return _digest(request.headers.get("user-agent", "unknown")[:1000])


def log_security_event(
    db: Session,
    request: Request,
    event_type: str,
    email: str | None = None,
    user_id: str | None = None,
    detail: str | None = None,
):
    db.add(SecurityEvent(
        event_type=event_type,
        user_id=user_id,
        subject_hash=email_hash(email) if email else None,
        ip_hash=ip_hash(request),
        user_agent_hash=ua_hash(request),
        detail=(detail or "")[:1000] or None,
    ))
    db.commit()


def login_locked(db: Session, request: Request, email: str, include_ip: bool = True) -> bool:
    cutoff = datetime.utcnow() - timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
    subject = email_hash(email)
    ip = ip_hash(request)

    account_failures = db.scalar(
        select(func.count(SecurityEvent.id)).where(
            SecurityEvent.event_type == "login_failed",
            SecurityEvent.subject_hash == subject,
            SecurityEvent.created_at >= cutoff,
        )
    ) or 0
    ip_failures = db.scalar(
        select(func.count(SecurityEvent.id)).where(
            SecurityEvent.event_type == "login_failed",
            SecurityEvent.ip_hash == ip,
            SecurityEvent.created_at >= cutoff,
        )
    ) or 0
    if account_failures >= LOGIN_MAX_FAILURES:
        return True
    return include_ip and ip_failures >= (LOGIN_MAX_FAILURES * 3)


def clear_login_failures(db: Session, request: Request, email: str):
    # Keep the immutable security trail, but a successful login creates a marker so the
    # operational history remains understandable without deleting prior events.
    log_security_event(db, request, "login_success", email=email)


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


async def csrf_protect(request: Request):
    token = ensure_csrf_token(request)
    if request.method in _SAFE_METHODS:
        return

    # Origin/Referer host matching is intentionally disabled for all portals.
    # Render and institutional proxies can rewrite these headers and incorrectly
    # block legitimate users. Public login and registration remain protected by
    # password verification, account lockout, rate limits and secure session cookies.
    if request.url.path in {"/login", "/register"}:
        return

    # Protected workflow POSTs still require the per-session CSRF token, but they
    # are no longer rejected based on Origin/Referer host comparison.
    header_token = request.headers.get("x-csrf-token")
    supplied = header_token
    if not supplied:
        try:
            form = await request.form()
            supplied = form.get("csrf_token")
        except Exception:
            supplied = None
    if not supplied or not hmac.compare_digest(str(supplied), token):
        logger.warning(
            "CSRF token rejection path=%s supplied=%s session_cookie=%s",
            request.url.path, bool(supplied), bool(request.cookies.get("ucc_irb_session")),
        )
        raise HTTPException(status_code=403, detail="Security token missing or invalid. Refresh the page and try again.")


def superadmin_ip_allowed(request: Request) -> bool:
    if not SUPERADMIN_ALLOWED_IPS:
        return True
    return client_ip(request) in SUPERADMIN_ALLOWED_IPS


def _rate_limit(key: str, limit: int, window_seconds: int = 60) -> bool:
    now = time.monotonic()
    bucket = _rate_buckets[key]
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


async def request_rate_limit(request: Request, call_next):
    if request.method not in _SAFE_METHODS:
        path = request.url.path
        ip = client_ip(request)
        if path.startswith("/secure/reviews/"):
            limit = RATE_LIMIT_SECURE_REVIEW_PER_MINUTE
            scope = "secure-review"
        elif path in {"/login", "/register"}:
            # Many students may share a campus/proxy IP. Account-level login
            # failure controls remain in force, so this only avoids false positives
            # from a busy shared network.
            limit = max(RATE_LIMIT_POSTS_PER_MINUTE, 300)
            scope = "public-auth"
        else:
            limit = RATE_LIMIT_POSTS_PER_MINUTE
            scope = "post"
        if not _rate_limit(f"{scope}:{ip}", limit):
            return _plain_response(429, "Too many requests. Please wait a moment and try again.")
    return await call_next(request)


def _plain_response(status_code: int, message: str):
    from starlette.responses import PlainTextResponse
    return PlainTextResponse(message, status_code=status_code, headers={"Retry-After": "60"})


async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'self'; frame-src 'self'; object-src 'none'; "
        "form-action 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
    )
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    sensitive_prefixes = ("/dashboard", "/applications/", "/documents/", "/secure/reviews/", "/system-admin", "/secretariat", "/college", "/irb", "/review")
    if request.url.path.startswith(sensitive_prefixes):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response
