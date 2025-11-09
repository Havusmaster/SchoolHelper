# app.py - Основной файл для Render: запускает monitor.py и main.py (с исправлениями ошибок)
# Исправления: 
# - Добавил os.environ['RENDER_APP_NAME'] = 'schoolhelper-1' если не задан, для правильного UPTIME_CHECK_URL.
# - Остальной код без изменений.

import os
import sqlite3
from flask import Flask, render_template_string, jsonify
import threading
import time
import requests
import asyncio

# Импорт исправленного main.py (bot_main)
from main import bot_main as main  # Теперь main.py имеет async def bot_main()

# === ФОНОВЫЙ ЗАПУСК БОТА ===
def start_bot():
    asyncio.run(main())

threading.Thread(target=start_bot, daemon=True).start()

# === НАСТРОЙКИ ===
DB_PATH = 'users.db'
BOT_TOKEN = os.getenv('TOKEN')
BOT_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
if 'RENDER_APP_NAME' not in os.environ:
    os.environ['RENDER_APP_NAME'] = 'schoolhelper-1'  # Фикс для вашего приложения
UPTIME_CHECK_URL = f"https://{os.getenv('https://schoolhelper-1', 'localhost')}.onrender.com"  # Фикс: Используй правильный URL от Render

# === FLASK ===
app = Flask(__name__)

# === HTML ===
HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SchoolBot Monitor</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 20px; }
        .card { background: #1e293b; padding: 20px; border-radius: 12px; margin: 15px 0; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
        h1 { color: #60a5fa; text-align: center; }
        .status { font-size: 1.5em; padding: 10px; border-radius: 8px; text-align: center; }
        .online { background: #166534; }
        .offline { background: #991b1b; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; }
        .stat { background: #334155; padding: 15px; border-radius: 8px; text-align: center; }
        .label { color: #94a3b8; font-size: 0.9em; }
        .value { font-size: 1.8em; font-weight: bold; color: #60a5fa; }
        .refresh { text-align: center; margin: 20px; }
        .refresh button { padding: 10px 20px; background: #3b82f6; border: none; color: white; border-radius: 6px; cursor: pointer; }
    </style>
</head>
<body>
    <h1>SchoolBot Monitor</h1>
    
    <div class="card">
        <div class="status" id="status">Проверка...</div>
    </div>

    <div class="card stats" id="stats">
        <div class="stat"><div class="label">Пользователей</div><div class="value" id="users">-</div></div>
        <div class="stat"><div class="label">Задач сегодня</div><div class="value" id="tasks">-</div></div>
        <div class="stat"><div class="label">Extra всего</div><div class="value" id="extra">-</div></div>
    </div>

    <div class="refresh">
        <button onclick="location.reload()">Обновить</button>
    </div>

    <script>
        function update() {
            fetch('/api/status').then(r => r.json()).then(data => {
                document.getElementById('status').textContent = data.bot_alive ? 'ONLINE' : 'OFFLINE';
                document.getElementById('status').className = 'status ' + (data.bot_alive ? 'online' : 'offline');
                document.getElementById('users').textContent = data.users;
                document.getElementById('tasks').textContent = data.tasks_today;
                document.getElementById('extra').textContent = data.total_extra;
            });
        }
        update();
        setInterval(update, 30000); // каждые 30 сек
    </script>
</body>
</html>
"""

# === API ===
@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/status')
def api_status():
    # Проверка Telegram API
    bot_alive = False
    try:
        resp = requests.get(BOT_URL, timeout=5)
        bot_alive = resp.status_code == 200
    except:
        pass

    # Статистика из БД
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM users')
        users = c.fetchone()[0]
        c.execute('SELECT SUM(daily_count) FROM users')
        tasks_today = c.fetchone()[0] or 0
        c.execute('SELECT SUM(extra_tasks) FROM users')
        total_extra = c.fetchone()[0] or 0
        conn.close()
    except:
        users = tasks_today = total_extra = 0

    return jsonify({
        'bot_alive': bot_alive,
        'users': users,
        'tasks_today': tasks_today,
        'total_extra': total_extra
    })

# === ФОНОВЫЙ ПИНГ (чтобы не засыпал) ===
def keep_awake():
    while True:
        try:
            requests.get(UPTIME_CHECK_URL, timeout=5)
        except:
            pass
        time.sleep(300)  # каждые 5 минут

# === ЗАПУСК ===
if __name__ == '__main__':
    # Запускаем пинг в фоне
    threading.Thread(target=keep_awake, daemon=True).start()

    # Запуск Flask
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)