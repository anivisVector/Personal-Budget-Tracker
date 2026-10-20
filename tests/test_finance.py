from datetime import date

from budget_app.finance import budget_status, health_score, month_bounds, monthly_series, shift_month
from budget_app.formatting import format_compact_inr, format_inr
from tests.conftest import add_txn


def test_month_helpers():
    assert shift_month(2026, 1, -1) == (2025, 12)
    assert shift_month(2026, 12, 1) == (2027, 1)
    assert shift_month(2026, 10, -13) == (2025, 9)
    assert month_bounds(2026, 12) == ('2026-12-01', '2027-01-01')


def test_indian_currency_formatting():
    assert format_inr(1234567.5) == '₹12,34,567.50'
    assert format_inr(999) == '₹999.00'
    assert format_inr(-1500, 0) == '−₹1,500'
    assert format_inr(250, 0, sign=True) == '+₹250'
    assert format_compact_inr(340000) == '₹3.4L'
    assert format_compact_inr(12500) == '₹12.5K'


def test_monthly_series_includes_empty_months_and_true_opening_balance(client, user, db):
    add_txn(client, type='income', category='Salary', amount='10000', date='2026-05-01')  # before the window
    add_txn(client, amount='2000', date='2026-08-10')
    add_txn(client, amount='500', date='2026-10-02')
    user_id = db.scalar('SELECT id FROM users')
    series = monthly_series(db, user_id, 2026, 10, 3)
    assert [r['key'] for r in series] == ['2026-08', '2026-09', '2026-10']
    assert [r['expense'] for r in series] == [2000, 0, 500]
    assert [r['balance'] for r in series] == [8000, 8000, 7500]  # starts from the real ₹10,000


def test_budget_pace_projection(client, user, db):
    food = db.scalar("SELECT id FROM categories WHERE name = 'Food' AND user_id IS NULL")
    client.post('/budgets/', data={'category_id': food, 'amount': '3100', 'month': 10, 'year': 2026})
    add_txn(client, amount='1500', date='2026-10-05')
    user_id = db.scalar('SELECT id FROM users')

    early = budget_status(db, user_id, 2026, 10, date(2026, 10, 10))[0]
    assert early['status'] == 'at_risk'          # ₹1,500 in 10 days -> ~₹4,650 by month end
    assert early['projected'] == 4650.0
    late = budget_status(db, user_id, 2026, 10, date(2026, 10, 31))[0]
    assert late['status'] == 'on_track'
    future = budget_status(db, user_id, 2026, 10, date(2026, 9, 15))[0]
    assert future['status'] == 'upcoming'


def test_health_score_is_explained():
    month = {'income': 50000, 'expense': 30000, 'count': 10}
    budgets = [{'status': 'on_track'}, {'status': 'over'}]
    result = health_score(month, balance=100000, budgets=budgets)
    assert [f['label'] for f in result['factors']] == ['Savings rate', 'Budget adherence', 'Cash cushion']
    assert result['score'] == sum(f['points'] for f in result['factors'])
    assert result['factors'][0]['points'] == 50  # 40% savings rate caps the factor
    assert result['factors'][1]['points'] == 15  # 1 of 2 budgets within limit
    assert health_score({'income': 0, 'expense': 0, 'count': 0}, 0, []) is None
