from flask import Blueprint, current_app, g, render_template, request

from ..db import get_db
from ..finance import fold_other, month_bounds, monthly_series, savings_rate, shift_month, spending_by_category, today_in
from ..security import login_required

bp = Blueprint('analytics', __name__, url_prefix='/analytics')

RANGES = (3, 6, 12, 24)


@bp.get('/')
@login_required
def index():
    db = get_db()
    today = today_in(current_app.extensions['settings'].timezone)
    months = request.args.get('months', type=int)
    months = months if months in RANGES else 6

    series = monthly_series(db, g.user['id'], today.year, today.month, months)
    start_y, start_m = shift_month(today.year, today.month, -(months - 1))
    window_start, _ = month_bounds(start_y, start_m)
    _, window_end = month_bounds(today.year, today.month)

    income = round(sum(r['income'] for r in series), 2)
    expense = round(sum(r['expense'] for r in series), 2)
    # Averages use completed months only, so the partial current month doesn't drag them down.
    completed = series[:-1] or series
    active = [r for r in series if r['income'] or r['expense']]
    stats = {
        'income': income,
        'expense': expense,
        'net': round(income - expense, 2),
        'savings_rate': savings_rate(income, expense),
        'avg_expense': round(sum(r['expense'] for r in completed) / len(completed), 2),
        'highest': max(active, key=lambda r: r['expense']) if active else None,
        'best_saving': max(active, key=lambda r: r['net']) if active else None,
    }
    categories = fold_other(spending_by_category(db, g.user['id'], window_start, window_end), keep=7)
    return render_template('analytics.html', series=series, stats=stats, categories=categories, months=months, ranges=RANGES)
