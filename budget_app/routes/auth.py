from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for

from ..db import get_db, integrity_errors
from ..demo import cleanup_expired, create_demo_user
from ..finance import today_in
from ..security import (
    clear_events,
    client_ip,
    count_events,
    hash_password,
    login_user,
    record_event,
    safe_next_url,
    verify_password,
)
from ..services import utc_now_iso
from ..validation import ValidationError, validate_registration

bp = Blueprint('auth', __name__)

LOGIN_WINDOW = 15 * 60
MAX_FAILS_PER_ACCOUNT = 8
MAX_FAILS_PER_IP = 30
DEMO_PER_IP_PER_HOUR = 6


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if g.user:
        return redirect(url_for('dashboard.index'))
    form, errors = {}, {}
    if request.method == 'POST':
        form = {k: request.form.get(k, '') for k in ('username', 'email')}
        db = get_db()
        try:
            clean = validate_registration(request.form)
            taken = db.one(
                'SELECT LOWER(username) = LOWER(?) AS same_name, LOWER(email) = LOWER(?) AS same_email FROM users '
                'WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?) LIMIT 1',
                (clean['username'], clean['email'], clean['username'], clean['email']),
            )
            if taken:
                key = 'username' if taken['same_name'] else 'email'
                raise ValidationError({key: f'That {key} is already registered.'})
            user_id = db.insert(
                'INSERT INTO users (username, email, password, created_at) VALUES (?, ?, ?, ?) RETURNING id',
                (clean['username'], clean['email'], hash_password(clean['password']), utc_now_iso()),
            )
            db.commit()
        except ValidationError as exc:
            errors = exc.errors
        except integrity_errors():
            db.rollback()
            errors = {'username': 'That username or email is already registered.'}
        else:
            login_user({'id': user_id, 'username': clean['username']})
            flash('Welcome! Your account is ready. Start by adding a transaction or setting a budget.', 'success')
            return redirect(url_for('dashboard.index'))
    return render_template('auth/register.html', form=form, errors=errors), 400 if errors else 200


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('dashboard.index'))
    identifier, error = '', None
    next_url = safe_next_url(request.values.get('next'))
    if request.method == 'POST':
        db = get_db()
        identifier = (request.form.get('identifier') or '').strip()[:254]
        password = request.form.get('password') or ''
        account_key, ip_key = identifier.lower(), client_ip()

        if (
            count_events(db, 'login_fail', account_key, LOGIN_WINDOW) >= MAX_FAILS_PER_ACCOUNT
            or count_events(db, 'login_fail_ip', ip_key, LOGIN_WINDOW) >= MAX_FAILS_PER_IP
        ):
            error = 'Too many failed attempts. Please wait 15 minutes and try again.'
            return render_template('auth/login.html', identifier=identifier, error=error, next=next_url), 429

        user = db.one(
            'SELECT id, username, password, is_demo FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?) '
            'ORDER BY id LIMIT 1',
            (identifier, identifier),
        )
        if user and not user['is_demo'] and verify_password(user['password'], password):
            clear_events(db, 'login_fail', account_key)
            db.commit()
            login_user(user)
            return redirect(next_url or url_for('dashboard.index'))

        if not user:
            verify_password(None, password)  # equalize timing for unknown accounts
        record_event(db, 'login_fail', account_key)
        record_event(db, 'login_fail_ip', ip_key)
        db.commit()
        error = 'Incorrect username/email or password.'
    status = 401 if error else 200
    return render_template('auth/login.html', identifier=identifier, error=error, next=next_url), status


@bp.post('/logout')
def logout():
    session.clear()
    flash('You have been signed out.', 'info')
    return redirect(url_for('pages.home'))


@bp.post('/demo')
def demo():
    settings = current_app.extensions['settings']
    if not settings.demo_enabled:
        abort(404)
    db = get_db()
    ip_key = client_ip()
    if count_events(db, 'demo', ip_key, 3600) >= DEMO_PER_IP_PER_HOUR:
        flash('Too many demo accounts from your network. Please try again later or create a free account.', 'warning')
        return redirect(url_for('pages.home'))
    record_event(db, 'demo', ip_key)
    cleanup_expired(db)
    user = create_demo_user(db, today_in(settings.timezone))
    db.commit()
    login_user(user)  # the demo banner in the app layout explains the account
    return redirect(url_for('dashboard.index'))
