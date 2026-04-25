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

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
active_processes = {}

# ================================
# 🛡️ ULTIMATE ANTI-DUPLICATE MIDDLEWARE 🛡️
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
    if not update: 
        return
        
    keys =[]
    if update.update_id:
        keys.append(f"uid_{update.update_id}")
    if update.message:
        keys.append(f"msg_{update.message.chat_id}_{update.message.message_id}")
        
    if not keys: return

    conn = sqlite3.connect(DB_PATH, timeout=5)
    c = conn.cursor()
    try:
        for key in keys:
            try:
                c.execute("INSERT INTO processed (id) VALUES (?)", (key,))
            except sqlite3.IntegrityError:
                raise ApplicationHandlerStop()
        conn.commit()
    except ApplicationHandlerStop:
        raise 
    except Exception as e:
        logging.error(f"DB Middleware Error: {e}")
    finally:
        conn.close()

# Global Lock for Queue System
global_task_lock = asyncio.Lock()

# ================================
# DUMMY SERVER FOR RENDER
# ================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running Perfectly!")
    def log_message(self, format, *args):
        pass

def run_dummy_server(port):
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# ================================
# UTILITY FUNCTIONS
# ================================
def clean_temp_files(*filepaths):
    for path in filepaths:
        if not path: continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

