# server.py — FastAPI бэкенд + Telegram webhook (без polling/threads)
from fastapi import FastAPI, HTTPException, Depends, Header, Body, Request
from pydantic import BaseModel
from typing import Dict
from dotenv import load_dotenv
import os
import logging

from telegram import Update
from telegram.ext import ContextTypes

# Импорт бота
from main import telegram_app  # Это готовая Application

from db import supabase, supabase_admin, add_extra_tasks, get_setting, set_setting

load_dotenv()

app = FastAPI(
    title="SchoolBot Backend + Bot",
    description="Telegram бот (webhook) + API со Swagger",
    version="1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

API_KEY = os.getenv("API_KEY")
APP_URL = os.getenv("APP_URL")  # https://schoolhelper-1.onrender.com

logging.basicConfig(level=logging.INFO)

# === Установка webhook при запуске ===
@app.on_event("startup")
async def startup_event():
    webhook_url = f"{APP_URL}/webhook"  # Путь для webhook
    await telegram_app.bot.set_webhook(webhook_url)
    logging.info(f"Webhook установлен на {webhook_url}")

# === Webhook эндпоинт для Telegram ===
@app.post("/webhook")
async def telegram_webhook(update: Dict = Body(...)):
    telegram_update = Update.de_json(update, telegram_app.bot)
    await telegram_app.process_update(telegram_update)
    return {"ok": True}

# === API роуты (как раньше) ===
async def auth(api_key: str = Header(None)):
    if api_key != API_KEY:
        raise HTTPException(401, "Неверный API ключ")

class ExtraRequest(BaseModel):
    user_id: int
    amount: int

class SettingRequest(BaseModel):
    key: str
    value: int

@app.get("/")
def home():
    return {"message": "SchoolBot работает! Бот на webhook, API: /docs"}

@app.get("/api/status")
def status():
    users = supabase.table('users').select('user_id', count='exact').execute().count or 0
    solved = supabase.table('history').select('id', count='exact').execute().count or 0
    return {"users": users, "total_solved": solved, "bot": "running on webhook"}

@app.get("/api/users")
def get_users(_: str = Depends(auth)):
    data = supabase.table('users').select('*').execute().data
    return data

@app.post("/api/add_extra")
def add_extra(req: ExtraRequest, _: str = Depends(auth)):
    add_extra_tasks(req.user_id, req.amount)
    return {"success": True, "message": f"Добавлено {req.amount} пользователю {req.user_id}"}

@app.get("/api/settings")
def get_settings(_: str = Depends(auth)):
    data = supabase.table('settings').select('*').execute().data
    return {d['key']: d['value'] for d in data}

@app.post("/api/settings")
def update_setting(req: SettingRequest, _: str = Depends(auth)):
    set_setting(req.key, req.value)
    return {"success": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))