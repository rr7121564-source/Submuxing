import os, time, asyncio, threading, io, json
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, TypeHandler, ApplicationHandlerStop
)

from config import BOT_TOKEN, OWNER_ID, PORT, SESSION_ID, global_task_lock, active_processes, EXTRACT_DATA, LANG_MAP
from database import init_db, is_user_auth, is_chat_auth, add_processed_id
from utils import mux_video, clean_temp_files, get_readable_time, extract_thumbnail, get_subtitles_info, extract_sub_logic

# --- HELPERS ---
def humanbytes(size):
    if not size: return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024

class ProgressFile(io.BufferedReader):
    def __init__(self, filename, status_msg, start_time, action="Uploading"):
        self._file = open(filename, 'rb')
        super().__init__(self._file)
        self._total_size = os.path.getsize(filename)
        self._status_msg = status_msg
        self._start_time = start_time
        self._last_update = 0
        self._current_size = 0
        self._action = action

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
            text = (f"📤 **{self._action}...**\n\n"
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

# --- EXTRACTION HANDLERS ---
async def extract_cmd(update, context):
    msg = update.message
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        return await msg.reply_text("⚠️ Reply to an MKV file with `/extract`.")
    
    target = msg.reply_to_message.video or msg.reply_to_message.document
    if not target.file_name.lower().endswith('.mkv'): return await msg.reply_text("⚠️ Only MKV supported.")
    
    status = await msg.reply_text("📥 **Scanning Subtitles...**")
    mkv_f = await context.bot.get_file(target.file_id)
    streams = await get_subtitles_info(mkv_f.file_path)
    
    if not streams: return await status.edit_text("❌ No subtitles found.")
    
    base_name = os.path.splitext(target.file_name)[0]
    user_id = update.effective_user.id

    # --- CASE 1: Single Subtitle (Auto Extract) ---
    if len(streams) == 1:
        await status.edit_text("⚙️ **Extracting Single Subtitle...**")
        idx, codec = streams[0]['index'], streams[0].get('codec_name', 'subrip')
        ext = ".ass" if codec == "ass" else ".srt"
        out = os.path.join(os.path.dirname(mkv_f.file_path), f"{base_name}{ext}")
        if await extract_sub_logic(mkv_f.file_path, idx, out):
            start_up = time.time()
            with ProgressFile(out, status, start_up, "Uploading Subtitle") as pf:
                await context.bot.send_document(chat_id=msg.chat_id, document=pf, filename=f"{base_name}{ext}", caption="✅ **Extracted Successfully!**")
            await status.delete()
        else: await status.edit_text("❌ Extraction Failed.")
        if os.path.exists(out): os.remove(out)
        return

    # --- CASE 2: Multiple Subtitles (Menu) ---
    EXTRACT_DATA[user_id] = {'path': mkv_f.file_path, 'name': base_name, 'streams': streams}
    btns = []
    for s in streams:
        idx, codec = s['index'], s.get('codec_name', 'subrip')
        tags = s.get('tags', {})
        lang = LANG_MAP.get(tags.get('language', 'und').lower(), tags.get('language', 'und').title())
        size = tags.get('NUMBER_OF_BYTES')
        text = f"{lang}"
        if size: text += f" ({humanbytes(int(size))})"
        btns.append([InlineKeyboardButton(f"{text} [{codec.upper()}]", callback_data=f"ext_{user_id}_{idx}")])
    
    # Add Extract All Button
    btns.append([InlineKeyboardButton("🔥 Extract All Subtitles 🔥", callback_data=f"extall_{user_id}")])
    
    await status.edit_text("📂 **Multiple Subtitles Found!**\nSelect one or extract all:", reply_markup=InlineKeyboardMarkup(btns))

async def do_extract_cb(update, context):
    query = update.callback_query
    data_parts = query.data.split("_")
    action = data_parts[0]
    uid = int(data_parts[1])

    if query.from_user.id != uid: return await query.answer("Access Denied!", show_alert=True)
    data = EXTRACT_DATA.get(uid)
    if not data: return await query.message.edit_text("❌ Session Expired.")

    # Single track selection from menu
    if action == "ext":
        idx = data_parts[2]
        await query.message.edit_text(f"⚙️ **Extracting Track {idx}...**")
        # Logic to find codec for extension
        codec = next((s.get('codec_name', 'subrip') for s in data['streams'] if str(s['index']) == idx), 'subrip')
        ext = ".ass" if codec == "ass" else ".srt"
        out = os.path.join(os.path.dirname(data['path']), f"{data['name']}_{idx}{ext}")
        
        if await extract_sub_logic(data['path'], idx, out):
            start_up = time.time()
            with ProgressFile(out, query.message, start_up, "Uploading Subtitle") as pf:
                await context.bot.send_document(chat_id=query.message.chat_id, document=pf, filename=f"{data['name']}_{idx}{ext}", caption=f"✅ **Track {idx} Extracted!**")
            await query.message.delete()
        else: await query.message.edit_text("❌ Extraction Failed.")
        if os.path.exists(out): os.remove(out)

    # Extract All tracks logic
    elif action == "extall":
        await query.message.edit_text("🚀 **Extracting All Subtitles...**\nPlease wait.")
        for s in data['streams']:
            idx, codec = s['index'], s.get('codec_name', 'subrip')
            ext = ".ass" if codec == "ass" else ".srt"
            out = os.path.join(os.path.dirname(data['path']), f"{data['name']}_track_{idx}{ext}")
            
            if await extract_sub_logic(data['path'], idx, out):
                # Upload without individual progress bar to avoid spamming updates, or small delay
                with open(out, 'rb') as f:
                    await context.bot.send_document(chat_id=query.message.chat_id, document=f, filename=f"{data['name']}_track_{idx}{ext}")
                if os.path.exists(out): os.remove(out)
        
        await query.message.edit_text("✅ **All subtitles extracted and sent!**")

# --- MUXING & OTHERS (Existing Logic) ---
async def check_access(update, context):
    if not update.effective_chat or not update.effective_user: return
    if update.effective_user.id == OWNER_ID: return
    if not is_chat_auth(update.effective_chat.id) and not is_user_auth(update.effective_user.id):
        raise ApplicationHandlerStop()

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🤖 **Muxing Bot Active!**\n\n1️⃣ Send MKV.\n2️⃣ Send Subtitle.\n3️⃣ Send Name (or /skip).\n\n💡 Use `/extract` to get subtitles.")

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document or update.message.video
    if not doc: return
    ext = os.path.splitext(doc.file_name)[1].lower()
    if ext in ['.mkv', '.mp4']:
        context.user_data.update({'mkv_id': doc.file_id, 'orig_name': doc.file_name, 'state': 'WAIT_SUB', 'mkv_msg_id': update.message.message_id})
        await update.message.reply_text("✅ MKV Received! Now send **Subtitle (.srt/.ass)**.")
    elif ext in ['.srt', '.ass'] and context.user_data.get('state') == 'WAIT_SUB':
        context.user_data.update({'sub_id': doc.file_id, 'state': 'WAIT_NAME', 'sub_msg_id': update.message.message_id})
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
    msg_list = [context.user_data.get('mkv_msg_id'), context.user_data.get('sub_msg_id'), context.user_data.get('name_msg_id')]
    data = {'chat_id': update.effective_chat.id, 'mkv_id': context.user_data['mkv_id'], 'sub_id': context.user_data['sub_id'], 'name': final_name, 'to_delete': msg_list}
    context.user_data.clear()
    status = await update.message.reply_text("⏳ **Added to Queue...**")
    asyncio.create_task(run_queue(context, data, status))

async def run_queue(context, data, status):
    async with global_task_lock:
        await status.edit_text("⚙️ **Initializing Task...**")
        tmp = os.path.abspath(f"task_{data['chat_id']}_{int(time.time())}")
        os.makedirs(tmp, exist_ok=True)
        out, thumb_path = os.path.join(tmp, data['name']), os.path.join(tmp, "thumb.jpg")
        try:
            m_f = await context.bot.get_file(data['mkv_id'], read_timeout=3600)
            s_f = await context.bot.get_file(data['sub_id'], read_timeout=3600)
            if await mux_video(m_f.file_path, s_f.file_path, out, data['chat_id'], status):
                await status.edit_text("🖼️ **Generating Preview...**")
                has_thumb = await extract_thumbnail(out, thumb_path)
                start_up = time.time()
                with ProgressFile(out, status, start_up) as pf:
                    thumb_file = open(thumb_path, 'rb') if has_thumb else None
                    try:
                        await context.bot.send_document(chat_id=data['chat_id'], document=pf, thumbnail=thumb_file, caption="Muxing complete", filename=data['name'])
                    finally:
                        if thumb_file: thumb_file.close()
                await delete_messages(context.bot, data['chat_id'], data['to_delete'])
                await status.delete()
            else: await status.edit_text("❌ Muxing Failed.")
        except Exception as e:
            try: await status.edit_text(f"❌ Error: {e}")
            except: pass
        finally: clean_temp_files(tmp)

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
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("extract", extract_cmd))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, handle_docs))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(do_extract_cb, pattern="^(ext|extall)_"))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern="^cancel_"))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
