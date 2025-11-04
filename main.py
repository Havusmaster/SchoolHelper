# SchoolBot — с автозагрузкой зависимостей
import os
import subprocess
import sys

# Автоустановка зависимостей
def install_requirements():
    if not os.path.exists('requirements.txt'):
        return
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'])

install_requirements()

# Теперь импортируем
import logging, re, sqlite3, requests
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from PIL import Image
import io, easyocr
from sympy import symbols, Eq, solve, Poly
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application, convert_xor
from flask import Flask
from threading import Thread

load_dotenv()
TOKEN = os.getenv('TOKEN')
DAILY_LIMIT = int(os.getenv('DAILY_LIMIT', 3))
REFERRAL_REWARD = int(os.getenv('REFERRAL_REWARD', 1))

logging.basicConfig(level=logging.INFO)
reader = easyocr.Reader(['ru', 'en'], gpu=False)

conn = sqlite3.connect('users.db', check_same_thread=False)
c = conn.cursor()
c.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, daily_count INTEGER DEFAULT 0, last_date TEXT, extra_tasks INTEGER DEFAULT 0)')
c.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, user_id INTEGER, ts TEXT, eq TEXT, sol TEXT)')
conn.commit()

# === ВСЁ ОСТАЛЬНОЕ — ТОТ ЖЕ КОД, ЧТО БЫЛ РАНЬШЕ ===
# (вставь сюда весь код от def get_user(...) и до конца)

# ВСТАВЬ СЮДА ВЕСЬ КОД ОТ "def get_user" ДО "app.run_polling()"
# (я сократил, чтобы не повторяться — скопируй из прошлого сообщения)

# ------------------- ВСТАВЬ СЮДА -------------------
def get_user(user_id):
    today = datetime.now().strftime('%Y-%m-%d')
    c.execute('SELECT daily_count, last_date, extra_tasks FROM users WHERE user_id=?', (user_id,))
    row = c.fetchone()
    if row:
        count, last, extra = row
        if last != today:
            count, extra = 0, extra
            c.execute('UPDATE users SET daily_count=0, last_date=? WHERE user_id=?', (today, user_id))
            conn.commit()
        return count, DAILY_LIMIT + extra
    else:
        c.execute('INSERT INTO users VALUES (?,0,?,0)', (user_id, today))
        conn.commit()
        return 0, DAILY_LIMIT

def inc(user_id):
    c.execute('UPDATE users SET daily_count = daily_count + 1 WHERE user_id=?', (user_id,))
    conn.commit()

def add_extra(user_id, n):
    c.execute('UPDATE users SET extra_tasks = extra_tasks + ? WHERE user_id=?', (n, user_id))
    conn.commit()

def save_history(user_id, eq, sol):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    c.execute('INSERT INTO history (user_id, ts, eq, sol) VALUES (?,?,?,?)', (user_id, ts, eq, sol))
    conn.commit()

def solve(txt):
    try:
        txt = re.sub(r'\s+', '', txt.lower())
        txt = txt.replace('х', 'x')
        txt = re.sub(r'([a-z])(\d)', r'\1**\2', txt)
        txt = re.sub(r'[^0-9a-z+\-*/()=.\^]', '', txt)
        if '=' not in txt: return "Нет '='", None
        l, r = txt.split('=', 1)
        x = symbols('x')
        trans = standard_transformations + (implicit_multiplication_application, convert_xor,)
        le, re = parse_expr(l, transformations=trans), parse_expr(r, transformations=trans)
        eq = Eq(le, re)
        sol = solve(eq, x)
        diff = (le - re).simplify()
        steps = [f"Уравнение: {txt}", f"Лево: {le} = Право: {re}", f"→ {diff} = 0"]
        poly = Poly(diff, x)
        if poly and poly.degree() == 1:
            a, b = poly.all_coeffs()
            steps += [f"{a}x = {-b}", f"x = {-b/a}", f"x = {sol[0]}"]
        else:
            steps.append(f"x = {sol}")
        return '\n'.join(steps), sol
    except Exception as e:
        return f"Ошибка: {e}", None

async def start(update: Update, context):
    u = update.message.from_user.id
    args = context.args
    if args and args[0].startswith('ref_'):
        ref = int(args[0].split('_')[1])
        if ref != u:
            add_extra(ref, REFERRAL_REWARD)
            await context.bot.send_message(ref, f"Друг пришёл! +{REFERRAL_REWARD} задача навсегда! 🚀")
    get_user(u)
    kb = [['Решить задачу'], ['Мой уровень', 'История'], ['Пригласить друга']]
    await update.message.reply_text('Salom! Меню:', reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def text(update: Update, context):
    t = update.message.text
    u = update.message.from_user.id
    count, limit = get_user(u)
    if t == 'Решить задачу':
        await update.message.reply_text('Фото или текст уравнения!')
        return
    if t == 'Мой уровень':
        await update.message.reply_text(f'Сегодня: {count}/{limit}')
        return
    if t == 'История':
        c.execute('SELECT ts, eq, sol FROM history WHERE user_id=? ORDER BY id DESC LIMIT 5', (u,))
        h = c.fetchall()
        msg = 'Последние:\n' if h else 'Пусто'
        for ts, eq, sol in h:
            msg += f"{ts}: {eq} → {sol}\n"
        await update.message.reply_text(msg)
        return
    if t == 'Пригласить друга':
        link = f"https://t.me/{context.bot.username}?start=ref_{u}"
        await update.message.reply_text(f"Твоя ссылка:\n{link}\n+{REFERRAL_REWARD} задача за друга!")
        return
    if count >= limit:
        await update.message.reply_text(f'Лимит! Пригласи друга → +{REFERRAL_REWARD}')
        return
    steps, sol = solve(t)
    await update.message.reply_text(steps or "Не понял")
    if sol:
        inc(u)
        save_history(u, t, str(sol))

async def photo(update: Update, context):
    u = update.message.from_user.id
    count, limit = get_user(u)
    if count >= limit:
        await update.message.reply_text('Лимит! Пригласи друга')
        return
    file = await update.message.photo[-1].get_file()
    bytes = await file.download_as_bytearray()
    img = Image.open(io.BytesIO(bytes))
    txt = ' '.join([x[1] for x in reader.readtext(img)])
    await update.message.reply_text(f"Текст: {txt}")
    steps, sol = solve(txt)
    await update.message.reply_text(steps)
    if sol:
        inc(u)
        save_history(u, txt, str(sol))

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
app.add_handler(MessageHandler(filters.PHOTO, photo))

# Flask — 24/7
flask = Flask('')
@flask.route('/')
def home():
    return "Bot жив! 🚀"
def run_flask():
    flask.run(host='0.0.0.0', port=8080)
Thread(target=run_flask).start()

print("Bot запущен!")
app.run_polling()