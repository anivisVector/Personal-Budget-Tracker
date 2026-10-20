"""Write operations and cross-cutting helpers shared by HTML views and the JSON API."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime

from flask import current_app

from . import classifier
from .ai import GeminiClient, generate_insights, insights_payload
from .db import Database
from .finance import categories_for, shift_month
from .security import count_events, record_event
from .validation import ValidationError, match_category


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def create_transaction(db: Database, user_id: int, txn: dict) -> int:
    """Insert an already-validated transaction (see validation.validate_transaction)."""
    txn_id = db.insert(
        'INSERT INTO transactions (user_id, title, amount, type, category, date, description, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id',
        (user_id, txn['title'], txn['amount'], txn['type'], txn['category'], txn['date'], txn['description'], utc_now_iso()),
    )
    classifier.cache.invalidate(user_id)
    return txn_id


def update_transaction(db: Database, user_id: int, txn_id: int, txn: dict) -> bool:
    cur = db.execute(
        'UPDATE transactions SET title = ?, amount = ?, type = ?, category = ?, date = ?, description = ? '
        'WHERE id = ? AND user_id = ?',
        (txn['title'], txn['amount'], txn['type'], txn['category'], txn['date'], txn['description'], txn_id, user_id),
    )
    classifier.cache.invalidate(user_id)
    return cur.rowcount > 0


def create_category(db: Database, user_id: int, clean: dict) -> int:
    if match_category(clean['name'], clean['type'], categories_for(db, user_id)):
        raise ValidationError({'name': f'You already have a {clean["type"]} category called "{clean["name"]}".'})
    return db.insert(
        'INSERT INTO categories (user_id, name, type) VALUES (?, ?, ?) RETURNING id',
        (user_id, clean['name'], clean['type']),
    )


def upsert_budget(db: Database, user_id: int, clean: dict) -> str:
    """Create or update the budget for (category, month, year). Returns 'created' or 'updated'."""
    category = next((c for c in categories_for(db, user_id, 'expense') if c['id'] == clean['category_id']), None)
    if not category:
        raise ValidationError({'category_id': 'Choose one of your expense categories.'})
    existing = db.one(
        'SELECT id FROM budgets WHERE user_id = ? AND category_id = ? AND month = ? AND year = ?',
        (user_id, clean['category_id'], clean['month'], clean['year']),
    )
    db.execute(
        'INSERT INTO budgets (user_id, category_id, amount, month, year) VALUES (?, ?, ?, ?, ?) '
        'ON CONFLICT (user_id, category_id, month, year) DO UPDATE SET amount = excluded.amount',
        (user_id, clean['category_id'], clean['amount'], clean['month'], clean['year']),
    )
    return 'updated' if existing else 'created'


def copy_previous_budgets(db: Database, user_id: int, year: int, month: int) -> tuple[int, int]:
    """Copy last month's budgets into this month without overwriting existing ones.

    Returns (copied, skipped_because_already_set)."""
    prev_year, prev_month = shift_month(year, month, -1)
    previous = db.all(
        'SELECT category_id, amount FROM budgets WHERE user_id = ? AND month = ? AND year = ?',
        (user_id, prev_month, prev_year),
    )
    existing = {
        r['category_id']
        for r in db.all('SELECT category_id FROM budgets WHERE user_id = ? AND month = ? AND year = ?', (user_id, month, year))
    }
    copied = 0
    for row in previous:
        if row['category_id'] in existing:
            continue
        db.execute(
            'INSERT INTO budgets (user_id, category_id, amount, month, year) VALUES (?, ?, ?, ?, ?)',
            (user_id, row['category_id'], row['amount'], month, year),
        )
        copied += 1
    return copied, len(previous) - copied


# -- AI access: quota + caching ------------------------------------------------------------


def ai_client(db: Database, user_id: int) -> tuple[GeminiClient | None, str | None]:
    """A Gemini client if AI is configured and the user is under today's quota.

    Returns (client, reason_unavailable)."""
    settings = current_app.extensions['settings']
    if not settings.ai_enabled:
        return None, 'not_configured'
    if count_events(db, 'ai', str(user_id), 86400) >= settings.ai_daily_limit:
        return None, 'daily_limit'
    record_event(db, 'ai', str(user_id))
    db.commit()
    return GeminiClient(settings.gemini_api_key, settings.gemini_model), None


INSIGHTS_TTL_SECONDS = 12 * 3600


def cached_insights(db: Database, user_id: int, snap: dict) -> dict:
    settings = current_app.extensions['settings']
    fingerprint = hashlib.sha256(
        json.dumps({'data': insights_payload(snap), 'ai': settings.ai_enabled, 'model': settings.gemini_model}, sort_keys=True).encode()
    ).hexdigest()
    row = db.one('SELECT fingerprint, payload, created_at FROM ai_cache WHERE user_id = ? AND kind = ?', (user_id, 'insights'))
    now = int(time.time())
    if row and row['fingerprint'] == fingerprint and now - int(row['created_at']) < INSIGHTS_TTL_SECONDS:
        return {**json.loads(row['payload']), 'cached': True}

    client, reason = ai_client(db, user_id) if snap['month']['count'] else (None, None)
    result = generate_insights(snap, client)
    if reason == 'daily_limit':
        result['note'] = 'Daily AI limit reached; showing rule-based insights.'
    db.execute(
        'INSERT INTO ai_cache (user_id, kind, fingerprint, payload, created_at) VALUES (?, ?, ?, ?, ?) '
        'ON CONFLICT (user_id, kind) DO UPDATE SET fingerprint = excluded.fingerprint, payload = excluded.payload, '
        'created_at = excluded.created_at',
        (user_id, 'insights', fingerprint, json.dumps(result), now),
    )
    db.commit()
    return {**result, 'cached': False}
