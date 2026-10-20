"""Financial queries and calculations shared by pages, the API and the assistant."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .db import Database
from .validation import parse_date

# -- Dates -----------------------------------------------------------------------


def today_in(tz_name: str) -> date:
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        return date.today()


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month_bounds(year: int, month: int) -> tuple[str, str]:
    """ISO start (inclusive) and end (exclusive) dates for a month."""
    next_year, next_month = shift_month(year, month, 1)
    return f'{year:04d}-{month:02d}-01', f'{next_year:04d}-{next_month:02d}-01'


def month_label(year: int, month: int, *, short: bool = False) -> str:
    name = calendar.month_abbr[month] if short else calendar.month_name[month]
    return f'{name} {year}'


# -- Categories -------------------------------------------------------------------


def categories_for(db: Database, user_id: int, type_: str | None = None) -> list[dict]:
    """The user's own categories plus the shared defaults, de-duplicated by name+type."""
    rows = db.all(
        'SELECT id, user_id, name, type FROM categories WHERE user_id = ? OR user_id IS NULL ORDER BY type, name',
        (user_id,),
    )
    merged: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row['name'].lower(), row['type'])
        is_custom = row['user_id'] is not None
        if key not in merged or is_custom:
            merged[key] = {'id': row['id'], 'name': row['name'], 'type': row['type'], 'is_custom': is_custom}
    result = sorted(merged.values(), key=lambda c: (c['type'], c['name'].lower()))
    if type_:
        result = [c for c in result if c['type'] == type_]
    return result


# -- Totals ------------------------------------------------------------------------------


def totals(db: Database, user_id: int, start: str | None = None, end: str | None = None) -> dict:
    sql = (
        "SELECT COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS income, "
        "COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS expense, "
        'COUNT(*) AS count FROM transactions WHERE user_id = ?'
    )
    params: list = [user_id]
    if start:
        sql += ' AND date >= ?'
        params.append(start)
    if end:
        sql += ' AND date < ?'
        params.append(end)
    row = db.one(sql, params) or {}
    income = round(float(row.get('income') or 0), 2)
    expense = round(float(row.get('expense') or 0), 2)
    return {'income': income, 'expense': expense, 'net': round(income - expense, 2), 'count': int(row.get('count') or 0)}


def savings_rate(income: float, expense: float) -> float | None:
    if income <= 0:
        return None
    return (income - expense) / income


def spending_by_category(db: Database, user_id: int, start: str | None = None, end: str | None = None) -> list[dict]:
    sql = "SELECT category, SUM(amount) AS total, COUNT(*) AS count FROM transactions WHERE user_id = ? AND type = 'expense'"
    params: list = [user_id]
    if start:
        sql += ' AND date >= ?'
        params.append(start)
    if end:
        sql += ' AND date < ?'
        params.append(end)
    sql += ' GROUP BY category ORDER BY total DESC'
    rows = db.all(sql, params)
    grand = sum(float(r['total'] or 0) for r in rows) or 1.0
    return [
        {
            'category': r['category'],
            'total': round(float(r['total'] or 0), 2),
            'count': int(r['count'] or 0),
            'share': float(r['total'] or 0) / grand,
        }
        for r in rows
    ]


def fold_other(rows: list[dict], keep: int = 7) -> list[dict]:
    """Keep the largest categories and fold the tail into one 'Other' row."""
    if len(rows) <= keep + 1:
        return rows
    head, tail = rows[:keep], rows[keep:]
    other = {
        'category': f'Other ({len(tail)})',
        'total': round(sum(r['total'] for r in tail), 2),
        'count': sum(r['count'] for r in tail),
        'share': sum(r['share'] for r in tail),
        'is_other': True,
    }
    return head + [other]


def recent_transactions(db: Database, user_id: int, limit: int = 6) -> list[dict]:
    return db.all(
        'SELECT id, title, category, amount, type, date, description FROM transactions '
        'WHERE user_id = ? ORDER BY date DESC, id DESC LIMIT ?',
        (user_id, limit),
    )


# -- Budgets ----------------------------------------------------------------------


