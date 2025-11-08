import logging
import os
import io
import requests
from datetime import datetime
from PIL import Image
# EasyOCR удален для MVP
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
CHANNEL_ID = int(os.getenv('CHANNEL_USERNAME'))  # Используем как chat_id канала

# Ссылка на канал (из .env или hardcoded)
CHANNEL_LINK = "https://t.me/+A9kwpodztGUzOTZi"

if not TOKEN:
    raise ValueError("TOKEN не найден в .env!")

# Логи
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

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
                await asyncio.sleep(2 ** attempt)
        except RetryAfter as e:
            logging.warning(f"RetryAfter: ждем {e.retry_after} секунд")
            await asyncio.sleep(e.retry_after + 1)
        except NetworkError:
            logging.warning(f"NetworkError (попытка {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logging.error(f"Ошибка при отправке: {e}")
            break
    return False

# Функция: Проверка подписки на канал
async def check_subscription(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
        return False

# Рефералы
async def referral(update: Update, context):
    user_id = update.message.from_user.id
    ref_link = f"https://t.me/{context.bot.username}?start={user_id}"
    await update.message.reply_text(
        f'Пригласи друга по ссылке: {ref_link}\n'
        f'За каждого друга +{REFERRAL_REWARD} задача в день навсегда! 🎁'
    )

# Команда /start
async def start(update: Update, context):
    user = update.message.from_user
    user_id = user.id
    upsert_user_profile(user_id, user.username, user.first_name)
    
    args = context.args
    if args and args[0].isdigit():
        referrer_id = int(args[0])
        if referrer_id != user_id:
            add_extra_tasks(referrer_id, REFERRAL_REWARD)
            await context.bot.send_message(referrer_id, f'Друг присоединился! +{REFERRAL_REWARD} задача навсегда 🎉')
    
    is_sub = await check_subscription(context.bot, user_id)
    if not is_sub:
        await update.message.reply_text(
            "Привет! Подпишись на канал для задач без лимита 👇",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Подписаться", url=CHANNEL_LINK)]])
        )
        return
    
    await update.message.reply_text(
        "Добро пожаловать! Выбери урок:",
        reply_markup=main_keyboard(user_id == ADMIN_ID)
    )

# Команда /stats (для админа)
async def stats(update: Update, context):
    if update.message.from_user.id != ADMIN_ID:
        return
    cursor.execute('SELECT COUNT(*), SUM(daily_count), SUM(extra_tasks) FROM users')
    row = cursor.fetchone()
    total, used, extra = (row if row is not None else (0, 0, 0))
    await update.message.reply_text(
        f'Пользователей: {total}\n'
        f'Задач решено сегодня: {used}\n'
        f'Всего extra_tasks: {extra}'
    )

# Команда /set_limit (для админа)
async def set_limit(update: Update, context):
    if update.message.from_user.id != ADMIN_ID:
        return
    try:
        user_id, new_limit = map(int, context.args)
        cursor.execute('UPDATE users SET extra_tasks = ? WHERE user_id = ?', (new_limit, user_id))
        conn.commit()
        await update.message.reply_text(f'Extra для {user_id} = {new_limit}')
    except:
        await update.message.reply_text('Использование: /set_limit <user_id> <extra>')

# Команда /users (для админа)
async def list_users(update: Update, context):
    if update.message.from_user.id != ADMIN_ID:
        return
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

# Обработчик callback для админа
async def admin_callbacks(update: Update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith('processed_'):
        msg_id = int(data.split('_')[1])
        cursor.execute('UPDATE support_messages SET processed = 1 WHERE id = ?', (msg_id,))
        conn.commit()
        await query.edit_message_text('Сообщение обработано ✅')
        # Показать следующее
        await send_next_support_message(query.message, context, after_id=msg_id)  # Используем query.message

# Функция: Показать следующее сообщение поддержки (если есть)
async def send_next_support_message(message, context, after_id=None):
    user_id = message.chat.id  # Используем chat.id для админа
    if user_id != ADMIN_ID:
        return
    query = 'SELECT id, user_id, username, first_name, text, timestamp FROM support_messages WHERE processed = 0'
    if after_id:
        query += ' AND id > ?'
        cursor.execute(query + ' ORDER BY id ASC LIMIT 1', (after_id,))
    else:
        cursor.execute(query + ' ORDER BY id ASC LIMIT 1')
    row = cursor.fetchone()
    if not row:
        await message.reply_text('Нет новых сообщений.', reply_markup=admin_keyboard())
        return
    msg_id, user_id, uname, fname, text, ts = row
    uname_disp = f"@{uname}" if uname else ''
    fname_disp = fname or 'Без имени'
    await message.reply_text(
        f'Сообщение #{msg_id} от {fname_disp} {uname_disp} ({user_id}) в {ts}:\n{text}',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Обработано ✅", callback_data=f"processed_{msg_id}")]
        ])
    )

# Обработчик текста (перемещена проверка support_mode выше для приоритета)
async def handle_text(update: Update, context):
    user = update.message.from_user
    user_id = user.id
    upsert_user_profile(user_id, user.username, user.first_name)
    text = update.message.text.strip()
    count, limit = get_user_level(user_id)
    
    mode = context.user_data.get('mode')
    
    # Проверка support_mode
    if context.user_data.get('support_mode'):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            'INSERT INTO support_messages (user_id, username, first_name, text, timestamp, processed) VALUES (?, ?, ?, ?, ?, 0)',
            (user_id, user.username, user.first_name, text, timestamp)
        )
        conn.commit()
        context.user_data['support_mode'] = False
        await update.message.reply_text('Сообщение отправлено администратору. Спасибо!', reply_markup=main_keyboard(user_id == ADMIN_ID))
        return
    
    if text == 'Уроки по алгебре':
        await safe_reply_text(update, 'Уроки по алгебре: \n1. Линейные: ax + b = 0\n2. Квадратные: ax² + bx + c = 0\n3. Высшие степени\nПример: 2x + 5 = 13\n\nТеперь отправь уравнение или задачу по алгебре для решения.')
        context.user_data['mode'] = 'algebra'
        return
    
    elif text == 'Уроки по геометрии':
        if not get_setting('geometry_enabled'):
            await safe_reply_text(update, 'Геометрия отключена администратором.')
            return
        await safe_reply_text(update, 'Уроки по геометрии: \n1. Площадь треугольника: ½ * основание * высота\n2. Площадь круга: π * r²\n3. Теорема Пифагора: c = √(a² + b²)\nПример: площадь треугольника 6 4\n\nТеперь отправь задачу по геометрии.')
        context.user_data['mode'] = 'geometry'
        return
    
    elif text == 'Уроки по физике':
        if not get_setting('physics_enabled'):
            await safe_reply_text(update, 'Физика отключена администратором.')
            return
        await safe_reply_text(update, 'Уроки по физике: \n1. Скорость: v = s / t\n2. Сила: F = m * a\n3. Работа: A = F * s\nПример: скорость 100 2\n\nТеперь отправь задачу по физике.')
        context.user_data['mode'] = 'physics'
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

# Фото: Распознать + решить (упрощено без OCR для MVP)
async def handle_photo(update: Update, context):
    await safe_reply_text(update, 'Фото-распознавание отключено в MVP. Пришли текст уравнения, пожалуйста.')

# /help
async def help_command(update: Update, context):
    await update.message.reply_text(
        "🔥 <b>SchoolBot — твой помощник!</b>\n\n"
        "Что умею:\n"
        "✅ Алгебра: уравнения 5–11 класс\n"
        "✅ Геометрия: площадь, периметр, Пифагор\n"
        "✅ Физика: скорость, сила, работа\n"
        "📸 Фото (отключено в MVP)\n"
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

app.run_polling()