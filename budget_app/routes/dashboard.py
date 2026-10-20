import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, g, render_template

from ..db import get_db
from ..finance import fold_other, snapshot, today_in
from ..security import login_required

bp = Blueprint('dashboard', __name__)


def _greeting(tz_name: str) -> str:
    try:
        hour = datetime.now(ZoneInfo(tz_name)).hour
    except Exception:
        hour = datetime.now().hour
    return 'Good morning' if 5 <= hour < 12 else 'Good afternoon' if 12 <= hour < 17 else 'Good evening'


def _month_progress(today):
    days = calendar.monthrange(today.year, today.month)[1]
    return {'day': today.day, 'days': days, 'pct': round(today.day / days * 100)}


@bp.get('/dashboard')
@login_required
def index():
    settings = current_app.extensions['settings']
    today = today_in(settings.timezone)
    snap = snapshot(get_db(), g.user['id'], today)
    active_budgets = [b for b in snap['budgets'] if b['status'] != 'upcoming']
    return render_template(
        'dashboard.html',
        snap=snap,
        spending=fold_other(snap['by_category'], keep=5),
        greeting=_greeting(settings.timezone),
        budgets_ok=sum(1 for b in active_budgets if b['status'] == 'on_track'),
        as_of=today,
        progress=_month_progress(today),
    )
