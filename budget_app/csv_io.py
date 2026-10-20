"""CSV export and import.

The export format round-trips: a file exported here can be imported back (or into
another account). Imports validate every row, report problems by row number, and
skip rows that already exist so re-importing the same file is safe.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from .db import Database
from .finance import categories_for
from .services import create_category, create_transaction
from .validation import ValidationError, match_category, validate_category, validate_transaction

COLUMNS = ['date', 'type', 'amount', 'category', 'title', 'description']
REQUIRED = {'date', 'type', 'amount', 'category'}
MAX_ROWS = 5000
_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _escape_cell(value) -> str:
    """Neutralise spreadsheet formula injection (a title like =HYPERLINK(...))."""
    text = '' if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _unescape_cell(value: str) -> str:
    return value[1:] if value.startswith("'") and value[1:2] in _FORMULA_PREFIXES else value


def export_rows(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow([r['date'], r['type'], f"{float(r['amount']):.2f}", _escape_cell(r['category']), _escape_cell(r.get('title')), _escape_cell(r.get('description'))])
    return buffer.getvalue()


@dataclass
class ImportResult:
    imported: int = 0
    duplicates: int = 0
    errors: list[tuple[int, str]] = field(default_factory=list)
    created_categories: list[str] = field(default_factory=list)

    @property
    def total_errors(self) -> int:
        return len(self.errors)


def _dedupe_key(txn: dict) -> tuple:
    return (txn['date'], txn['type'], round(float(txn['amount']), 2), (txn['title'] or txn['category']).strip().lower())


def import_csv(db: Database, user_id: int, raw: bytes, *, create_missing: bool) -> ImportResult:
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValidationError({'file': 'The file must be UTF-8 encoded CSV. Re-save it as "CSV UTF-8" and try again.'}) from None

    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or '').strip().lower() for h in (reader.fieldnames or [])}
    missing = REQUIRED - headers
    if missing:
        raise ValidationError({'file': f'Missing column(s): {", ".join(sorted(missing))}. Expected: {", ".join(COLUMNS)}.'})

    categories = categories_for(db, user_id)
    result = ImportResult()
    parsed: list[tuple[int, dict]] = []

    for line_no, row in enumerate(reader, start=2):  # header is line 1
        if line_no - 1 > MAX_ROWS:
            result.errors.append((line_no, f'Stopped after {MAX_ROWS} rows. Split the file and import the rest.'))
            break
        data = {(k or '').strip().lower(): _unescape_cell((v or '').strip()) for k, v in row.items() if k}
        if not any(data.values()):
            continue
        type_ = (data.get('type') or '').lower()
        if create_missing and type_ in ('income', 'expense') and data.get('category') and not match_category(data['category'], type_, categories):
            try:
                clean_cat = validate_category({'name': data['category'], 'type': type_})
                create_category(db, user_id, clean_cat)
                result.created_categories.append(f'{clean_cat["name"]} ({type_})')
                categories = categories_for(db, user_id)
            except ValidationError as exc:
                result.errors.append((line_no, str(exc)))
                continue
        try:
            parsed.append((line_no, validate_transaction(data, categories)))
        except ValidationError as exc:
            result.errors.append((line_no, '; '.join(exc.errors.values())))

    if parsed:
        dates = [t['date'] for _, t in parsed]
        existing = db.all(
            'SELECT date, type, amount, title, category FROM transactions WHERE user_id = ? AND date >= ? AND date <= ?',
            (user_id, min(dates), max(dates)),
        )
        seen = {_dedupe_key(r) for r in existing}
        for _, txn in parsed:
            if _dedupe_key(txn) in seen:
                result.duplicates += 1
                continue
            create_transaction(db, user_id, txn)
            result.imported += 1
    return result
