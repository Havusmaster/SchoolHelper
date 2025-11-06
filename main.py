import logging
import os
import io
import requests
from datetime import datetime
from PIL import Image
# EasyOCR импортируем лениво внутри функции, чтобы экономить память
import sqlite3

from flask import Flask
import threading

# Импорт модулей решения задач
from algebra import solve_equation

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, Poll
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, PollHandler, PollAnswerHandler
import json
from telegram.error import TimedOut, NetworkError, RetryAfter
import asyncio

# Загружаем .env
from dotenv import load_dotenv
load_dotenv()

# TOKEN
TOKEN = os.getenv('TOKEN')

# Конфиг из .env
DAILY_LIMIT = int(os.getenv('DAILY_LIMIT', 3))
REFERRAL_REWARD = int(os.getenv('REFERRAL_REWARD', 1))

# Админ ID (замени на свой user_id)
ADMIN_ID = int(os.getenv('ADMIN_ID'))  # Укажи здесь свой Telegram user_id для админа

if not TOKEN:
    raise ValueError("TOKEN не найден в .env!")

# Логи
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Настройки OCR из окружения (для экономии памяти на Render можно выключить)
OCR_ENABLED = os.getenv('OCR_ENABLED', '0') in ('1', 'true', 'True')
OCR_LANGS = os.getenv('OCR_LANGS', 'en')  # по умолчанию только 'en' для меньшей памяти

_ocr_reader = None

def get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is not None:
        return _ocr_reader
    # Ленивая загрузка только при первом обращении
    import easyocr  # импорт здесь, чтобы не грузить модуль при старте
    langs = [lang.strip() for lang in OCR_LANGS.split(',') if lang.strip()]
    _ocr_reader = easyocr.Reader(langs, gpu=False)
    return _ocr_reader

