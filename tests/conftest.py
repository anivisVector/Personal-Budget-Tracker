"""Test fixtures.

Runs against a fresh SQLite file per test by default. Set TEST_DATABASE_URL to a
Postgres URL (as CI does) to run the same suite against Postgres; each test then
starts from an empty `public` schema.
"""

import os
import re

import pytest

from budget_app import create_app
from budget_app.classifier import cache as classifier_cache

PG_URL = os.getenv('TEST_DATABASE_URL')


def _reset_postgres(url: str) -> None:
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute('DROP SCHEMA IF EXISTS public CASCADE')
        conn.execute('CREATE SCHEMA public')


@pytest.fixture
def make_app(tmp_path):
    def factory(**overrides):
        if PG_URL:
            _reset_postgres(PG_URL)
        flask_overrides = {'TESTING': True, 'WTF_CSRF_ENABLED': False, **overrides.pop('flask', {})}
        config = {
            'secret_key': 'test-secret',
            'database_url': PG_URL,
            'sqlite_path': str(tmp_path / 'test.db'),
            'gemini_api_key': None,
            'is_production': False,
            'demo_enabled': True,
            'flask': flask_overrides,
            **overrides,
        }
        classifier_cache._items.clear()
        return create_app(config)

    return factory


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    from budget_app.db import connect

    settings = app.extensions['settings']
    conn = connect(settings.database_url, settings.sqlite_path)
    if conn.dialect == 'postgres':
        # Inspection queries shouldn't hold a transaction open while the app works.
        conn.conn.autocommit = True
    yield conn
    conn.close()


def register(client, username='asha', email=None, password='correct horse 1'):
    return client.post(
        '/register',
        data={
            'username': username,
            'email': email or f'{username}@example.com',
            'password': password,
            'confirm_password': password,
        },
    )


def login(client, identifier='asha', password='correct horse 1'):
    return client.post('/login', data={'identifier': identifier, 'password': password})


@pytest.fixture
def user(client):
    """A registered, signed-in user."""
    response = register(client)
    assert response.status_code == 302
    return 'asha'


def add_txn(client, **fields):
    data = {'type': 'expense', 'amount': '250', 'category': 'Food', 'date': '2026-10-05', 'title': '', 'description': ''}
    data.update(fields)
    return client.post('/transactions/new', data=data)


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, 'csrf meta tag missing'
    return match.group(1)
