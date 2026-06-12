from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import hashlib
import os
import random
import time
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

DB_PATH = 'database.db'

BTC_BUY_RATE = 7_500_000
GOLD_BUY_RATE = 15_000
SILVER_BUY_RATE_PER_3G = 1_000
STARTING_RUPEES = 100

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            rupees REAL DEFAULT 100,
            bitcoin REAL DEFAULT 0,
            gold REAL DEFAULT 0,
            silver REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_daily_claim TIMESTAMP DEFAULT NULL
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            asset TEXT,
            amount REAL,
            price REAL,
            result REAL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            asset TEXT NOT NULL,
            amount REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(sender_id) REFERENCES users(id),
            FOREIGN KEY(receiver_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    ''')
    conn.commit()
    conn.close()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user(user_id):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return user

def calc_wealth(user):
    return (user['rupees'] +
            user['bitcoin'] * BTC_BUY_RATE +
            user['gold'] * GOLD_BUY_RATE +
            user['silver'] * (1000 / 3))

def get_rank(wealth):
    if wealth >= 10_000_000: return '💎 Diamond'
    if wealth >= 5_000_000: return '🥇 Gold'
    if wealth >= 1_000_000: return '🥈 Silver'
    if wealth >= 500_000: return '🥉 Bronze'
    return '🔰 Newcomer'

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def unread_count(user_id):
    conn = get_db()
    count = conn.execute('SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0', (user_id,)).fetchone()[0]
    conn.close()
    return count

@app.context_processor
def inject_notifications():
    if 'user_id' in session:
        return {'unread_notifications': unread_count(session['user_id'])}
    return {'unread_notifications': 0}

# ─── Auth ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if not username or not password:
            flash('Username and password required.', 'error')
            return render_template('register.html')
        if len(username) < 3 or len(username) > 20:
            flash('Username must be 3–20 characters.', 'error')
            return render_template('register.html')
        conn = get_db()
        existing = conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
        if existing:
            conn.close()
            flash('Username already taken.', 'error')
            return render_template('register.html')
        conn.execute('INSERT INTO users (username, password_hash) VALUES (?,?)',
                     (username, hash_password(password)))
        conn.commit()
        conn.close()
        flash('Account created! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username=? AND password_hash=?',
                            (username, hash_password(password))).fetchone()
        conn.close()
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─── Dashboard ───────────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    user = get_user(session['user_id'])
    wealth = calc_wealth(user)
    rank = get_rank(wealth)
    return render_template('dashboard.html', user=user, wealth=wealth, rank=rank)

# ─── Bitcoin ─────────────────────────────────────────────────────────────────

@app.route('/market/btc', methods=['GET', 'POST'])
@login_required
def btc_market():
    user = get_user(session['user_id'])
    sell_price = None
    if request.method == 'POST':
        action = request.form.get('action')
        try:
            amount = float(request.form.get('amount', 0))
        except ValueError:
            flash('Invalid amount.', 'error')
            return redirect(url_for('btc_market'))
        if amount <= 0:
            flash('Amount must be positive.', 'error')
            return redirect(url_for('btc_market'))

        conn = get_db()
        u = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()

        if action == 'buy':
            cost = amount * BTC_BUY_RATE
            if u['rupees'] < cost:
                conn.close()
                flash('Insufficient funds.', 'error')
                return redirect(url_for('btc_market'))
            conn.execute('UPDATE users SET rupees=rupees-?, bitcoin=bitcoin+? WHERE id=?',
                         (cost, amount, session['user_id']))
            conn.execute('INSERT INTO transactions (user_id,type,asset,amount,price,result) VALUES (?,?,?,?,?,?)',
                         (session['user_id'], 'buy', 'BTC', amount, BTC_BUY_RATE, -cost))
            conn.commit()
            conn.close()
            flash(f'Bought {amount} BTC for ₹{cost:,.2f}', 'success')

        elif action == 'sell':
            if u['bitcoin'] < amount:
                conn.close()
                flash('Insufficient BTC.', 'error')
                return redirect(url_for('btc_market'))
            price = random.uniform(5_000_000, 10_000_000)
            proceeds = amount * price
            conn.execute('UPDATE users SET rupees=rupees+?, bitcoin=bitcoin-? WHERE id=?',
                         (proceeds, amount, session['user_id']))
            conn.execute('INSERT INTO transactions (user_id,type,asset,amount,price,result) VALUES (?,?,?,?,?,?)',
                         (session['user_id'], 'sell', 'BTC', amount, price, proceeds))
            conn.commit()
            conn.close()
            flash(f'Sold {amount} BTC at ₹{price:,.0f}/BTC → ₹{proceeds:,.2f}', 'success')

        return redirect(url_for('btc_market'))
    return render_template('btc_market.html', user=user, buy_rate=BTC_BUY_RATE)

# ─── Gold ─────────────────────────────────────────────────────────────────────

@app.route('/market/gold', methods=['GET', 'POST'])
@login_required
def gold_market():
    user = get_user(session['user_id'])
    if request.method == 'POST':
        action = request.form.get('action')
        try:
            amount = float(request.form.get('amount', 0))
        except ValueError:
            flash('Invalid amount.', 'error')
            return redirect(url_for('gold_market'))
        if amount <= 0:
            flash('Amount must be positive.', 'error')
            return redirect(url_for('gold_market'))

        conn = get_db()
        u = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()

        if action == 'buy':
            cost = amount * GOLD_BUY_RATE
            if u['rupees'] < cost:
                conn.close()
                flash('Insufficient funds.', 'error')
                return redirect(url_for('gold_market'))
            conn.execute('UPDATE users SET rupees=rupees-?, gold=gold+? WHERE id=?',
                         (cost, amount, session['user_id']))
            conn.execute('INSERT INTO transactions (user_id,type,asset,amount,price,result) VALUES (?,?,?,?,?,?)',
                         (session['user_id'], 'buy', 'Gold', amount, GOLD_BUY_RATE, -cost))
            conn.commit()
            conn.close()
            flash(f'Bought {amount}g Gold for ₹{cost:,.2f}', 'success')

        elif action == 'sell':
            if u['gold'] < amount:
                conn.close()
                flash('Insufficient Gold.', 'error')
                return redirect(url_for('gold_market'))
            price = random.uniform(10_000, 20_000)
            proceeds = amount * price
            conn.execute('UPDATE users SET rupees=rupees+?, gold=gold-? WHERE id=?',
                         (proceeds, amount, session['user_id']))
            conn.execute('INSERT INTO transactions (user_id,type,asset,amount,price,result) VALUES (?,?,?,?,?,?)',
                         (session['user_id'], 'sell', 'Gold', amount, price, proceeds))
            conn.commit()
            conn.close()
            flash(f'Sold {amount}g Gold at ₹{price:,.0f}/g → ₹{proceeds:,.2f}', 'success')

        return redirect(url_for('gold_market'))
    return render_template('gold_market.html', user=user, buy_rate=GOLD_BUY_RATE)

# ─── Silver ──────────────────────────────────────────────────────────────────

@app.route('/market/silver', methods=['GET', 'POST'])
@login_required
def silver_market():
    user = get_user(session['user_id'])
    if request.method == 'POST':
        action = request.form.get('action')
        try:
            amount = float(request.form.get('amount', 0))
        except ValueError:
            flash('Invalid amount.', 'error')
            return redirect(url_for('silver_market'))
        if amount <= 0:
            flash('Amount must be positive.', 'error')
            return redirect(url_for('silver_market'))

        conn = get_db()
        u = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()

        if action == 'buy':
            cost = amount * (SILVER_BUY_RATE_PER_3G / 3)
            if u['rupees'] < cost:
                conn.close()
                flash('Insufficient funds.', 'error')
                return redirect(url_for('silver_market'))
            conn.execute('UPDATE users SET rupees=rupees-?, silver=silver+? WHERE id=?',
                         (cost, amount, session['user_id']))
            conn.execute('INSERT INTO transactions (user_id,type,asset,amount,price,result) VALUES (?,?,?,?,?,?)',
                         (session['user_id'], 'buy', 'Silver', amount, SILVER_BUY_RATE_PER_3G/3, -cost))
            conn.commit()
            conn.close()
            flash(f'Bought {amount}g Silver for ₹{cost:,.2f}', 'success')

        elif action == 'sell':
            if u['silver'] < amount:
                conn.close()
                flash('Insufficient Silver.', 'error')
                return redirect(url_for('silver_market'))
            rate_per_3g = random.uniform(1_000, 10_000)
            price_per_g = rate_per_3g / 3
            proceeds = amount * price_per_g
            conn.execute('UPDATE users SET rupees=rupees+?, silver=silver-? WHERE id=?',
                         (proceeds, amount, session['user_id']))
            conn.execute('INSERT INTO transactions (user_id,type,asset,amount,price,result) VALUES (?,?,?,?,?,?)',
                         (session['user_id'], 'sell', 'Silver', amount, price_per_g, proceeds))
            conn.commit()
            conn.close()
            flash(f'Sold {amount}g Silver at ₹{rate_per_3g:,.0f}/3g → ₹{proceeds:,.2f}', 'success')

        return redirect(url_for('silver_market'))
    return render_template('silver_market.html', user=user, buy_rate_per_3g=SILVER_BUY_RATE_PER_3G)

# ─── Investment ───────────────────────────────────────────────────────────────

@app.route('/invest', methods=['GET', 'POST'])
@login_required
def invest():
    user = get_user(session['user_id'])
    result = None
    if request.method == 'POST':
        try:
            amount = float(request.form.get('amount', 0))
        except ValueError:
            flash('Invalid amount.', 'error')
            return redirect(url_for('invest'))
        if amount <= 0:
            flash('Amount must be positive.', 'error')
            return redirect(url_for('invest'))
        conn = get_db()
        u = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
        if u['rupees'] < amount:
            conn.close()
            flash('Insufficient funds.', 'error')
            return redirect(url_for('invest'))
        pct = random.uniform(-0.10, 0.10)
        final = amount * (1 + pct)
        profit = final - amount
        conn.execute('UPDATE users SET rupees=rupees-?+? WHERE id=?',
                     (amount, final, session['user_id']))
        conn.execute('INSERT INTO transactions (user_id,type,asset,amount,price,result,note) VALUES (?,?,?,?,?,?,?)',
                     (session['user_id'], 'invest', 'INR', amount, pct*100, final,
                      f'{pct*100:+.2f}%'))
        conn.commit()
        conn.close()
        result = {'invested': amount, 'pct': pct*100, 'final': final, 'profit': profit}
    conn2 = get_db()
    history = conn2.execute(
        "SELECT * FROM transactions WHERE user_id=? AND type='invest' ORDER BY created_at DESC LIMIT 10",
        (session['user_id'],)).fetchall()
    conn2.close()
    return render_template('invest.html', user=user, result=result, history=history)

# ─── Transfer ─────────────────────────────────────────────────────────────────

@app.route('/transfer', methods=['GET', 'POST'])
@login_required
def transfer():
    user = get_user(session['user_id'])
    if request.method == 'POST':
        recipient_name = request.form.get('recipient', '').strip()
        asset = request.form.get('asset')
        try:
            amount = float(request.form.get('amount', 0))
        except ValueError:
            flash('Invalid amount.', 'error')
            return redirect(url_for('transfer'))

        if amount <= 0:
            flash('Amount must be positive.', 'error')
            return redirect(url_for('transfer'))
        if asset not in ('rupees', 'bitcoin', 'gold', 'silver'):
            flash('Invalid asset.', 'error')
            return redirect(url_for('transfer'))

        conn = get_db()
        sender = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
        recipient = conn.execute('SELECT * FROM users WHERE username=?', (recipient_name,)).fetchone()

        if not recipient:
            conn.close()
            flash('User not found.', 'error')
            return redirect(url_for('transfer'))
        if recipient['id'] == session['user_id']:
            conn.close()
            flash('Cannot transfer to yourself.', 'error')
            return redirect(url_for('transfer'))
        if sender[asset] < amount:
            conn.close()
            flash('Insufficient balance.', 'error')
            return redirect(url_for('transfer'))

        conn.execute(f'UPDATE users SET {asset}={asset}-? WHERE id=?', (amount, session['user_id']))
        conn.execute(f'UPDATE users SET {asset}={asset}+? WHERE id=?', (amount, recipient['id']))
        conn.execute('INSERT INTO transfers (sender_id,receiver_id,asset,amount) VALUES (?,?,?,?)',
                     (session['user_id'], recipient['id'], asset, amount))

        asset_labels = {'rupees': f'₹{amount:,.2f}', 'bitcoin': f'{amount} BTC',
                        'gold': f'{amount}g Gold', 'silver': f'{amount}g Silver'}
        msg = f'You received {asset_labels[asset]} from {sender["username"]}'
        conn.execute('INSERT INTO notifications (user_id,message) VALUES (?,?)', (recipient['id'], msg))
        conn.execute('INSERT INTO transactions (user_id,type,asset,amount,note) VALUES (?,?,?,?,?)',
                     (session['user_id'], 'transfer_out', asset, amount, f'To {recipient_name}'))
        conn.execute('INSERT INTO transactions (user_id,type,asset,amount,note) VALUES (?,?,?,?,?)',
                     (recipient['id'], 'transfer_in', asset, amount, f'From {sender["username"]}'))
        conn.commit()
        conn.close()
        flash(f'Sent {asset_labels[asset]} to {recipient_name}', 'success')
        return redirect(url_for('transfer'))
    return render_template('transfer.html', user=user)

# ─── Notifications ────────────────────────────────────────────────────────────

@app.route('/notifications')
@login_required
def notifications():
    conn = get_db()
    notifs = conn.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC',
                          (session['user_id'],)).fetchall()
    conn.execute('UPDATE notifications SET is_read=1 WHERE user_id=?', (session['user_id'],))
    conn.commit()
    conn.close()
    return render_template('notifications.html', notifications=notifs)

# ─── Daily Reward ─────────────────────────────────────────────────────────────

@app.route('/daily', methods=['GET', 'POST'])
@login_required
def daily():
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
    now = datetime.utcnow()
    can_claim = True
    next_claim = None
    if user['last_daily_claim']:
        last = datetime.strptime(user['last_daily_claim'], '%Y-%m-%d %H:%M:%S.%f') \
               if '.' in str(user['last_daily_claim']) \
               else datetime.strptime(str(user['last_daily_claim']), '%Y-%m-%d %H:%M:%S')
        diff = now - last
        if diff < timedelta(hours=24):
            can_claim = False
            next_claim = last + timedelta(hours=24)
    conn.close()

    if request.method == 'POST' and can_claim:
        conn = get_db()
        conn.execute('UPDATE users SET rupees=rupees+1, last_daily_claim=? WHERE id=?',
                     (now, session['user_id']))
        conn.execute('INSERT INTO transactions (user_id,type,asset,amount,result) VALUES (?,?,?,?,?)',
                     (session['user_id'], 'daily_reward', 'INR', 1, 1))
        conn.commit()
        conn.close()
        flash('Claimed ₹1 daily reward!', 'success')
        return redirect(url_for('daily'))
    return render_template('daily.html', can_claim=can_claim, next_claim=next_claim,
                           user=get_user(session['user_id']))

# ─── History ─────────────────────────────────────────────────────────────────

@app.route('/history')
@login_required
def history():
    conn = get_db()
    txns = conn.execute('SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC',
                        (session['user_id'],)).fetchall()
    conn.close()
    return render_template('history.html', transactions=txns)

# ─── Leaderboard ─────────────────────────────────────────────────────────────

@app.route('/leaderboard')
@login_required
def leaderboard():
    conn = get_db()
    users = conn.execute('SELECT * FROM users').fetchall()
    conn.close()
    ranked = sorted(users, key=lambda u: calc_wealth(u), reverse=True)[:100]
    board = [(i+1, u, calc_wealth(u), get_rank(calc_wealth(u))) for i, u in enumerate(ranked)]
    return render_template('leaderboard.html', board=board)

# ─── Profile ─────────────────────────────────────────────────────────────────

@app.route('/profile/<username>')
@login_required
def profile(username):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    conn.close()
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('leaderboard'))
    wealth = calc_wealth(user)
    rank = get_rank(wealth)
    return render_template('profile.html', puser=user, wealth=wealth, rank=rank)

# ─── Sell Price Preview API ───────────────────────────────────────────────────

@app.route('/api/sell_price/<asset>')
@login_required
def sell_price_api(asset):
    prices = {
        'btc': random.uniform(5_000_000, 10_000_000),
        'gold': random.uniform(10_000, 20_000),
        'silver_per_3g': random.uniform(1_000, 10_000)
    }
    if asset not in prices:
        return jsonify({'error': 'Invalid asset'}), 400
    return jsonify({'price': round(prices[asset], 2), 'asset': asset})

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
