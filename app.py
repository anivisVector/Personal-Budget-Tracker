# ---------------- Personal Budget Tracker ----------------
# Tech Stack: Python Flask + SQLite + HTML/CSS/JS (Chart.js)
# Resume-ready full version

import csv
import io
import os
import sqlite3
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, Response, flash, redirect, render_template, request, session, url_for
from flask import g

load_dotenv()

from init_db import init_sqlite

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'fallback_dev_key')

SQLITE_PATH = os.getenv(
    'SQLITE_PATH',
    os.path.join(os.path.dirname(__file__), 'budget_tracker.db'),
)

DEFAULT_PER_PAGE = 20


def _dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def get_db():
    db = g.get('db')
    if db is None:
        # timeout helps avoid transient "database is locked" errors under concurrency
        db = sqlite3.connect(SQLITE_PATH, timeout=30)
        db.row_factory = _dict_factory
        # Pragmas are per-connection
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('PRAGMA journal_mode = WAL')
        db.execute('PRAGMA synchronous = NORMAL')
        db.execute('PRAGMA busy_timeout = 5000')
        g.db = db
    return db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


# Ensure the SQLite database exists (tables + default categories)
try:
    init_sqlite(SQLITE_PATH)
except Exception:
    # Avoid crashing on import; runtime requests will surface issues.
    pass


def _parse_int(value, default=None, min_value=None, max_value=None):
    try:
        value_int = int(value)
    except (TypeError, ValueError):
        return default
    if min_value is not None and value_int < min_value:
        return default
    if max_value is not None and value_int > max_value:
        return default
    return value_int


def _current_month_year():
    now = datetime.now()
    return now.month, now.year


def _month_name(month_num: int) -> str:
    try:
        return datetime(2000, month_num, 1).strftime('%b')
    except Exception:
        return str(month_num)


@app.route('/')
def home():
    return render_template('home.html')


@app.route('/favicon.ico')
def favicon():
    return '', 204


