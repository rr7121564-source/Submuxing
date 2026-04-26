import os, time, asyncio, threading, io, json
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, TypeHandler, ApplicationHandlerStop
)

from config import BOT_TOKEN, OWNER_ID, PORT, SESSION_ID, global_task_lock, active_processes, EXTRACT_DATA, LANG_MAP
from database import init_db, is_user_auth, is_chat_auth, add_processed_id
from utils import mux_video, clean_temp_files, get_readable_time, extract_thumbnail

# --- HELPERS ---
def humanbytes(size):
    if not size: return "0 B"
    for unit in['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024

class ProgressFile(io.BufferedReader):
    def __init__(self, filename, status_msg, start_time):
        self._file = open(filename, 'rb')
        super().__init__(self._file)
        self._total_size = os.path.getsize(filename)
        self._status_msg = status_msg
        self._start_time = start_time
        self._last_update = 0
        self._current_size = 0

    def read(self, size=-1):
        chunk = self._file.read(size)
        self._current_size += len(chunk)
        asyncio.create_task(self._update_progress())
        return chunk

    async def _update_progress(self):
        now = time.time()
        if (now - self._last_update) > 8 or self._current_size == self._total_size:
            self._last_update = now
            perc = (self._current_size / self._total_size) * 100
            elapsed = now - self._start_time
            speed = self._current_size / elapsed if elapsed > 0 else 0
            eta = (self._total_size - self._current_size) / speed if speed > 0 else 0
            bar = "■" * int(perc / 10) + "□" * (10 - int(perc / 10))
            
            text = (f"📤 **Uploading to Telegram...**\n\n"
                    f"P: `[{bar}]` {perc:.2f}%\n"
                    f"📂 Size: {humanbytes(self._current_size)} / {humanbytes(self._total_size)}\n"
                    f"🚀 Speed: {humanbytes(speed)}/s\n"
                    f"⏳ ETA: {get_readable_time(eta)}")
            try: await self._status_msg.edit_text(text)
            except: pass

async def delete_messages(bot, chat_id, message_ids):
    for msg_id in message_ids:
        if msg_id:
            try: await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except: pass

# --- EXTRACTION HELPERS & HANDLERS ---
def get_lang_name(code):
    return LANG_MAP.get(code.lower(), code.title())

async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        return await msg.reply_text("⚠️ Reply to an MKV file with `/extract`.")
    
    user_id = msg.from_user.id
    target = msg.reply_to_message.video or msg.reply_to_message.document
    if not target.file_name.lower().endswith('.mkv'): 
        return await msg.reply_text("⚠️ Only MKV supported.")
    
    bot_msg = await msg.reply_text("📥 **Scanning Subtitles...**")
    
    mkv_f = await context.bot.get_file(target.file_id, read_timeout=3600)
    
    cmd =['ffprobe', '-v', 'error', '-select_streams', 's', '-show_entries', 'stream=index,codec_name:stream_tags=language,NUMBER_OF_BYTES', '-of', 'json', mkv_f.file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    
    streams = json.loads(stdout.decode()).get('streams', []) if stdout else[]
    if not streams: 
        return await bot_msg.edit_text("❌ No subtitles found.")
    
    base_name = os.path.splitext(target.file_name)[0]
    
    # Single Sub Extract
    if len(streams) == 1:
        await bot_msg.edit_text("⚙️ **Extracting Single Subtitle...**")
        idx, codec = streams[0]['index'], streams[0].get('codec_name', 'subrip')
        ext = ".ass" if codec == "ass" else ".srt"
        out = os.path.abspath(f"{base_name}{ext}")
        
        await asyncio.create_subprocess_exec('ffmpeg', '-y', '-i', mkv_f.file_path, '-map', f"0:{idx}", '-c:s', 'copy', out).wait()
        
        with open(out, 'rb') as f:
            await context.bot.send_document(msg.chat_id, document=f, caption="✅ **Extracted Successfully!**")
            
        await bot_msg.delete()
        if os.path.exists(out): os.remove(out)
        return

    # Multi Sub List
    EXTRACT_DATA[user_id] = {'path': mkv_f.file_path, 'name': base_name, 'streams': {}}
    btns =[]
    for s in streams:
        idx, codec = s['index'], s.get('codec_name', 'subrip')
        tags = s.get('tags', {})
        lang = get_lang_name(tags.get('language', 'und'))
        size = tags.get('NUMBER_OF_BYTES')
        text = f"{lang}"
        if size:
            kb = int(size)/1024
            text += f" ({kb/1024:.2f} MB)" if kb > 1024 else f" ({kb:.0f} KB)"
        
        EXTRACT_DATA[user_id]['streams'][str(idx)] = ".ass" if codec == "ass" else ".srt"
        btns.append([InlineKeyboardButton(text, callback_data=f"ext_{user_id}_{idx}")])
    
    await bot_msg.edit_text("📂 **Select Language to Extract:**", reply_markup=InlineKeyboardMarkup(btns))

async def do_extract_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, uid, idx = query.data.split("_")
    if query.from_user.id != int(uid): 
        return await query.answer("Access Denied!", show_alert=True)
    
    data = EXTRACT_DATA.get(int(uid))
    if not data: 
        return await query.message.edit_text("❌ Session Expired.")
    
    await query.message.edit_text("⚙️ **Extracting...**")
    ext = data['streams'].get(idx, ".srt")
    out = os.path.abspath(f"{data['name']}_{idx}{ext}")
    
    await asyncio.create_subprocess_exec('ffmpeg', '-y', '-i', data['path'], '-map', f"0:{idx}", '-c:s', 'copy', out).wait()
    
    with open(out, 'rb') as f:
        await context.bot.send_document(query.message.chat_id, document=f, caption="✅ **Extracted!**")
        
    await query.message.delete()
    if os.path.exists(out): os.remove(out)


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
    await update.message.reply_text("🤖 **Muxing Bot Active!**\n\n1️⃣ Send MKV.\n2️⃣ Send Subtitle.\n3️⃣ Send Name (or /skip).\n4️⃣ Reply MKV with `/extract` to extract subs.")

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc: return
    ext = os.path.splitext(doc.file_name)[1].lower()
    
    if ext == '.mkv':
        context.user_data.update({
            'mkv_id': doc.file_id, 'orig_name': doc.file_name, 
            'state': 'WAIT_SUB', 'mkv_msg_id': update.message.message_id
        })
        await update.message.reply_text("✅ MKV Received! Now send **Subtitle (.srt/.ass)**.")
    
    elif ext in['.srt', '.ass'] and context.user_data.get('state') == 'WAIT_SUB':
        context.user_data.update({
            'sub_id': doc.file_id, 'state': 'WAIT_NAME', 'sub_msg_id': update.message.message_id
        })
        await update.message.reply_text("✅ Subtitle Received! Send **New Name** or /skip.")

async def cmd_skip(update, context):
    if context.user_data.get('state') == 'WAIT_NAME':
        context.user_data['name_msg_id'] = update.message.message_id
        await start_task(update, context, context.user_data['orig_name'])

async def handle_text(update, context):
    if context.user_data.get('state') == 'WAIT_NAME':
        name = update.message.text.strip()
        if not name.lower().endswith('.mkv'): name += '.mkv'
        context.user_data['name_msg_id'] = update.message.message_id
        await start_task(update, context, name)

async def start_task(update, context, final_name):
    msg_list =[
        context.user_data.get('mkv_msg_id'),
        context.user_data.get('sub_msg_id'),
        context.user_data.get('name_msg_id')
    ]
    data = {
        'chat_id': update.effective_chat.id, 
        'mkv_id': context.user_data['mkv_id'], 
        'sub_id': context.user_data['sub_id'], 
        'name': final_name,
        'to_delete': msg_list
    }
    context.user_data.clear()
    status = await update.message.reply_text("⏳ **Added to Queue...**")
    asyncio.create_task(run_queue(context, data, status))

async def run_queue(context, data, status):
    async with global_task_lock:
        await status.edit_text("⚙️ **Initializing Task...**")
        tmp = os.path.abspath(f"task_{data['chat_id']}_{int(time.time())}")
        os.makedirs(tmp, exist_ok=True)
        out = os.path.join(tmp, data['name'])
        thumb_path = os.path.join(tmp, "thumb.jpg")
        
        try:
            m_f = await context.bot.get_file(data['mkv_id'], read_timeout=3600)
            s_f = await context.bot.get_file(data['sub_id'], read_timeout=3600)
            
            # 1. Muxing
            success = await mux_video(m_f.file_path, s_f.file_path, out, data['chat_id'], status)
            
            if success:
                # 2. Extract Thumbnail (Preview)
                await status.edit_text("🖼️ **Generating Preview...**")
                has_thumb = await extract_thumbnail(out, thumb_path)
                
                # 3. Uploading
                start_up = time.time()
                await status.edit_text("📤 **Uploading Document...**")
                
                with ProgressFile(out, status, start_up) as pf:
                    # Thumbnail file open karein agar extract hui hai
                    thumb_file = open(thumb_path, 'rb') if has_thumb else None
                    try:
                        await context.bot.send_document(
                            chat_id=data['chat_id'], 
                            document=pf, 
                            thumbnail=thumb_file, # Ye document icon ki jagah show hoga
                            caption="Muxing complete",
                            filename=data['name'],
                            read_timeout=3600,
                            write_timeout=3600
                        )
                    finally:
                        if thumb_file: thumb_file.close()
                
                # 4. Cleanup
                await delete_messages(context.bot, data['chat_id'], data['to_delete'])
                await status.delete()
                
            else:
                await status.edit_text("❌ **Muxing Failed.**")
        except Exception as e:
            try: await status.edit_text(f"❌ **Error:** {e}")
            except: pass
        finally:
            clean_temp_files(tmp)

async def cancel_cb(update, context):
    cid = update.effective_chat.id
    if cid in active_processes:
        active_processes[cid].terminate()
        await update.callback_query.edit_message_text("🛑 **Stopped.**")

# --- MAIN ---
def main():
    init_db()
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), BaseHTTPRequestHandler).serve_forever(), daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).base_url("http://127.0.0.1:8081/bot").local_mode(True).build()
    
    app.add_handler(TypeHandler(Update, check_access), group=-2)
    app.add_handler(TypeHandler(Update, block_duplicates), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(CommandHandler("extract", cmd_extract))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, handle_docs))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern=r"^cancel_"))
    app.add_handler(CallbackQueryHandler(do_extract_cb, pattern=r"^ext_"))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
