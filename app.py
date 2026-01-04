# ---------------- Personal Budget Tracker ----------------
# Tech Stack: Python Flask + MySQL + HTML/CSS/JS (Chart.js)
# Resume-ready full version

from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
from dotenv import load_dotenv
from sympy import python

load_dotenv()

from flask_mysqldb import MySQL
import MySQLdb.cursors

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'fallback_dev_key')


app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = 'av2903'  
app.config['MYSQL_DB'] = 'budget_tracker'

mysql = MySQL(app)

@app.route('/')
def home():
    return render_template('home.html')


# ---------------- AUTH ----------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    message = ''
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM users WHERE username = %s OR email = %s', (username, email))
        account = cursor.fetchone()
        if account:
            flash('Account already exists!', 'danger')
        else:
            cursor.execute('INSERT INTO users(username, email, password) VALUES(%s, %s, %s)',
                           (username, email, password))
            mysql.connection.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
    return render_template('register.html', message=message)

@app.route('/login', methods=['GET', 'POST'])
def login():
    message = ''
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM users WHERE username = %s AND password = %s', (username, password))
        user = cursor.fetchone()
        if user:
            session['loggedin'] = True
            session['id'] = user['id']
            session['username'] = user['username']
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            message = 'Invalid username or password!'
            flash(message, 'danger')
    return render_template('login.html', message=message)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ---------------- DASHBOARD ----------------
@app.route('/dashboard')
def dashboard():
    if 'loggedin' in session:
        user_id = session['id']
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

        cursor.execute('SELECT * FROM transactions WHERE user_id = %s ORDER BY date DESC LIMIT 5', (user_id,))
        transactions = cursor.fetchall()

        cursor.execute('SELECT SUM(amount) FROM transactions WHERE user_id = %s AND type = "income"', (user_id,))
        total_income = cursor.fetchone()['SUM(amount)'] or 0

        cursor.execute('SELECT SUM(amount) FROM transactions WHERE user_id = %s AND type = "expense"', (user_id,))
        total_expense = cursor.fetchone()['SUM(amount)'] or 0

        balance = total_income - total_expense

        # Notifications
        notifications = []
        if balance < 500:
            notifications.append({
                'type': 'warning',
                'message': f'Low balance! Your current balance is ₹{balance}.'
            })

        # Overspending check for current month
        from datetime import datetime
        now = datetime.now()
        month = now.month
        year = now.year
        # Get budgets for current month
        cursor.execute('SELECT b.amount, c.name, b.category_id FROM budgets b JOIN categories c ON b.category_id = c.id WHERE b.user_id = %s AND b.month = %s AND b.year = %s', (user_id, month, year))
        budgets = cursor.fetchall()
        for budget in budgets:
            category_id = budget['category_id']
            category_name = budget['name']
            budget_amount = budget['amount']
            cursor.execute('SELECT SUM(amount) FROM transactions WHERE user_id = %s AND category = %s AND type = "expense" AND MONTH(date) = %s AND YEAR(date) = %s', (user_id, category_name, month, year))
            spent = cursor.fetchone()['SUM(amount)'] or 0
            if spent > budget_amount:
                notifications.append({
                    'type': 'danger',
                    'message': f'Overspending alert! You spent ₹{spent} in {category_name} (budget: ₹{budget_amount}).'
                })

        return render_template('dashboard.html', balance=balance, transactions=transactions, total_income=total_income, total_expense=total_expense, notifications=notifications)
    return redirect(url_for('login'))

# ---------------- TRANSACTIONS ----------------
@app.route('/transactions')
def transactions():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    # fetch categories (user-specific + global)
    cursor.execute('SELECT name, type FROM categories WHERE user_id = %s OR user_id IS NULL', (user_id,))
    categories = cursor.fetchall()
    cursor.execute('SELECT * FROM transactions WHERE user_id = %s ORDER BY date DESC', (user_id,))
    transactions = cursor.fetchall()
    return render_template('transactions.html', categories=categories, transactions=transactions)

