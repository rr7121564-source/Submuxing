from telegram import Update
from telegram.ext import ContextTypes
from config import OWNER_ID
from database.db_handler import manage_auth

async def admin_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    
    msg_text = update.message.text.split()
    if len(msg_text) < 2: 
        return await update.message.reply_text("Usage: `/add_user ID` or `/add_chat ID`")
    
    try:
        target_id = int(msg_text[1])
        action = msg_text[0] # /add_user, /rem_user etc.
        
        manage_auth(action, target_id)
        
        emoji = "✅" if "add" in action else "❌"
        status = "authorized" if "add" in action else "removed"
        await update.message.reply_text(f"{emoji} Target `{target_id}` {status}.")
    except ValueError:
        await update.message.reply_text("❌ Invalid ID. Please send a number.")
