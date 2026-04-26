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
    for mid in message_ids:
        if mid:
            try: await bot.delete_message(chat_id, mid)
            except: pass

# --- EXTRACTION HANDLERS ---
async def extract_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        return await msg.reply_text("⚠️ Reply to an MKV file with `/extract`.")
    
    target = msg.reply_to_message.video or msg.reply_to_message.document
    if not target.file_name.lower().endswith('.mkv'): return await msg.reply_text("⚠️ Only MKV supported.")
    
    status = await msg.reply_text("📥 **Scanning Subtitles...** (Deep Scan)")
    
    try:
        mkv_f = await context.bot.get_file(target.file_id)
        streams = await get_subtitles_info(mkv_f.file_path)
        
        if not streams:
            return await status.edit_text("❌ **No subtitles found!**\nThis file might have hardcoded subtitles or empty metadata.")

        user_id = update.effective_user.id
        base_name = os.path.splitext(target.file_name)[0]
        EXTRACT_DATA[user_id] = {'path': mkv_f.file_path, 'name': base_name, 'streams': streams}
        
        btns = []
        for s in streams:
            idx = s['index']
            codec = s.get('codec_name', 'subrip')
            tags = s.get('tags', {})
            lang_code = tags.get('language', 'und').lower()
            lang_full = LANG_MAP.get(lang_code, lang_code.title())
            title = tags.get('title', '')
            
            display_name = f"{lang_full}"
            if title: display_name += f" ({title})"
            
            size_bytes = tags.get('NUMBER_OF_BYTES')
            size_text = f" | {humanbytes(int(size_bytes))}" if size_bytes else ""
            
            btns.append([InlineKeyboardButton(
                f"🌐 {display_name}{size_text} [{codec.upper()}]", 
                callback_data=f"ext_{user_id}_{idx}"
            )])
        
        btns.append([InlineKeyboardButton("🔥 Extract All Subtitles 🔥", callback_data=f"extall_{user_id}")])
        await status.edit_text(f"📂 **{len(streams)} Tracks Found!**\nSelect to download:", reply_markup=InlineKeyboardMarkup(btns))
        
    except Exception as e:
        await status.edit_text(f"❌ **System Error:** {str(e)}")

async def do_extract_cb(update, context):
    query = update.callback_query
    parts = query.data.split("_")
    uid = int(parts[1])
    if query.from_user.id != uid: return await query.answer("Access Denied!", show_alert=True)
    data = EXTRACT_DATA.get(uid)
    if not data: return await query.message.edit_text("❌ Session Expired.")

    if parts[0] == "ext":
        idx = parts[2]
        await query.message.edit_text(f"⚙️ **Extracting Track {idx}...**")
        codec = next((s.get('codec_name', 'subrip') for s in data['streams'] if str(s['index']) == idx), 'subrip')
        
        # Image-based subtitles (PGS) cannot be extracted to text files easily
        if 'pgs' in codec.lower() or 'dvd' in codec.lower():
            ext = ".sup" # Binary format
        else:
            ext = ".ass" if codec == "ass" else ".srt"
            
        out = os.path.join(os.path.dirname(data['path']), f"sub_{idx}{ext}")
        
        if await extract_sub_logic(data['path'], idx, out):
            start_up = time.time()
            with ProgressFile(out, query.message, start_up, "Uploading Subtitle") as pf:
                await context.bot.send_document(
                    chat_id=query.message.chat_id, 
                    document=pf, 
                    filename=f"{data['name']}_track_{idx}{ext}", 
                    caption=f"✅ **Extracted:** `Track {idx}`"
                )
            await query.message.delete()
        else: await query.message.edit_text("❌ Extraction Failed.")
        if os.path.exists(out): os.remove(out)

    elif parts[0] == "extall":
        await query.message.edit_text("🚀 **Extracting All Tracks...**")
        for s in data['streams']:
            idx, codec = s['index'], s.get('codec_name', 'subrip')
            ext = ".ass" if codec == "ass" else ".srt"
            if 'pgs' in codec.lower(): ext = ".sup"
            
            out = os.path.join(os.path.dirname(data['path']), f"all_{idx}{ext}")
            if await extract_sub_logic(data['path'], idx, out):
                with open(out, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=query.message.chat_id, 
                        document=f, 
                        filename=f"{data['name']}_track_{idx}{ext}"
                    )
                if os.path.exists(out): os.remove(out)
        await query.message.edit_text("✅ **All subtitles sent!**")

# --- REST OF THE CODE (Muxing, Start, etc.) ---
# ... (Purana muxing logic as it is use karein) ...
# (main() function bhi same rakhein)
