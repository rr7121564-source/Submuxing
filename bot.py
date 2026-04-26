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
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, TypeHandler, ApplicationHandlerStop
)

# ================================
# SETUP & LOGGING
# ================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

SESSION_ID = str(uuid.uuid4())[:8]
OWNER_ID = int(os.getenv("ADMIN_ID", 0))
active_processes = {}
global_task_lock = asyncio.Lock()

DB_PATH = os.path.abspath("bot_management.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS processed (id TEXT PRIMARY KEY)')
    c.execute('CREATE TABLE IF NOT EXISTS auth_users (user_id INTEGER PRIMARY KEY)')
    c.execute('CREATE TABLE IF NOT EXISTS auth_chats (chat_id INTEGER PRIMARY KEY)')
    conn.commit()
    conn.close()

init_db()

def is_auth(update: Update):
    uid = update.effective_user.id
    cid = update.effective_chat.id
    if uid == OWNER_ID: return True
    conn = sqlite3.connect(DB_PATH)
    u_auth = conn.execute("SELECT 1 FROM auth_users WHERE user_id = ?", (uid,)).fetchone()
    c_auth = conn.execute("SELECT 1 FROM auth_chats WHERE chat_id = ?", (cid,)).fetchone()
    conn.close()
    return True if (u_auth or c_auth) else False

# ================================
# UPLOAD PROGRESS WRAPPER
# ================================
class ProgressFile(object):
    def __init__(self, filename, callback, status_msg, start_time):
        self._file = open(filename, 'rb')
        self._callback = callback
        self._status_msg = status_msg
        self._start_time = start_time
        self._total_size = os.path.getsize(filename)
        self._current_size = 0
        self._last_update = 0

    def read(self, size=-1):
        data = self._file.read(size)
        self._current_size += len(data)
        
        now = time.time()
        # Update progress every 15 seconds to avoid Telegram Flood limits
        if now - self._last_update > 15:
            asyncio.create_task(self._callback(self._current_size, self._total_size, self._status_msg, self._start_time))
            self._last_update = now
        return data

    def close(self):
        self._file.close()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()

# ================================
# HELPERS
# ================================
def get_prog_bar(perc):
    filled = int(perc / 10)
    return "▰" * filled + "▱" * (10 - filled)

def human_size(bytes, units=[' bytes', ' KB', ' MB', ' GB', ' TB']):
    return str(round(bytes, 2)) + units[0] if bytes < 1024 else human_size(bytes / 1024, units[1:])

async def get_video_duration(file_path):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    try: return float(stdout.decode().strip())
    except: return 0.0

# ================================
# MUXING PROGRESS LOGIC
# ================================
async def mux_video_live(mkv_path, sub_path, out_path, chat_id, status_msg, total_size_bytes):
    duration = await get_video_duration(mkv_path)
    os.makedirs("fonts", exist_ok=True)
    font_args = []
    for idx, f in enumerate(os.listdir("fonts")):
        fp = os.path.join("fonts", f)
        ext = os.path.splitext(f)[1].lower()
        mtype = "application/x-truetype-font" if ext in ['.ttf', '.ttc'] else "application/vnd.ms-opentype" if ext == '.otf' else ""
        if mtype: font_args.extend(["-attach", fp, f"-metadata:s:t:{idx}", f"mimetype={mtype}"])

    cmd = ['ffmpeg', '-y', '-i', mkv_path, '-i', sub_path, '-map', '0:v', '-map', '0:a?', '-map', '1', '-c', 'copy', '-disposition:s:0', 'default', '-metadata:s:s:0', 'language=eng', '-metadata:s:s:0', 'title=Hinglish'] + font_args + ['-progress', 'pipe:1', out_path]
    
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    active_processes[chat_id] = proc
    
    start_t = time.time()
    last_up = 0

    while True:
        line = await proc.stdout.readline()
        if not line: break
        line = line.decode('utf-8').strip()
        
        if line.startswith('out_time_us='):
            now = time.time()
            if now - last_up > 15:
                try:
                    cur_us = int(line.split('=')[1])
                    perc = min(99.9, (cur_us / 1000000 / duration) * 100) if duration > 0 else 0
                    processed_bytes = (perc / 100) * total_size_bytes
                    elapsed = now - start_t
                    speed_bps = processed_bytes / elapsed if elapsed > 0 else 0
                    
                    text = (
                        f"Muxing start\n\n"
                        f"{get_prog_bar(perc)} {perc:.2f}%\n\n"
                        f"Speed            processing\n"
                        f"{human_size(speed_bps)}/s     {human_size(processed_bytes)} / {human_size(total_size_bytes)}"
                    )
                    await status_msg.edit_text(text)
                    last_up = now
                except: pass

    await proc.wait()
    if chat_id in active_processes: del active_processes[chat_id]
    return proc.returncode == 0

# ================================
# UPLOAD PROGRESS CALLBACK
# ================================
async def upload_progress_update(current, total, status_msg, start_time):
    now = time.time()
    perc = (current / total) * 100
    elapsed = now - start_time
    speed = current / elapsed if elapsed > 0 else 0
    text = (
        f"Uploading start\n\n"
        f"{get_prog_bar(perc)} {perc:.2f}%\n\n"
        f"Speed            processing\n"
        f"{human_size(speed)}/s     {human_size(current)} / {human_size(total)}"
    )
    try: await status_msg.edit_text(text)
    except: pass

# ================================
# HANDLERS
# ================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update): return
    text = (
        "Wᴇʟᴄᴏᴍᴇ Tᴏ Sᴏғᴛsᴜʙ Bᴏᴛ\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Sᴛᴇᴘs :\n"
        "1. Sᴇɴᴅ ᴠɪᴅᴇᴏ (ᴍᴋᴠ)\n"
        "2. Sᴇɴᴅ sᴜʙᴛɪᴛʟᴇ (sʀᴛ/ᴀss)\n"
        "3. Sᴇᴛ ᴏᴜᴛᴘᴜᴛ ɴᴀᴍᴇ (ᴏʀ sᴋɪᴘ)\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Gᴇᴛ ᴍᴋᴠ ᴡɪᴛʜ sᴜʙᴛɪᴛʟᴇ, ғᴏɴᴛs"
    )
    await update.message.reply_text(text)

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update): return
    doc = update.message.document
    if not doc: return
    ext = os.path.splitext(doc.file_name)[1].lower()

    if ext == '.mkv':
        context.user_data['mkv_id'] = doc.file_id
        context.user_data['orig_name'] = doc.file_name
        context.user_data['file_size'] = doc.file_size
        context.user_data['state'] = 'WAIT_SUB'
        await update.message.reply_text("Mᴋᴠ Rᴇᴄᴇɪᴠᴇᴅ\nɴᴏᴡ sᴇɴᴅ ᴛʜᴇ sᴜʙᴛɪᴛʟᴇ (sʀᴛ/ᴀss) ғɪʟᴇ")
    
    elif ext in ['.srt', '.ass'] and context.user_data.get('state') == 'WAIT_SUB':
        context.user_data['sub_id'] = doc.file_id
        context.user_data['state'] = 'WAIT_NAME'
        await update.message.reply_text("Sᴜʙᴛɪᴛʟᴇ Rᴇᴄᴇɪᴠᴇᴅ\nsᴇɴᴅ ᴀ ɴᴀᴍᴇ ᴏʀ /skip ᴛᴏ ᴋᴇᴇᴘ ᴏʀɪɢɪɴᴀʟ")

