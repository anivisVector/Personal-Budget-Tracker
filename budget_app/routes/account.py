import json

from flask import Blueprint, Response, flash, g, redirect, render_template, request, session, url_for

from ..db import get_db, integrity_errors
from ..security import hash_password, login_required, verify_password
from ..services import utc_now_iso
from ..validation import EMAIL_RE, password_problem

bp = Blueprint('account', __name__, url_prefix='/account')


def _render(errors=None, status=200):
    db = get_db()
    counts = db.one(
        'SELECT (SELECT COUNT(*) FROM transactions WHERE user_id = ?) AS transactions, '
        '(SELECT COUNT(*) FROM budgets WHERE user_id = ?) AS budgets, '
        '(SELECT COUNT(*) FROM categories WHERE user_id = ?) AS categories',
        (g.user['id'], g.user['id'], g.user['id']),
    )
    return render_template('account.html', counts=counts, errors=errors or {}), status


def _check_current_password(errors: dict) -> bool:
    if g.user['is_demo']:
        errors['current_password'] = 'Demo accounts can\'t change sign-in details.'
        return False
    stored = get_db().scalar('SELECT password FROM users WHERE id = ?', (g.user['id'],))
    if not verify_password(stored, request.form.get('current_password') or ''):
        errors['current_password'] = 'Your current password is incorrect.'
        return False
    return True


@bp.get('/')
@login_required
def index():
    return _render()


@bp.post('/email')
@login_required
def change_email():
    errors: dict = {}
    email = (request.form.get('email') or '').strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        errors['email'] = 'Enter a valid email address.'
    elif _check_current_password(errors):
        db = get_db()
        if db.one('SELECT 1 AS x FROM users WHERE LOWER(email) = ? AND id <> ?', (email, g.user['id'])):
            errors['email'] = 'That email is already used by another account.'
        else:
            try:
                db.execute('UPDATE users SET email = ? WHERE id = ?', (email, g.user['id']))
                db.commit()
            except integrity_errors():
                db.rollback()
                errors['email'] = 'That email is already used by another account.'
    if errors:
        return _render(errors, 400)
    flash('Email updated.', 'success')
    return redirect(url_for('account.index'))


@bp.post('/password')
@login_required
def change_password():
    errors: dict = {}
    new = request.form.get('new_password') or ''
    problem = password_problem(new)
    if problem:
        errors['new_password'] = problem
    elif new != (request.form.get('confirm_password') or ''):
        errors['confirm_password'] = 'Passwords do not match.'
    elif _check_current_password(errors):
        db = get_db()
        db.execute('UPDATE users SET password = ? WHERE id = ?', (hash_password(new), g.user['id']))
        db.commit()
    if errors:
        return _render(errors, 400)
    flash('Password changed.', 'success')
    return redirect(url_for('account.index'))


@bp.get('/export.json')
@login_required
def export_json():
    db = get_db()
    user_id = g.user['id']
    data = {
        'exported_at': utc_now_iso(),
        'account': {'username': g.user['username'], 'email': g.user['email'], 'created_at': g.user['created_at']},
        'categories': db.all('SELECT name, type FROM categories WHERE user_id = ? ORDER BY type, name', (user_id,)),
        'budgets': db.all(
            'SELECT c.name AS category, b.amount, b.month, b.year FROM budgets b JOIN categories c ON c.id = b.category_id '
            'WHERE b.user_id = ? ORDER BY b.year, b.month, c.name',
            (user_id,),
        ),
        'transactions': db.all(
            'SELECT date, type, amount, category, title, description FROM transactions WHERE user_id = ? ORDER BY date, id',
            (user_id,),
        ),
    }
    return Response(
        json.dumps(data, indent=2, ensure_ascii=False),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename="budget-data-export.json"', 'Cache-Control': 'private, no-store'},
    )


@bp.post('/delete')
@login_required
def delete():
    errors: dict = {}
    if (request.form.get('confirm') or '').strip() != 'DELETE':
        errors['confirm'] = 'Type DELETE to confirm.'
    elif g.user['is_demo'] or _check_current_password(errors):
        db = get_db()
        # Foreign keys cascade to categories, transactions, budgets and cached insights.
        db.execute('DELETE FROM users WHERE id = ?', (g.user['id'],))
        db.commit()
        session.clear()
        flash('Your account and all of its data have been permanently deleted.', 'info')
        return redirect(url_for('pages.home'))
    return _render(errors, 400)
