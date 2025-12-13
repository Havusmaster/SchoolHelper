# main.py — Только настройка бота (для импорта в server.py)
import logging
import os
import io
from PIL import Image
import easyocr

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from dotenv import load_dotenv
load_dotenv()

from algebra import solve_equation
from db import *

TOKEN = os.getenv('TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))

logging.basicConfig(level=logging.INFO)

_ocr_reader = None
def get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        _ocr_reader = easyocr.Reader(['ru', 'en'], gpu=False)
    return _ocr_reader

def main_keyboard(is_admin: bool = False):
    kb = [['Уроки по алгебре'], ['Мой уровень', 'История'], ['Пригласить друга', 'Поддержка']]
    if is_admin:
        kb.append(['Админ панель'])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user_profile(user.id, user.username, user.first_name)
    await update.message.reply_text(
        "Привет! Я SchoolBot — решаю уравнения по алгебре.\nВыбери урок:",
        reply_markup=main_keyboard(user.id == ADMIN_ID)
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if text == 'Уроки по алгебре':
        context.user_data['mode'] = 'algebra'
        await update.message.reply_text("Пришли уравнение (например: 2x + 5 = 13) или фото:")
        return

    if text == 'Мой уровень':
        count, limit = get_user_level(user_id)
        limit_text = 'неограничено' if limit == float('inf') else limit
        await update.message.reply_text(f"Задач сегодня: {count}/{limit_text}")
        return

    if text == 'История':
        history = get_history(user_id)
        msg = '\n'.join([f"{eq} → {sol}" for eq, sol in history]) or "История пуста"
        await update.message.reply_text(msg)
        return

    if context.user_data.get('mode') == 'algebra':
        count, limit = get_user_level(user_id)
        if user_id != ADMIN_ID and count >= limit:
            await update.message.reply_text("Лимит на сегодня исчерпан! Пригласи друга за +1 задачу.")
            return

        steps, solution = solve_equation(text)
        await update.message.reply_text(steps or "Не удалось решить.", parse_mode='HTML')
        if solution:
            increment_count(user_id)
            add_to_history(user_id, text, str(solution))

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    count, limit = get_user_level(user_id)
    if user_id != ADMIN_ID and count >= limit:
        await update.message.reply_text("Лимит исчерпан!")
        return

    file = await update.message.photo[-1].get_file()
    bytes_data = await file.download_as_bytearray()
    img = Image.open(io.BytesIO(bytes_data))

    reader = get_ocr_reader()
    result = reader.readtext(img, detail=0, paragraph=True)
    text = ' '.join(result)

    await update.message.reply_text(f"Распознанный текст:\n{text}\n\nРешаю...")
    steps, solution = solve_equation(text)
    await update.message.reply_text(steps or "Не удалось распознать уравнение.", parse_mode='HTML')
    if solution:
        increment_count(user_id)
        add_to_history(user_id, text, str(solution))

# Строим приложение (без run_polling!)
telegram_app = Application.builder().token(TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))