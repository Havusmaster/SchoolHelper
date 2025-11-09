# monitor.py — ЭТО ГЛАВНЫЙ ФАЙЛ ДЛЯ RENDER
import os
import sqlite3
import threading
import time
import requests
from datetime import datetime
from flask import Flask, render_template_string, jsonify

from telegram.ext import Application
from main import *  # Импортируем ВСЕ функции из main.py

# === FLASK ===
app = Flask(__name__)

# === HTML МОНИТОРИНГ ===
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

# === ЗАПУСК БОТА В ФОНЕ ===
def run_bot():
    application = Application.builder().token(os.getenv('TOKEN')).build()

    # Добавляем все хендлеры из main.py
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("set_limit", set_limit))
    app.add_handler(CommandHandler("users", list_users))
    app.add_handler(CallbackQueryHandler(admin_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))  # Один хендлер для текста
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(check_sub_button, pattern="^check_again$"))

    app.add_error_handler(error_handler)
    # Запускаем polling (но теперь ТОЛЬКО ОДИН!)
    application.run_polling()

# === KEEP AWAKE ===
def keep_awake():
    url = f"https://{os.getenv('https://schoolhelper-1.onrender.com')}"
    while True:
        try:
            requests.get(url, timeout=5)
        except:
            pass
        time.sleep(300)

# === ЗАПУСК ВСЯГО ===
if __name__ == '__main__':
    # 1. Бот в фоне
    threading.Thread(target=run_bot, daemon=True).start()
    
    # 2. Пинг в фоне
    threading.Thread(target=keep_awake, daemon=True).start()

    # 3. Flask мониторинг
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)