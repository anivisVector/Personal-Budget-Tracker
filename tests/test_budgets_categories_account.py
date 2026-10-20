from tests.conftest import add_txn, login, register


def _category_id(db, name):
    return db.scalar('SELECT id FROM categories WHERE user_id IS NULL AND name = ?', (name,))


def test_budget_upsert_is_unique_per_category_and_month(client, user, db):
    food = _category_id(db, 'Food')
    for amount in ('5000', '6500'):
        response = client.post('/budgets/', data={'category_id': food, 'amount': amount, 'month': 10, 'year': 2026})
        assert response.status_code == 302
    rows = db.all('SELECT amount FROM budgets')
    assert rows == [{'amount': 6500}]


def test_budget_rejects_income_category_and_bad_values(client, user, db):
    salary = _category_id(db, 'Salary')
    response = client.post('/budgets/', data={'category_id': salary, 'amount': '100', 'month': 10, 'year': 2026})
    assert response.status_code == 400
    response = client.post('/budgets/', data={'category_id': _category_id(db, 'Food'), 'amount': '-1', 'month': 13, 'year': 2026})
    assert response.status_code == 400
    assert db.scalar('SELECT COUNT(*) AS n FROM budgets') == 0


def test_copy_previous_never_overwrites(client, user, db):
    food, rent = _category_id(db, 'Food'), _category_id(db, 'Rent')
    client.post('/budgets/', data={'category_id': food, 'amount': '5000', 'month': 9, 'year': 2026})
    client.post('/budgets/', data={'category_id': rent, 'amount': '18000', 'month': 9, 'year': 2026})
    client.post('/budgets/', data={'category_id': food, 'amount': '7000', 'month': 10, 'year': 2026})
    response = client.post('/budgets/copy-previous', data={'month': 10, 'year': 2026}, follow_redirects=True)
    assert 'Copied 1 budget' in response.get_data(as_text=True)
    october = {r['category_id']: r['amount'] for r in db.all('SELECT category_id, amount FROM budgets WHERE month = 10')}
    assert october == {food: 7000, rent: 18000}


def test_budget_page_shows_status(client, user, db):
    client.post('/budgets/', data={'category_id': _category_id(db, 'Food'), 'amount': '100', 'month': 9, 'year': 2026})
    add_txn(client, amount='150', date='2026-09-10')
    page = client.get('/budgets/?year=2026&month=9').get_data(as_text=True)
    assert 'Over budget' in page and '₹50.00 over budget' in page


def test_categories_create_duplicate_and_delete_rules(client, user, db):
    assert client.post('/categories/', data={'name': 'Gym', 'type': 'expense'}).status_code == 302
    duplicate = client.post('/categories/', data={'name': 'gym', 'type': 'expense'})
    assert duplicate.status_code == 400 and 'already have' in duplicate.get_data(as_text=True)
    default_dup = client.post('/categories/', data={'name': 'FOOD', 'type': 'expense'})
    assert default_dup.status_code == 400

    gym = db.scalar("SELECT id FROM categories WHERE name = 'Gym'")
    client.post('/budgets/', data={'category_id': gym, 'amount': '1500', 'month': 10, 'year': 2026})
    blocked = client.post(f'/categories/{gym}/delete', follow_redirects=True)
    assert 'has budgets' in blocked.get_data(as_text=True)

    db.execute('DELETE FROM budgets')
    db.commit()
    client.post(f'/categories/{gym}/delete')
    assert db.scalar("SELECT COUNT(*) AS n FROM categories WHERE name = 'Gym'") == 0
    # Shared defaults can never be deleted by a user.
    client.post(f'/categories/{_category_id(db, "Food")}/delete')
    assert _category_id(db, 'Food') is not None


def test_category_names_are_escaped_in_html(client, user):
    client.post('/categories/', data={'name': '<script>alert(1)</script>', 'type': 'expense'})
    page = client.get('/categories/').get_data(as_text=True)
    assert '<script>alert(1)</script>' not in page
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page


def test_change_password_requires_current_password(client, user):
    wrong = client.post('/account/password', data={'current_password': 'nope', 'new_password': 'brand new pass', 'confirm_password': 'brand new pass'})
    assert wrong.status_code == 400 and 'current password is incorrect' in wrong.get_data(as_text=True)
    ok = client.post('/account/password', data={'current_password': 'correct horse 1', 'new_password': 'brand new pass', 'confirm_password': 'brand new pass'})
    assert ok.status_code == 302
    client.post('/logout')
    assert login(client, 'asha', 'correct horse 1').status_code == 401
    assert login(client, 'asha', 'brand new pass').status_code == 302


def test_change_email_rejects_taken_address(app):
    a, b = app.test_client(), app.test_client()
    register(a, 'first')
    register(b, 'second')
    response = b.post('/account/email', data={'email': 'FIRST@example.com', 'current_password': 'correct horse 1'})
    assert response.status_code == 400 and 'already used' in response.get_data(as_text=True)


def test_delete_account_removes_all_user_data(client, user, db):
    add_txn(client)
    client.post('/categories/', data={'name': 'Gym', 'type': 'expense'})
    refused = client.post('/account/delete', data={'current_password': 'correct horse 1', 'confirm': 'delete'})
    assert refused.status_code == 400
    response = client.post('/account/delete', data={'current_password': 'correct horse 1', 'confirm': 'DELETE'})
    assert response.status_code == 302
    for table in ('users', 'transactions'):
        assert db.scalar(f'SELECT COUNT(*) AS n FROM {table}') == 0
    assert db.scalar('SELECT COUNT(*) AS n FROM categories WHERE user_id IS NOT NULL') == 0
    assert client.get('/dashboard').status_code == 302


def test_json_export_contains_only_own_data(app):
    a, b = app.test_client(), app.test_client()
    register(a, 'owner')
    register(b, 'other')
    add_txn(a, title='Mine')
    add_txn(b, title='Theirs')
    data = a.get('/account/export.json').get_json()
    assert [t['title'] for t in data['transactions']] == ['Mine']
    assert data['account']['username'] == 'owner'
