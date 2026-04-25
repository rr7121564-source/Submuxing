import os
import json
import time
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
active_processes = {}

# ================================
# DUMMY SERVER FOR RENDER (Health Check)
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
        if path and os.path.exists(path):
            try:
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
    except json.JSONDecodeError: return[]

    extracted_files =[]
    base_name = os.path.splitext(original_name)[0]

    for stream in data.get('streams', []):
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

async def mux_video(mkv_path, sub_path, output_path, chat_id, status_msg):
    duration = await get_duration(mkv_path)
    os.makedirs("fonts", exist_ok=True)
    font_args =[]
    font_index = 0

    for font_file in os.listdir("fonts"):
        font_path = os.path.join("fonts", font_file)
        ext = os.path.splitext(font_file)[1].lower()
        mimetype = ""
        if ext in['.ttf', '.ttc']: mimetype = "application/x-truetype-font"
        elif ext == '.otf': mimetype = "application/vnd.ms-opentype"
            
        if mimetype:
            font_args.extend(["-attach", font_path, f"-metadata:s:t:{font_index}", f"mimetype={mimetype}"])
            font_index += 1

    cmd =['ffmpeg', '-y', '-i', mkv_path, '-i', sub_path, '-map', '0:v', '-map', '0:a?', '-map', '1', '-c', 'copy'] + font_args +['-progress', 'pipe:1', output_path]
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
                
                if now - last_update_time > 3:
                    last_update_time = now
                    elapsed = now - start_time
                    eta_str = time.strftime('%H:%M:%S', time.gmtime((elapsed / percentage) * (100 - percentage))) if percentage > 0 else "..."
                    text = f"⚙️ Muxing Progress\n\nProgress: {percentage:.2f}%\nSpeed: {speed}\nETA: {eta_str}"
                    cancel_kbd = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{chat_id}")]])
                    try: await status_msg.edit_text(text, reply_markup=cancel_kbd)
                    except Exception: pass

    await proc.wait()
    if chat_id in active_processes: del active_processes[chat_id]
    return proc.returncode == 0


# ================================
# TELEGRAM HANDLERS
# ================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 Hello! I am your Fast MKV Muxing & Extraction Bot.\n\n"
        "Here is what I can do:\n"
        "1️⃣ Send me any MKV movie/episode.\n"
        "2️⃣ Reply with /sub to add your own subtitles (.srt/.ass).\n"
        "3️⃣ Reply with /extract to pull all subtitles out of the MKV file.\n\n"
        "📌 Note: I support files up to 2GB and process them at max speed!"
    )
    await update.message.reply_text(msg)

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc: return
    ext = os.path.splitext(doc.file_name)[1].lower()

    if ext == '.mkv':
        msg = "🎥 MKV received!\n\n• To mux a subtitle: Reply to this message with /sub\n• To extract subtitles: Reply to this message with /extract"
        await update.message.reply_text(msg)
    elif ext in ['.srt', '.ass']:
        if context.user_data.get('state') == 'WAITING_FOR_SUB':
            context.user_data['state'] = None
            asyncio.create_task(process_muxing(update, context))

async def cmd_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.document:
        return await msg.reply_text("Please reply to an MKV file message with /sub.")
    doc = msg.reply_to_message.document
    if not doc.file_name.lower().endswith('.mkv'):
        return await msg.reply_text("The replied message is not an MKV file.")

    context.user_data['mkv_file_id'] = doc.file_id
    context.user_data['mkv_file_name'] = doc.file_name
    context.user_data['state'] = 'WAITING_FOR_SUB'
    await msg.reply_text("✅ MKV selected!\nNow upload the subtitle file (.srt or .ass).")

async def process_muxing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sub_doc = update.message.document
    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(chat_id=chat_id, text="📥 Acquiring files... (Super Fast Mode)")
    ts = int(time.time())

    output_mkv = os.path.abspath(f"muxed_{chat_id}_{ts}.mkv")

    try:
        # MAGIC TRICK 1: No python downloading! Get direct local path from C++ Server
        mkv_file = await context.bot.get_file(context.user_data.get('mkv_file_id'), read_timeout=3600)
        mkv_path = mkv_file.file_path 
        
        sub_file = await context.bot.get_file(sub_doc.file_id, read_timeout=3600)
        sub_path = sub_file.file_path 
        
        await status_msg.edit_text("⚙️ Starting mux process...\n(Old subtitles will be removed)")
        success = await mux_video(mkv_path, sub_path, output_mkv, chat_id, status_msg)

        if success:
            await status_msg.edit_text("📤 Uploading MKV to Telegram... (High Speed)")
            
            # MAGIC TRICK 2: File Teleportation. Python sends path, C++ Server uploads directly.
            file_uri = f"file://{output_mkv}"
            start_upload = time.time()
            
            upload_task = asyncio.create_task(
                context.bot.send_document(
                    chat_id=chat_id, 
                    document=file_uri, 
                    read_timeout=3600, 
                    write_timeout=3600
                )
            )

            # Live timer
            while not upload_task.done():
                elapsed = int(time.time() - start_upload)
                try:
                    await status_msg.edit_text(f"📤 Uploading MKV to Telegram...\n\n⏱ Elapsed Time: {elapsed} Seconds\n(Pushing file at Max Speed...)")
                except Exception:
                    pass
                await asyncio.sleep(5)
            
            await upload_task 
            await status_msg.delete()
        else:
            if context.user_data.get('cancelled'): await status_msg.edit_text("❌ Process cancelled by user.")
            else: await status_msg.edit_text("⚠️ An error occurred during muxing.")
    except Exception as e:
        await status_msg.edit_text(f"Error: {str(e)}")
    finally:
        # We only delete OUR output file. We don't touch mkv_path because it's managed by C++ server cache
        clean_temp_files(output_mkv)
        context.user_data['state'] = None


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
    ts = int(time.time())
    extracted_files =[]

    try:
        # MAGIC TRICK: No Python Download
        mkv_file = await context.bot.get_file(doc.file_id, read_timeout=3600)
        mkv_path = mkv_file.file_path 
        
        await status_msg.edit_text("⚙️ Extracting subtitles...")
        
        extracted_files = await extract_subtitles(mkv_path, doc.file_name)

        if not extracted_files:
            return await status_msg.edit_text("❌ No subtitle streams found in this MKV.")

        await status_msg.edit_text(f"📤 Found {len(extracted_files)} subtitles. Uploading (High Speed)...")
        for sub_file in extracted_files:
            # MAGIC TRICK: Direct File Path Upload
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
        await query.edit_message_text("🛑 Cancelling process...")
    else:
        await query.answer("No active process to cancel.", show_alert=True)


# ================================
# MAIN ENTRY POINT
# ================================
def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("Error: BOT_TOKEN is missing!")
        return

    os.makedirs("fonts", exist_ok=True)
    
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=run_dummy_server, args=(port,), daemon=True).start()
    print(f"Dummy Web Server running on port {port}...")

    app = (
        ApplicationBuilder()
        .token(token)
        .base_url("http://127.0.0.1:8081/bot")
        .base_file_url("http://127.0.0.1:8081/file/bot")
        .local_mode(True)
        .connect_timeout(100)
        .read_timeout(3600)
        .write_timeout(3600)
        .pool_timeout(100)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("sub", cmd_sub))
    app.add_handler(CommandHandler("extract", cmd_extract))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(cancel_callback, pattern=r"^cancel_"))

    print("Bot is up and polling perfectly...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
