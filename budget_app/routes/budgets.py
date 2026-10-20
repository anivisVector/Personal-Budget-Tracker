from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from ..finance import budget_status, categories_for, month_bounds, month_label, shift_month, spending_by_category, today_in
from ..formatting import format_inr
from ..security import login_required
from ..services import copy_previous_budgets, upsert_budget
from ..validation import ValidationError, parse_month, parse_year, validate_budget

bp = Blueprint('budgets', __name__, url_prefix='/budgets')


def _period():
    today = today_in(current_app.extensions['settings'].timezone)
    try:
        return parse_year(request.values.get('year')), parse_month(request.values.get('month')), today
    except ValueError:
        return today.year, today.month, today


def _render(year, month, today, form=None, errors=None, status=200):
    db = get_db()
    user_id = g.user['id']
    statuses = budget_status(db, user_id, year, month, today)
    start, end = month_bounds(year, month)
    budgeted = {b['category'].lower() for b in statuses}
    unbudgeted = [s for s in spending_by_category(db, user_id, start, end) if s['category'].lower() not in budgeted]
    prev_y, prev_m = shift_month(year, month, -1)
    next_y, next_m = shift_month(year, month, 1)
    return (
        render_template(
            'budgets.html',
            budgets=statuses,
            unbudgeted=unbudgeted,
            categories=categories_for(db, user_id, 'expense'),
            year=year,
            month=month,
            label=month_label(year, month),
            prev={'year': prev_y, 'month': prev_m},
            next={'year': next_y, 'month': next_m},
            is_current=(year, month) == (today.year, today.month),
            totals={'budget': sum(b['budget'] for b in statuses), 'spent': sum(b['spent'] for b in statuses)},
            form=form or {},
            errors=errors or {},
            edit_id=request.args.get('edit', type=int),
        ),
        status,
    )


@bp.get('/')
@login_required
def index():
    return _render(*_period())


@bp.post('/')
@login_required
def save():
    db = get_db()
    year, month, today = _period()
    try:
        clean = validate_budget(request.form)
        action = upsert_budget(db, g.user['id'], clean)
    except ValidationError as exc:
        return _render(year, month, today, form=request.form, errors=exc.errors, status=400)
    db.commit()
    flash(f'Budget {action}: {format_inr(clean["amount"], 0)} for {month_label(clean["year"], clean["month"])}.', 'success')
    return redirect(url_for('budgets.index', year=clean['year'], month=clean['month']))


@bp.post('/<int:budget_id>/delete')
@login_required
def delete(budget_id: int):
    db = get_db()
    row = db.one('SELECT id, month, year FROM budgets WHERE id = ? AND user_id = ?', (budget_id, g.user['id']))
    if not row:
        flash('Budget not found.', 'danger')
        return redirect(url_for('budgets.index'))
    db.execute('DELETE FROM budgets WHERE id = ? AND user_id = ?', (budget_id, g.user['id']))
    db.commit()
    flash('Budget removed.', 'success')
    return redirect(url_for('budgets.index', year=row['year'], month=row['month']))


@bp.post('/copy-previous')
@login_required
def copy_previous():
    db = get_db()
    year, month, _ = _period()
    copied, skipped = copy_previous_budgets(db, g.user['id'], year, month)
    db.commit()
    if copied == 0 and skipped == 0:
        flash('There are no budgets in the previous month to copy.', 'warning')
    elif copied == 0:
        flash('Every budget from last month is already set for this month. Nothing was changed.', 'info')
    else:
        extra = f' {skipped} already existed and were left unchanged.' if skipped else ''
        flash(f'Copied {copied} budget(s) from last month.{extra}', 'success')
    return redirect(url_for('budgets.index', year=year, month=month))
