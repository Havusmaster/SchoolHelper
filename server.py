# server.py — FastAPI + Telegram webhook + админ панель + Swagger на /swagger
from fastapi import FastAPI, HTTPException, Depends, Header, Body
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Dict
from dotenv import load_dotenv
import os
import logging

from telegram import Update
from telegram.ext import ContextTypes

# Импорт бота
from main import telegram_app  # Application

from db import supabase, supabase_admin, add_extra_tasks, get_setting, set_setting

load_dotenv()

app = FastAPI(
    title="SchoolBot Backend + Bot",
    description="Telegram бот (webhook) + API со Swagger",
    version="1.0",
    docs_url="/swagger",  # ← Swagger на /swagger
    redoc_url="/redoc"
)

API_KEY = os.getenv("API_KEY")
APP_URL = os.getenv("APP_URL")  # https://schoolhelper-1.onrender.com (без /)

logging.basicConfig(level=logging.INFO)

# === Админ панель HTML ===
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>SchoolBot Admin Panel</title>
    <style>
        body { font-family: Arial; background: #f4f4f4; color: #333; padding: 20px; }
        .card { background: white; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .stats { display: flex; gap: 20px; }
        .stat { flex: 1; text-align: center; }
        #users-list { list-style: none; padding: 0; }
        li { margin-bottom: 10px; }
    </style>
</head>
<body>
    <h1>SchoolBot Admin Panel</h1>
    <div class="card">
        <h2>Статистика</h2>
        <div class="stats" id="stats"></div>
    </div>
    <div class="card">
        <h2>Пользователи</h2>
        <ul id="users-list"></ul>
    </div>
    <script>
        async function fetchData(url) { return (await fetch(url, {headers: {'x-api-key': 'твой_API_KEY_из_env'}})).json(); }  // Замени на свой API_KEY

        async function loadStatus() {
            const data = await fetchData('/api/status');
            const stats = document.getElementById('stats');
            stats.innerHTML = `
                <div class="stat"><strong>Пользователей:</strong> ${data.users}</div>
                <div class="stat"><strong>Решено всего:</strong> ${data.total_solved}</div>
            `;
        }

        async function loadUsers() {
            const users = await fetchData('/api/users');
            const list = document.getElementById('users-list');
            users.forEach(u => {
                const li = document.createElement('li');
                li.innerHTML = `ID: ${u.user_id} | Имя: ${u.first_name || 'нет'} | Extra: ${u.extra_tasks || 0}
                    <input type="number" id="extra-${u.user_id}" placeholder="Добавить extra">
                    <button onclick="addExtra(${u.user_id})">Добавить</button>`;
                list.appendChild(li);
            });
        }

        async function addExtra(userId) {
            const amount = document.getElementById(`extra-${userId}`).value;
            if (amount) {
                await fetch('/api/add_extra', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'x-api-key': 'твой_API_KEY_из_env'},
                    body: JSON.stringify({user_id: userId, amount: parseInt(amount)})
                });
                alert('Добавлено!');
                location.reload();
            }
        }

        loadStatus();
        loadUsers();
    </script>
</body>
</html>
"""
# Замени 'твой_API_KEY_из_env' на реальный, или используй env в JS (но для простоты — харCODE, или передай через сервер)

# === Главная: Админ панель ===
@app.get("/")
async def admin_panel():
    return HTMLResponse(ADMIN_HTML)

# === Установка webhook ===
@app.on_event("startup")
async def startup_event():
    await telegram_app.initialize()  # Fix for initialize error
    webhook_url = f"{APP_URL}/webhook"
    await telegram_app.bot.set_webhook(webhook_url)
    logging.info(f"Webhook установлен на {webhook_url}")

# === Webhook ===
@app.post("/webhook")
async def telegram_webhook(update: Dict = Body(...)):
    telegram_update = Update.de_json(update, telegram_app.bot)
    await telegram_app.process_update(telegram_update)
    return {"ok": True}

# === API ===
async def auth(api_key: str = Header(None)):
    if api_key != API_KEY:
        raise HTTPException(401, "Неверный API ключ")

class ExtraRequest(BaseModel):
    user_id: int
    amount: int

class SettingRequest(BaseModel):
    key: str
    value: int

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
    return {"success": True}

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