def budget_status(db: Database, user_id: int, year: int, month: int, today: date) -> list[dict]:
    """Budgets for a month with spend, pace projection and a status label."""
    start, end = month_bounds(year, month)
    budgets = db.all(
        'SELECT b.id, b.category_id, b.amount, b.month, b.year, c.name FROM budgets b '
        'JOIN categories c ON c.id = b.category_id WHERE b.user_id = ? AND b.month = ? AND b.year = ? '
        'ORDER BY c.name',
        (user_id, month, year),
    )
    if not budgets:
        return []
    spent_rows = db.all(
        "SELECT category, SUM(amount) AS spent FROM transactions WHERE user_id = ? AND type = 'expense' "
        'AND date >= ? AND date < ? GROUP BY category',
        (user_id, start, end),
    )
    spent_by_name: dict[str, float] = {}
    for row in spent_rows:
        key = row['category'].lower()
        spent_by_name[key] = spent_by_name.get(key, 0.0) + float(row['spent'] or 0)

    days_in_month = calendar.monthrange(year, month)[1]
    is_current = (year, month) == (today.year, today.month)
    is_future = (year, month) > (today.year, today.month)
    elapsed = today.day if is_current else days_in_month

    result = []
    for b in budgets:
        limit = float(b['amount'] or 0)
        spent = round(spent_by_name.get(b['name'].lower(), 0.0), 2)
        projected = round(spent / elapsed * days_in_month, 2) if (is_current and elapsed) else spent
        if is_future:
            status = 'upcoming'
        elif spent > limit:
            status = 'over'
        elif is_current and projected > limit:
            status = 'at_risk'
        else:
            status = 'on_track'
        result.append(
            {
                'id': b['id'],
                'category_id': b['category_id'],
                'category': b['name'],
                'month': b['month'],
                'year': b['year'],
                'budget': round(limit, 2),
                'spent': spent,
                'remaining': round(limit - spent, 2),
                'percent': (spent / limit * 100.0) if limit else 0.0,
                'projected': projected,
                'expected_to_date': round(limit * elapsed / days_in_month, 2) if is_current else None,
                'status': status,
            }
        )
    return result


# -- Analytics -------------------------------------------------------------------------


def monthly_series(db: Database, user_id: int, end_year: int, end_month: int, months: int) -> list[dict]:
    """One row per calendar month (gaps included) with a true running balance."""
    start_year, start_month = shift_month(end_year, end_month, -(months - 1))
    window_start, _ = month_bounds(start_year, start_month)
    _, window_end = month_bounds(end_year, end_month)

    opening = totals(db, user_id, end=window_start)['net']
    rows = db.all(
        "SELECT substr(date, 1, 7) AS ym, "
        "SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END) AS income, "
        "SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) AS expense "
        'FROM transactions WHERE user_id = ? AND date >= ? AND date < ? GROUP BY substr(date, 1, 7)',
        (user_id, window_start, window_end),
    )
    by_month = {r['ym']: r for r in rows}

    series = []
    running = opening
    for i in range(months):
        y, m = shift_month(start_year, start_month, i)
        key = f'{y:04d}-{m:02d}'
        income = round(float((by_month.get(key) or {}).get('income') or 0), 2)
        expense = round(float((by_month.get(key) or {}).get('expense') or 0), 2)
        running = round(running + income - expense, 2)
        series.append(
            {
                'key': key,
                'label': month_label(y, m, short=True),
                'year': y,
                'month': m,
                'income': income,
                'expense': expense,
                'net': round(income - expense, 2),
                'balance': running,
                'savings_rate': savings_rate(income, expense),
            }
        )
    return series


# -- Health score ---------------------------------------------------------------------


def health_score(month: dict, balance: float, budgets: list[dict]) -> dict | None:
    """A transparent 0-100 score. Each factor is shown to the user with its points."""
    if month['count'] == 0 and balance == 0:
        return None
    factors = []

    rate = savings_rate(month['income'], month['expense'])
    if rate is None:
        sr_points = 10 if month['expense'] == 0 else 0
        sr_detail = 'No income recorded this month.'
    else:
        sr_points = round(max(0.0, min(50.0, 20 + rate * 100)))
        sr_detail = f'You kept {rate * 100:.0f}% of this month\'s income.'
    factors.append({'label': 'Savings rate', 'points': sr_points, 'max': 50, 'detail': sr_detail})

    if budgets:
        active = [b for b in budgets if b['status'] != 'upcoming']
        within = sum(1 for b in active if b['status'] != 'over')
        at_risk = sum(1 for b in active if b['status'] == 'at_risk')
        b_points = round(max(0.0, 30 * (within / len(active)) - 3 * min(at_risk, 3))) if active else 15
        b_detail = f'{within} of {len(active)} budgets within limit' + (f', {at_risk} at risk.' if at_risk else '.')
    else:
        b_points, b_detail = 15, 'No budgets set for this month (neutral).'
    factors.append({'label': 'Budget adherence', 'points': b_points, 'max': 30, 'detail': b_detail})

    monthly_spend = month['expense']
    if balance <= 0:
        cushion_points, cushion_detail = 0, 'Overall balance is zero or negative.'
    elif monthly_spend <= 0 or balance >= 3 * monthly_spend:
        cushion_points, cushion_detail = 20, 'Balance covers 3+ months of current spending.'
    elif balance >= monthly_spend:
        cushion_points, cushion_detail = 14, 'Balance covers at least one month of spending.'
    else:
        cushion_points, cushion_detail = 8, 'Balance covers less than one month of spending.'
    factors.append({'label': 'Cash cushion', 'points': cushion_points, 'max': 20, 'detail': cushion_detail})

    score = sum(f['points'] for f in factors)
    band = 'strong' if score >= 75 else 'fair' if score >= 50 else 'needs attention'
    return {'score': score, 'band': band, 'factors': factors}


