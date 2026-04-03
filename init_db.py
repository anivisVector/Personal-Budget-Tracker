import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()


SQLITE_PATH = os.getenv(
    'SQLITE_PATH',
    os.path.join(os.path.dirname(__file__), 'budget_tracker.db'),
)


SCHEMA = '''
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    amount NUMERIC NOT NULL,
    type TEXT NOT NULL,
    date TEXT NOT NULL,
    description TEXT,
    title TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    amount NUMERIC NOT NULL,
    month INTEGER NOT NULL,
    year INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_transactions_user_date ON transactions(user_id, date);
CREATE INDEX IF NOT EXISTS idx_transactions_user_type ON transactions(user_id, type);
CREATE INDEX IF NOT EXISTS idx_budgets_user_month_year ON budgets(user_id, month, year);
'''


DEFAULT_CATEGORIES = [
    # Expense Categories
    (None, 'Food', 'expense'),
    (None, 'Groceries', 'expense'),
    (None, 'Transport', 'expense'),
    (None, 'Bills', 'expense'),
    (None, 'Shopping', 'expense'),
    (None, 'Entertainment', 'expense'),
    (None, 'Health', 'expense'),
    (None, 'Education', 'expense'),
    (None, 'Rent', 'expense'),
    (None, 'Others', 'expense'),

    # Income Categories
    (None, 'Salary', 'income'),
    (None, 'Freelance', 'income'),
    (None, 'Business', 'income'),
    (None, 'Investment', 'income'),
    (None, 'Gift', 'income'),
    (None, 'Other Income', 'income'),
]


PLAIN_CATEGORY_ALIASES = {
    # Convert emoji / replacement-character variants into plain labels
    ('Food', 'expense'): ['Food 🍔', 'Food ?', 'Food?', 'Food �', 'Food�'],
    ('Groceries', 'expense'): ['Groceries 🛒', 'Groceries ?', 'Groceries?', 'Groceries �', 'Groceries�'],
    ('Transport', 'expense'): ['Transport 🚕', 'Transport ?', 'Transport?', 'Transport �', 'Transport�'],
    ('Bills', 'expense'): ['Bills 💡', 'Bills ?', 'Bills?', 'Bills �', 'Bills�'],
    ('Shopping', 'expense'): ['Shopping 🛍️', 'Shopping ?', 'Shopping?', 'Shopping �', 'Shopping�'],
    ('Entertainment', 'expense'): ['Entertainment 🎬', 'Entertainment ?', 'Entertainment?', 'Entertainment �', 'Entertainment�'],
    ('Health', 'expense'): ['Health 🏥', 'Health ?', 'Health?', 'Health �', 'Health�'],
    ('Education', 'expense'): ['Education 📚', 'Education ?', 'Education?', 'Education �', 'Education�'],
    ('Rent', 'expense'): ['Rent 🏠', 'Rent ?', 'Rent?', 'Rent �', 'Rent�'],
}


def _ensure_utf8mb4(cursor, db):
    # No-op for SQLite (kept to preserve structure).
    return


def _make_in_clause(values):
    if not values:
        return '(NULL)', tuple()
    placeholders = ', '.join(['?'] * len(values))
    return f'({placeholders})', tuple(values)


def _migrate_category_aliases(cursor, db):
    changed = 0
    for (plain_name, type_), aliases in PLAIN_CATEGORY_ALIASES.items():
        in_sql, in_params = _make_in_clause(aliases)

        cursor.execute(
            f'UPDATE categories SET name = ? WHERE type = ? AND name IN {in_sql}',
            (plain_name, type_, *in_params),
        )
        changed += cursor.rowcount or 0

        cursor.execute(
            f'UPDATE transactions SET category = ? WHERE type = ? AND category IN {in_sql}',
            (plain_name, type_, *in_params),
        )
        changed += cursor.rowcount or 0

    if changed:
        db.commit()
    return changed


def _seed_default_categories(cursor, db):
    inserted = 0
    updated = 0
    for user_id, name, type_ in DEFAULT_CATEGORIES:
        cursor.execute(
            'SELECT id FROM categories WHERE user_id IS NULL AND name = ? AND type = ? LIMIT 1',
            (name, type_),
        )
        exists = cursor.fetchone()
        if exists:
            continue

        cursor.execute(
            'INSERT INTO categories(user_id, name, type) VALUES(?, ?, ?)',
            (user_id, name, type_),
        )
        inserted += 1

    if inserted or updated:
        db.commit()
    return inserted, updated

def init_sqlite(db_path: str = SQLITE_PATH) -> str:
    dir_name = os.path.dirname(db_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    db = sqlite3.connect(db_path)
    try:
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        cursor.executescript(SCHEMA)
        db.commit()

        migrated = _migrate_category_aliases(cursor, db)
        inserted, updated = _seed_default_categories(cursor, db)

        if migrated:
            print(f"Migrated {migrated} category/transaction labels.")
        if inserted or updated:
            print(f"Seeded {inserted} default categories. Updated {updated} categories.")
        else:
            print("Default categories already present.")
    finally:
        db.close()

    return db_path


def main():
    try:
        path = init_sqlite(SQLITE_PATH)
        print(f"SQLite database ready: {path}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()