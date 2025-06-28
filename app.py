from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import uuid
from datetime import datetime
from decimal import Decimal
import re

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Change this in production
DATABASE = 'atm.db'

# Initialize database
def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS accounts (
        id TEXT PRIMARY KEY,
        account_number TEXT UNIQUE NOT NULL,
        pin_hash TEXT NOT NULL,
        name TEXT NOT NULL,
        balance DECIMAL NOT NULL DEFAULT 0.00,
        created_at TEXT NOT NULL
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS transactions (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        type TEXT NOT NULL,
        amount DECIMAL NOT NULL,
        timestamp TEXT NOT NULL,
        FOREIGN KEY (account_id) REFERENCES accounts (id)
    )
    ''')
    
    conn.commit()
    conn.close()

# Database connection helper
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# Input validation
def validate_account_number(account_number):
    return re.match(r'^\d{10,12}$', account_number)

def validate_pin(pin):
    return re.match(r'^\d{4,6}$', pin)

def validate_amount(amount):
    try:
        amount = Decimal(amount)
        return amount > 0
    except:
        return False

# Custom template filters
def format_card_number(value):
    """Format account number to show only last 4 digits"""
    if not value:
        return '•••• •••• •••• ••••'
    visible_part = value[-4:]
    return f'•••• •••• •••• {visible_part}'

def format_date(value):
    """Format ISO date string to readable format"""
    if not value:
        return ''
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%f').strftime('%b %d, %Y %I:%M %p')
    except ValueError:
        return value

# Register filters with Jinja2
app.jinja_env.filters['format_card_number'] = format_card_number
app.jinja_env.filters['format_date'] = format_date

# Website Routes
@app.route('/')
def index():
    if 'account_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/dashboard')
def dashboard():
    if 'account_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    account = conn.execute('''
    SELECT id, account_number, name, balance 
    FROM accounts 
    WHERE id = ?
    ''', (session['account_id'],)).fetchone()
    
    if not account:
        session.clear()
        return redirect(url_for('index'))
    
    transactions = conn.execute('''
    SELECT type, amount, timestamp 
    FROM transactions 
    WHERE account_id = ?
    ORDER BY timestamp DESC
    LIMIT 10
    ''', (session['account_id'],)).fetchall()
    
    conn.close()
    
    return render_template('dashboard.html', 
                         account=dict(account),
                         transactions=[dict(tx) for tx in transactions])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# API Routes
@app.route('/api/accounts', methods=['POST'])
def create_account():
    data = request.get_json()
    
    if not all(key in data for key in ['account_number', 'pin', 'name']):
        return jsonify({'error': 'Missing required fields'}), 400
    
    if not validate_account_number(data['account_number']):
        return jsonify({'error': 'Account number must be 10-12 digits'}), 400
    
    if not validate_pin(data['pin']):
        return jsonify({'error': 'PIN must be 4-6 digits'}), 400
    
    pin_hash = generate_password_hash(data['pin'])
    account_id = str(uuid.uuid4())
    
    try:
        conn = get_db()
        conn.execute('''
        INSERT INTO accounts (id, account_number, pin_hash, name, created_at)
        VALUES (?, ?, ?, ?, ?)
        ''', (account_id, data['account_number'], pin_hash, data['name'], datetime.now().isoformat()))
        
        # Create initial transaction
        transaction_id = str(uuid.uuid4())
        conn.execute('''
        INSERT INTO transactions (id, account_id, type, amount, timestamp)
        VALUES (?, ?, ?, ?, ?)
        ''', (transaction_id, account_id, 'ACCOUNT_CREATED', '0', datetime.now().isoformat()))
        
        conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Account number already exists'}), 400
    finally:
        conn.close()
    
    return jsonify({
        'message': 'Account created successfully',
        'account_id': account_id,
        'account_number': data['account_number']
    }), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    
    if not all(key in data for key in ['account_number', 'pin']):
        return jsonify({'error': 'Missing account number or PIN'}), 400
    
    conn = get_db()
    account = conn.execute('''
    SELECT id, account_number, pin_hash, name, balance 
    FROM accounts 
    WHERE account_number = ?
    ''', (data['account_number'],)).fetchone()
    conn.close()
    
    if not account or not check_password_hash(account['pin_hash'], data['pin']):
        return jsonify({'error': 'Invalid account number or PIN'}), 401
    
    session['account_id'] = account['id']
    
    return jsonify({
        'message': 'Login successful',
        'account': dict(account)
    })

@app.route('/api/accounts/<account_id>/balance', methods=['GET'])
def get_balance(account_id):
    if 'account_id' not in session or session['account_id'] != account_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    account = conn.execute('''
    SELECT id, account_number, name, balance 
    FROM accounts 
    WHERE id = ?
    ''', (account_id,)).fetchone()
    conn.close()
    
    if not account:
        return jsonify({'error': 'Account not found'}), 404
    
    return jsonify({
        'account_number': account['account_number'],
        'balance': str(account['balance']),
        'name': account['name']
    })

@app.route('/api/accounts/<account_id>/withdraw', methods=['POST'])
def withdraw(account_id):
    if 'account_id' not in session or session['account_id'] != account_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    
    if 'amount' not in data:
        return jsonify({'error': 'Amount is required'}), 400
    
    if not validate_amount(data['amount']):
        return jsonify({'error': 'Invalid amount'}), 400
    
    amount = Decimal(data['amount'])
    
    conn = get_db()
    try:
        # Get current balance
        account = conn.execute('SELECT balance FROM accounts WHERE id = ?', (account_id,)).fetchone()
        if not account:
            return jsonify({'error': 'Account not found'}), 404
        
        current_balance = Decimal(account['balance'])
        
        if current_balance < amount:
            return jsonify({'error': 'Insufficient funds'}), 400
        
        # Update balance
        new_balance = current_balance - amount
        conn.execute('UPDATE accounts SET balance = ? WHERE id = ?', (str(new_balance), account_id))
        
        # Record transaction
        transaction_id = str(uuid.uuid4())
        conn.execute('''
        INSERT INTO transactions (id, account_id, type, amount, timestamp)
        VALUES (?, ?, ?, ?, ?)
        ''', (transaction_id, account_id, 'WITHDRAWAL', str(amount), datetime.now().isoformat()))
        
        conn.commit()
        
        return jsonify({
            'message': 'Withdrawal successful',
            'new_balance': str(new_balance),
            'transaction_id': transaction_id
        })
    finally:
        conn.close()

@app.route('/api/accounts/<account_id>/deposit', methods=['POST'])
def deposit(account_id):
    if 'account_id' not in session or session['account_id'] != account_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    
    if 'amount' not in data:
        return jsonify({'error': 'Amount is required'}), 400
    
    if not validate_amount(data['amount']):
        return jsonify({'error': 'Invalid amount'}), 400
    
    amount = Decimal(data['amount'])
    
    conn = get_db()
    try:
        # Get current balance
        account = conn.execute('SELECT balance FROM accounts WHERE id = ?', (account_id,)).fetchone()
        if not account:
            return jsonify({'error': 'Account not found'}), 404
        
        current_balance = Decimal(account['balance'])
        
        # Update balance
        new_balance = current_balance + amount
        conn.execute('UPDATE accounts SET balance = ? WHERE id = ?', (str(new_balance), account_id))
        
        # Record transaction
        transaction_id = str(uuid.uuid4())
        conn.execute('''
        INSERT INTO transactions (id, account_id, type, amount, timestamp)
        VALUES (?, ?, ?, ?, ?)
        ''', (transaction_id, account_id, 'DEPOSIT', str(amount), datetime.now().isoformat()))
        
        conn.commit()
        
        return jsonify({
            'message': 'Deposit successful',
            'new_balance': str(new_balance),
            'transaction_id': transaction_id
        })
    finally:
        conn.close()

@app.route('/api/accounts/<account_id>/transactions', methods=['GET'])
def get_transactions(account_id):
    if 'account_id' not in session or session['account_id'] != account_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    try:
        account = conn.execute('SELECT id FROM accounts WHERE id = ?', (account_id,)).fetchone()
        if not account:
            return jsonify({'error': 'Account not found'}), 404
        
        transactions = conn.execute('''
        SELECT type, amount, timestamp 
        FROM transactions 
        WHERE account_id = ?
        ORDER BY timestamp DESC
        LIMIT 50
        ''', (account_id,)).fetchall()
        
        return jsonify({
            'transactions': [dict(tx) for tx in transactions]
        })
    finally:
        conn.close()

# Error handlers
@app.errorhandler(404)
def not_found(error):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(error):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    return render_template('500.html'), 500

if __name__ == '__main__':
    init_db()
    app.run(debug=True)