# main.py (исправленная версия - фикс indentation и реализовал недостающие функции из оригинала)
# Исправления:
# - Добавил тело для async def send_next_support_message(...) на основе логики поддержки (из оригинального описания).
# - Добавил тело для async def admin_callbacks(...) для обработки кнопок "Обработано" и "Следующее".
# - Добавил обработку support_mode в начале handle_text (сохранение сообщения в БД).
# - Добавил вызов secret_phrase в handle_text если текст == "этат очен харашо" (поскольку handler не добавлен).
# - Убрал async def safe_reply_text из indented блока (оно было после комментария, но теперь правильно).
# - Фикс: В add_to_history добавил недостающий параметр solution (был truncated).
# - Остальной код без изменений, только фиксы ошибок.

import logging
import os
import io
import requests
from datetime import datetime
from PIL import Image
# EasyOCR импортируем лениво внутри функции, чтобы экономить память
import sqlite3

# Импорт модулей решения задач
from algebra import solve_equation
from geometry import solve_geometry
from physics import solve_physics

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, CallbackContext
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

# ID канала для проверки подписки (из .env, это -1003173491640)
CHANNEL_ID = int(os.getenv('CHANNEL_ID', os.getenv('CHANNEL_USERNAME')))  # Фикс: Работает с CHANNEL_USERNAME или CHANNEL_ID

# Ссылка на канал (из .env или hardcoded)
CHANNEL_LINK = "https://t.me/A9kwpodztGUzOTZi"

if not TOKEN:
    raise ValueError("TOKEN не найден в .env!")

# Логи
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Настройки OCR из окружения (для экономии памяти на Render можно выключить)
OCR_ENABLED = os.getenv('OCR_ENABLED', '1') in ('1', 'true', 'True')
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

# Таблица для настроек (новая)
cursor.execute('''
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value INTEGER DEFAULT 0
)
''')
conn.commit()

# Функции для настроек
def get_setting(key):
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    row = cursor.fetchone()
    return row[0] if row else 0  # По умолчанию выключено

def set_setting(key, value):
    cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()

# Инициализация настроек по умолчанию (выключены, как в закомментированном коде)
if get_setting('geometry_enabled') == 0:
    set_setting('geometry_enabled', 0)
if get_setting('physics_enabled') == 0:
    set_setting('physics_enabled', 0)

# Основная клавиатура (динамическая на основе настроек)
def main_keyboard(is_admin: bool):
    geometry_enabled = get_setting('geometry_enabled')
    physics_enabled = get_setting('physics_enabled')
    
    keyboard = [
        ['Уроки по алгебре'],
    ]
    if geometry_enabled:
        keyboard.append(['Уроки по геометрии'])
    if physics_enabled:
        keyboard.append(['Уроки по физике'])
    
    keyboard += [
        ['Мой уровень', 'История'], 
        ['Пригласить друга'], 
        ['Поддержка']
    ]
    if is_admin:
        keyboard.append(['Админ панель'])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Клавиатура админа (добавлены кнопки для вкл/выкл)
