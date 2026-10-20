from werkzeug.security import check_password_hash

from budget_app.schema import migrate
from tests.conftest import login, register


def test_register_hashes_password_and_signs_in(client, db):
    response = register(client, 'ravi')
    assert response.status_code == 302 and response.headers['Location'].endswith('/dashboard')
    stored = db.scalar('SELECT password FROM users WHERE username = ?', ('ravi',))
    assert stored.startswith('scrypt:') and check_password_hash(stored, 'correct horse 1')
    assert client.get('/dashboard').status_code == 200


def test_register_validation_errors(client):
    response = client.post('/register', data={'username': 'a', 'email': 'nope', 'password': 'short', 'confirm_password': 'short'})
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert 'Use 3-32 letters' in body and 'valid email' in body and 'at least 8 characters' in body


def test_register_rejects_duplicate_username_case_insensitively(client):
    register(client, 'Meera')
    client.post('/logout')
    response = register(client, 'meera', email='other@example.com')
    assert response.status_code == 400
    assert 'already registered' in response.get_data(as_text=True)


def test_login_with_username_or_email(client):
    register(client)
    client.post('/logout')
    assert login(client, 'asha').status_code == 302
    client.post('/logout')
    assert login(client, 'ASHA@example.com').status_code == 302


def test_login_failure_is_generic(client):
    register(client)
    client.post('/logout')
    wrong_password = login(client, 'asha', 'nope nope nope')
    unknown_user = login(client, 'nobody', 'nope nope nope')
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert 'Incorrect username/email or password' in wrong_password.get_data(as_text=True)


def test_login_is_rate_limited_per_account(client):
    register(client)
    client.post('/logout')
    for _ in range(8):
        assert login(client, 'asha', 'wrong password!').status_code == 401
    locked = login(client, 'asha', 'correct horse 1')
    assert locked.status_code == 429


def test_login_ignores_external_next_url(client):
    register(client)
    client.post('/logout')
    response = client.post('/login?next=https://evil.example/steal', data={'identifier': 'asha', 'password': 'correct horse 1'})
    assert response.headers['Location'].endswith('/dashboard')
    client.post('/logout')
    response = client.post('/login?next=/budgets/', data={'identifier': 'asha', 'password': 'correct horse 1'})
    assert response.headers['Location'].endswith('/budgets/')


def test_logout_requires_post(client, user):
    assert client.get('/logout').status_code == 405
    assert client.post('/logout').status_code == 302
    assert client.get('/dashboard').status_code == 302


def test_protected_pages_redirect_and_api_returns_401(client):
    assert '/login' in client.get('/transactions/').headers['Location']
    response = client.get('/api/insights')
    assert response.status_code == 401 and response.is_json


def test_legacy_plaintext_passwords_are_hashed_by_migration(app, client, db):
    db.execute("INSERT INTO users (username, email, password) VALUES ('legacy', 'legacy@example.com', 'oldpass99')")
    db.execute('DELETE FROM schema_migrations WHERE version = 7')
    db.commit()
    assert migrate(db) == [7]
    stored = db.scalar("SELECT password FROM users WHERE username = 'legacy'")
    assert stored.startswith('scrypt:')
    assert login(client, 'legacy', 'oldpass99').status_code == 302


def test_demo_account_is_created_with_data_and_cannot_password_login(client, db):
    response = client.post('/demo')
    assert response.status_code == 302 and response.headers['Location'].endswith('/dashboard')
    demo = db.one('SELECT id, username, is_demo FROM users WHERE is_demo = 1')
    assert demo and demo['username'].startswith('demo-')
    assert db.scalar('SELECT COUNT(*) AS n FROM transactions WHERE user_id = ?', (demo['id'],)) > 20
    assert 'Demo account' in client.get('/dashboard').get_data(as_text=True)
    client.post('/logout')
    assert login(client, demo['username'], 'anything at all').status_code == 401