# Add transaction route
@app.route('/add_transaction', methods=['POST'])
def add_transaction():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    title = request.form['title']
    amount = request.form['amount']
    txn_type = request.form['type']
    category = request.form['category']
    date = request.form['date']
    description = request.form.get('description', '')
    cursor.execute('''
        INSERT INTO transactions (user_id, title, amount, type, category, date, description)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    ''', (user_id, title, amount, txn_type, category, date, description))
    mysql.connection.commit()
    return redirect(url_for('transactions'))


# ---------------- CATEGORIES ----------------
@app.route('/categories', methods=['GET', 'POST'])
def categories():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    if request.method == 'POST':
        name = request.form['name']
        type_ = request.form['type']
        cursor.execute('INSERT INTO categories(user_id, name, type) VALUES(%s, %s, %s)', (user_id, name, type_))
        mysql.connection.commit()
        return redirect(url_for('categories'))

    cursor.execute('SELECT * FROM categories WHERE user_id = %s', (user_id,))
    categories = cursor.fetchall()

    return render_template('categories.html', categories=categories)

# ---------------- BUDGET ----------------
@app.route('/budget', methods=['GET', 'POST'])
def budget():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    if request.method == 'POST':
        category_id = request.form['category_id']
        amount = request.form['amount']
        month = request.form['month']
        year = request.form['year']

        cursor.execute('SELECT * FROM budgets WHERE user_id = %s AND category_id = %s AND month = %s AND year = %s',
                       (user_id, category_id, month, year))
        existing = cursor.fetchone()

        if existing:
            cursor.execute('UPDATE budgets SET amount = %s WHERE id = %s', (amount, existing['id']))
        else:
            cursor.execute('INSERT INTO budgets(user_id, category_id, amount, month, year) VALUES(%s, %s, %s, %s, %s)',
                           (user_id, category_id, amount, month, year))
        mysql.connection.commit()
        return redirect(url_for('budget'))

    cursor.execute('SELECT * FROM categories WHERE user_id = %s', (user_id,))
    categories = cursor.fetchall()

    cursor.execute('''
        SELECT b.*, c.name FROM budgets b 
        JOIN categories c ON b.category_id = c.id 
        WHERE b.user_id = %s 
        ORDER BY year DESC, month DESC
    ''', (user_id,))
    budgets = cursor.fetchall()

    return render_template('budget.html', categories=categories, budgets=budgets)




# ---------------- PROFILE ----------------
@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    message = ''
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        cursor.execute('UPDATE users SET email = %s, password = %s WHERE id = %s', (email, password, user_id))
        mysql.connection.commit()
        message = 'Profile updated successfully.'

    cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))
    user = cursor.fetchone()
    return render_template('profile.html', user=user, message=message)

# ---------------- MAIN ----------------

# ---------------- ANALYTICS ----------------
@app.route('/analytics')
def analytics():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    user_id = session['id']
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Monthly income/expense aggregation
    cursor.execute('''
        SELECT MONTH(date) AS month, YEAR(date) AS year,
            SUM(CASE WHEN type = "income" THEN amount ELSE 0 END) AS income,
            SUM(CASE WHEN type = "expense" THEN amount ELSE 0 END) AS expense
        FROM transactions
        WHERE user_id = %s
        GROUP BY year, month
        ORDER BY year DESC, month DESC
        LIMIT 12
    ''', (user_id,))
    monthly_data = cursor.fetchall()

    # Category breakdown (expenses only)
    cursor.execute('''
        SELECT category, SUM(amount) AS total
        FROM transactions
        WHERE user_id = %s AND type = "expense"
        GROUP BY category
    ''', (user_id,))
    category_data = cursor.fetchall()

    # Generate unique colors for each category
    base_colors = [
        '#FF6384', '#36A2EB', '#FFCE56', '#8E44AD', '#2ECC71', '#E67E22', '#3498DB',
        '#F39C12', '#1ABC9C', '#D35400', '#C0392B', '#7F8C8D', '#27AE60', '#2980B9', '#9B59B6', '#34495E', '#16A085', '#E84393', '#00B894', '#6C5CE7'
    ]
    category_colors = []
    for i in range(len(category_data)):
        category_colors.append(base_colors[i % len(base_colors)])

    return render_template('analytics.html', monthly_data=monthly_data, category_data=category_data, category_colors=category_colors)

if __name__ == '__main__':
    app.run(debug=True)

 
