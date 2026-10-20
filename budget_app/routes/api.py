"""JSON endpoints used by the dashboard, transaction form and voice assistant.

All endpoints require a signed-in session; state-changing ones also require the
CSRF token in the `X-CSRFToken` header (enforced globally by Flask-WTF).
"""

from flask import Blueprint, current_app, g, jsonify, request, url_for

from .. import classifier
from ..ai import PAGES, interpret_command, parse_transaction, rule_command
from ..db import get_db
from ..finance import categories_for, snapshot, today_in
from ..security import login_required
from ..services import ai_client, cached_insights, create_category, create_transaction, upsert_budget
from ..validation import ValidationError, validate_budget, validate_category, validate_transaction

bp = Blueprint('api', __name__, url_prefix='/api')


def _json() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _error(exc: ValidationError, status: int = 422):
    return jsonify({'error': str(exc), 'fields': exc.errors}), status


def _today():
    return today_in(current_app.extensions['settings'].timezone)


@bp.post('/transactions')
@login_required
def create_transaction_api():
    db = get_db()
    try:
        clean = validate_transaction(_json(), categories_for(db, g.user['id']))
    except ValidationError as exc:
        return _error(exc)
    txn_id = create_transaction(db, g.user['id'], clean)
    db.commit()
    return jsonify({'id': txn_id, 'transaction': clean}), 201


@bp.post('/budgets')
@login_required
def upsert_budget_api():
    db = get_db()
    try:
        clean = validate_budget(_json())
        action = upsert_budget(db, g.user['id'], clean)
    except ValidationError as exc:
        return _error(exc)
    db.commit()
    return jsonify({'action': action, 'budget': clean}), 200


@bp.post('/categories')
@login_required
def create_category_api():
    db = get_db()
    try:
        clean = validate_category(_json())
        cat_id = create_category(db, g.user['id'], clean)
    except ValidationError as exc:
        return _error(exc, 409 if 'already' in str(exc) else 422)
    db.commit()
    return jsonify({'id': cat_id, 'category': clean}), 201


@bp.post('/ai/parse')
@login_required
def parse_api():
    db = get_db()
    client, reason = ai_client(db, g.user['id'])
    try:
        result = parse_transaction(_json().get('text', ''), categories_for(db, g.user['id']), _today(), client)
    except ValidationError as exc:
        return _error(exc)
    return jsonify({**result, 'ai_unavailable': reason})


@bp.post('/ai/suggest-category')
@login_required
def suggest_api():
    data = _json()
    text = f"{data.get('title') or ''} {data.get('description') or ''}".strip()[:200]
    type_ = data.get('type') if data.get('type') in ('income', 'expense') else None
    suggestion = classifier.suggest(get_db(), g.user['id'], text, type_) if text else None
    return jsonify({'suggestion': suggestion})


@bp.get('/insights')
@login_required
def insights_api():
    db = get_db()
    snap = snapshot(db, g.user['id'], _today())
    return jsonify({'insights': cached_insights(db, g.user['id'], snap), 'health': snap['health']})


@bp.post('/assistant')
@login_required
def assistant_api():
    db = get_db()
    today = _today()
    text = str(_json().get('text', ''))
    categories = categories_for(db, g.user['id'])
    # "yes"/"no" replies don't need a model call (or a slot of the daily AI quota).
    quick = rule_command(text, categories, today) if text.strip() else {'intent': 'unknown'}
    client = None if quick['intent'] in ('confirm', 'cancel') else ai_client(db, g.user['id'])[0]
    try:
        result = interpret_command(text, categories, snapshot(db, g.user['id'], today), today, client)
    except ValidationError as exc:
        return _error(exc)
    if result.get('page'):
        result['url'] = url_for(PAGES[result['page']])
    return jsonify(result)