# ---------------- AUTH ----------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ? OR email = ?', (username, email))
        account = cursor.fetchone()
        if account:
            flash('Account already exists!', 'danger')
        else:
            cursor.execute('INSERT INTO users(username, email, password) VALUES(?, ?, ?)',
                           (username, email, password))
            db.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password))
        user = cursor.fetchone()
        if user:
            session['loggedin'] = True
            session['id'] = user['id']
            session['username'] = user['username']
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password!', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ---------------- DASHBOARD ----------------
@app.route('/dashboard')
def dashboard():
    if 'loggedin' in session:
        user_id = session['id']
        db = get_db()
        cursor = db.cursor()

        month = _parse_int(request.args.get('month'), None, 1, 12)
        year = _parse_int(request.args.get('year'), None, 2000, 2100)
        if not month or not year:
            month, year = _current_month_year()

        cursor.execute('SELECT * FROM transactions WHERE user_id = ? ORDER BY date DESC, id DESC LIMIT 5', (user_id,))
        transactions = cursor.fetchall()

        # All-time totals
        cursor.execute('SELECT COALESCE(SUM(amount), 0) AS total FROM transactions WHERE user_id = ? AND type = "income"', (user_id,))
        total_income = cursor.fetchone()['total'] or 0

        cursor.execute('SELECT COALESCE(SUM(amount), 0) AS total FROM transactions WHERE user_id = ? AND type = "expense"', (user_id,))
        total_expense = cursor.fetchone()['total'] or 0

        balance = total_income - total_expense

        # Selected month totals
        cursor.execute(
            'SELECT COALESCE(SUM(amount), 0) AS total FROM transactions WHERE user_id = ? AND type = "income" AND CAST(strftime("%m", date) AS INTEGER) = ? AND CAST(strftime("%Y", date) AS INTEGER) = ?',
            (user_id, month, year),
        )
        monthly_income = cursor.fetchone()['total'] or 0

        cursor.execute(
            'SELECT COALESCE(SUM(amount), 0) AS total FROM transactions WHERE user_id = ? AND type = "expense" AND CAST(strftime("%m", date) AS INTEGER) = ? AND CAST(strftime("%Y", date) AS INTEGER) = ?',
            (user_id, month, year),
        )
        monthly_expense = cursor.fetchone()['total'] or 0

        monthly_net = monthly_income - monthly_expense

        # Notifications
        notifications = []
        if balance < 500:
            notifications.append({
                'type': 'warning',
                'message': f'Low balance! Your current balance is ₹{balance}.'
            })

        # Categories for quick add + consistent dropdowns
        cursor.execute(
            'SELECT name, type FROM categories WHERE user_id = ? OR user_id IS NULL GROUP BY name, type ORDER BY type ASC, name ASC',
            (user_id,),
        )
        categories = cursor.fetchall()

        # Overspending check + budget progress for selected month
        cursor.execute(
            'SELECT b.amount, c.name, b.category_id FROM budgets b JOIN categories c ON b.category_id = c.id WHERE b.user_id = ? AND b.month = ? AND b.year = ?',
            (user_id, month, year),
        )
        budgets = cursor.fetchall()

        cursor.execute(
            'SELECT category, SUM(amount) AS spent FROM transactions WHERE user_id = ? AND type = "expense" AND CAST(strftime("%m", date) AS INTEGER) = ? AND CAST(strftime("%Y", date) AS INTEGER) = ? GROUP BY category',
            (user_id, month, year),
        )
        spent_rows = cursor.fetchall()
        spent_by_category = {row['category']: float(row['spent'] or 0) for row in spent_rows}

        budget_progress = []
        for budget in budgets:
            category_name = budget['name']
            budget_amount = float(budget['amount'] or 0)
            spent = float(spent_by_category.get(category_name, 0))
            percent = (spent / budget_amount * 100.0) if budget_amount > 0 else 0.0
            overspent = spent > budget_amount and budget_amount > 0
            remaining = budget_amount - spent

            budget_progress.append(
                {
                    'category': category_name,
                    'budget': budget_amount,
                    'spent': spent,
                    'remaining': remaining,
                    'percent': percent,
                    'overspent': overspent,
                }
            )

            if overspent:
                notifications.append(
                    {
                        'type': 'danger',
                        'message': f'Overspending alert! You spent ₹{spent:.2f} in {category_name} (budget: ₹{budget_amount:.2f}).',
                    }
                )

        # Top 3 closest-to-limit categories
        budget_progress_sorted = sorted(budget_progress, key=lambda x: x['percent'], reverse=True)
        top_progress = budget_progress_sorted[:3]

        return render_template(
            'dashboard.html',
            balance=balance,
            transactions=transactions,
            total_income=total_income,
            total_expense=total_expense,
            monthly_income=monthly_income,
            monthly_expense=monthly_expense,
            monthly_net=monthly_net,
            month=month,
            year=year,
            month_name=_month_name(month),
            categories=categories,
            notifications=notifications,
            budget_progress=top_progress,
        )
    return redirect(url_for('login'))

# ---------------- TRANSACTIONS ----------------
@app.route('/transactions')
def transactions():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    db = get_db()
    cursor = db.cursor()
    # fetch categories (user-specific + global)
    cursor.execute(
        'SELECT name, type FROM categories WHERE user_id = ? OR user_id IS NULL GROUP BY name, type ORDER BY type ASC, name ASC',
        (user_id,),
    )
    categories = cursor.fetchall()

    q = (request.args.get('q') or '').strip()
    txn_type = (request.args.get('type') or '').strip()
    category = (request.args.get('category') or '').strip()
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()
    sort = (request.args.get('sort') or 'date_desc').strip()
    page = _parse_int(request.args.get('page'), 1, 1, 10_000) or 1
    per_page = _parse_int(request.args.get('per_page'), DEFAULT_PER_PAGE, 5, 100) or DEFAULT_PER_PAGE

    where = ['user_id = ?']
    params = [user_id]

    if txn_type in {'income', 'expense'}:
        where.append('type = ?')
        params.append(txn_type)
    if category:
        where.append('category = ?')
        params.append(category)
    if date_from:
        where.append('date >= ?')
        params.append(date_from)
    if date_to:
        where.append('date <= ?')
        params.append(date_to)
    if q:
        where.append('(title LIKE ? OR description LIKE ?)')
        like = f'%{q}%'
        params.extend([like, like])

    where_sql = ' AND '.join(where)

    order_map = {
        'date_desc': 'date DESC, id DESC',
        'date_asc': 'date ASC, id ASC',
        'amount_desc': 'amount DESC, id DESC',
        'amount_asc': 'amount ASC, id ASC',
    }
    order_by = order_map.get(sort, order_map['date_desc'])

    # Pagination counts
    cursor.execute(f'SELECT COUNT(*) AS cnt FROM transactions WHERE {where_sql}', tuple(params))
    total_count = cursor.fetchone()['cnt'] or 0
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page
    cursor.execute(
        f'SELECT * FROM transactions WHERE {where_sql} ORDER BY {order_by} LIMIT ? OFFSET ?',
        tuple(params + [per_page, offset]),
    )
    transactions = cursor.fetchall()

    # Summary totals for filtered set
    cursor.execute(
        f'''SELECT
              SUM(CASE WHEN type = "income" THEN amount ELSE 0 END) AS income,
              SUM(CASE WHEN type = "expense" THEN amount ELSE 0 END) AS expense
            FROM transactions
            WHERE {where_sql}''',
        tuple(params),
    )
    sums = cursor.fetchone() or {}
    filtered_income = sums.get('income') or 0
    filtered_expense = sums.get('expense') or 0

    edit_id = _parse_int(request.args.get('edit'), None, 1)
    edit_txn = None
    if edit_id:
        cursor.execute('SELECT * FROM transactions WHERE id = ? AND user_id = ?', (edit_id, user_id))
        edit_txn = cursor.fetchone()

    return render_template(
        'transactions.html',
        categories=categories,
        transactions=transactions,
        q=q,
        filter_type=txn_type,
        filter_category=category,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        total_count=total_count,
        filtered_income=filtered_income,
        filtered_expense=filtered_expense,
        edit_txn=edit_txn,
    )


