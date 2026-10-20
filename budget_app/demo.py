"""Throwaway demo accounts so visitors can explore the app without signing up.

Each click creates a separate account seeded with ~4 months of realistic sample
data. Demo accounts are flagged `is_demo` and deleted after 24 hours.
"""

from __future__ import annotations

import random
import secrets
from datetime import UTC, date, datetime, timedelta

from .db import Database
from .finance import categories_for, shift_month
from .security import hash_password
from .services import utc_now_iso

DEMO_TTL_HOURS = 24

_RECURRING = [
    # (day, title, category, type, amount)
    (1, 'Monthly salary', 'Salary', 'income', 68000),
    (3, 'Flat rent', 'Rent', 'expense', 18000),
    (6, 'Electricity bill', 'Bills', 'expense', 1750),
    (8, 'Broadband', 'Bills', 'expense', 799),
    (12, 'Mobile recharge', 'Bills', 'expense', 399),
    (15, 'Netflix subscription', 'Entertainment', 'expense', 649),
]
_VARIABLE = [
    # (times per month, title choices, category, (low, high))
    (6, ['Swiggy order', 'Zomato dinner', 'Lunch with team', 'Cafe coffee'], 'Food', (180, 950)),
    (4, ['BigBasket order', 'Weekly vegetables', 'DMart groceries', 'Blinkit milk and eggs'], 'Groceries', (450, 2600)),
    (8, ['Uber to office', 'Metro card recharge', 'Ola auto', 'Petrol'], 'Transport', (90, 1200)),
    (1, ['Amazon order', 'Myntra shoes', 'Flipkart headphones'], 'Shopping', (900, 4200)),
    (1, ['Movie tickets', 'Bowling with friends', 'Concert tickets'], 'Entertainment', (400, 1800)),
    (1, ['Pharmacy', 'Doctor consultation', 'Gym membership'], 'Health', (300, 2200)),
]
_BUDGETS = {'Food': 6000, 'Groceries': 8000, 'Transport': 3500, 'Shopping': 3000, 'Entertainment': 2500}


def cleanup_expired(db: Database) -> int:
    cutoff = (datetime.now(UTC) - timedelta(hours=DEMO_TTL_HOURS)).strftime('%Y-%m-%dT%H:%M:%SZ')
    cur = db.execute('DELETE FROM users WHERE is_demo = 1 AND created_at < ?', (cutoff,))
    return cur.rowcount or 0


def create_demo_user(db: Database, today: date) -> dict:
    username = f'demo-{secrets.token_hex(3)}'
    user_id = db.insert(
        'INSERT INTO users (username, email, password, is_demo, created_at) VALUES (?, ?, ?, 1, ?) RETURNING id',
        (username, f'{username}@demo.invalid', hash_password(secrets.token_urlsafe(24)), utc_now_iso()),
    )
    rng = random.Random(user_id)  # noqa: S311 - sample data, not security-sensitive
    rows = []
    for offset in range(3, -1, -1):
        year, month = shift_month(today.year, today.month, -offset)
        last_day = (date(*shift_month(year, month, 1), 1) - timedelta(days=1)).day
        limit_day = today.day if offset == 0 else last_day

        for day, title, category, type_, amount in _RECURRING:
            if day <= limit_day:
                rows.append((title, category, type_, amount, date(year, month, day)))
        if offset in (1, 3):
            rows.append(('Website project', 'Freelance', 'income', rng.choice([8000, 12000, 15000]), date(year, month, min(20, limit_day))))
        for times, titles, category, (low, high) in _VARIABLE:
            # Scale the current (partial) month so pace projections look realistic.
            count = round(times * limit_day / last_day) if offset == 0 else times
            for _ in range(count):
                amount = round(rng.uniform(low, high) / 10) * 10
                rows.append((rng.choice(titles), category, 'expense', amount, date(year, month, rng.randint(1, limit_day))))

    now = utc_now_iso()
    for title, category, type_, amount, when in rows:
        db.execute(
            'INSERT INTO transactions (user_id, title, amount, type, category, date, description, created_at) '
            "VALUES (?, ?, ?, ?, ?, ?, '', ?)",
            (user_id, title, amount, type_, category, when.isoformat(), now),
        )

    by_name = {c['name']: c['id'] for c in categories_for(db, user_id, 'expense')}
    for name, amount in _BUDGETS.items():
        if name in by_name:
            for offset in (1, 0):
                year, month = shift_month(today.year, today.month, -offset)
                db.execute(
                    'INSERT INTO budgets (user_id, category_id, amount, month, year) VALUES (?, ?, ?, ?, ?)',
                    (user_id, by_name[name], amount, month, year),
                )
    return {'id': user_id, 'username': username}
