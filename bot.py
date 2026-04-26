import os, time, asyncio, threading, io
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, TypeHandler, ApplicationHandlerStop
)

from config import BOT_TOKEN, OWNER_ID, PORT, SESSION_ID, global_task_lock, active_processes, EXTRACT_DATA, LANG_MAP
from database import init_db, is_user_auth, is_chat_auth, add_processed_id
from utils import mux_video, clean_temp_files, get_readable_time, extract_thumbnail, get_subtitles, extract_specific_subtitle

# --- HELPERS ---
def humanbytes(size):
    if not size: return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024

def get_lang_name(code):
    return LANG_MAP.get(code.lower(), code.title())

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

# --- MIDDLEWARES ---
async def check_access(update, context):
    if not update.effective_chat or not update.effective_user: return
    if update.effective_user.id == OWNER_ID: return
    if not is_chat_auth(update.effective_chat.id) and not is_user_auth(update.effective_user.id):
        raise ApplicationHandlerStop()

# --- EXTRACTION HANDLERS ---
async def extract_cmd(update, context):
    msg = update.message
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        return await msg.reply_text("⚠️ Reply to an MKV file with `/extract`.")
    
    target = msg.reply_to_message.video or msg.reply_to_message.document
    if not target.file_name.lower().endswith('.mkv'):
        return await msg.reply_text("⚠️ Only MKV files are supported.")
    
    status = await msg.reply_text("📥 **Scanning Subtitles...**")
    mkv_f = await context.bot.get_file(target.file_id)
    streams = await get_subtitles(mkv_f.file_path)
    
    if not streams: return await status.edit_text("❌ No subtitles found.")
    
    user_id = update.effective_user.id
    EXTRACT_DATA[user_id] = {'path': mkv_f.file_path, 'name': os.path.splitext(target.file_name)[0], 'streams': {}}
    btns = []
    for s in streams:
        idx, codec = s['index'], s.get('codec_name', 'subrip')
        lang = get_lang_name(s.get('tags', {}).get('language', 'und'))
        EXTRACT_DATA[user_id]['streams'][str(idx)] = ".ass" if codec == "ass" else ".srt"
        btns.append([InlineKeyboardButton(f"{lang} [{codec.upper()}]", callback_data=f"ext_{user_id}_{idx}")])
    
    await status.edit_text("📂 **Select Subtitle to Extract:**", reply_markup=InlineKeyboardMarkup(btns))

async def do_extract_cb(update, context):
    query = update.callback_query
    _, uid, idx = query.data.split("_")
    if query.from_user.id != int(uid): return await query.answer("Access Denied!", show_alert=True)
    
    data = EXTRACT_DATA.get(int(uid))
    if not data: return await query.message.edit_text("❌ Session Expired.")
    
    await query.message.edit_text("⚙️ **Extracting...**")
    ext = data['streams'].get(idx, ".srt")
    out_name = f"{data['name']}{ext}"
    out_path = os.path.join(os.path.dirname(data['path']), out_name)
    
    if await extract_specific_subtitle(data['path'], idx, out_path):
        start_up = time.time()
        with ProgressFile(out_path, query.message, start_up, "Uploading Subtitle") as pf:
            await context.bot.send_document(chat_id=query.message.chat_id, document=pf, filename=out_name, caption=f"✅ **Extracted:** `{out_name}`")
        await query.message.delete()
    else: await query.message.edit_text("❌ Extraction Failed.")
    if os.path.exists(out_path): os.remove(out_path)

# --- MUXING HANDLERS ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🤖 **Muxing Bot Active!**\n\n1️⃣ Send MKV.\n2️⃣ Send Subtitle.\n3️⃣ Send Name (or /skip).\n\n💡 Use `/extract` by replying to an MKV to get subtitles.")

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
        await status.edit_text("⚙️ **Initializing...**")
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

def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).base_url("http://127.0.0.1:8081/bot").local_mode(True).build()
    app.add_handler(TypeHandler(Update, check_access), group=-2)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("extract", extract_cmd))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, handle_docs))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(do_extract_cb, pattern="^ext_"))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern="^cancel_"))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
