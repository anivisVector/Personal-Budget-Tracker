from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from ..finance import categories_for
from ..security import login_required
from ..services import create_category
from ..validation import ValidationError, validate_category

bp = Blueprint('categories', __name__, url_prefix='/categories')


def _render(form=None, errors=None, status=200):
    db = get_db()
    user_id = g.user['id']
    usage = {
        (r['category'].lower(), r['type']): int(r['n'])
        for r in db.all('SELECT category, type, COUNT(*) AS n FROM transactions WHERE user_id = ? GROUP BY category, type', (user_id,))
    }
    budgeted = {r['category_id'] for r in db.all('SELECT DISTINCT category_id FROM budgets WHERE user_id = ?', (user_id,))}
    cats = categories_for(db, user_id)
    for c in cats:
        c['usage'] = usage.get((c['name'].lower(), c['type']), 0)
        c['has_budget'] = c['id'] in budgeted
    grouped = {
        'expense': [c for c in cats if c['type'] == 'expense'],
        'income': [c for c in cats if c['type'] == 'income'],
    }
    return render_template('categories.html', grouped=grouped, form=form or {}, errors=errors or {}), status


@bp.get('/')
@login_required
def index():
    return _render()


@bp.post('/')
@login_required
def create():
    db = get_db()
    try:
        clean = validate_category(request.form)
        create_category(db, g.user['id'], clean)
    except ValidationError as exc:
        return _render(form=request.form, errors=exc.errors, status=400)
    db.commit()
    flash(f'Added {clean["type"]} category "{clean["name"]}".', 'success')
    return redirect(url_for('categories.index'))


@bp.post('/<int:category_id>/delete')
@login_required
def delete(category_id: int):
    db = get_db()
    user_id = g.user['id']
    cat = db.one('SELECT id, name FROM categories WHERE id = ? AND user_id = ?', (category_id, user_id))
    if not cat:
        flash('Only categories you created can be deleted.', 'danger')
        return redirect(url_for('categories.index'))
    if db.one('SELECT 1 AS x FROM budgets WHERE user_id = ? AND category_id = ? LIMIT 1', (user_id, category_id)):
        flash(f'"{cat["name"]}" has budgets. Delete those budgets first.', 'danger')
        return redirect(url_for('categories.index'))
    db.execute('DELETE FROM categories WHERE id = ? AND user_id = ?', (category_id, user_id))
    db.commit()
    flash(f'Deleted "{cat["name"]}". Existing transactions keep their label.', 'success')
    return redirect(url_for('categories.index'))
