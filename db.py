# db.py
import os
from datetime import datetime
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))
supabase_admin: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
DAILY_LIMIT = int(os.getenv('DAILY_LIMIT', 111))

# === Пользователи ===
def get_user_level(user_id: int):
    today = datetime.now().strftime('%Y-%m-%d')
    resp = supabase.table('users').select("*").eq('user_id', user_id).execute()
    if resp.data:
        user = resp.data[0]
        if user['last_date'] != today:
            supabase.table('users').update({'daily_count': 0, 'last_date': today}).eq('user_id', user_id).execute()
            count = 0
        else:
            count = user['daily_count']
        extra = user.get('extra_tasks', 0)
    else:
        supabase.table('users').insert({'user_id': user_id, 'daily_count': 0, 'last_date': today, 'extra_tasks': 0}).execute()
        count = 0
        extra = 0
    limit = DAILY_LIMIT + extra
    if user_id == ADMIN_ID:
        limit = float('inf')
    return count, limit

def increment_count(user_id: int):
    current = supabase.table('users').select('daily_count').eq('user_id', user_id).execute().data[0]['daily_count']
    supabase.table('users').update({'daily_count': current + 1}).eq('user_id', user_id).execute()

def add_extra_tasks(user_id: int, amount: int):
    current = supabase.table('users').select('extra_tasks').eq('user_id', user_id).execute()
    extra = current.data[0]['extra_tasks'] if current.data else 0
    supabase_admin.table('users').update({'extra_tasks': extra + amount}).eq('user_id', user_id).execute()

def upsert_user_profile(user_id: int, username: str | None, first_name: str | None):
    resp = supabase.table('users').select('user_id').eq('user_id', user_id).execute()
    data = {'username': username, 'first_name': first_name}
    if resp.data:
        supabase.table('users').update(data).eq('user_id', user_id).execute()
    else:
        data.update({'user_id': user_id, 'daily_count': 0, 'last_date': datetime.now().strftime('%Y-%m-%d'), 'extra_tasks': 0})
        supabase.table('users').insert(data).execute()

# === История ===
def add_to_history(user_id: int, equation: str, solution: str):
    supabase.table('history').insert({
        'user_id': user_id,
        'timestamp': datetime.now().isoformat(),
        'equation': equation,
        'solution': solution
    }).execute()

def get_history(user_id: int, limit: int = 5):
    resp = supabase.table('history').select('equation, solution').eq('user_id', user_id).order('id', desc=True).limit(limit).execute()
    return [(r['equation'], r['solution']) for r in resp.data]

# === Настройки ===
def get_setting(key: str, default: int = 0):
    resp = supabase.table('settings').select('value').eq('key', key).execute()
    return resp.data[0]['value'] if resp.data else default

def set_setting(key: str, value: int):
    supabase_admin.table('settings').upsert({'key': key, 'value': value}).execute()