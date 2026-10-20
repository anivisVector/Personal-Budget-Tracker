import pytest

from budget_app.config import ConfigError, load_settings
from budget_app.db import libpq_url
from tests.conftest import csrf_from, register


def test_security_headers(client):
    response = client.get('/')
    csp = response.headers['Content-Security-Policy']
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "'unsafe-inline'" not in csp.split('script-src')[1].split(';')[0]  # scripts need a nonce
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['X-Frame-Options'] == 'DENY'


def test_signed_in_pages_are_not_cacheable(client, user):
    assert client.get('/dashboard').headers['Cache-Control'] == 'private, no-store'


def test_session_cookie_flags(make_app):
    app = make_app(is_production=True)
    client = app.test_client()
    response = register(client, 'secure')
    cookie = response.headers['Set-Cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=Lax' in cookie


def test_csrf_is_enforced_on_forms_and_api(make_app):
    app = make_app(flask={'WTF_CSRF_ENABLED': True})
    client = app.test_client()
    # Form POST without a token is rejected.
    blocked = client.post('/register', data={'username': 'mallory', 'email': 'm@example.com', 'password': 'x' * 10, 'confirm_password': 'x' * 10})
    assert blocked.status_code == 302
    assert client.get('/dashboard').status_code == 302  # not signed in: the account was never created
    token = csrf_from(client.get('/register').get_data(as_text=True))
    ok = client.post('/register', data={'csrf_token': token, 'username': 'alice', 'email': 'a@example.com', 'password': 'x' * 10, 'confirm_password': 'x' * 10})
    assert ok.status_code == 302 and ok.headers['Location'].endswith('/dashboard')
    # Signing in rotates the session, so the pre-login token must no longer work.
    assert client.post('/api/transactions', json={}, headers={'X-CSRFToken': token}).status_code == 400
    # JSON API needs the fresh token in the X-CSRFToken header.
    token = csrf_from(client.get('/dashboard').get_data(as_text=True))
    assert client.post('/api/transactions', json={}).status_code == 400
    with_header = client.post('/api/transactions', json={}, headers={'X-CSRFToken': token})
    assert with_header.status_code == 422  # passed CSRF, failed validation as expected


def test_production_requires_secret_key(monkeypatch):
    monkeypatch.delenv('SECRET_KEY', raising=False)
    with pytest.raises(ConfigError):
        load_settings({'is_production': True, 'secret_key': ''})


def test_vercel_requires_database_url(monkeypatch):
    monkeypatch.setenv('VERCEL', '1')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('POSTGRES_URL', raising=False)
    with pytest.raises(ConfigError, match='DATABASE_URL'):
        load_settings({'secret_key': 'x'})


def test_vercel_accepts_postgres_url(monkeypatch):
    monkeypatch.setenv('VERCEL', '1')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('POSTGRES_URL', 'postgres://u:p@db.example.com:6543/postgres')
    assert load_settings({'secret_key': 'x'}).database_url == 'postgres://u:p@db.example.com:6543/postgres'


def test_libpq_url_drops_unknown_parameters():
    url = 'postgres://u:p@h:6543/postgres?sslmode=require&supa=base-pooler.x&options=-c%20search_path%3Dpublic'
    assert libpq_url(url) == 'postgres://u:p@h:6543/postgres?sslmode=require&options=-c%20search_path%3Dpublic'
    assert libpq_url('postgres://u:p@h/db') == 'postgres://u:p@h/db'


def test_healthz(client):
    response = client.get('/healthz')
    assert response.status_code == 200 and response.get_json()['status'] == 'ok'


def test_demo_creation_is_rate_limited(client):
    for _ in range(6):
        assert client.post('/demo').headers['Location'].endswith('/dashboard')
        client.post('/logout')
    limited = client.post('/demo', follow_redirects=True)
    assert 'Too many demo accounts' in limited.get_data(as_text=True)


def test_expired_demo_accounts_are_cleaned_up(client, db):
    client.post('/demo')
    db.execute("UPDATE users SET created_at = '2000-01-01T00:00:00Z' WHERE is_demo = 1")
    db.commit()
    client.post('/logout')
    client.post('/demo')
    assert db.scalar('SELECT COUNT(*) AS n FROM users WHERE is_demo = 1') == 1


def test_unknown_pages_and_api_errors(client, user):
    assert client.get('/does-not-exist').status_code == 404
    api_404 = client.get('/api/nothing')
    assert api_404.status_code == 404 and api_404.is_json
