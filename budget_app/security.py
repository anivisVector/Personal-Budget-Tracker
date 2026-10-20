"""Authentication helpers, rate limiting, and HTTP security headers."""

from __future__ import annotations

import secrets
import time
from functools import wraps
from urllib.parse import urlsplit

from flask import current_app, g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

# A real hash of a random value; checked when the username doesn't exist so a
# failed login takes the same time whether or not the account is real.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


def is_password_hash(value: str) -> bool:
    """True for Werkzeug-format hashes (`method$salt$hash`)."""
    return value.startswith(('scrypt:', 'pbkdf2:')) and value.count('$') == 2


def verify_password(stored: str | None, candidate: str) -> bool:
    if not stored or not is_password_hash(stored):
        check_password_hash(_DUMMY_HASH, candidate)
        return False
    return check_password_hash(stored, candidate)


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def safe_next_url(target: str | None) -> str | None:
    """Only allow same-site relative redirects (prevents open redirects)."""
    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith('/') or target.startswith('//') or '\\' in target:
        return None
    return target


def same_site_referrer() -> str | None:
    """The Referer as a relative path, only if it points at this site."""
    ref = request.referrer
    if not ref:
        return None
    parts = urlsplit(ref)
    if parts.netloc != request.host:
        return None
    return safe_next_url(parts.path + (f'?{parts.query}' if parts.query else ''))


def login_user(user: dict) -> None:
    session.clear()  # drop any pre-login state (session fixation defence)
    session.permanent = True
    session['user_id'] = int(user['id'])
    session['username'] = user['username']


def wants_json() -> bool:
    return request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json'


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get('user') is None:
            if wants_json():
                return jsonify({'error': 'Please sign in again.'}), 401
            return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))
        return view(*args, **kwargs)

    return wrapped


# -- Rate limiting -----------------------------------------------------------
# Backed by the database (not process memory) so limits hold across serverless
# instances.


def client_ip() -> str:
    return (request.remote_addr or 'unknown')[:64]


def count_events(db, bucket: str, key: str, window_seconds: int) -> int:
    since = int(time.time()) - window_seconds
    return int(
        db.scalar(
            'SELECT COUNT(*) AS n FROM rate_events WHERE bucket = ? AND key = ? AND created_at >= ?',
            (bucket, key, since),
            default=0,
        )
    )


def record_event(db, bucket: str, key: str) -> None:
    now = int(time.time())
    db.execute('INSERT INTO rate_events (bucket, key, created_at) VALUES (?, ?, ?)', (bucket, key, now))
    # Opportunistic cleanup keeps the table small without a background job.
    if secrets.randbelow(50) == 0:
        db.execute('DELETE FROM rate_events WHERE created_at < ?', (now - 3 * 86400,))


def clear_events(db, bucket: str, key: str) -> None:
    db.execute('DELETE FROM rate_events WHERE bucket = ? AND key = ?', (bucket, key))


# -- Security headers ----------------------------------------------------------

CDN_SCRIPT = 'https://cdn.jsdelivr.net'
CDN_STYLE = 'https://cdnjs.cloudflare.com'


def csp_nonce() -> str:
    nonce = g.get('csp_nonce')
    if nonce is None:
        nonce = g.csp_nonce = secrets.token_urlsafe(16)
    return nonce


def apply_security_headers(response):
    nonce = g.get('csp_nonce', '')
    script_src = f"'self' {CDN_SCRIPT}" + (f" 'nonce-{nonce}'" if nonce else '')
    response.headers.setdefault(
        'Content-Security-Policy',
        '; '.join(
            [
                "default-src 'self'",
                f'script-src {script_src}',
                # Inline style attributes are used for data-driven widths (progress bars).
                f"style-src 'self' {CDN_STYLE} 'unsafe-inline'",
                f"font-src 'self' {CDN_STYLE} data:",
                "img-src 'self' data:",
                "connect-src 'self'",
                "object-src 'none'",
                "base-uri 'self'",
                "form-action 'self'",
                "frame-ancestors 'none'",
            ]
        ),
    )
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'microphone=(self), camera=(), geolocation=(), payment=()')
    if current_app.extensions['settings'].is_production:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=63072000; includeSubDomains')
    if g.get('user') is not None and 'Cache-Control' not in response.headers:
        # Signed-in pages contain personal financial data; never let shared caches keep them.
        response.headers['Cache-Control'] = 'private, no-store'
    return response