async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') == 'WAIT_NAME':
        context.user_data['state'] = None
        await start_task(update, context, context.user_data['orig_name'])

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') == 'WAIT_NAME':
        name = update.message.text.strip()
        if not name.lower().endswith('.mkv'): name += '.mkv'
        context.user_data['state'] = None
        await start_task(update, context, name)

async def start_task(update: Update, context: ContextTypes.DEFAULT_TYPE, final_name: str):
    data = {
        'chat_id': update.effective_chat.id,
        'mkv_id': context.user_data['mkv_id'],
        'sub_id': context.user_data['sub_id'],
        'size': context.user_data['file_size'],
        'name': final_name
    }
    status = await update.message.reply_text("⏳ Processing...")
    asyncio.create_task(run_queue(context, data, status))

async def run_queue(context, data, status):
    async with global_task_lock:
        tmp = os.path.abspath(f"task_{data['chat_id']}_{int(time.time())}")
        os.makedirs(tmp, exist_ok=True)
        out = os.path.join(tmp, data['name'])
        thumb = os.path.join(tmp, "thumb.jpg")
        
        try:
            m_f = await context.bot.get_file(data['mkv_id'], read_timeout=3600)
            s_f = await context.bot.get_file(data['sub_id'], read_timeout=3600)
            
            # 1. MUXING
            success = await mux_video_live(m_f.file_path, s_f.file_path, out, data['chat_id'], status, data['size'])
            
            if success:
                # 2. AUTO THUMBNAIL
                os.system(f'ffmpeg -y -i "{out}" -ss 00:00:10 -vframes 1 "{thumb}"')
                
                # 3. UPLOADING
                await status.edit_text("Uploading start")
                start_u = time.time()
                
                th_file = open(thumb, 'rb') if os.path.exists(thumb) else None
                try:
                    # Wrapped File for progress tracking
                    with ProgressFile(out, upload_progress_update, status, start_u) as pf:
                        await context.bot.send_document(
                            chat_id=data['chat_id'],
                            document=pf,
                            thumbnail=th_file,
                            caption="muxing complete",
                            read_timeout=3600,
                            write_timeout=3600
                        )
                finally:
                    if th_file: th_file.close()
                await status.delete()
            else: await status.edit_text("❌ Muxing Failed.")
        except Exception as e: 
            await status.edit_text(f"Error: {e}")
            logging.error(f"Task Error: {e}")
        finally:
            try: shutil.rmtree(tmp)
            except: pass

# ================================
# ADMIN & SYSTEM
# ================================
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"ID: `{update.effective_chat.id}`", parse_mode='Markdown')

async def admin_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    cmd = update.message.text.split()
    if len(cmd) < 2: return
    target_id = int(cmd[1]); action = cmd[0]
    conn = sqlite3.connect(DB_PATH)
    if "add_user" in action: conn.execute("INSERT OR IGNORE INTO auth_users VALUES (?)", (target_id,))
    elif "add_chat" in action: conn.execute("INSERT OR IGNORE INTO auth_chats VALUES (?)", (target_id,))
    conn.commit(); conn.close()
    await update.message.reply_text("✅ Authorized")

class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, *args): pass

def main():
    token = os.getenv("BOT_TOKEN")
    if not token: return
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), Health).serve_forever(), daemon=True).start()

    app = ApplicationBuilder().token(token).base_url("http://127.0.0.1:8081/bot").base_file_url("http://127.0.0.1:8081/file/bot").local_mode(True).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler(["add_user", "add_chat"], admin_auth))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print(f"Bot Started | Admin: {OWNER_ID}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
