# admin_panel.py
import os
import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for, flash
from datetime import datetime

# --- Настройки ---
DB_PATH = 'users.db'
ADMIN_PASSWORD = os.getenv('ADMIN_PANEL_PASSWORD', 'admin123')  # Укажи в .env или оставь по умолчанию

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Для flash-сообщений

# --- Подключение к базе ---
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# --- HTML шаблон (встроенный, без внешних файлов) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Админ-панель SchoolBot</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #f4f4f9; }
        h1, h2 { color: #2c3e50; }
        .container { max-width: 900px; margin: auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
        table { width: 100%; border-collapse: collapse; margin: 20px 0; }
        th, td { border: 1px solid #ddd; padding: 10px; text-align: left; }
        th { background: #3498db; color: white; }
        tr:nth-child(even) { background: #f9f9f9; }
        .btn { padding: 8px 16px; margin: 5px; background: #27ae60; color: white; text-decoration: none; border-radius: 5px; }
        .btn-danger { background: #e74c3c; }
        .form-group { margin: 15px 0; }
        input, button { padding: 10px; font-size: 16px; }
        .flash { padding: 10px; margin: 10px 0; background: #dff0d8; border: 1px solid #3c763d; border-radius: 5px; }
    </style>
</head>
<body>
<div class="container">
    <h1>Админ-панель SchoolBot</h1>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for msg in messages %}
          <div class="flash">{{ msg }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <h2>Статистика</h2>
    <p><strong>Всего пользователей:</strong> {{ stats.total_users }}</p>
    <p><strong>Задач решено сегодня:</strong> {{ stats.tasks_today }}</p>
    <p><strong>Всего extra_tasks:</strong> {{ stats.total_extra }}</p>

    <h2>Управление пользователями</h2>
    <form method="post" action="{{ url_for('set_extra') }}" class="form-group">
        <input type="number" name="user_id" placeholder="ID пользователя" required>
        <input type="number" name="extra" placeholder="Extra задачи" required>
        <button type="submit">Установить</button>
    </form>

    <h2>Последние пользователи</h2>
    <table>
        <tr>
            <th>ID</th>
            <th>Имя</th>
            <th>Username</th>
            <th>Extra</th>
            <th>Сегодня</th>
        </tr>
        {% for user in users %}
        <tr>
            <td>{{ user.user_id }}</td>
            <td>{{ user.first_name or '—' }}</td>
            <td>{{ '@' + user.username if user.username else '—' }}</td>
            <td>{{ user.extra_tasks }}</td>
            <td>{{ user.daily_count }}</td>
        </tr>
        {% endfor %}
    </table>

    <h2>Сообщения в поддержку</h2>
    {% if support_messages %}
    <table>
        <tr>
            <th>ID</th>
            <th>От кого</th>
            <th>Текст</th>
            <th>Время</th>
            <th>Действие</th>
        </tr>
        {% for msg in support_messages %}
        <tr {% if msg.processed %}style="opacity:0.6;"{% endif %}>
            <td>{{ msg.id }}</td>
            <td>{{ msg.first_name }} (@{{ msg.username }})<br><small>{{ msg.user_id }}</small></td>
            <td>{{ msg.text }}</td>
            <td>{{ msg.timestamp }}</td>
            <td>
                {% if not msg.processed %}
                <form method="post" action="{{ url_for('mark_processed', msg_id=msg.id) }}" style="display:inline;">
                    <button type="submit" class="btn btn-danger">Обработано</button>
                </form>
                {% else %}
                <i>Обработано</i>
                {% endif %}
            </td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <p>Нет сообщений.</p>
    {% endif %}

    <hr>
    <p><a href="{{ url_for('logout') }}" style="color:#e74c3c;">Выйти</a></p>
</div>
</body>
</html>
"""

# --- Авторизация ---
def check_auth():
    return request.cookies.get('auth') == '1'

def requires_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not check_auth():
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# --- Маршруты ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            resp = redirect(url_for('dashboard'))
            resp.set_cookie('auth', '1')
            return resp
        else:
            flash('Неверный пароль!')
    return '''
    <form method="post" style="text-align:center;margin-top:100px;">
        <h2>Вход в админку</h2>
        <input type="password" name="password" placeholder="Пароль" required style="padding:10px;font-size:16px;"><br><br>
        <button type="submit" style="padding:10px 20px;font-size:16px;">Войти</button>
    </form>
    '''

@app.route('/logout')
def logout():
    resp = redirect(url_for('login'))
    resp.set_cookie('auth', '', expires=0)
    return resp

@app.route('/')
@requires_auth
def dashboard():
    db = get_db()
    
    # Статистика
    cursor = db.execute('SELECT COUNT(*) AS total_users, SUM(daily_count) AS tasks_today, SUM(extra_tasks) AS total_extra FROM users')
    stats = cursor.fetchone()

    # Пользователи
    users = db.execute('''
        SELECT user_id, first_name, username, extra_tasks, daily_count 
        FROM users 
        ORDER BY user_id DESC LIMIT 50
    ''').fetchall()

    # Сообщения поддержки
    support_messages = db.execute('''
        SELECT * FROM support_messages 
        ORDER BY id DESC LIMIT 50
    ''').fetchall()

    return render_template_string(HTML_TEMPLATE, stats=stats, users=users, support_messages=support_messages)

@app.route('/set_extra', methods=['POST'])
@requires_auth
def set_extra():
    user_id = request.form['user_id']
    extra = request.form['extra']
    db = get_db()
    db.execute('UPDATE users SET extra_tasks = ? WHERE user_id = ?', (extra, user_id))
    db.commit()
    flash(f'Extra задачи для {user_id} установлены: {extra}')
    return redirect(url_for('dashboard'))

@app.route('/mark_processed/<int:msg_id>', methods=['POST'])
@requires_auth
def mark_processed(msg_id):
    db = get_db()
    db.execute('UPDATE support_messages SET processed = 1 WHERE id = ?', (msg_id,))
    db.commit()
    flash(f'Сообщение #{msg_id} помечено как обработанное')
    return redirect(url_for('dashboard'))

# --- Запуск ---
if __name__ == '__main__':
    # Для Render — слушаем на 0.0.0.0 и порту из окружения
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)