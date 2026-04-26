import os
import json
import time
import asyncio
import logging
import threading
import shutil
import sqlite3
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import RetryAfter
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, TypeHandler, ApplicationHandlerStop
)

# ================================
# SETUP & CONFIG
# ================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

SESSION_ID = str(uuid.uuid4())[:8]
OWNER_ID = int(os.getenv("ADMIN_ID", 0)) # Set this in Env Vars
active_processes = {}
global_task_lock = asyncio.Lock()

# ================================
# 🛡️ DATABASE (Auth & Duplicates) 🛡️
# ================================
DB_PATH = os.path.abspath("bot_lock.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS processed (id TEXT PRIMARY KEY)')
    c.execute('CREATE TABLE IF NOT EXISTS auth_users (user_id INTEGER PRIMARY KEY)')
    c.execute('CREATE TABLE IF NOT EXISTS auth_chats (chat_id INTEGER PRIMARY KEY)')
    conn.commit()
    conn.close()

init_db()

# --- Helper Functions for Auth ---
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

# ================================
# 🛡️ SECURITY MIDDLEWARE 🛡️
# ================================
async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user: return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Owner has absolute access
    if user_id == OWNER_ID: return

    # Check if Group or User is Authorized
    if not is_chat_auth(chat_id) and not is_user_auth(user_id):
        # Ignore silent or send a restricted message once
        if update.message and update.message.text and update.message.text.startswith('/start'):
            await update.message.reply_text("⛔ **Access Denied!**\nYou or this group is not authorized to use this bot.\nContact Admin.")
        raise ApplicationHandlerStop()

async def block_duplicates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message: return
    msg = update.effective_message
    key = f"{msg.chat_id}_{msg.message_id}"
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute("INSERT OR IGNORE INTO processed (id) VALUES (?)", (key,))
        conn.commit()
        if conn.total_changes == 0: raise ApplicationHandlerStop()
    finally: conn.close()

# ================================
# 👑 ADMIN COMMANDS 👑
# ================================
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"👤 Your ID: `{update.effective_user.id}`\n👥 Chat ID: `{update.effective_chat.id}`", parse_mode='Markdown')

async def admin_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    cmd = update.message.text.split()
    if len(cmd) < 2: return await update.message.reply_text("Usage: `/add_user ID` or `/add_chat ID`")
    
    target_id = int(cmd[1])
    action = cmd[0]
    conn = sqlite3.connect(DB_PATH)
    
    if action == "/add_user":
        conn.execute("INSERT OR IGNORE INTO auth_users VALUES (?)", (target_id,))
        await update.message.reply_text(f"✅ User `{target_id}` authorized.")
    elif action == "/rem_user":
        conn.execute("DELETE FROM auth_users WHERE user_id = ?", (target_id,))
        await update.message.reply_text(f"❌ User `{target_id}` access removed.")
    elif action == "/add_chat":
        conn.execute("INSERT OR IGNORE INTO auth_chats VALUES (?)", (target_id,))
        await update.message.reply_text(f"✅ Chat `{target_id}` authorized.")
    elif action == "/rem_chat":
        conn.execute("DELETE FROM auth_chats WHERE chat_id = ?", (target_id,))
        await update.message.reply_text(f"❌ Chat `{target_id}` access removed.")
    
    conn.commit()
    conn.close()

# ================================
# DUMMY SERVER & UTILS (FFMPEG etc.)
# ================================
# ... (Wahi purana mux_video aur extraction wala logic yahan aayega) ...
# Maine space bachane ke liye niche sirf main handlers dikhaye hain

# [Yahan wahi saare functions (mux_video, get_duration, start_task etc.) jo upar the, repeat honge]
# (Make sure to keep those functions from the previous version)

async def mux_video(mkv_path, sub_path, output_path, chat_id, status_msg):
    # Same as previous logic
    # ...
    return True # Placeholder

# [Utility functions code here]

# ================================
# BOT HANDLERS
# ================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "🤖 **Muxing Bot Active!**\n\nSend MKV to begin."
    await update.message.reply_text(msg)

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc: return
    ext = os.path.splitext(doc.file_name)[1].lower()
    if ext == '.mkv':
        context.user_data['mkv_id'] = doc.file_id
        context.user_data['orig_name'] = doc.file_name
        context.user_data['state'] = 'WAIT_SUB'
        await update.message.reply_text("✅ MKV Received! Send Subtitle.")
    elif ext in ['.srt', '.ass'] and context.user_data.get('state') == 'WAIT_SUB':
        # ... logic for task start ...
        pass

# ================================
# MAIN ENTRY
# ================================
def main():
    token = os.getenv("BOT_TOKEN")
    if not token: return
    
    # Run dummy server in thread
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), HealthCheckHandler).serve_forever(), daemon=True).start()

    app = ApplicationBuilder().token(token).base_url("http://127.0.0.1:8081/bot").base_file_url("http://127.0.0.1:8081/file/bot").local_mode(True).build()

    # 1. Security Check (Sabse pehle)
    app.add_handler(TypeHandler(Update, check_access), group=-2)
    # 2. Duplicate Check
    app.add_handler(TypeHandler(Update, block_duplicates), group=-1)
    
    # Admin Commands
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler(["add_user", "rem_user", "add_chat", "rem_chat"], admin_auth))
    
    # Bot Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    # ... (Other handlers like Photo, Text etc. same as before) ...

    print(f"--- ADMIN {OWNER_ID} SESSION {SESSION_ID} STARTED ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
