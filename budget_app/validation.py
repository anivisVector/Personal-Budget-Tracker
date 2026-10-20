"""Input validation shared by HTML forms, the JSON API, CSV import and the assistant.

Every write path funnels through these functions, so the database only ever sees
clean values: positive 2-decimal amounts, ISO dates, known types and categories.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

TYPES = ('income', 'expense')
MAX_AMOUNT = Decimal('100000000')  # ₹10 crore per entry is plenty for a personal tracker
USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{3,32}$')
EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$')
MIN_PASSWORD = 8
MAX_PASSWORD = 128


class ValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__('; '.join(errors.values()))
        self.errors = errors


def clean_text(value, max_len: int) -> str:
    text = ' '.join(str(value or '').split())  # collapse whitespace, drop control newlines
    return text[:max_len]


def parse_amount(value) -> Decimal:
    raw = str(value if value is not None else '').strip().replace(',', '')
    raw = re.sub(r'^(?:₹|rs\.?|inr)\s*', '', raw, flags=re.IGNORECASE)
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError('Enter an amount like 499 or 499.50.') from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError('Amount must be greater than zero.')
    if amount > MAX_AMOUNT:
        raise ValueError('Amount is too large.')
    return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def parse_date(value, *, field_label: str = 'Date') -> str:
    raw = str(value or '').strip()
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f'{field_label} must be a valid date (YYYY-MM-DD).') from None
    if not (1970 <= parsed.year <= 2100):
        raise ValueError(f'{field_label} must be between 1970 and 2100.')
    return parsed.isoformat()


def parse_type(value) -> str:
    type_ = str(value or '').strip().lower()
    if type_ not in TYPES:
        raise ValueError('Choose income or expense.')
    return type_


def parse_month(value) -> int:
    try:
        month = int(value)
    except (TypeError, ValueError):
        raise ValueError('Month must be 1-12.') from None
    if not 1 <= month <= 12:
        raise ValueError('Month must be 1-12.')
    return month


def parse_year(value) -> int:
    try:
        year = int(value)
    except (TypeError, ValueError):
        raise ValueError('Enter a valid year.') from None
    if not 2000 <= year <= 2100:
        raise ValueError('Year must be between 2000 and 2100.')
    return year


def _collect(errors: dict, key: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        errors[key] = str(exc)
        return None


def match_category(name: str, type_: str | None, categories: list[dict]) -> dict | None:
    """Case-insensitive lookup among the user's available categories."""
    # Tolerate labels like "Food (expense)" echoed back by a language model.
    wanted = re.sub(r'\s*\((income|expense)\)\s*$', '', (name or '').strip(), flags=re.IGNORECASE).lower()
    if not wanted:
        return None
    for cat in categories:
        if cat['name'].lower() == wanted and (type_ is None or cat['type'] == type_):
            return cat
    return None


def validate_transaction(data, categories: list[dict]) -> dict:
    """Validate a transaction payload (form or JSON). Returns clean values or raises."""
    errors: dict[str, str] = {}
    amount = _collect(errors, 'amount', parse_amount, data.get('amount'))
    type_ = _collect(errors, 'type', parse_type, data.get('type'))
    date_value = _collect(errors, 'date', parse_date, data.get('date'))

    category_name = clean_text(data.get('category'), 60)
    category = None
    if not category_name:
        errors['category'] = 'Choose a category.'
    elif type_:
        category = match_category(category_name, type_, categories)
        if not category:
            errors['category'] = f'"{category_name}" is not one of your {type_} categories.'

    title = clean_text(data.get('title'), 80)
    description = clean_text(data.get('description'), 240)
    if errors:
        raise ValidationError(errors)
    return {
        'amount': float(amount),
        'type': type_,
        'date': date_value,
        'category': category['name'],
        'title': title or category['name'],
        'description': description,
    }


def validate_category(data) -> dict:
    errors: dict[str, str] = {}
    name = clean_text(data.get('name'), 40)
    if len(name) < 2:
        errors['name'] = 'Category name must be 2-40 characters.'
    type_ = _collect(errors, 'type', parse_type, data.get('type'))
    if errors:
        raise ValidationError(errors)
    return {'name': name, 'type': type_}


def validate_budget(data) -> dict:
    errors: dict[str, str] = {}
    try:
        category_id = int(data.get('category_id'))
    except (TypeError, ValueError):
        category_id = None
        errors['category_id'] = 'Choose an expense category.'
    amount = _collect(errors, 'amount', parse_amount, data.get('amount'))
    month = _collect(errors, 'month', parse_month, data.get('month'))
    year = _collect(errors, 'year', parse_year, data.get('year'))
    if errors:
        raise ValidationError(errors)
    return {'category_id': category_id, 'amount': float(amount), 'month': month, 'year': year}


def validate_registration(data) -> dict:
    errors: dict[str, str] = {}
    username = str(data.get('username') or '').strip()
    email = str(data.get('email') or '').strip().lower()
    password = str(data.get('password') or '')
    confirm = str(data.get('confirm_password') or '')
    if not USERNAME_RE.match(username):
        errors['username'] = 'Use 3-32 letters, numbers, dots, dashes or underscores.'
    if len(email) > 254 or not EMAIL_RE.match(email):
        errors['email'] = 'Enter a valid email address.'
    password_error = password_problem(password)
    if password_error:
        errors['password'] = password_error
    elif password != confirm:
        errors['confirm_password'] = 'Passwords do not match.'
    if errors:
        raise ValidationError(errors)
    return {'username': username, 'email': email, 'password': password}


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f'Use at least {MIN_PASSWORD} characters.'
    if len(password) > MAX_PASSWORD:
        return f'Use at most {MAX_PASSWORD} characters.'
    if password.strip() == '':
        return 'Password cannot be blank.'
    return None