@app.route('/transactions/export.csv')
def export_transactions_csv():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    db = get_db()
    cursor = db.cursor()

    q = (request.args.get('q') or '').strip()
    txn_type = (request.args.get('type') or '').strip()
    category = (request.args.get('category') or '').strip()
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()
    sort = (request.args.get('sort') or 'date_desc').strip()

    where = ['user_id = ?']
    params = [user_id]
    if txn_type in {'income', 'expense'}:
        where.append('type = ?')
        params.append(txn_type)
    if category:
        where.append('category = ?')
        params.append(category)
    if date_from:
        where.append('date >= ?')
        params.append(date_from)
    if date_to:
        where.append('date <= ?')
        params.append(date_to)
    if q:
        where.append('(title LIKE ? OR description LIKE ?)')
        like = f'%{q}%'
        params.extend([like, like])
    where_sql = ' AND '.join(where)

    order_map = {
        'date_desc': 'date DESC, id DESC',
        'date_asc': 'date ASC, id ASC',
        'amount_desc': 'amount DESC, id DESC',
        'amount_asc': 'amount ASC, id ASC',
    }
    order_by = order_map.get(sort, order_map['date_desc'])

    cursor.execute(
        f'SELECT id, title, amount, type, category, date, description FROM transactions WHERE {where_sql} ORDER BY {order_by}',
        tuple(params),
    )
    rows = cursor.fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['id', 'title', 'amount', 'type', 'category', 'date', 'description'])
    for row in rows:
        writer.writerow(
            [
                row.get('id'),
                row.get('title') or '',
                row.get('amount') or 0,
                row.get('type') or '',
                row.get('category') or '',
                row.get('date') or '',
                row.get('description') or '',
            ]
        )

    output = buf.getvalue()
    buf.close()
    filename = f"transactions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        output,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )

