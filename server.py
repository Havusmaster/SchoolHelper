# server.py — ВСЁ В ОДНОМ: FastAPI бэкенд + Telegram бот в фоне
import os
import threading
import asyncio
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
import uvicorn

# Импортируем бота
from main import app as telegram_app  # Это Application из python-telegram-bot
from db import supabase, supabase_admin, add_extra_tasks, get_setting, set_setting

load_dotenv()

app = FastAPI(
    title="SchoolBot Backend + Bot",
    description="Telegram бот + API со Swagger",
    version="1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

API_KEY = os.getenv("API_KEY")

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
    return {"message": "SchoolBot работает! Бот активен, API: /docs"}

@app.get("/api/status")
def status():
    users = supabase.table('users').select('user_id', count='exact').execute().count or 0
    solved = supabase.table('history').select('id', count='exact').execute().count or 0
    return {"users": users, "total_solved": solved, "bot": "running"}

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

# === Запуск Telegram бота в отдельном потоке ===
def run_bot():
    print("Запускаем Telegram бота...")
    asyncio.run(telegram_app.run_polling(drop_pending_updates=True))

# Запускаем бота в фоне
threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, workers=1)