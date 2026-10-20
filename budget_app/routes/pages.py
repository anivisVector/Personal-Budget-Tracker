from flask import Blueprint, current_app, g, jsonify, redirect, render_template, url_for

from ..db import get_db
from ..schema import MIGRATIONS, current_version

bp = Blueprint('pages', __name__)


@bp.get('/')
def home():
    if g.user:
        return redirect(url_for('dashboard.index'))
    return render_template('home.html')


@bp.get('/favicon.ico')
def favicon():
    return redirect(url_for('static', filename='favicon.svg'), code=301)


@bp.get('/healthz')
def healthz():
    """Liveness + database check for uptime monitors and the daily keep-alive cron.

    Runs a real query so a free-tier Postgres project registers activity."""
    try:
        version = current_version(get_db())
        ok = version == MIGRATIONS[-1][0]
        body = {'status': 'ok' if ok else 'degraded', 'database': 'ok', 'schema_version': version}
        return jsonify(body), 200 if ok else 503
    except Exception:
        current_app.logger.exception('Health check failed')
        return jsonify({'status': 'error', 'database': 'unreachable'}), 503
