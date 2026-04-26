import sqlite3
from telegram import Update
from telegram.ext import ContextTypes, ApplicationHandlerStop
from database.db_handler import is_user_auth, is_chat_auth, DB_PATH
from config import OWNER_ID

async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user: return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if user_id == OWNER_ID: return
    if not is_chat_auth(chat_id) and not is_user_auth(user_id):
        raise ApplicationHandlerStop()

async def block_duplicates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message: return
    msg = update.effective_message
    key = f"{msg.chat_id}_{msg.message_id}"
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("INSERT OR IGNORE INTO processed (id) VALUES (?)", (key,))
        conn.commit()
        if conn.total_changes == 0: raise ApplicationHandlerStop()
    finally: conn.close()
