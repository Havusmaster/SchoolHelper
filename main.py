import logging
import os
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler
import easyocr
import requests
from PIL import Image
import io
from sympy import symbols, Eq, solve, simplify, Poly, sqrt
from sympy.solvers import solve as sym_solve
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application, convert_xor
import sqlite3
from datetime import datetime, timedelta
import re

# Загружаем .env
load_dotenv()

# TOKEN
TOKEN = os.getenv('TOKEN')

# Конфиг из .env
DAILY_LIMIT = int(os.getenv('DAILY_LIMIT', 3))
REFERRAL_REWARD = int(os.getenv('REFERRAL_REWARD', 1))

if not TOKEN:
    raise ValueError("TOKEN не найден в .env!")

# Логи
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# EasyOCR reader
reader = easyocr.Reader(['ru', 'en'], gpu=False)

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

# Функция: Увеличить счётчик
def increment_count(user_id):
    cursor.execute('UPDATE users SET daily_count = daily_count + 1 WHERE user_id = ?', (user_id,))
    conn.commit()

# Функция: Добавить extra_tasks за реферала
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

# Функция: Реферальная система
async def referral(update: Update, context):
    user_id = update.message.from_user.id
    ref_link = f"https://t.me/{context.bot.username}?start=ref_{user_id}"
    await update.message.reply_text(
        f"Пригласи друга — получи +{REFERRAL_REWARD} задачу в день!\n\n"
        f"Твоя ссылка: {ref_link}\n\n"
        f"Друг перейдёт — твой лимит навсегда +{REFERRAL_REWARD} задач/день."
    )

# Функция: Решить уравнение + пошагово
def solve_equation(equation_text):
    try:
        # Очистка (как раньше)
        text = re.sub(r'\s+', '', equation_text)
        text = text.lower().replace('х', 'x').replace('ь', '').replace("'", '').replace('"', '').replace('`', '').replace('’', '').replace('‘', '')
        text = re.sub(r'([a-z])(\d)', r'\1**\2', text)
        text = re.sub(r'[^0-9a-z+\-*/()=.\^]', '', text)

        if '=' not in text:
            return "Ошибка: Нет '='. Пример: '2x+5=13'", None
        
        left, right = text.split('=', 1)
        left = left.strip()
        right = right.strip()
        
        if not left or not right:
            return "Ошибка: Пустая сторона.", None
        
        x = symbols('x')
        transformations = standard_transformations + (implicit_multiplication_application, convert_xor,)
        
        left_expr = parse_expr(left, transformations=transformations)
        right_expr = parse_expr(right, transformations=transformations)
        
        eq = Eq(left_expr, right_expr)
        solution = sym_solve(eq, x)
        
        # Шаги (как раньше)
        steps = []
        steps.append(f"Уравнение: {equation_text}")
        steps.append(f"Очищенное: {left} = {right}")
        
        diff_expr = simplify(left_expr - right_expr)
        steps.append(f"Влево: {diff_expr} = 0")
        
        poly = Poly(diff_expr, x)
        if poly is not None:
            degree = poly.degree()
            coeffs = poly.all_coeffs()
            if degree == 1:
                a = coeffs[0]
                b = coeffs[1] if len(coeffs) > 1 else 0
                steps.append(f"{a}x = {-b}")
                steps.append(f"x = {-b} / {a}")
                steps.append(f"x = {solution[0]}")
            elif degree == 2:
                a = coeffs[0]
                b = coeffs[1] if len(coeffs) > 1 else 0
                c = coeffs[2] if len(coeffs) > 2 else 0
                steps.append(f"{a}x² + {b}x + {c} = 0")
                disc = simplify(b**2 - 4*a*c)
                steps.append(f"D = {disc}")
                if disc >= 0:
                    steps.append(f"x1 = {solution[0]}")
                    steps.append(f"x2 = {solution[1]}")
            else:
                steps.append(f"x = {solution}")
        else:
            steps.append(f"x = {solution}")
        
        return '\n'.join(steps), solution
    except Exception as e:
        return f"Ошибка: {str(e)}. Введи вручную.", None

# /start с меню и рефералами
async def start(update: Update, context):
    user_id = update.message.from_user.id
    args = context.args
    
    # Если по рефералке
    if args and args[0].startswith('ref_'):
        referrer_id = int(args[0].split('_')[1])
        if referrer_id != user_id:
            add_extra_tasks(referrer_id, REFERRAL_REWARD)
            await context.bot.send_message(referrer_id, f"Друг зарегистрировался! Твой лимит +{REFERRAL_REWARD} задач/день навсегда! 🚀")
    
    # Обычный старт
    get_user_level(user_id)
    keyboard = [['Решить задачу'], ['Мой уровень', 'История'], ['Пригласить друга']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text('Salom! Выбери в меню:', reply_markup=reply_markup)

# Обработчик текста (объединили кнопки и решение)
async def handle_text(update: Update, context):
    text = update.message.text
    user_id = update.message.from_user.id
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
    
    else:
        # Решение уравнения
        if count >= limit:
            await update.message.reply_text(f'Лимит! Пригласи друга за +{REFERRAL_REWARD} задачу в день.')
            return
        
        steps, solution = solve_equation(text)
        await update.message.reply_text(steps)
        
        if solution:
            increment_count(user_id)
            add_to_history(user_id, text, str(solution))

# Фото: Распознать + решить
async def handle_photo(update: Update, context):
    user_id = update.message.from_user.id
    count, limit = get_user_level(user_id)
    
    if count >= limit:
        await update.message.reply_text(f'Лимит! Пригласи друга за +{REFERRAL_REWARD} задачу в день.')
        return
    
    photo = await update.message.photo[-1].get_file()
    photo_url = photo.file_path
    response = requests.get(photo_url)
    img = Image.open(io.BytesIO(response.content))
    
    result = reader.readtext(img)
    text = ' '.join([detection[1] for detection in result])
    
    if text.strip():
        await update.message.reply_text(f"Matn: {text}")
        steps, solution = solve_equation(text)
        await update.message.reply_text(steps)
        if solution:
            increment_count(user_id)
            add_to_history(user_id, text, str(solution))
    else:
        await update.message.reply_text("Matn topilmadi.")

# Запуск
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))  # Один хендлер для текста
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

app.run_polling()