# Add transaction route
@app.route('/add_transaction', methods=['POST'])
def add_transaction():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    db = get_db()
    cursor = db.cursor()
    title = (request.form.get('title') or '').strip()
    amount = request.form['amount']
    txn_type = request.form['type']
    category = request.form['category']
    date = request.form['date']
    description = request.form.get('description', '')

    # Title is UX-only; if user leaves it blank, default to category.
    if not title:
        title = (category or '').strip() or 'Transaction'
    cursor.execute('''
        INSERT INTO transactions (user_id, title, amount, type, category, date, description)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, title, amount, txn_type, category, date, description))
    db.commit()
    return redirect(url_for('transactions'))


@app.route('/transaction/<int:txn_id>/delete', methods=['POST'])
def delete_transaction(txn_id: int):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    db = get_db()
    cursor = db.cursor()
    cursor.execute('DELETE FROM transactions WHERE id = ? AND user_id = ?', (txn_id, user_id))
    db.commit()
    flash('Transaction deleted.', 'success')
    return redirect(url_for('transactions'))


@app.route('/transaction/<int:txn_id>/edit', methods=['GET', 'POST'])
def edit_transaction(txn_id: int):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT * FROM transactions WHERE id = ? AND user_id = ?', (txn_id, user_id))
    txn = cursor.fetchone()
    if not txn:
        flash('Transaction not found.', 'danger')
        return redirect(url_for('transactions'))

    if request.method == 'POST':
        amount = request.form['amount']
        txn_type = request.form['type']
        category = request.form['category']
        date = request.form['date']
        description = request.form.get('description', '')

        # Title is optional in the UI. If the form doesn't provide it, preserve
        # the existing stored title instead of overwriting it.
        if 'title' in request.form:
            title = (request.form.get('title') or '').strip()
            if not title:
                title = (category or '').strip() or 'Transaction'
        else:
            title = (txn.get('title') or '').strip() or (category or '').strip() or 'Transaction'
        cursor.execute(
            '''UPDATE transactions
               SET title = ?, amount = ?, type = ?, category = ?, date = ?, description = ?
               WHERE id = ? AND user_id = ?''',
            (title, amount, txn_type, category, date, description, txn_id, user_id),
        )
        db.commit()
        flash('Transaction updated.', 'success')
        return redirect(url_for('transactions'))

    # GET: show edit mode inside the transactions page
    return redirect(url_for('transactions', edit=txn_id))


# ---------------- CATEGORIES ----------------
@app.route('/categories', methods=['GET', 'POST'])
def categories():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    db = get_db()
    cursor = db.cursor()

    if request.method == 'POST':
        name = request.form['name']
        type_ = request.form['type']
        cursor.execute('INSERT INTO categories(user_id, name, type) VALUES(?, ?, ?)', (user_id, name, type_))
        db.commit()
        return redirect(url_for('categories'))

    # Show only categories created by the current user (keep global defaults out of this page).
    cursor.execute('SELECT * FROM categories WHERE user_id = ? ORDER BY type ASC, name ASC', (user_id,))
    categories = cursor.fetchall()

    return render_template('categories.html', categories=categories)


@app.route('/category/<int:category_id>/delete', methods=['POST'])
def delete_category(category_id: int):
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    user_id = session['id']
    db = get_db()
    cursor = db.cursor()

    # Only allow deleting categories created by the logged-in user.
    cursor.execute(
        'SELECT id, name FROM categories WHERE id = ? AND user_id = ? LIMIT 1',
        (category_id, user_id),
    )
    category = cursor.fetchone()
    if not category:
        flash('Category not found (or cannot be deleted).', 'danger')
        return redirect(url_for('categories'))

    # Prevent accidental budget deletions; budgets FK would cascade on delete.
    cursor.execute(
        'SELECT 1 FROM budgets WHERE user_id = ? AND category_id = ? LIMIT 1',
        (user_id, category_id),
    )
    has_budget = cursor.fetchone()
    if has_budget:
        flash('This category is used in a budget. Delete the budget first.', 'danger')
        return redirect(url_for('categories'))

    cursor.execute('DELETE FROM categories WHERE id = ? AND user_id = ?', (category_id, user_id))
    db.commit()
    flash('Category deleted.', 'success')
    return redirect(url_for('categories'))

# ---------------- BUDGET ----------------
@app.route('/budget', methods=['GET', 'POST'])
def budget():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    db = get_db()
    cursor = db.cursor()

    view_month = _parse_int(request.args.get('month'), None, 1, 12)
    view_year = _parse_int(request.args.get('year'), None, 2000, 2100)
    if not view_month or not view_year:
        view_month, view_year = _current_month_year()

    if request.method == 'POST':
        category_id = request.form['category_id']
        amount = request.form['amount']
        month = request.form['month']
        year = request.form['year']

        # Budgets are only for expense categories.
        cursor.execute(
            'SELECT id FROM categories WHERE id = ? AND type = ? AND (user_id = ? OR user_id IS NULL) LIMIT 1',
            (category_id, 'expense', user_id),
        )
        category_ok = cursor.fetchone()
        if not category_ok:
            flash('Please select a valid expense category.', 'danger')
            return redirect(url_for('budget', month=month, year=year))

        cursor.execute('SELECT * FROM budgets WHERE user_id = ? AND category_id = ? AND month = ? AND year = ?',
                       (user_id, category_id, month, year))
        existing = cursor.fetchone()

        if existing:
            cursor.execute('UPDATE budgets SET amount = ? WHERE id = ?', (amount, existing['id']))
        else:
            cursor.execute('INSERT INTO budgets(user_id, category_id, amount, month, year) VALUES(?, ?, ?, ?, ?)',
                           (user_id, category_id, amount, month, year))
        db.commit()
        return redirect(url_for('budget', month=month, year=year))

    cursor.execute(
        'SELECT * FROM categories WHERE type = ? AND (user_id = ? OR user_id IS NULL) ORDER BY name ASC',
        ('expense', user_id),
    )
    categories = cursor.fetchall()

    cursor.execute(
        '''
        SELECT b.*, c.name FROM budgets b
        JOIN categories c ON b.category_id = c.id
        WHERE b.user_id = ? AND b.month = ? AND b.year = ? AND c.type = 'expense'
        ORDER BY c.name ASC
        ''',
        (user_id, view_month, view_year),
    )
    budgets = cursor.fetchall()

    edit_id = _parse_int(request.args.get('edit'), None, 1, None)
    edit_budget = None
    if edit_id:
        # Only allow editing budgets owned by the logged-in user and currently in view.
        edit_budget = next((b for b in budgets if int(b.get('id') or 0) == edit_id), None)
        if not edit_budget:
            flash('Budget not found.', 'danger')

    cursor.execute(
        'SELECT category, SUM(amount) AS spent FROM transactions WHERE user_id = ? AND type = "expense" AND CAST(strftime("%m", date) AS INTEGER) = ? AND CAST(strftime("%Y", date) AS INTEGER) = ? GROUP BY category',
        (user_id, view_month, view_year),
    )
    spent_rows = cursor.fetchall()
    spent_by_category = {row['category']: float(row['spent'] or 0) for row in spent_rows}

    budget_rows = []
    for b in budgets:
        budget_amount = float(b['amount'] or 0)
        spent = float(spent_by_category.get(b['name'], 0))
        remaining = budget_amount - spent
        percent = (spent / budget_amount * 100.0) if budget_amount > 0 else 0.0
        overspent = spent > budget_amount and budget_amount > 0
        budget_rows.append(
            {
                **b,
                'spent': spent,
                'remaining': remaining,
                'percent': percent,
                'overspent': overspent,
            }
        )

    return render_template(
        'budget.html',
        categories=categories,
        budgets=budget_rows,
        edit_budget=edit_budget,
        month=view_month,
        year=view_year,
        month_name=_month_name(view_month),
    )


@app.route('/budget/<int:budget_id>/delete', methods=['POST'])
def delete_budget(budget_id: int):
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    user_id = session['id']
    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        'SELECT id, month, year FROM budgets WHERE id = ? AND user_id = ? LIMIT 1',
        (budget_id, user_id),
    )
    row = cursor.fetchone()
    if not row:
        flash('Budget not found (or cannot be deleted).', 'danger')
        return redirect(url_for('budget'))

    cursor.execute('DELETE FROM budgets WHERE id = ? AND user_id = ?', (budget_id, user_id))
    db.commit()
    flash('Budget deleted.', 'success')

    month = _parse_int(request.form.get('month'), row.get('month'), 1, 12)
    year = _parse_int(request.form.get('year'), row.get('year'), 2000, 2100)
    if month and year:
        return redirect(url_for('budget', month=month, year=year))
    return redirect(url_for('budget'))


@app.route('/budget/copy_previous', methods=['POST'])
def copy_previous_budget():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    db = get_db()
    cursor = db.cursor()

    month = _parse_int(request.form.get('month'), None, 1, 12)
    year = _parse_int(request.form.get('year'), None, 2000, 2100)
    if not month or not year:
        month, year = _current_month_year()

    prev_month = month - 1
    prev_year = year
    if prev_month == 0:
        prev_month = 12
        prev_year -= 1

    cursor.execute(
        'SELECT category_id, amount FROM budgets WHERE user_id = ? AND month = ? AND year = ?',
        (user_id, prev_month, prev_year),
    )
    prev_budgets = cursor.fetchall()
    if not prev_budgets:
        flash('No budgets found for the previous month.', 'warning')
        return redirect(url_for('budget', month=month, year=year))

    for row in prev_budgets:
        category_id = row['category_id']
        amount = row['amount']
        cursor.execute(
            'SELECT id FROM budgets WHERE user_id = ? AND category_id = ? AND month = ? AND year = ?',
            (user_id, category_id, month, year),
        )
        existing = cursor.fetchone()
        if existing:
            cursor.execute('UPDATE budgets SET amount = ? WHERE id = ?', (amount, existing['id']))
        else:
            cursor.execute(
                'INSERT INTO budgets(user_id, category_id, amount, month, year) VALUES(?, ?, ?, ?, ?)',
                (user_id, category_id, amount, month, year),
            )

    db.commit()
    flash('Copied budgets from previous month.', 'success')
    return redirect(url_for('budget', month=month, year=year))




# ---------------- PROFILE ----------------
@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    db = get_db()
    cursor = db.cursor()

    message = ''
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        cursor.execute('UPDATE users SET email = ?, password = ? WHERE id = ?', (email, password, user_id))
        db.commit()
        message = 'Profile updated successfully.'

    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    return render_template('profile.html', user=user, message=message)

# ---------------- MAIN ----------------

# ---------------- ANALYTICS ----------------
@app.route('/analytics')
def analytics():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    db = get_db()
    cursor = db.cursor()

    months = _parse_int(request.args.get('months'), 12, 1, 36) or 12

    # Monthly income/expense aggregation
    cursor.execute(
        '''
        SELECT CAST(strftime('%m', date) AS INTEGER) AS month,
            CAST(strftime('%Y', date) AS INTEGER) AS year,
            SUM(CASE WHEN type = "income" THEN amount ELSE 0 END) AS income,
            SUM(CASE WHEN type = "expense" THEN amount ELSE 0 END) AS expense
        FROM transactions
        WHERE user_id = ?
        GROUP BY year, month
        ORDER BY year DESC, month DESC
        LIMIT ?
        ''',
        (user_id, months),
    )
    monthly_rows = cursor.fetchall()

    # Convert to chronological order + add net + cumulative balance
    monthly_rows = list(reversed(monthly_rows))
    running = 0.0
    monthly_data = []
    for row in monthly_rows:
        income = float(row.get('income') or 0)
        expense = float(row.get('expense') or 0)
        net = income - expense
        running += net
        monthly_data.append(
            {
                **row,
                'income': income,
                'expense': expense,
                'net': net,
                'balance': running,
            }
        )

    # Category breakdown (expenses only)
    cursor.execute(
        '''
        SELECT category, SUM(amount) AS total
        FROM transactions
        WHERE user_id = ? AND type = "expense"
        GROUP BY category
        ORDER BY total DESC
        ''',
        (user_id,),
    )
    category_data = cursor.fetchall()

    # Insights (based on the returned data)
    top_category = category_data[0]['category'] if category_data else None
    top_category_total = float(category_data[0]['total']) if category_data else 0

    highest_month = None
    highest_month_value = 0.0
    for row in monthly_data:
        if float(row['expense']) > highest_month_value:
            highest_month_value = float(row['expense'])
            highest_month = f"{row['month']}/{row['year']}"

    avg_monthly_expense = (sum(float(r['expense']) for r in monthly_data) / len(monthly_data)) if monthly_data else 0

    # Generate unique colors for each category
    base_colors = [
        '#FF6384', '#36A2EB', '#FFCE56', '#8E44AD', '#2ECC71', '#E67E22', '#3498DB',
        '#F39C12', '#1ABC9C', '#D35400', '#C0392B', '#7F8C8D', '#27AE60', '#2980B9', '#9B59B6', '#34495E', '#16A085', '#E84393', '#00B894', '#6C5CE7'
    ]
    category_colors = []
    for i in range(len(category_data)):
        category_colors.append(base_colors[i % len(base_colors)])

    return render_template(
        'analytics.html',
        monthly_data=monthly_data,
        category_data=category_data,
        category_colors=category_colors,
        months=months,
        top_category=top_category,
        top_category_total=top_category_total,
        highest_month=highest_month,
        highest_month_value=highest_month_value,
        avg_monthly_expense=avg_monthly_expense,
    )

if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_DEBUG', '0') == '1')

 
