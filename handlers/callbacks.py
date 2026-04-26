from telegram import Update
from telegram.ext import ContextTypes
from config import active_processes

async def cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # callback_data format: "cancel_12345"
    chat_id = int(query.data.split("_")[1])
    
    if chat_id in active_processes:
        process = active_processes[chat_id]
        process.terminate() # FFmpeg stop kar dega
        await query.edit_message_text("🛑 Process stopped by user.")
    else:
        await query.answer("No active process found for this task.", show_alert=True)
