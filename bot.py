import os
import json
import time
import asyncio
import logging
import threading
import shutil
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import RetryAfter
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, TypeHandler, ApplicationHandlerStop
)

# ================================
# SETUP & LOGGING
# ================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
active_processes = {}
global_task_lock = asyncio.Lock()

# ================================
# 🛡️ ANTI-DUPLICATE DB 🛡️
# ================================
DB_PATH = os.path.abspath("bot_lock.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS processed (id TEXT PRIMARY KEY)')
    conn.commit()
    conn.close()

init_db()

async def block_duplicates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update or not update.message: return
    key = f"msg_{update.message.chat_id}_{update.message.message_id}"
    conn = sqlite3.connect(DB_PATH, timeout=5)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO processed (id) VALUES (?)", (key,))
        conn.commit()
    except sqlite3.IntegrityError:
        raise ApplicationHandlerStop()
    finally:
        conn.close()

# ================================
# DUMMY SERVER FOR RENDER
# ================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running Perfectly!")
    def log_message(self, *args): pass

def run_dummy_server(port):
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# ================================
# FFMEPG & UTILS
# ================================
def clean_temp_files(path):
    try:
        if os.path.isdir(path): shutil.rmtree(path)
        elif os.path.exists(path): os.remove(path)
    except: pass

async def get_duration(file_path):
    cmd =['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    try: return float(stdout.decode().strip())
    except: return 0.0

async def extract_subtitles(mkv_path, original_name):
    cmd =['ffprobe', '-v', 'error', '-select_streams', 's', '-show_entries', 'stream=index,codec_name', '-of', 'json', mkv_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    data = json.loads(stdout.decode())
    extracted = []
    base = os.path.splitext(original_name)[0]
    for stream in data.get('streams', []):
        idx, codec = stream['index'], stream.get('codec_name', 'srt')
        ext = ".ass" if codec == "ass" else ".srt"
        out = os.path.abspath(f"{base}_{idx}{ext}")
        ex_cmd = ['ffmpeg', '-y', '-i', mkv_path, '-map', f"0:{idx}", '-c:s', 'copy', out]
        ex_proc = await asyncio.create_subprocess_exec(*ex_cmd, stderr=asyncio.subprocess.DEVNULL)
        await ex_proc.wait()
        if os.path.exists(out): extracted.append(out)
    return extracted

async def create_square_thumb(inp, outp):
    cmd = ['ffmpeg', '-y', '-i', inp, '-vf', "crop='min(iw,ih)':'min(iw,ih)',scale=320:320", outp]
    p = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.DEVNULL)
    await p.wait()

async def mux_video(mkv_path, sub_path, output_path, chat_id, status_msg):
    duration = await get_duration(mkv_path)
    os.makedirs("fonts", exist_ok=True)
    font_args = []
    for idx, f in enumerate(os.listdir("fonts")):
        fp = os.path.join("fonts", f)
        ext = os.path.splitext(f)[1].lower()
        mtype = "application/x-truetype-font" if ext in ['.ttf', '.ttc'] else "application/vnd.ms-opentype" if ext == '.otf' else ""
        if mtype: font_args.extend(["-attach", fp, f"-metadata:s:t:{idx}", f"mimetype={mtype}"])

    cmd = ['ffmpeg', '-y', '-i', mkv_path, '-i', sub_path, '-map', '0:v', '-map', '0:a?', '-map', '1', '-c', 'copy', '-disposition:s:0', 'default', '-metadata:s:s:0', 'language=eng', '-metadata:s:s:0', 'title=Hinglish'] + font_args + ['-progress', 'pipe:1', output_path]
    
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    active_processes[chat_id] = proc
    last_up = time.time()

    while True:
        line = await proc.stdout.readline()
        if not line: break
        line = line.decode('utf-8').strip()
        if line.startswith('out_time_us='):
            cur = int(line.split('=')[1]) / 1000000
            if duration > 0 and time.time() - last_up > 12:
                perc = min(100, (cur / duration) * 100)
                try:
                    await status_msg.edit_text(f"⚙️ Muxing: {perc:.2f}%\n(Wait for upload...)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{chat_id}")]]))
                    last_up = time.time()
                except: pass
    await proc.wait()
    if chat_id in active_processes: del active_processes[chat_id]
    return proc.returncode == 0

