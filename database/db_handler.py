import sqlite3
import os
from config import OWNER_ID

DB_PATH = os.path.abspath("bot_management.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS processed (id TEXT PRIMARY KEY)')
    c.execute('CREATE TABLE IF NOT EXISTS auth_users (user_id INTEGER PRIMARY KEY)')
    c.execute('CREATE TABLE IF NOT EXISTS auth_chats (chat_id INTEGER PRIMARY KEY)')
    conn.commit()
    conn.close()

def is_user_auth(user_id):
    if user_id == OWNER_ID: return True
    conn = sqlite3.connect(DB_PATH)
    res = conn.execute("SELECT 1 FROM auth_users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return True if res else False

def is_chat_auth(chat_id):
    if chat_id == OWNER_ID: return True
    conn = sqlite3.connect(DB_PATH)
    res = conn.execute("SELECT 1 FROM auth_chats WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return True if res else False

def manage_auth(action, target_id):
    conn = sqlite3.connect(DB_PATH)
    if action == "/add_user":
        conn.execute("INSERT OR IGNORE INTO auth_users VALUES (?)", (target_id,))
    elif action == "/rem_user":
        conn.execute("DELETE FROM auth_users WHERE user_id = ?", (target_id,))
    elif action == "/add_chat":
        conn.execute("INSERT OR IGNORE INTO auth_chats VALUES (?)", (target_id,))
    elif action == "/rem_chat":
        conn.execute("DELETE FROM auth_chats WHERE chat_id = ?", (target_id,))
    conn.commit()
    conn.close()
