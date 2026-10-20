"""Application settings, read once from the environment.

Local development uses a `.env` file (see `.env.example`); on Vercel every value
comes from Project Settings -> Environment Variables.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class ConfigError(RuntimeError):
    """Raised when the environment is unsafe or incomplete for the current mode."""


@dataclass
class Settings:
    is_production: bool
    secret_key: str
    database_url: str | None
    sqlite_path: str
    timezone: str
    gemini_api_key: str | None
    gemini_model: str
    ai_daily_limit: int
    demo_enabled: bool
    auto_migrate: bool
    behind_proxy: bool
    asset_version: str
    extra: dict = field(default_factory=dict)

    @property
    def uses_postgres(self) -> bool:
        return bool(self.database_url)

    @property
    def ai_enabled(self) -> bool:
        return bool(self.gemini_api_key)


def load_settings(overrides: dict | None = None) -> Settings:
    overrides = overrides or {}
    on_vercel = os.getenv('VERCEL') == '1'
    is_production = overrides.get(
        'is_production',
        os.getenv('APP_ENV', '').lower() == 'production' or os.getenv('VERCEL_ENV') in {'production', 'preview'},
    )

    secret_key = overrides.get('secret_key') or os.getenv('SECRET_KEY', '')
    if not secret_key:
        if is_production:
            raise ConfigError('SECRET_KEY must be set in production. Generate one with: python -c "import secrets; print(secrets.token_hex(32))"')
        # Development only: a random key per process. Sessions reset on restart, which is fine locally.
        secret_key = secrets.token_hex(32)

    database_url = overrides.get('database_url', os.getenv('DATABASE_URL') or None)
    if on_vercel and not database_url:
        raise ConfigError(
            'DATABASE_URL is required on Vercel: the serverless filesystem is ephemeral, so SQLite data would be lost. '
            'Connect a Postgres database (e.g. Neon from the Vercel Marketplace) to this project.'
        )

    api_key = overrides.get('gemini_api_key', os.getenv('GEMINI_API_KEY') or None)
    if api_key in {'your_gemini_api_key_here', 'changeme'}:
        api_key = None

    return Settings(
        is_production=bool(is_production),
        secret_key=secret_key,
        database_url=database_url,
        sqlite_path=overrides.get('sqlite_path') or os.getenv('SQLITE_PATH') or str(PROJECT_ROOT / 'budget_tracker.db'),
        timezone=overrides.get('timezone') or os.getenv('APP_TIMEZONE', 'Asia/Kolkata'),
        gemini_api_key=api_key,
        gemini_model=overrides.get('gemini_model') or os.getenv('GEMINI_MODEL', 'gemini-3.5-flash-lite'),
        ai_daily_limit=overrides.get('ai_daily_limit', _int('AI_DAILY_LIMIT', 40)),
        demo_enabled=overrides.get('demo_enabled', _bool('DEMO_MODE_ENABLED', True)),
        auto_migrate=overrides.get('auto_migrate', _bool('AUTO_MIGRATE', True)),
        behind_proxy=overrides.get('behind_proxy', on_vercel or _bool('BEHIND_PROXY', False)),
        asset_version=(os.getenv('VERCEL_GIT_COMMIT_SHA') or secrets.token_hex(4))[:12],
        extra=overrides.get('extra', {}),
    )


def flask_config(settings: Settings) -> dict:
    return {
        'SECRET_KEY': settings.secret_key,
        'SESSION_COOKIE_NAME': 'bt_session',
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'SESSION_COOKIE_SECURE': settings.is_production,
        'PERMANENT_SESSION_LIFETIME': timedelta(days=7),
        'MAX_CONTENT_LENGTH': 2 * 1024 * 1024,  # CSV imports are capped well below this
        'WTF_CSRF_TIME_LIMIT': None,  # token lives as long as the session
        'WTF_CSRF_SSL_STRICT': settings.is_production,
        'JSON_SORT_KEYS': False,
    }
