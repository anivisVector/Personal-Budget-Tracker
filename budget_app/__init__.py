"""Ledgerly: a personal budget tracker (Flask app factory)."""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import PROJECT_ROOT, flask_config, load_settings
from .db import close_db, connect, get_db
from .finance import today_in
from .formatting import category_icon, format_compact_inr, format_inr, format_percent, nice_date
from .schema import migrate
from .security import apply_security_headers, csp_nonce, same_site_referrer, wants_json

csrf = CSRFProtect()
log = logging.getLogger('budget_app')

APP_NAME = 'Ledgerly'


def _configure_logging(app: Flask) -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    app.logger.setLevel(logging.INFO)


def create_app(overrides: dict | None = None) -> Flask:
    overrides = overrides or {}
    load_dotenv(PROJECT_ROOT / '.env', override=False)
    settings = load_settings(overrides)

    app = Flask(
        __name__,
        static_folder=str(PROJECT_ROOT / 'public' / 'static'),
        static_url_path='/static',
    )
    app.config.update(flask_config(settings))
    app.config.update(overrides.get('flask', {}))
    app.extensions['settings'] = settings
    _configure_logging(app)

    if settings.behind_proxy:
        # Vercel terminates TLS and sets X-Forwarded-*; trust exactly one hop.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    csrf.init_app(app)
    app.teardown_appcontext(close_db)

    @app.before_request
    def load_user():
        g.user = None
        if request.endpoint == 'static':
            return
        user_id = session.get('user_id')
        if user_id:
            user = get_db().one('SELECT id, username, email, is_demo, created_at FROM users WHERE id = ?', (user_id,))
            if user:
                g.user = user
            else:
                session.clear()  # account was deleted

    app.after_request(apply_security_headers)

    @app.context_processor
    def inject_globals():
        return {
            'app_name': APP_NAME,
            'csrf_token': generate_csrf,
            'csp_nonce': csp_nonce,
            'current_user': g.get('user'),
            'ai_enabled': settings.ai_enabled,
            'demo_enabled': settings.demo_enabled,
            'asset_version': settings.asset_version,
        }

    app.add_template_filter(format_inr, 'inr')
    app.add_template_filter(format_compact_inr, 'inr_compact')
    app.add_template_filter(format_percent, 'pct')
    app.add_template_filter(nice_date, 'nice_date')
    app.add_template_filter(category_icon, 'cat_icon')
    app.jinja_env.globals['today'] = lambda: today_in(settings.timezone)

    _register_error_handlers(app)
    _register_blueprints(app)

    if settings.auto_migrate:
        _run_migrations(app)

    return app


def _run_migrations(app: Flask) -> None:
    settings = app.extensions['settings']
    db = connect(settings.database_url, settings.sqlite_path)
    try:
        applied = migrate(db)
        if applied:
            app.logger.info('Database migrated (applied %s).', applied)
    except Exception:
        # Don't crash the import; /healthz and requests will surface the problem.
        app.logger.exception('Database migration failed')
    finally:
        db.close()


def _register_blueprints(app: Flask) -> None:
    from .routes import account, analytics, api, auth, budgets, categories, dashboard, pages, transactions

    for module in (pages, auth, dashboard, transactions, categories, budgets, analytics, account, api):
        app.register_blueprint(module.bp)


def _register_error_handlers(app: Flask) -> None:
    def render_error(code: int, title: str, message: str):
        if wants_json():
            return jsonify({'error': message}), code
        return render_template('error.html', code=code, title=title, message=message), code

    @app.errorhandler(CSRFError)
    def csrf_error(exc):
        if wants_json():
            return jsonify({'error': 'Your session expired. Refresh the page and try again.'}), 400
        flash('Your session expired or the form was out of date. Please try again.', 'warning')
        return redirect(same_site_referrer() or url_for('pages.home'))

    @app.errorhandler(400)
    def bad_request(exc):
        return render_error(400, 'Bad request', 'Something in that request was missing or invalid.')

    @app.errorhandler(404)
    def not_found(exc):
        return render_error(404, 'Page not found', 'The page you\'re looking for doesn\'t exist or was moved.')

    @app.errorhandler(405)
    def method_not_allowed(exc):
        return render_error(405, 'Not allowed', 'That action isn\'t available here.')

    @app.errorhandler(413)
    def too_large(exc):
        return render_error(413, 'File too large', 'Uploads are limited to 1 MB.')

    @app.errorhandler(500)
    def server_error(exc):
        app.logger.exception('Unhandled error on %s %s', request.method, request.path)
        return render_error(500, 'Something went wrong', 'An unexpected error occurred. Please try again in a moment.')