# База данных SQLite
conn = sqlite3.connect('users.db')
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    daily_count INTEGER DEFAULT 0,
    last_date TEXT,
    extra_tasks INTEGER DEFAULT 0
)
''')
try:
    cursor.execute("ALTER TABLE users ADD COLUMN extra_tasks INTEGER DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass  # Колонка уже существует

# Доп. колонки профиля пользователя
try:
    cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass
try:
    cursor.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass

cursor.execute('''
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    timestamp TEXT,
    equation TEXT,
    solution TEXT
)
''')
conn.commit()

# Таблица для сообщений поддержки
cursor.execute('''
CREATE TABLE IF NOT EXISTS support_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    first_name TEXT,
    text TEXT,
    timestamp TEXT,
    processed INTEGER DEFAULT 0
)
''')
conn.commit()

# Таблицы для опросов
cursor.execute('''
CREATE TABLE IF NOT EXISTS polls (
    poll_id TEXT PRIMARY KEY,
    question TEXT,
    options_json TEXT,
    total_voter_count INTEGER DEFAULT 0,
    is_closed INTEGER DEFAULT 0,
    last_update TEXT
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS poll_answers (
    poll_id TEXT,
    user_id INTEGER,
    option_ids TEXT,
    username TEXT,
    first_name TEXT,
    PRIMARY KEY (poll_id, user_id)
)
''')
conn.commit()

# Основная клавиатура
def main_keyboard(is_admin: bool):
    keyboard = [['Решить задачу'], ['Мой уровень', 'История'], ['Пригласить друга'], ['Поддержка']]
    if is_admin:
        keyboard.append(['Админ панель'])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Клавиатура админа
def admin_keyboard():
    keyboard = [
        ['Статистика', 'Пользователи'],
        ['Опросы'],
        ['Все сообщения'],
        ['Назад']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Функция: Получить/обновить пользователя
def get_user_level(user_id):
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('SELECT daily_count, last_date, extra_tasks FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    extra_tasks = 0
    count = 0
    if row:
        count, last_date, extra_tasks = row
        if last_date != today:
            count = 0
            cursor.execute('UPDATE users SET daily_count = 0, last_date = ? WHERE user_id = ?', (today, user_id))
            conn.commit()
    else:
        cursor.execute('INSERT INTO users (user_id, daily_count, last_date, extra_tasks) VALUES (?, 0, ?, 0)', (user_id, today))
        conn.commit()
    limit = DAILY_LIMIT + extra_tasks
    return count, limit

# Обновить имя/юзернейм пользователя в базе
def upsert_user_profile(user_id: int, username: str | None, first_name: str | None):
    cursor.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,))
    exists = cursor.fetchone() is not None
    if exists:
        cursor.execute(
            'UPDATE users SET username = ?, first_name = ? WHERE user_id = ?',
            (username, first_name, user_id)
        )
    else:
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute(
            'INSERT INTO users (user_id, daily_count, last_date, extra_tasks, username, first_name) VALUES (?, 0, ?, 0, ?, ?)',
            (user_id, today, username, first_name)
        )
    conn.commit()

# Функция: Увеличить счётчик
def increment_count(user_id):
    cursor.execute('UPDATE users SET daily_count = daily_count + 1 WHERE user_id = ?', (user_id,))
    conn.commit()

# Функция: Добавить extra_tasks за реферала или админа
def add_extra_tasks(user_id, amount):
    cursor.execute('UPDATE users SET extra_tasks = extra_tasks + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()

# Функция: Добавить в историю
def add_to_history(user_id, equation, solution):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT INTO history (user_id, timestamp, equation, solution) VALUES (?, ?, ?, ?)', (user_id, timestamp, equation, solution))
    conn.commit()

# Функция: Получить историю
def get_history(user_id):
    cursor.execute('SELECT timestamp, equation, solution FROM history WHERE user_id = ? ORDER BY id DESC LIMIT 10', (user_id,))
    return cursor.fetchall()

# Функция: Безопасная отправка сообщения с повторными попытками
async def safe_reply_text(update: Update, text: str, parse_mode=None, reply_markup=None, max_retries=3):
    """Отправляет сообщение с обработкой ошибок и повторными попытками"""
    for attempt in range(max_retries):
        try:
            if parse_mode:
                await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
            else:
                await update.message.reply_text(text, reply_markup=reply_markup)
            return True
        except TimedOut:
            logging.warning(f"TimedOut при отправке сообщения (попытка {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)  # Ждём 2 секунды перед повторной попыткой
            else:
                logging.error("Не удалось отправить сообщение после всех попыток")
                try:
                    await update.message.reply_text("⏱️ Время ожидания истекло. Попробуй ещё раз.")
                except:
                    pass
                return False
        except RetryAfter as e:
            logging.warning(f"RetryAfter: нужно подождать {e.retry_after} секунд")
            await asyncio.sleep(e.retry_after + 1)
        except NetworkError as e:
            logging.warning(f"NetworkError при отправке сообщения (попытка {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
            else:
                logging.error("Не удалось отправить сообщение из-за сетевой ошибки")
                return False
        except Exception as e:
            logging.error(f"Неожиданная ошибка при отправке сообщения: {e}")
            return False
    return False

# Глобальный обработчик ошибок
async def error_handler(update: object, context):
    """Обрабатывает ошибки, возникающие в хендлерах"""
    from telegram.ext import ContextTypes
    
    if isinstance(context, ContextTypes.DEFAULT_TYPE):
        error = context.error
    else:
        error = context
    
    logging.error(f"Ошибка при обработке обновления: {error}")
    
    if isinstance(error, TimedOut):
        logging.warning("Ошибка таймаута обработана - бот продолжит работу")
    elif isinstance(error, NetworkError):
        logging.warning(f"Ошибка сети: {error} - бот продолжит работу")
    elif isinstance(error, RetryAfter):
        logging.warning(f"Нужно подождать: {error.retry_after} секунд")
    else:
        logging.error(f"Необработанная ошибка: {error}")

# Функция: Реферальная система
async def referral(update: Update, context):
    user_id = update.message.from_user.id
    ref_link = f"https://t.me/{context.bot.username}?start=ref_{user_id}"
    await update.message.reply_text(
        f"Пригласи друга — получи +{REFERRAL_REWARD} задачу в день!\n\n"
        f"Твоя ссылка: {ref_link}\n\n"
        f"Друг перейдёт — твой лимит навсегда +{REFERRAL_REWARD} задач/день."
    )

# /start с меню, рефералами и опросом
async def start(update: Update, context):
    user = update.message.from_user
    user_id = user.id
    upsert_user_profile(user_id, user.username, user.first_name)
    args = context.args
    
    # Если по рефералке
    if args and args[0].startswith('ref_'):
        referrer_id = int(args[0].split('_')[1])
        if referrer_id != user_id:
            add_extra_tasks(referrer_id, REFERRAL_REWARD)
            await context.bot.send_message(referrer_id, f"Друг зарегистрировался! Твой лимит +{REFERRAL_REWARD} задач/день навсегда! 🚀")
    
    # Обычный старт
    get_user_level(user_id)
    reply_markup = main_keyboard(user_id == ADMIN_ID)
    await update.message.reply_text('Salom! Выбери в меню:', reply_markup=reply_markup)
    
    # Опрос при старте
    await update.message.reply_poll(
        question="Какой новый предмет добавить?",
        options=["Kimyo (Химия)", "Geometriya (Геометрия)"],
        is_anonymous=False,
        allows_multiple_answers=False
    )

# Команда: /stats (только админ)
async def stats(update: Update, context):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        return
    cursor.execute('SELECT COUNT(*), SUM(daily_count), SUM(extra_tasks) FROM users')
    row = cursor.fetchone()
    total, used, extra = (row if row is not None else (0, 0, 0))
    await update.message.reply_text(
        f'Пользователей: {total}\n'
        f'Задач решено сегодня: {used}\n'
        f'Всего extra_tasks: {extra}'
    )

# Обработчик обновлений опросов (агрегированное состояние опроса)
async def on_poll(update: Update, context):
    poll = update.poll
    if not poll:
        return
    options = [{'text': opt.text, 'voter_count': opt.voter_count} for opt in poll.options]
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        'INSERT OR REPLACE INTO polls (poll_id, question, options_json, total_voter_count, is_closed, last_update) VALUES (?, ?, ?, ?, ?, ?)',
        (poll.id, poll.question, json.dumps(options, ensure_ascii=False), poll.total_voter_count, int(poll.is_closed), now)
    )
    conn.commit()

# Обработчик ответов пользователей в опросах
async def on_poll_answer(update: Update, context):
    ans = update.poll_answer
    if not ans:
        return
    user = ans.user
    option_ids = json.dumps(ans.option_ids)
    cursor.execute(
        'INSERT OR REPLACE INTO poll_answers (poll_id, user_id, option_ids, username, first_name) VALUES (?, ?, ?, ?, ?)',
        (ans.poll_id, user.id if user else None, option_ids, getattr(user, 'username', None), getattr(user, 'first_name', None))
    )
    conn.commit()

# Показ следующего сообщения поддержки администратору
async def send_next_support_message(update: Update, context, after_id: int | None):
    if after_id is None:
        cursor.execute('SELECT id, user_id, username, first_name, text, timestamp FROM support_messages WHERE processed = 0 ORDER BY id ASC LIMIT 1')
    else:
        cursor.execute('SELECT id, user_id, username, first_name, text, timestamp FROM support_messages WHERE processed = 0 AND id > ? ORDER BY id ASC LIMIT 1', (after_id,))
    row = cursor.fetchone()
    if not row:
        await update.message.reply_text('Нет новых сообщений.', reply_markup=admin_keyboard())
        return
    msg_id, uid, uname, fname, text, ts = row
    uname_disp = f"@{uname}" if uname else '(нет username)'
    header = f"ID:{msg_id} | {ts}\nОт: {uid} {uname_disp} {fname or ''}\n\n{text}"
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton('Обработано ✅', callback_data=f'support_done:{msg_id}'),
            InlineKeyboardButton('Следующее ▶️', callback_data=f'support_next:{msg_id}')
        ]
    ])
    await update.message.reply_text(header, reply_markup=kb)

# Обработчик inline-кнопок админа
async def admin_callbacks(update: Update, context):
    query = update.callback_query
    data = query.data or ''
    await query.answer()
    if not data or update.effective_user.id != ADMIN_ID:
        return
    if data.startswith('support_done:'):
        _, sid = data.split(':', 1)
        try:
            sid_i = int(sid)
        except ValueError:
            return
        cursor.execute('UPDATE support_messages SET processed = 1 WHERE id = ?', (sid_i,))
        conn.commit()
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text('Отмечено как обработано.', reply_markup=admin_keyboard())
    elif data.startswith('support_next:'):
        _, sid = data.split(':', 1)
        try:
            sid_i = int(sid)
        except ValueError:
            return
        # Отправим следующее сообщение
        dummy_update = Update(update.update_id, message=query.message)
        await send_next_support_message(dummy_update, context, after_id=sid_i)

# Команда: /set_limit <user_id> <кол-во> (только админ)
async def set_limit(update: Update, context):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text('Формат: /set_limit <user|@username|id> <кол-во>')
        return
    try:
        ref = args[0]
        # Разрешить id или @username
        target: int | None = None
        if ref.startswith('@'):
            uname = ref[1:]
            cursor.execute('SELECT user_id FROM users WHERE LOWER(username) = LOWER(?)', (uname,))
            row = cursor.fetchone()
            if row:
                target = int(row[0])
        else:
            try:
                target = int(ref)
            except ValueError:
                # Попробуем как username без @
                cursor.execute('SELECT user_id FROM users WHERE LOWER(username) = LOWER(?)', (ref,))
                row = cursor.fetchone()
                if row:
                    target = int(row[0])
        if target is None:
            await update.message.reply_text('Пользователь не найден. Используй /users <поиск> чтобы найти.')
            return
        amount = int(args[1])
        add_extra_tasks(target, amount)
        await update.message.reply_text(f'Пользователю {target} добавлено {amount} extra_tasks')
    except ValueError:
        await update.message.reply_text('Неверный формат. Пример: /set_limit @username 5')

# Команда: /users [filter]
async def list_users(update: Update, context):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        return
    q = ' '.join(context.args) if context.args else ''
    if q:
        like = f"%{q.lower()}%"
        cursor.execute(
            'SELECT user_id, username, first_name, extra_tasks FROM users WHERE LOWER(COALESCE(username, "")) LIKE ? OR LOWER(COALESCE(first_name, "")) LIKE ? ORDER BY user_id DESC LIMIT 20',
            (like, like)
        )
    else:
        cursor.execute('SELECT user_id, username, first_name, extra_tasks FROM users ORDER BY user_id DESC LIMIT 20')
    rows = cursor.fetchall()
    if not rows:
        await update.message.reply_text('Пользователи не найдены.')
        return
    lines = ['Последние пользователи:']
    for uid, uname, fname, extra in rows:
        uname_disp = f"@{uname}" if uname else '(нет username)'
        fname_disp = fname or ''
        lines.append(f"{uid} — {uname_disp} — {fname_disp} — extra:{extra}")
    await update.message.reply_text('\n'.join(lines))

# Обработчик текста (объединили кнопки и решение)
async def handle_text(update: Update, context):
    text = update.message.text
    user = update.message.from_user
    user_id = user.id
    upsert_user_profile(user_id, user.username, user.first_name)
    count, limit = get_user_level(user_id)
    
    if text == 'Решить задачу':
        await update.message.reply_text('Пришли фото или текст уравнения (например: 2x + 5 = 13)')
        return
    
    elif text == 'Мой уровень':
        await update.message.reply_text(f'Сегодня: {count}/{limit} задач')
        return
    
    elif text == 'История':
        history = get_history(user_id)
        if history:
            msg = 'Последние задачи:\n'
            for ts, eq, sol in history:
                msg += f"{ts}: {eq} → {sol}\n"
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text('История пуста.')
        return
    
    elif text == 'Пригласить друга':
        await referral(update, context)
        return
    
    elif text == 'Админ панель' and user_id == ADMIN_ID:
        await update.message.reply_text('Админ панель: выбери действие.', reply_markup=admin_keyboard())
        return
    elif text == 'Назад' and user_id == ADMIN_ID:
        await update.message.reply_text('Назад в меню.', reply_markup=main_keyboard(True))
        return
    elif text == 'Статистика' and user_id == ADMIN_ID:
        cursor.execute('SELECT COUNT(*), SUM(daily_count), SUM(extra_tasks) FROM users')
        row = cursor.fetchone()
        total, used, extra = (row if row is not None else (0, 0, 0))
        await update.message.reply_text(
            f'Пользователей: {total}\n'
            f'Задач решено сегодня: {used}\n'
            f'Всего extra_tasks: {extra}',
            reply_markup=admin_keyboard()
        )
        return
    elif text == 'Пользователи' and user_id == ADMIN_ID:
        cursor.execute('SELECT user_id, username, first_name, extra_tasks FROM users ORDER BY user_id DESC LIMIT 20')
        rows = cursor.fetchall()
        if not rows:
            await update.message.reply_text('Пользователи не найдены.', reply_markup=admin_keyboard())
            return
        lines = ['Последние пользователи:']
        for uid, uname, fname, extra in rows:
            uname_disp = f"@{uname}" if uname else '(нет username)'
            fname_disp = fname or ''
            lines.append(f"{uid} — {uname_disp} — {fname_disp} — extra:{extra}")
        await update.message.reply_text('\n'.join(lines), reply_markup=admin_keyboard())
        return
    elif text == 'Опросы' and user_id == ADMIN_ID:
        # Показ последних 3 опросов с суммарными результатами
        cursor.execute('SELECT poll_id, question, options_json, total_voter_count, is_closed, last_update FROM polls ORDER BY last_update DESC LIMIT 3')
        polls = cursor.fetchall()
        if not polls:
            await update.message.reply_text('Опросов пока нет.', reply_markup=admin_keyboard())
            return
        blocks = []
        for poll_id, question, options_json, total, is_closed, ts in polls:
            try:
                options = json.loads(options_json or '[]')
            except:
                options = []
            lines = [f'Вопрос: {question}', f'Итоги (всего: {total}, статус: {"закрыт" if is_closed else "открыт"})']
            for opt in options:
                lines.append(f"- {opt.get('text', '')}: {opt.get('voter_count', 0)}")
            lines.append(f"poll_id: {poll_id}")
            if ts:
                lines.append(f"обновлено: {ts}")
            blocks.append('\n'.join(lines))
        await update.message.reply_text('\n\n'.join(blocks), reply_markup=admin_keyboard())
        return
    elif text == 'Все сообщения' and user_id == ADMIN_ID:
        await send_next_support_message(update, context, after_id=None)
        return
    
    elif context.user_data.get('support_mode'):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            'INSERT INTO support_messages (user_id, username, first_name, text, timestamp, processed) VALUES (?, ?, ?, ?, ?, 0)',
            (user_id, user.username, user.first_name, text, timestamp)
        )
        conn.commit()
        context.user_data['support_mode'] = False
        await update.message.reply_text('Сообщение отправлено администратору. Спасибо!', reply_markup=main_keyboard(user_id == ADMIN_ID))
        return
    elif text == 'Поддержка':
        context.user_data['support_mode'] = True
        await update.message.reply_text('Напиши своё сообщение. Я передам администратору.', reply_markup=main_keyboard(user_id == ADMIN_ID))
        return
    else:
        # Решение уравнения
        if count >= limit:
            await safe_reply_text(update, f'Лимит! Пригласи друга за +{REFERRAL_REWARD} задачу в день.')
            return
        
        steps, solution = solve_equation(text)
        await safe_reply_text(update, steps, parse_mode='HTML')
        
        if solution:
            increment_count(user_id)
            add_to_history(user_id, text, str(solution))

# Фото: Распознать + решить
async def handle_photo(update: Update, context):
    user = update.message.from_user
    user_id = user.id
    upsert_user_profile(user_id, user.username, user.first_name)
    count, limit = get_user_level(user_id)
    
    if count >= limit:
        await safe_reply_text(update, f'Лимит! Пригласи друга за +{REFERRAL_REWARD} задачу в день.')
        return
    
    if not OCR_ENABLED:
        await safe_reply_text(update, 'OCR отключен для экономии памяти. Пришли текст уравнения, пожалуйста.')
        return
    photo = await update.message.photo[-1].get_file()
    photo_url = photo.file_path
    response = requests.get(photo_url)
    img = Image.open(io.BytesIO(response.content))
    
    try:
        reader = get_ocr_reader()
        result = reader.readtext(img)
        text = ' '.join([detection[1] for detection in result])
    except Exception as e:
        logging.error(f"OCR error: {e}")
        await safe_reply_text(update, 'Не получилось распознать фото. Пришли текст уравнения, пожалуйста.')
        return
    
    if text.strip():
        await safe_reply_text(update, f"Matn: {text}")
        steps, solution = solve_equation(text)
        await safe_reply_text(update, steps, parse_mode='HTML')
        if solution:
            increment_count(user_id)
            add_to_history(user_id, text, str(solution))
    else:
        await safe_reply_text(update, "Matn topilmadi.")

# Запуск
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("set_limit", set_limit))
app.add_handler(CommandHandler("users", list_users))
app.add_handler(CallbackQueryHandler(admin_callbacks))
app.add_handler(PollHandler(on_poll))
app.add_handler(PollAnswerHandler(on_poll_answer))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))  # Один хендлер для текста
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# Добавляем глобальный обработчик ошибок
app.add_error_handler(error_handler)

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return """
    <h1>🧮 MathBot работает!</h1>
    <p>Бот решает уравнения в Telegram</p>
    <hr>
    <pre>
Пользователей: <b>много</b>
Задач сегодня: <b>тысячи</b>
Статус: <span style="color:green">ONLINE ✅</span>
    </pre>
    <footer>© 2025 | Deploy на Render</footer>
    """

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

if __name__ == '__main__':
    # Запускаем Flask в отдельном потоке
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Запускаем Telegram-бота
    print("🚀 Бот и сайт запущены!")
    app.run_polling()