def admin_keyboard():
    keyboard = [
        ['Статистика', 'Пользователи'],
        ['Все сообщения'],
        ['Вкл/Выкл Геометрию', 'Вкл/Выкл Физику'],
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
    timestamp = datetime.now().isoformat()
    cursor.execute('INSERT INTO history (user_id, timestamp, equation, solution) VALUES (?, ?, ?, ?)',
                   (user_id, timestamp, equation, solution))
    conn.commit()

# /start
async def start(update: Update, context):
    user = update.effective_user
    user_id = user.id
    upsert_user_profile(user_id, user.username, user.first_name)

    # Проверка подписки
    is_subscribed = await check_subscription(context.bot, user_id)
    if not is_subscribed:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Подписаться", url=CHANNEL_LINK)]])
        await update.message.reply_text(
            "Подпишись на канал, чтобы пользоваться ботом 👇",
            reply_markup=keyboard
        )
        return

    # Клавиатура
    reply_markup = main_keyboard(user_id == ADMIN_ID)
    await update.message.reply_text(
        "Привет! Я SchoolBot — решаю задачи по школьным предметам.\n"
        f"Лимит: {DAILY_LIMIT} в день (+{REFERRAL_REWARD} за друга).\n"
        "Выбери урок:",
        reply_markup=reply_markup
    )

# Проверка подписки
async def check_subscription(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

# /stats (админ)
async def stats(update: Update, context):
    if update.message.from_user.id != ADMIN_ID:
        return
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    cursor.execute('SELECT SUM(daily_count) FROM users')
    total_tasks = cursor.fetchone()[0] or 0
    await update.message.reply_text(f'Пользователей: {total_users}\nЗадач сегодня: {total_tasks}')

# /set_limit (админ)
async def set_limit(update: Update, context):
    if update.message.from_user.id != ADMIN_ID:
        return
    try:
        args = context.args
        user_id = int(args[0])
        new_limit = int(args[1])
        add_extra_tasks(user_id, new_limit - DAILY_LIMIT)
        await update.message.reply_text(f'Лимит для {user_id} установлен на {new_limit}')
    except:
        await update.message.reply_text('Использование: /set_limit <user_id> <new_limit>')

# /users (админ)
async def list_users(update: Update, context):
    if update.message.from_user.id != ADMIN_ID:
        return
    cursor.execute('SELECT user_id, daily_count, extra_tasks FROM users')
    users = cursor.fetchall()
    text = '\n'.join([f'{u[0]}: {u[1]}/{DAILY_LIMIT + u[2]}' for u in users])
    await update.message.reply_text(text or 'Нет пользователей')

# Callback для админа
async def admin_callbacks(update: Update, context):
    query = update.callback_query
    data = query.data
    if data.startswith('processed_'):
        msg_id = int(data.split('_')[1])
        cursor.execute('UPDATE support_messages SET processed = 1 WHERE id = ?', (msg_id,))
        conn.commit()
        await query.edit_message_text("Сообщение отмечено как обработанное.")
        await send_next_support_message(query.message, context, msg_id)
    elif data.startswith('next_'):
        after_id = int(data.split('_')[1])
        await send_next_support_message(query.message, context, after_id)

# Функция отправки сообщений поддержки (с next)
async def send_next_support_message(message, context, after_id=None):
    cursor.execute('SELECT id, user_id, username, first_name, text FROM support_messages WHERE processed = 0 AND id > ? ORDER BY id ASC LIMIT 1', (after_id or 0,))
    row = cursor.fetchone()
    if row:
        msg_id, uid, uname, fname, txt = row
        text = f"Сообщение от {fname} (@{uname or 'нет'}): {txt}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Обработано", callback_data=f"processed_{msg_id}")],
            [InlineKeyboardButton("Следующее", callback_data=f"next_{msg_id}")]
        ])
        await message.reply_text(text, reply_markup=keyboard)
    else:
        await message.reply_text("Нет новых сообщений.")

# Безопасная отправка с retry для TimedOut
async def safe_reply_text(update, text, parse_mode=None, retry_count=3):
    for attempt in range(retry_count):
        try:
            await update.message.reply_text(text, parse_mode=parse_mode)
            return
        except TimedOut:
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
        except Exception as e:
            logging.error(f"Error sending message: {e}")
            break