# ================================
# BOT HANDLERS
# ================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear() # Purana data saaf
    await update.message.reply_text("🤖 **Bot Active!**\n\n1️⃣ Send MKV file.\n2️⃣ Send Subtitle file.\n3️⃣ Send New Name (or /skip).")

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc: return
    ext = os.path.splitext(doc.file_name)[1].lower()

    if ext == '.mkv':
        context.user_data['mkv_id'] = doc.file_id
        context.user_data['orig_name'] = doc.file_name
        context.user_data['state'] = 'WAIT_SUB'
        await update.message.reply_text("✅ **MKV Received!**\n📥 Now send the **Subtitle (.srt/.ass)** file directly.")

    elif ext in ['.srt', '.ass']:
        if context.user_data.get('state') == 'WAIT_SUB':
            context.user_data['sub_id'] = doc.file_id
            context.user_data['state'] = 'WAIT_NAME'
            await update.message.reply_text("✅ **Subtitle Received!**\n✏️ Send a **New Name** for the file or /skip.")
        else:
            await update.message.reply_text("⚠️ Send MKV first.")

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

async def cmd_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = 'WAIT_THUMB'
    await update.message.reply_text("🖼️ Send a photo for the thumbnail.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') == 'WAIT_THUMB':
        context.user_data['thumb_id'] = update.message.photo[-1].file_id
        context.user_data['state'] = None
        await update.message.reply_text("✅ Thumbnail saved!")

async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rep = update.message.reply_to_message
    if not rep or not rep.document or not rep.document.file_name.endswith('.mkv'):
        return await update.message.reply_text("Reply to an MKV with /extract")
    
    status = await update.message.reply_text("📥 Extracting...")
    try:
        m_file = await context.bot.get_file(rep.document.file_id)
        subs = await extract_subtitles(m_file.file_path, rep.document.file_name)
        for s in subs:
            await context.bot.send_document(chat_id=update.effective_chat.id, document=f"file://{s}")
            clean_temp_files(s)
        await status.delete()
    except Exception as e: await status.edit_text(f"Error: {e}")

async def start_task(update, context, final_name):
    data = {
        'chat_id': update.effective_chat.id,
        'mkv_id': context.user_data['mkv_id'],
        'sub_id': context.user_data['sub_id'],
        'name': final_name,
        'thumb': context.user_data.get('thumb_id')
    }
    status = await update.message.reply_text("⏳ Added to Queue...")
    asyncio.create_task(run_queue(context, data, status))

async def run_queue(context, data, status):
    async with global_task_lock:
        await status.edit_text("⚙️ Processing your file...")
        uid = f"{data['chat_id']}_{int(time.time())}"
        tmp = os.path.abspath(f"task_{uid}")
        os.makedirs(tmp, exist_ok=True)
        out = os.path.join(tmp, data['name'])
        
        try:
            m_f = await context.bot.get_file(data['mkv_id'], read_timeout=3600)
            s_f = await context.bot.get_file(data['sub_id'], read_timeout=3600)
            
            t_path = None
            if data['thumb']:
                t_raw = os.path.join(tmp, "t.jpg")
                t_path = os.path.join(tmp, "thumb.jpg")
                tf = await context.bot.get_file(data['thumb'])
                await tf.download_to_drive(t_raw)
                await create_square_thumb(t_raw, t_path)

            success = await mux_video(m_f.file_path, s_f.file_path, out, data['chat_id'], status)
            if success:
                await status.edit_text("📤 Uploading...")
                with open(t_path, 'rb') as th if t_path else None as th:
                    await context.bot.send_document(chat_id=data['chat_id'], document=f"file://{out}", thumbnail=th, read_timeout=3600, write_timeout=3600)
                await status.delete()
            else: await status.edit_text("❌ Muxing Failed.")
        except Exception as e: await status.edit_text(f"Error: {e}")
        finally: clean_temp_files(tmp)

async def cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if cid in active_processes:
        active_processes[cid].terminate()
        await update.callback_query.edit_message_text("🛑 Process Stopped.")
    else: await update.callback_query.answer("No active process.")

# ================================
# MAIN
# ================================
def main():
    token = os.getenv("BOT_TOKEN")
    threading.Thread(target=run_dummy_server, args=(int(os.environ.get("PORT", 10000)),), daemon=True).start()

    app = ApplicationBuilder().token(token).base_url("http://127.0.0.1:8081/bot").base_file_url("http://127.0.0.1:8081/file/bot").local_mode(True).build()

    app.add_handler(TypeHandler(Update, block_duplicates), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(CommandHandler("thumbnail", cmd_thumbnail))
    app.add_handler(CommandHandler("extract", cmd_extract))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern=r"^cancel_"))

    print("Bot Started - Clean Mode")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
