import sqlite3
import os
from config import OWNER_ID

DATA_DIR = "/data" if os.path.exists("/data") else os.environ.get("DATA_DIR", ".")
DB_PATH = os.path.abspath(os.path.join(DATA_DIR, "bot_management.db"))

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('CREATE TABLE IF NOT EXISTS processed (id TEXT PRIMARY KEY)')
        c.execute('CREATE TABLE IF NOT EXISTS auth_users (user_id INTEGER PRIMARY KEY)')
        c.execute('CREATE TABLE IF NOT EXISTS auth_chats (chat_id INTEGER PRIMARY KEY)')
        c.execute('CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, rename_format TEXT, thumb_id TEXT, font_id TEXT, logo_id TEXT, dump_id TEXT)')
        
        try: c.execute('ALTER TABLE user_settings ADD COLUMN logo_id TEXT')
        except: pass
        try: c.execute('ALTER TABLE user_settings ADD COLUMN dump_id TEXT')
        except: pass
        conn.commit()

def is_user_auth(user_id):
    if user_id == OWNER_ID: return True
    with sqlite3.connect(DB_PATH) as conn:
        res = conn.execute("SELECT 1 FROM auth_users WHERE user_id = ?", (user_id,)).fetchone()
    return bool(res)

def add_auth_user(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO auth_users (user_id) VALUES (?)", (user_id,))

def del_auth_user(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM auth_users WHERE user_id = ?", (user_id,))

def is_chat_auth(chat_id):
    if chat_id == OWNER_ID: return True
    with sqlite3.connect(DB_PATH) as conn:
        res = conn.execute("SELECT 1 FROM auth_chats WHERE chat_id = ?", (chat_id,)).fetchone()
    return bool(res)

def add_auth_chat(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO auth_chats (chat_id) VALUES (?)", (chat_id,))

def del_auth_chat(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM auth_chats WHERE chat_id = ?", (chat_id,))

def get_user_settings(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        res = conn.execute("SELECT rename_format, thumb_id, font_id, logo_id FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
        if res:
            return {"rename_format": res[0], "thumb_id": res[1], "font_id": res[2], "logo_id": res[3]}
        return {"rename_format": None, "thumb_id": None, "font_id": None, "logo_id": None}

def update_user_setting(user_id, key, value):
    settings = get_user_settings(user_id)
    settings[key] = value
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
        conn.execute(f"UPDATE user_settings SET {key} = ? WHERE user_id = ?", (value, user_id))
        conn.commit()

def add_processed_id(key):
    with sqlite3.connect(DB_PATH) as conn:
        try:
            conn.execute("INSERT INTO processed (id) VALUES (?)", (key,))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def set_user_dump(user_id, dump_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
        conn.execute("UPDATE user_settings SET dump_id = ? WHERE user_id = ?", (str(dump_id) if dump_id else None, user_id))
        conn.commit()

def get_user_dump(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        res = conn.execute("SELECT dump_id FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
        return res[0] if res and res[0] else None
