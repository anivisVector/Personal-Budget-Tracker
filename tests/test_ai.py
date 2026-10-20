import io
import json
import urllib.error
from datetime import date

import pytest

from budget_app.ai import (
    AIError,
    GeminiClient,
    extract_amount,
    extract_date,
    interpret_command,
    parse_transaction,
    rule_command,
)
from budget_app.classifier import train
from budget_app.schema import DEFAULT_CATEGORIES
from budget_app.validation import ValidationError
from tests.conftest import add_txn, register

TODAY = date(2026, 10, 20)  # a Tuesday
CATS = [{'id': i, 'name': n, 'type': t, 'is_custom': False} for i, (n, t) in enumerate(DEFAULT_CATEGORIES, 1)]
SNAP = {
    'today': '2026-10-20', 'month_label': 'October 2026', 'balance': 12000.0,
    'month': {'income': 50000.0, 'expense': 20000.0, 'net': 30000.0, 'count': 12},
    'previous_month': {'income': 50000.0, 'expense': 30000.0, 'net': 20000.0, 'count': 20},
    'savings_rate': 0.6, 'top_categories': [{'category': 'Rent', 'total': 15000.0}], 'budgets': [],
    'over_budget': [], 'at_risk': [], 'recent': [],
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_opener(payload=None, error=None):
    calls = []

    def opener(request, timeout):
        calls.append(json.loads(request.data))
        if error:
            raise error
        body = {'candidates': [{'content': {'parts': [{'text': json.dumps(payload)}]}}]}
        return FakeResponse(json.dumps(body).encode())

    opener.calls = calls
    return opener


# -- Rule-based language parsing ------------------------------------------------------

@pytest.mark.parametrize(
    'text, expected',
    [('spent 2.5k on dinner', 2500), ('paid ₹1,200 for wifi', 1200), ('bought 2 pizzas for 900', 900), ('got 1.5 lakh bonus', 150000), ('hello', None)],
)
def test_extract_amount(text, expected):
    assert extract_amount(text) == expected


@pytest.mark.parametrize(
    'text, expected',
    [('yesterday', '2026-10-19'), ('last friday', '2026-10-16'), ('on monday', '2026-10-19'), ('3 days ago', '2026-10-17'), ('on 2026-09-30', '2026-09-30'), ('no date', '2026-10-20')],
)
def test_extract_date(text, expected):
    assert extract_date(text, TODAY) == expected


def test_rule_parse_picks_type_and_category():
    result = parse_transaction('paid 450 for uber yesterday', CATS, TODAY, client=None)
    assert result['source'] == 'rules'
    assert result['transaction'] == {
        'amount': 450.0, 'type': 'expense', 'date': '2026-10-19', 'category': 'Transport', 'title': 'Uber',
        'description': 'paid 450 for uber yesterday',
    }
    income = parse_transaction('received salary 68000', CATS, TODAY, client=None)['transaction']
    assert income['type'] == 'income' and income['category'] == 'Salary'


def test_rule_parse_without_amount_raises():
    with pytest.raises(ValidationError):
        parse_transaction('dinner with friends', CATS, TODAY, client=None)


@pytest.mark.parametrize(
    'text, intent',
    [('yes', 'confirm'), ('no thanks', 'cancel'), ('set food budget to 6000', 'set_budget'), ('add a gym expense category', 'add_category'),
     ("what's my balance", 'query_balance'), ('open analytics', 'navigate'), ('paid 300 for lunch', 'add_transaction'),
     ('how am I doing on budgets', 'query_budget'), ('help', 'help'), ('sing a song', 'unknown')],
)
def test_rule_command_intents(text, intent):
    assert rule_command(text, CATS, TODAY)['intent'] == intent


def test_assistant_replies_use_real_numbers_not_model_text():
    result = interpret_command("what's my balance", CATS, SNAP, TODAY, client=None)
    assert '12,000 rupees' in result['speak']


# -- Gemini client + fallbacks -----------------------------------------------------------

def test_gemini_client_builds_schema_request_and_parses_json():
    opener = fake_opener({'is_transaction': True, 'amount': 2500, 'type': 'expense', 'category': 'Food (expense)', 'date': '2026-10-16', 'title': 'Dinner'})
    client = GeminiClient('k', 'gemini-test', opener=opener)
    result = parse_transaction('spent 2.5k on dinner last friday', CATS, TODAY, client)
    assert result['source'] == 'gemini'
    assert result['transaction']['category'] == 'Food' and result['transaction']['amount'] == 2500.0
    sent = opener.calls[0]
    assert sent['generationConfig']['responseMimeType'] == 'application/json'
    assert 'Food' in sent['generationConfig']['responseSchema']['properties']['category']['enum']
    assert 'asha' not in json.dumps(sent)  # no identifying data in the prompt


def test_gemini_failure_falls_back_to_rules():
    error = urllib.error.HTTPError('u', 429, 'quota', {}, None)
    client = GeminiClient('k', 'gemini-test', opener=fake_opener(error=error))
    result = parse_transaction('paid 450 for uber', CATS, TODAY, client)
    assert result['source'] == 'rules' and result['transaction']['category'] == 'Transport'


def test_model_output_is_validated_like_user_input():
    client = GeminiClient('k', 'gemini-test', opener=fake_opener({'is_transaction': True, 'amount': -3, 'type': 'expense', 'category': 'Food', 'date': '2026-10-16', 'title': 'x'}))
    with pytest.raises(ValidationError):
        parse_transaction('weird', CATS, TODAY, client)


def test_gemini_client_errors():
    with pytest.raises(AIError):
        GeminiClient('k', 'bad model/../x')
    bad_json = GeminiClient('k', 'gemini-test', opener=lambda req, timeout: FakeResponse(b'{"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}'))
    with pytest.raises(AIError):
        bad_json.generate_json(system='s', prompt='p', schema={})


def test_assistant_with_gemini_validates_proposed_budget():
    client = GeminiClient('k', 'gemini-test', opener=fake_opener({'intent': 'set_budget', 'category': 'Salary', 'amount': 5000}))
    result = interpret_command('set salary budget to 5000', CATS, SNAP, TODAY, client)
    assert result['intent'] == 'unknown' and result['action'] is None  # income categories can't have budgets


# -- Classifier ----------------------------------------------------------------------------

def test_classifier_learns_from_history():
    rows = [{'title': t, 'description': '', 'category': c, 'type': 'expense'} for t, c in [
        ('Swiggy order', 'Food'), ('Zomato dinner', 'Food'), ('Swiggy lunch', 'Food'),
        ('Uber to office', 'Transport'), ('Ola ride home', 'Transport'), ('Uber airport', 'Transport'),
    ]]
    model = train(rows)
    assert model.predict('swiggy breakfast')[0] == 'Food'
    assert model.predict('uber')[0] == 'Transport'
    assert model.predict('completely unseen words') is None
    assert train(rows[:3]) is None  # too few examples / classes


def test_suggestions_never_leak_between_users(app):
    alice, bob = app.test_client(), app.test_client()
    register(alice, 'alice')
    register(bob, 'bob')
    alice.post('/categories/', data={'name': 'Secret Hobby', 'type': 'expense'})
    for i in range(6):
        add_txn(alice, title=f'falconry gear {i}', category='Secret Hobby')
        add_txn(alice, title=f'pizza night {i}', category='Food')
    assert alice.post('/api/ai/suggest-category', json={'title': 'falconry gear'}).get_json()['suggestion']['category'] == 'Secret Hobby'
    assert bob.post('/api/ai/suggest-category', json={'title': 'falconry gear'}).get_json()['suggestion'] is None


# -- Insights caching & quota ----------------------------------------------------------------

def test_insights_are_cached(client, user, db):
    add_txn(client, date=TODAY.isoformat())
    first = client.get('/api/insights').get_json()
    second = client.get('/api/insights').get_json()
    assert first['insights']['source'] == 'rules' and first['insights']['cached'] is False
    assert second['insights']['cached'] is True
    add_txn(client, amount='999', date=TODAY.isoformat())  # data changed -> recomputed
    assert client.get('/api/insights').get_json()['insights']['cached'] is False


def test_daily_ai_quota_switches_to_rules(make_app, monkeypatch):
    app = make_app(gemini_api_key='fake-key', ai_daily_limit=2)
    calls = []

    def fake_generate(self, **kwargs):
        calls.append(1)
        return {'is_transaction': True, 'amount': 100, 'type': 'expense', 'category': 'Food', 'date': '2026-10-01', 'title': 'Snack'}

    monkeypatch.setattr(GeminiClient, 'generate_json', fake_generate)
    client = app.test_client()
    register(client)
    sources = [client.post('/api/ai/parse', json={'text': 'spent 100 on snack'}).get_json() for _ in range(3)]
    assert [s['source'] for s in sources] == ['gemini', 'gemini', 'rules']
    assert sources[2]['ai_unavailable'] == 'daily_limit'
    assert len(calls) == 2
