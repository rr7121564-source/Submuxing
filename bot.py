import os, time, asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, TypeHandler, ApplicationHandlerStop
)

from config import BOT_TOKEN, OWNER_ID, PORT, SESSION_ID, global_task_lock, active_processes
from database import init_db, is_user_auth, is_chat_auth, add_processed_id
from utils import mux_video, clean_temp_files, get_readable_time

def humanbytes(size):
    if not size: return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024

# --- MIDDLEWARES ---
async def check_access(update, context):
    if not update.effective_chat or not update.effective_user: return
    if update.effective_user.id == OWNER_ID: return
    if not is_chat_auth(update.effective_chat.id) and not is_user_auth(update.effective_user.id):
        raise ApplicationHandlerStop()

async def block_duplicates(update, context):
    if not update.effective_message: return
    key = f"{update.effective_message.chat_id}_{update.effective_message.message_id}"
    if not add_processed_id(key): raise ApplicationHandlerStop()

# --- HANDLERS ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🤖 **Muxing Bot Active!**\n\n1️⃣ Send MKV.\n2️⃣ Send Subtitle.\n3️⃣ Send Name (or /skip).")

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc: return
    ext = os.path.splitext(doc.file_name)[1].lower()
    if ext == '.mkv':
        context.user_data.update({'mkv_id': doc.file_id, 'orig_name': doc.file_name, 'state': 'WAIT_SUB'})
        await update.message.reply_text("✅ MKV Received! Now send **Subtitle (.srt/.ass)**.")
    elif ext in ['.srt', '.ass'] and context.user_data.get('state') == 'WAIT_SUB':
        context.user_data.update({'sub_id': doc.file_id, 'state': 'WAIT_NAME'})
        await update.message.reply_text("✅ Subtitle Received! Send **New Name** (with .mkv) or /skip.")

async def cmd_skip(update, context):
    if context.user_data.get('state') == 'WAIT_NAME':
        await start_task(update, context, context.user_data['orig_name'])

async def handle_text(update, context):
    if context.user_data.get('state') == 'WAIT_NAME':
        name = update.message.text.strip()
        if not name.lower().endswith('.mkv'): name += '.mkv'
        await start_task(update, context, name)

async def start_task(update, context, final_name):
    context.user_data['state'] = None
    data = {'chat_id': update.effective_chat.id, 'mkv_id': context.user_data['mkv_id'], 
            'sub_id': context.user_data['sub_id'], 'name': final_name}
    status = await update.message.reply_text("⏳ **Added to Queue...**")
    asyncio.create_task(run_queue(context, data, status))

async def run_queue(context, data, status):
    async with global_task_lock:
        await status.edit_text("⚙️ **Initializing Task...**")
        tmp = os.path.abspath(f"task_{data['chat_id']}_{int(time.time())}")
        os.makedirs(tmp, exist_ok=True)
        out = os.path.join(tmp, data['name'])
        
        try:
            # Downloading
            m_f = await context.bot.get_file(data['mkv_id'], read_timeout=3600)
            s_f = await context.bot.get_file(data['sub_id'], read_timeout=3600)
            
            # Muxing with Progress
            success = await mux_video(m_f.file_path, s_f.file_path, out, data['chat_id'], status)
            
            if success:
                f_size = os.path.getsize(out)
                await status.edit_text(f"📤 **Uploading to Telegram...**\n📂 **Size:** {humanbytes(f_size)}")
                await context.bot.send_document(chat_id=data['chat_id'], document=f"file://{out}", read_timeout=3600)
                await status.delete()
            else:
                await status.edit_text("❌ **Muxing Failed.**")
        except Exception as e:
            await status.edit_text(f"❌ **Error:** {e}")
        finally:
            clean_temp_files(tmp)

async def cancel_cb(update, context):
    cid = update.effective_chat.id
    if cid in active_processes:
        active_processes[cid].terminate()
        await update.callback_query.edit_message_text("🛑 **Process Stopped by User.**")

# --- WEB & MAIN ---
class HealthCheck(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(f"OK {SESSION_ID}".encode())

def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), HealthCheck).serve_forever(), daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).base_url("http://127.0.0.1:8081/bot").local_mode(True).build()
    
    app.add_handler(TypeHandler(Update, check_access), group=-2)
    app.add_handler(TypeHandler(Update, block_duplicates), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern=r"^cancel_"))
    
    print(f"--- BOT STARTED WITH PROGRESS BAR ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
