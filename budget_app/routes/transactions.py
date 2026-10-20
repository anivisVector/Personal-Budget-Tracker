from datetime import datetime

from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, url_for

from .. import classifier
from ..csv_io import MAX_ROWS, export_rows, import_csv
from ..db import get_db
from ..finance import TxnFilters, categories_for, today_in
from ..formatting import format_inr
from ..security import login_required, safe_next_url
from ..services import create_transaction, update_transaction
from ..validation import ValidationError, validate_transaction

bp = Blueprint('transactions', __name__, url_prefix='/transactions')

PER_PAGE = 20


@bp.get('/')
@login_required
def index():
    db = get_db()
    user_id = g.user['id']
    filters = TxnFilters.from_args(request.args)
    where, params = filters.where(user_id)

    summary = db.one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS income, "
        f"COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS expense FROM transactions WHERE {where}",
        params,
    )
    total = int(summary['count'])
    pages = max(1, -(-total // PER_PAGE))
    try:
        page = min(max(1, int(request.args.get('page', 1))), pages)
    except ValueError:
        page = 1
    rows = db.all(
        f'SELECT id, title, category, amount, type, date, description FROM transactions WHERE {where} '
        f'ORDER BY {filters.order_by} LIMIT ? OFFSET ?',
        params + [PER_PAGE, (page - 1) * PER_PAGE],
    )
    has_any = total > 0 or bool(db.one('SELECT 1 AS x FROM transactions WHERE user_id = ? LIMIT 1', (user_id,)))
    return render_template(
        'transactions/index.html',
        rows=rows,
        filters=filters,
        summary=summary,
        page=page,
        pages=pages,
        total=total,
        has_any=has_any,
        categories=categories_for(db, user_id),
    )


def _form_page(*, txn=None, form=None, errors=None, status=200):
    db = get_db()
    today = today_in(current_app.extensions['settings'].timezone)
    # Prefill from the query string (used by "Edit details" on the dashboard quick add).
    defaults = {k: request.args.get(k, '')[:240] for k in ('amount', 'category', 'title', 'description')}
    defaults['type'] = request.args.get('type') if request.args.get('type') in ('income', 'expense') else 'expense'
    defaults['date'] = request.args.get('date') or today.isoformat()
    values = form or (txn or defaults)
    return (
        render_template(
            'transactions/form.html',
            txn=txn,
            values=values,
            errors=errors or {},
            categories=categories_for(db, g.user['id']),
            next=safe_next_url(request.values.get('next')),
        ),
        status,
    )


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    if request.method == 'GET':
        return _form_page()
    db = get_db()
    try:
        clean = validate_transaction(request.form, categories_for(db, g.user['id']))
    except ValidationError as exc:
        return _form_page(form=request.form, errors=exc.errors, status=400)
    create_transaction(db, g.user['id'], clean)
    db.commit()
    flash(f'Added {clean["type"]} of {format_inr(clean["amount"])} to {clean["category"]}.', 'success')
    if request.form.get('add_another'):
        return redirect(url_for('transactions.new', type=clean['type'], next=request.form.get('next') or None))
    return redirect(safe_next_url(request.form.get('next')) or url_for('transactions.index'))


def _owned(txn_id: int) -> dict:
    txn = get_db().one(
        'SELECT id, title, category, amount, type, date, description FROM transactions WHERE id = ? AND user_id = ?',
        (txn_id, g.user['id']),
    )
    if not txn:
        abort(404)
    return txn


@bp.route('/<int:txn_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(txn_id: int):
    txn = _owned(txn_id)
    if request.method == 'GET':
        return _form_page(txn=txn)
    db = get_db()
    try:
        clean = validate_transaction(request.form, categories_for(db, g.user['id']))
    except ValidationError as exc:
        return _form_page(txn=txn, form=request.form, errors=exc.errors, status=400)
    update_transaction(db, g.user['id'], txn_id, clean)
    db.commit()
    flash('Transaction updated.', 'success')
    return redirect(safe_next_url(request.form.get('next')) or url_for('transactions.index'))


@bp.post('/<int:txn_id>/delete')
@login_required
def delete(txn_id: int):
    txn = _owned(txn_id)
    db = get_db()
    db.execute('DELETE FROM transactions WHERE id = ? AND user_id = ?', (txn['id'], g.user['id']))
    db.commit()
    classifier.cache.invalidate(g.user['id'])
    flash(f'Deleted "{txn["title"] or txn["category"]}" ({txn["date"]}).', 'success')
    return redirect(safe_next_url(request.form.get('next')) or url_for('transactions.index'))


@bp.get('/export.csv')
@login_required
def export_csv():
    filters = TxnFilters.from_args(request.args)
    where, params = filters.where(g.user['id'])
    rows = get_db().all(
        f'SELECT date, type, amount, category, title, description FROM transactions WHERE {where} ORDER BY {filters.order_by}',
        params,
    )
    filename = f'transactions-{datetime.now().strftime("%Y%m%d-%H%M%S")}.csv'
    return Response(
        export_rows(rows),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'private, no-store'},
    )


@bp.route('/import', methods=['GET', 'POST'])
@login_required
def import_view():
    result, error = None, None
    if request.method == 'POST':
        upload = request.files.get('file')
        if not upload or not upload.filename:
            error = 'Choose a CSV file to import.'
        elif not upload.filename.lower().endswith('.csv'):
            error = 'Only .csv files are supported.'
        else:
            raw = upload.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                error = 'The file is larger than 1 MB. Split it into smaller files.'
            else:
                db = get_db()
                try:
                    result = import_csv(db, g.user['id'], raw, create_missing=bool(request.form.get('create_missing')))
                    db.commit()
                except ValidationError as exc:
                    db.rollback()
                    error = str(exc)
    return render_template('transactions/import.html', result=result, error=error, max_rows=MAX_ROWS), 400 if error else 200