def snapshot(db: Database, user_id: int, today: date) -> dict:
    """Everything the dashboard, AI insights and voice assistant need, in one place."""
    start, end = month_bounds(today.year, today.month)
    prev_y, prev_m = shift_month(today.year, today.month, -1)
    prev_start, prev_end = month_bounds(prev_y, prev_m)

    all_time = totals(db, user_id)
    month = totals(db, user_id, start, end)
    previous = totals(db, user_id, prev_start, prev_end)
    budgets = budget_status(db, user_id, today.year, today.month, today)
    by_category = spending_by_category(db, user_id, start, end)

    return {
        'today': today.isoformat(),
        'month_label': month_label(today.year, today.month),
        'balance': all_time['net'],
        'all_time': all_time,
        'month': month,
        'previous_month': previous,
        'savings_rate': savings_rate(month['income'], month['expense']),
        'top_categories': by_category[:5],
        'by_category': by_category,
        'budgets': budgets,
        'over_budget': [b['category'] for b in budgets if b['status'] == 'over'],
        'at_risk': [b['category'] for b in budgets if b['status'] == 'at_risk'],
        'recent': recent_transactions(db, user_id, 5),
        'health': health_score(month, all_time['net'], budgets),
    }


# -- Transaction filtering --------------------------------------------------------------

SORTS = {
    'date_desc': 'date DESC, id DESC',
    'date_asc': 'date ASC, id ASC',
    'amount_desc': 'amount DESC, id DESC',
    'amount_asc': 'amount ASC, id ASC',
}


@dataclass
class TxnFilters:
    q: str = ''
    type: str = ''
    category: str = ''
    date_from: str = ''
    date_to: str = ''
    sort: str = 'date_desc'

    @classmethod
    def from_args(cls, args) -> TxnFilters:
        def safe_date(value):
            try:
                return parse_date(value) if value else ''
            except ValueError:
                return ''

        type_ = (args.get('type') or '').strip()
        sort = (args.get('sort') or 'date_desc').strip()
        return cls(
            q=(args.get('q') or '').strip()[:80],
            type=type_ if type_ in ('income', 'expense') else '',
            category=(args.get('category') or '').strip()[:60],
            date_from=safe_date(args.get('date_from')),
            date_to=safe_date(args.get('date_to')),
            sort=sort if sort in SORTS else 'date_desc',
        )

    def as_query(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v and not (k == 'sort' and v == 'date_desc')}

    @property
    def active(self) -> bool:
        return any([self.q, self.type, self.category, self.date_from, self.date_to])

    def where(self, user_id: int) -> tuple[str, list]:
        clauses, params = ['user_id = ?'], [user_id]
        if self.type:
            clauses.append('type = ?')
            params.append(self.type)
        if self.category:
            clauses.append('LOWER(category) = LOWER(?)')
            params.append(self.category)
        if self.date_from:
            clauses.append('date >= ?')
            params.append(self.date_from)
        if self.date_to:
            clauses.append('date <= ?')
            params.append(self.date_to)
        if self.q:
            like = '%' + self.q.lower().replace('\\', '').replace('%', '').replace('_', '') + '%'
            clauses.append("(LOWER(COALESCE(title, '')) LIKE ? OR LOWER(COALESCE(description, '')) LIKE ? OR LOWER(category) LIKE ?)")
            params.extend([like, like, like])
        return ' AND '.join(clauses), params

    @property
    def order_by(self) -> str:
        return SORTS[self.sort]