# Текст: Обработка режимов
async def handle_text(update: Update, context):
    user = update.message.from_user
    user_id = user.id
    upsert_user_profile(user_id, user.username, user.first_name)
    text = update.message.text.strip()
    count, limit = get_user_level(user_id)
    mode = context.user_data.get('mode', None)

    if context.user_data.get('support_mode', False):
        timestamp = datetime.now().isoformat()
        cursor.execute('INSERT INTO support_messages (user_id, username, first_name, text, timestamp) VALUES (?, ?, ?, ?, ?)',
                       (user_id, user.username, user.first_name, text, timestamp))
        conn.commit()
        await safe_reply_text(update, 'Сообщение отправлено админу. Спасибо!')
        context.user_data['support_mode'] = False
        return

    if text.lower() == "этат очен харашо":
        await secret_phrase(update, context)
        return

    if text == 'Уроки по алгебре':
        context.user_data['mode'] = 'algebra'
        await safe_reply_text(update, 'Пришли уравнение: 2x + 5 = 13 или фото.')
        return
    elif text == 'Уроки по геометрии':
        if get_setting('geometry_enabled') == 0:
            await safe_reply_text(update, 'Геометрия отключена.')
            return
        context.user_data['mode'] = 'geometry'
        await safe_reply_text(update, 'Пришли задачу: площадь треугольника 6 4')
        return
    elif text == 'Уроки по физике':
        if get_setting('physics_enabled') == 0:
            await safe_reply_text(update, 'Физика отключена.')
            return
        context.user_data['mode'] = 'physics'
        await safe_reply_text(update, 'Пришли задачу: скорость 100 2')
        return
    elif text == 'Мой уровень':
        await safe_reply_text(update, f'Задач сегодня: {count}/{limit}')
        return
    elif text == 'История':
        cursor.execute('SELECT equation, solution FROM history WHERE user_id = ? ORDER BY id DESC LIMIT 5', (user_id,))
        hist = cursor.fetchall()
        lines = [f'{eq}: {sol}' for eq, sol in hist]
        await safe_reply_text(update, '\n'.join(lines) or 'История пуста.')
        return
    elif text == 'Пригласить друга':
        await safe_reply_text(update, f'Ссылка: https://t.me/your_bot?start={user_id}\nЗа друга +{REFERRAL_REWARD} задача.')
        return
    elif text == 'Админ панель' and user_id == ADMIN_ID:
        await update.message.reply_text('Админ панель:', reply_markup=admin_keyboard())
        return
    elif text == 'Назад' and user_id == ADMIN_ID:
        await update.message.reply_text('Назад в главное.', reply_markup=main_keyboard(True))
        return
    elif text == 'Статистика' and user_id == ADMIN_ID:
        await stats(update, context)
        return
    elif text == 'Пользователи' and user_id == ADMIN_ID:
        cursor.execute('SELECT user_id, username, first_name, extra_tasks FROM users')
        rows = cursor.fetchall()
        lines = []
        for uid, uname, fname, extra in rows:
            uname_disp = '@' + uname if uname else '(нет username)'
            fname_disp = fname or ''
            lines.append(f"{uid} — {uname_disp} — {fname_disp} — extra:{extra}")
        await update.message.reply_text('\n'.join(lines), reply_markup=admin_keyboard())
        return
    elif text == 'Все сообщения' and user_id == ADMIN_ID:
        await send_next_support_message(update.message, context, after_id=None)
        return
    
    elif text == 'Вкл/Выкл Геометрию' and user_id == ADMIN_ID:
        current = get_setting('geometry_enabled')
        new = 1 - current
        set_setting('geometry_enabled', new)
        status = "включена" if new else "выключена"
        await update.message.reply_text(f'Геометрия {status}.', reply_markup=admin_keyboard())
        return
    
    elif text == 'Вкл/Выкл Физику' and user_id == ADMIN_ID:
        current = get_setting('physics_enabled')
        new = 1 - current
        set_setting('physics_enabled', new)
        status = "включена" if new else "выключена"
        await update.message.reply_text(f'Физика {status}.', reply_markup=admin_keyboard())
        return
    
    elif text == 'Поддержка':
        context.user_data['support_mode'] = True
        await update.message.reply_text('Напиши своё сообщение. Я передам администратору.', reply_markup=main_keyboard(user_id == ADMIN_ID))
        return
    
    # Проверки режимов перемещены сюда, после всех кнопок
    if mode == 'algebra':
        if count >= limit:
            await safe_reply_text(update, f'Лимит! Пригласи друга за +{REFERRAL_REWARD} задачу в день.')
            return
        steps, solution = solve_equation(text)
        await safe_reply_text(update, steps, parse_mode='HTML')
        if solution:
            increment_count(user_id)
            add_to_history(user_id, text, str(solution))
        return
    
    elif mode == 'geometry':
        if count >= limit:
            await safe_reply_text(update, f'Лимит! Пригласи друга за +{REFERRAL_REWARD} задачу в день.')
            return
        steps, solution = solve_geometry(text)
        await safe_reply_text(update, steps)
        if solution:
            increment_count(user_id)
            add_to_history(user_id, text, str(solution))
        return
    
    elif mode == 'physics':
        if count >= limit:
            await safe_reply_text(update, f'Лимит! Пригласи друга за +{REFERRAL_REWARD} задачу в день.')
            return
        steps, solution = solve_physics(text)
        await safe_reply_text(update, steps)
        if solution:
            increment_count(user_id)
            add_to_history(user_id, text, str(solution))
        return
    
    else:
        # Нет автоматического решения
        await safe_reply_text(update, 'Выбери урок, чтобы увидеть объяснение и решить задачу.')

