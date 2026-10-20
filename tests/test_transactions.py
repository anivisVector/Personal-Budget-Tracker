import io

from tests.conftest import add_txn, register


def test_add_transaction_and_list(client, user):
    response = add_txn(client, amount='1,250.505', title='Team lunch')
    assert response.status_code == 302
    page = client.get('/transactions/').get_data(as_text=True)
    assert 'Team lunch' in page and '₹1,250.51' in page  # rounded half-up to paise


def test_invalid_transactions_are_rejected_with_field_errors(client, user, db):
    cases = [
        ({'amount': '-5'}, 'greater than zero'),
        ({'amount': 'abc'}, 'Enter an amount'),
        ({'date': '2026-13-40'}, 'valid date'),
        ({'type': 'gift'}, 'income or expense'),
        ({'category': 'Salary'}, 'not one of your expense categories'),  # income category on an expense
    ]
    for fields, message in cases:
        response = add_txn(client, **fields)
        assert response.status_code == 400, fields
        assert message in response.get_data(as_text=True), fields
    assert db.scalar('SELECT COUNT(*) AS n FROM transactions') == 0


def test_title_defaults_to_category(client, user, db):
    add_txn(client, title='   ')
    assert db.scalar('SELECT title FROM transactions') == 'Food'


def test_edit_and_delete_own_transaction(client, user, db):
    add_txn(client, title='Coffee')
    txn_id = db.scalar('SELECT id FROM transactions')
    response = client.post(f'/transactions/{txn_id}/edit', data={'type': 'expense', 'amount': '99', 'category': 'Food', 'date': '2026-10-06', 'title': 'Better coffee'})
    assert response.status_code == 302
    assert db.one('SELECT title, amount FROM transactions WHERE id = ?', (txn_id,)) == {'title': 'Better coffee', 'amount': 99}
    assert client.post(f'/transactions/{txn_id}/delete').status_code == 302
    assert db.scalar('SELECT COUNT(*) AS n FROM transactions') == 0


def test_users_cannot_touch_each_others_transactions(app, db):
    alice, bob = app.test_client(), app.test_client()
    register(alice, 'alice')
    register(bob, 'bob')
    add_txn(alice, title='Alice private')
    txn_id = db.scalar('SELECT id FROM transactions')

    assert bob.get(f'/transactions/{txn_id}/edit').status_code == 404
    assert bob.post(f'/transactions/{txn_id}/edit', data={'type': 'expense', 'amount': '1', 'category': 'Food', 'date': '2026-10-01'}).status_code == 404
    assert bob.post(f'/transactions/{txn_id}/delete').status_code == 404
    assert 'Alice private' not in bob.get('/transactions/').get_data(as_text=True)
    assert 'Alice private' not in bob.get('/transactions/export.csv').get_data(as_text=True)
    assert db.scalar('SELECT COUNT(*) AS n FROM transactions') == 1


def test_filters_search_and_pagination(client, user):
    for i in range(25):
        add_txn(client, title=f'Item {i:02d}', date=f'2026-09-{(i % 28) + 1:02d}')
    add_txn(client, type='income', category='Salary', amount='50000', title='October pay', date='2026-10-01')

    page1 = client.get('/transactions/').get_data(as_text=True)
    assert '26</strong> results' in page1 and 'Page 1 of 2' in page1
    income_only = client.get('/transactions/?type=income').get_data(as_text=True)
    assert 'October pay' in income_only and 'Item 01' not in income_only
    search = client.get('/transactions/?q=item 0').get_data(as_text=True)
    assert '10</strong> results' in search
    ranged = client.get('/transactions/?date_from=2026-09-01&date_to=2026-09-03').get_data(as_text=True)
    assert '3</strong> results' in ranged
    empty = client.get('/transactions/?q=zzzz').get_data(as_text=True)
    assert 'No matches' in empty


def test_csv_export_escapes_formulas_and_round_trips(client, user, db):
    add_txn(client, title='=HYPERLINK("http://evil")', amount='10')
    exported = client.get('/transactions/export.csv')
    assert exported.mimetype == 'text/csv'
    text = exported.get_data(as_text=True)
    assert text.splitlines()[0] == 'date,type,amount,category,title,description'
    assert "'=HYPERLINK" in text  # neutralised for spreadsheets

    # Re-importing the same export is a no-op (duplicates are detected).
    result = client.post('/transactions/import', data={'file': (io.BytesIO(text.encode()), 'export.csv')}, content_type='multipart/form-data')
    assert result.status_code == 200
    assert db.scalar('SELECT COUNT(*) AS n FROM transactions') == 1
    assert db.scalar('SELECT title FROM transactions') == '=HYPERLINK("http://evil")'


def test_csv_import_validates_rows_and_creates_categories(client, user, db):
    csv_text = (
        'Date,Type,Amount,Category,Title\n'
        '2026-10-01,income,68000,Salary,Pay\n'
        '2026-10-02,expense,450.5,Groceries,BigBasket\n'
        '2026-10-03,expense,-20,Food,Bad amount\n'
        'not-a-date,expense,20,Food,Bad date\n'
        '2026-10-04,expense,1200,Pet care,Vet visit\n'
    )
    response = client.post(
        '/transactions/import',
        data={'file': (io.BytesIO(csv_text.encode()), 'history.csv'), 'create_missing': '1'},
        content_type='multipart/form-data',
    )
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert db.scalar('SELECT COUNT(*) AS n FROM transactions') == 3
    assert 'Pet care (expense)' in body
    assert 'greater than zero' in body and 'valid date' in body


def test_csv_import_rejects_missing_columns_and_non_csv(client, user):
    bad = client.post('/transactions/import', data={'file': (io.BytesIO(b'a,b\n1,2\n'), 'x.csv')}, content_type='multipart/form-data')
    assert bad.status_code == 400 and 'Missing column' in bad.get_data(as_text=True)
    wrong = client.post('/transactions/import', data={'file': (io.BytesIO(b'hello'), 'x.txt')}, content_type='multipart/form-data')
    assert wrong.status_code == 400 and 'Only .csv' in wrong.get_data(as_text=True)


def test_json_api_validates(client, user):
    ok = client.post('/api/transactions', json={'type': 'expense', 'amount': 99, 'category': 'food', 'date': '2026-10-01'})
    assert ok.status_code == 201 and ok.get_json()['transaction']['category'] == 'Food'
    bad = client.post('/api/transactions', json={'type': 'expense', 'amount': 0, 'category': 'Nope', 'date': 'x'})
    assert bad.status_code == 422
    assert set(bad.get_json()['fields']) == {'amount', 'category', 'date'}