async def get_duration(file_path):
    cmd =['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    try: return float(stdout.decode().strip())
    except ValueError: return 0.0

async def extract_subtitles(mkv_path, original_name):
    cmd =['ffprobe', '-v', 'error', '-select_streams', 's', '-show_entries', 'stream=index,codec_name', '-of', 'json', mkv_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    try: data = json.loads(stdout.decode())
    except json.JSONDecodeError: return []

    extracted_files =[]
    base_name = os.path.splitext(original_name)[0]

    for stream in data.get('streams',[]):
        index = stream['index']
        codec = stream.get('codec_name', 'subrip')
        if codec == "ass": ext = ".ass"
        elif codec == "subrip": ext = ".srt"
        else: ext = ".vtt"
            
        outfile = os.path.abspath(f"{base_name}_{index}{ext}")
        ext_cmd =['ffmpeg', '-y', '-i', mkv_path, '-map', f"0:{index}", '-c:s', 'copy', outfile]
        ext_proc = await asyncio.create_subprocess_exec(*ext_cmd, stderr=asyncio.subprocess.DEVNULL)
        await ext_proc.wait()

        if ext_proc.returncode == 0 and os.path.exists(outfile):
            extracted_files.append(outfile)
    return extracted_files

async def create_square_thumbnail(input_path, output_path):
    cmd =[
        'ffmpeg', '-y', '-i', input_path,
        '-vf', "crop='min(iw,ih)':'min(iw,ih)',scale=320:320",
        output_path
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()

async def mux_video(mkv_path, sub_path, output_path, chat_id, status_msg):
    duration = await get_duration(mkv_path)
    os.makedirs("fonts", exist_ok=True)
    attach_args =[]
    attach_index = 0

    for font_file in os.listdir("fonts"):
        font_path = os.path.join("fonts", font_file)
        ext = os.path.splitext(font_file)[1].lower()
        mimetype = ""
        if ext in ['.ttf', '.ttc']: mimetype = "application/x-truetype-font"
        elif ext == '.otf': mimetype = "application/vnd.ms-opentype"
            
        if mimetype:
            attach_args.extend(["-attach", font_path, f"-metadata:s:t:{attach_index}", f"mimetype={mimetype}"])
            attach_index += 1

    cmd =[
        'ffmpeg', '-y', '-i', mkv_path, '-i', sub_path, 
        '-map', '0:v', '-map', '0:a?', '-map', '1', 
        '-c', 'copy',
        '-disposition:s:0', 'default',
        '-metadata:s:s:0', 'language=eng',
        '-metadata:s:s:0', 'title=Hinglish'
    ] + attach_args + ['-progress', 'pipe:1', output_path]
    
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    active_processes[chat_id] = proc

    start_time = time.time()
    last_update_time = start_time
    speed = "N/A"

    while True:
        line = await proc.stdout.readline()
        if not line: break
        line = line.decode('utf-8').strip()

        if line.startswith('speed='): speed = line.split('=')[1]
        if line.startswith('out_time_us='):
            out_time_us = line.split('=')[1]
            if out_time_us.isdigit() and duration > 0:
                percentage = min(100, (int(out_time_us) / 1000000 / duration) * 100)
                now = time.time()
                
                # FIX: Update UI only every 15 seconds to prevent Flood Error
                if now - last_update_time > 15:
                    last_update_time = now
                    elapsed = now - start_time
                    eta_str = time.strftime('%H:%M:%S', time.gmtime((elapsed / percentage) * (100 - percentage))) if percentage > 0 else "..."
                    text = f"⚙️ Muxing Progress\n\nProgress: {percentage:.2f}%\nSpeed: {speed}\nETA: {eta_str}"
                    cancel_kbd = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{chat_id}")]])
                    try: 
                        await status_msg.edit_text(text, reply_markup=cancel_kbd)
                    except RetryAfter as e:
                        # FIX: Handle Telegram Rate Limits gracefully
                        await asyncio.sleep(e.retry_after + 1)
                    except Exception: 
                        pass

    await proc.wait()
    if chat_id in active_processes: del active_processes[chat_id]
    return proc.returncode == 0

# ================================
# COMMANDS & HANDLERS
# ================================
async def cmd_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thumb_id = context.user_data.get('thumb_id')
    if thumb_id:
        await update.message.reply_photo(photo=thumb_id, caption="🖼️ This is your **Current Thumbnail**.\nSend a new image (photo) to replace it, or type /skip to cancel.")
    else:
        await update.message.reply_text("🖼️ You don't have a thumbnail set yet.\nSend an image (photo) now, or type /skip to cancel.")
    context.user_data['state'] = 'WAITING_FOR_THUMBNAIL'

async def cmd_delthumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'thumb_id' in context.user_data:
        del context.user_data['thumb_id']
        await update.message.reply_text("🗑️ Thumbnail removed successfully!")
    else:
        await update.message.reply_text("⚠️ You don't have any thumbnail set.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') == 'WAITING_FOR_THUMBNAIL':
        context.user_data['state'] = None 
        photo = update.message.photo[-1]
        context.user_data['thumb_id'] = photo.file_id
        await update.message.reply_text("✅ Thumbnail Saved Successfully!\nIt will be auto-cropped to 1:1. Send /delthumb to remove it.")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 Hello! I am your Queue Based Muxing Bot.\n\n"
        "1️⃣ Send an MKV file.\n"
        "2️⃣ Reply with /sub to add subtitle.\n"
        "3️⃣ /thumbnail - Set a Cover Picture.\n"
        "4️⃣ Add multiple tasks, I will process them one by one!\n\n"
        "📌 Send me an MKV to begin."
    )
    await update.message.reply_text(msg)

async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    if state == 'WAITING_FOR_RENAME':
        context.user_data['state'] = None
        original_name = context.user_data.get('original_mkv_name')
        await queue_task_start(update, context, original_name)
    elif state == 'WAITING_FOR_THUMBNAIL':
        context.user_data['state'] = None
        await update.message.reply_text("❌ Thumbnail update skipped.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') == 'WAITING_FOR_RENAME':
        context.user_data['state'] = None 
        new_name = update.message.text.strip()
        if not new_name.lower().endswith('.mkv'):
            new_name += '.mkv'
        await queue_task_start(update, context, new_name)

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc: return
    ext = os.path.splitext(doc.file_name)[1].lower()

    if ext == '.mkv':
        context.user_data['original_mkv_name'] = doc.file_name
        msg = "🎥 MKV received!\n\n• To mux a subtitle: Reply to this message with /sub"
        await update.message.reply_text(msg)
    elif ext in ['.srt', '.ass']:
        if context.user_data.get('state') == 'WAITING_FOR_SUB':
            context.user_data['sub_file_id'] = doc.file_id
            context.user_data['sub_file_name'] = doc.file_name
            context.user_data['state'] = 'WAITING_FOR_RENAME'
            
            await update.message.reply_text(
                "✅ Subtitle received!\n\n"
                "✏️ **Now, send a New Name for the final MKV file.** (e.g. `MyMovie_2026.mkv`)\n\n"
                "Or simply send /skip to keep the original name."
            )

async def cmd_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.document:
        return await msg.reply_text("Please reply to an MKV file message with /sub.")
    doc = msg.reply_to_message.document
    if not doc.file_name.lower().endswith('.mkv'):
        return await msg.reply_text("The replied message is not an MKV file.")

    context.user_data['mkv_file_id'] = doc.file_id
    context.user_data['original_mkv_name'] = doc.file_name
    context.user_data['state'] = 'WAITING_FOR_SUB'
    await msg.reply_text("✅ MKV selected!\nNow upload the subtitle file (.srt or .ass).")

# ================================
# QUEUE WORKER SYSTEM
# ================================
async def queue_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE, final_name: str):
    task_data = {
        'chat_id': update.effective_chat.id,
        'mkv_id': context.user_data['mkv_file_id'],
        'sub_id': context.user_data['sub_file_id'],
        'final_name': final_name,
        'thumb_id': context.user_data.get('thumb_id')
    }
    
    status_msg = await update.message.reply_text("⏳ **Added to Queue!** Please wait for your turn...")
    asyncio.create_task(run_queued_process(context, task_data, status_msg))

async def run_queued_process(context, task_data, status_msg):
    async with global_task_lock:
        if context.user_data.get('cancelled'):
            await status_msg.edit_text("❌ Task was cancelled.")
            context.user_data['cancelled'] = False
            return
            
        await status_msg.edit_text("⚙️ Your turn! Acquiring files...")
        await process_muxing_core(context, task_data, status_msg)

async def process_muxing_core(context, task_data, status_msg):
    chat_id = task_data['chat_id']
    ts = int(time.time())
    
    task_dir = os.path.abspath(f"task_{chat_id}_{ts}")
    os.makedirs(task_dir, exist_ok=True)
    
    output_mkv = os.path.join(task_dir, task_data['final_name'])
    
    thumb_path = None
    square_thumb_path = None
    thumb_file_obj = None

    try:
        mkv_file = await context.bot.get_file(task_data['mkv_id'], read_timeout=3600)
        mkv_path = mkv_file.file_path 
        
        sub_file = await context.bot.get_file(task_data['sub_id'], read_timeout=3600)
        sub_path = sub_file.file_path 
        
        if task_data['thumb_id']:
            thumb_path = os.path.join(task_dir, "original_cover.jpg")
            square_thumb_path = os.path.join(task_dir, "square_cover.jpg")
            
            thumb_file = await context.bot.get_file(task_data['thumb_id'], read_timeout=100)
            await thumb_file.download_to_drive(thumb_path)
            await create_square_thumbnail(thumb_path, square_thumb_path)

        await status_msg.edit_text("⚙️ Starting Fast Muxing...\n(Subtitle: Hinglish | Autoplay: ON)")
        success = await mux_video(mkv_path, sub_path, output_mkv, chat_id, status_msg)

        if success:
            file_uri = f"file://{output_mkv}"
            start_upload = time.time()
            
            kwargs = {
                'chat_id': chat_id,
                'document': file_uri,
                'read_timeout': 3600,
                'write_timeout': 3600
            }
            
            if square_thumb_path and os.path.exists(square_thumb_path):
                thumb_file_obj = open(square_thumb_path, 'rb')
                kwargs['thumbnail'] = thumb_file_obj
            
            upload_task = asyncio.create_task(context.bot.send_document(**kwargs))

            while not upload_task.done():
                elapsed = int(time.time() - start_upload)
                try:
                    await status_msg.edit_text(f"📤 Uploading: **{task_data['final_name']}**\n\n⏱ Elapsed Time: {elapsed} Seconds")
                except RetryAfter as e:
                    # FIX: Handle Telegram Rate Limits gracefully during upload
                    await asyncio.sleep(e.retry_after + 1)
                except Exception:
                    pass
                
                # FIX: Check upload progress only every 15 seconds
                await asyncio.sleep(15) 
            
            await upload_task 
            await status_msg.delete()
        else:
            if context.user_data.get('cancelled'): await status_msg.edit_text("❌ Process cancelled.")
            else: await status_msg.edit_text("⚠️ An error occurred during muxing.")
            
    except Exception as e:
        await status_msg.edit_text(f"Error: {str(e)}")
    finally:
        if thumb_file_obj:
            thumb_file_obj.close()
        clean_temp_files(task_dir)

async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.document:
        return await msg.reply_text("Please reply to an MKV file message with /extract.")
    doc = msg.reply_to_message.document
    if not doc.file_name.lower().endswith('.mkv'):
        return await msg.reply_text("The replied message is not an MKV file.")

    asyncio.create_task(process_extraction(update, context, doc))

async def process_extraction(update: Update, context: ContextTypes.DEFAULT_TYPE, doc):
    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(chat_id=chat_id, text="📥 Acquiring MKV for extraction...")
    extracted_files =[]

    try:
        mkv_file = await context.bot.get_file(doc.file_id, read_timeout=3600)
        mkv_path = mkv_file.file_path 
        
        await status_msg.edit_text("⚙️ Extracting subtitles...")
        extracted_files = await extract_subtitles(mkv_path, doc.file_name)

        if not extracted_files:
            return await status_msg.edit_text("❌ No subtitle streams found in this MKV.")

        await status_msg.edit_text(f"📤 Found {len(extracted_files)} subtitles. Uploading (High Speed)...")
        for sub_file in extracted_files:
            file_uri = f"file://{sub_file}"
            await context.bot.send_document(chat_id=chat_id, document=file_uri, read_timeout=3600, write_timeout=3600)
            
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"Error: {str(e)}")
    finally:
        clean_temp_files(*extracted_files)

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    if chat_id in active_processes:
        active_processes[chat_id].terminate()
        context.user_data['cancelled'] = True
        await query.edit_message_text("🛑 Cancelling active process...")
    else:
        context.user_data['cancelled'] = True
        await query.edit_message_text("🗑️ Cancelled waiting tasks.")

# ================================
# MAIN ENTRY POINT
# ================================
def main():
    token = os.getenv("BOT_TOKEN")
    if not token: return

    os.makedirs("fonts", exist_ok=True)
    threading.Thread(target=run_dummy_server, args=(int(os.environ.get("PORT", 10000)),), daemon=True).start()

    app = (
        ApplicationBuilder()
        .token(token)
        .base_url("http://127.0.0.1:8081/bot")
        .base_file_url("http://127.0.0.1:8081/file/bot")
        .local_mode(True)
        .connect_timeout(100).read_timeout(3600).write_timeout(3600).pool_timeout(100)
        .build()
    )

    app.add_handler(TypeHandler(Update, block_duplicates), group=-1)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(CommandHandler("thumbnail", cmd_thumbnail))
    app.add_handler(CommandHandler("delthumb", cmd_delthumb))
    app.add_handler(CommandHandler("sub", cmd_sub))
    app.add_handler(CommandHandler("extract", cmd_extract))
    
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    
    app.add_handler(CallbackQueryHandler(cancel_callback, pattern=r"^cancel_"))

    print("Bot is up! Strict DB Middleware Queue System Active...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