# Фото: Распознать + решить (оставили как алгебру по умолчанию, но можно изменить)
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
        await safe_reply_text(update, f"Текст: {text}")
        steps, solution = solve_equation(text)
        await safe_reply_text(update, steps, parse_mode='HTML')
        if solution:
            increment_count(user_id)
            add_to_history(user_id, text, str(solution))
    else:
        await safe_reply_text(update, "Текст не найден.")

# /help
async def help_command(update: Update, context):
    await update.message.reply_text(
        "🔥 <b>SchoolBot — твой помощник!</b>\n\n"
        "Что умею:\n"
        "✅ Алгебра: уравнения 5–11 класс\n"
        "✅ Геометрия: площадь, периметр, Пифагор\n"
        "✅ Физика: скорость, сила, работа\n"
        "📸 Фото\n"
        "🎁 +1 за друга\n"
        "🏆 Лимит 111/день\n\n"
        "Выбери урок, затем отправь задачу.",
        parse_mode='HTML'
    )

# Кнопка проверки подписки
async def check_sub_button(update: Update, context):
    query = update.callback_query
    await query.answer()
    is_sub = await check_subscription(context.bot, query.from_user.id)
    if is_sub:
        await query.edit_message_text(
            "✅ Ты в канале! Можешь решать задачи без лимита.\n"
            "Нажми /start — поехали!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Обновить", callback_data="check_again")]])
        )
    else:
        await query.edit_message_text("❌ Ты отписался. Подпишись снова 👇", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Подписаться", url=CHANNEL_LINK)]]
        ))

# Секретная фраза
async def secret_phrase(update: Update, context):
    if update.message.text.strip().lower() == "этат очен харашо":
        # Замени на реальный стикер ID, если есть
        # await update.message.reply_sticker("CAACAgIAAxkBAAIBUmcbF...")  
        await update.message.reply_text(
            "ЭТО ОЧЕНЬ ХОРОШО! ✅\n"
            "Ты нашёл пасхалку! +10 задач навсегда 🎉",
            reply_markup=main_keyboard(update.message.from_user.id == ADMIN_ID)
        )
        add_extra_tasks(update.message.from_user.id, 10)

# Глобальный обработчик ошибок
async def error_handler(update: object, context: CallbackContext) -> None:
    """Log the error raised by the bot."""
    logging.error("Exception while handling an update:", exc_info=context.error)

# Запуск
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("set_limit", set_limit))
app.add_handler(CommandHandler("users", list_users))
app.add_handler(CallbackQueryHandler(admin_callbacks))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))  # Один хендлер для текста
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CallbackQueryHandler(check_sub_button, pattern="^check_again$"))

# Добавляем глобальный обработчик ошибок
app.add_error_handler(error_handler)

# Wrapper для запуска в asyncio (фикс ошибки)
async def bot_main():
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    # Ждем бесконечно (для фона)
    await asyncio.Event().wait()