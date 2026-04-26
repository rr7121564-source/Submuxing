import os, time, asyncio, threading, json, re, shutil, sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, TypeHandler, ApplicationHandlerStop
)

from config import BOT_TOKEN, OWNER_ID, PORT, SESSION_ID, global_task_lock, active_processes, EXTRACT_DATA, LANG_MAP
from database import init_db, is_user_auth, is_chat_auth, add_processed_id
from utils import mux_video, clean_temp_files, get_readable_time, extract_thumbnail

# --- GLOBAL VARIABLES & DB ---
current_active_tasks = 0
all_tasks = set()
RENAME_PREF = {}

def init_bot_db():
    with sqlite3.connect("bot_management.db") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS topics (letter TEXT PRIMARY KEY, thread_id INTEGER)")
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")

def get_dump_id():
    with sqlite3.connect("bot_management.db") as conn:
        res = conn.execute("SELECT value FROM settings WHERE key='dump_id'").fetchone()
        return int(res[0]) if res and res[0] else None

def set_dump_id(chat_id):
    with sqlite3.connect("bot_management.db") as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('dump_id', ?)", (str(chat_id),))
        conn.execute("DELETE FROM topics") # Flush old topics if group changes
        conn.commit()

def get_thread_id(letter):
    with sqlite3.connect("bot_management.db") as conn:
        res = conn.execute("SELECT thread_id FROM topics WHERE letter=?", (letter,)).fetchone()
        return res[0] if res else None

def save_thread_id(letter, thread_id):
    with sqlite3.connect("bot_management.db") as conn:
        conn.execute("INSERT OR REPLACE INTO topics (letter, thread_id) VALUES (?, ?)", (letter, thread_id))
        conn.commit()

def delete_thread_id(letter):
    with sqlite3.connect("bot_management.db") as conn:
        conn.execute("DELETE FROM topics WHERE letter=?", (letter,))
        conn.commit()

async def delete_messages(bot, chat_id, message_ids):
    for msg_id in message_ids:
        if msg_id:
            try: await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except: pass

# --- AUTO RENAME LOGIC ---
def auto_rename(orig_name):
    try:
        base_name, ext = os.path.splitext(orig_name)
        if not ext: ext = '.mkv'
        
        ep_match = re.search(r'-\s*(\d+)', base_name)
        ep = ep_match.group(1) if ep_match else "01"
        
        q_match = re.search(r'(1080p|720p|480p|2160p|4k)', base_name, re.IGNORECASE)
        quality = q_match.group(1).lower() if q_match else "1080p"
        
        if '-' in base_name:
            title_part = base_name.split('-')[0]
        else:
            title_part = base_name
            
        title_part = re.sub(r'\[.*?\]', '', title_part).strip()
        words = title_part.split()
        short_title = " ".join(words[:4]) if len(words) > 0 else "Video"
        
        return f"[E{ep}] {short_title} [{quality}] @lpxempire{ext}"
    except Exception:
        return orig_name

# --- TEXTS & KEYBOARDS ---
def start_text():
    return (
        "✨ **Welcome to Pro SubMuxer Bot!** ✨\n\n"
        "I am an advanced bot designed to seamlessly merge your subtitles with MKV videos without losing quality.\n\n"
        "📌 **How to Use:**\n"
        "1️⃣ Send an **MKV** video file.\n"
        "2️⃣ Send a **Subtitle** file (`.srt` or `.ass`).\n"
        "3️⃣ Relax while I do the magic!\n\n"
        "💡 _Click the buttons below to explore more features._"
    )

def start_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📚 Help & Commands", callback_data="show_help")],
        [InlineKeyboardButton("🗑 Clear Background Tasks", callback_data="clear_tasks")]
    ])

def help_text():
    return (
        "🛠 **Bot Commands & Features** 🛠\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🔹 /start - Check bot status\n"
        "🔹 /startname - 🟢 Turn ON Auto-Rename\n"
        "🔹 /stopname - 🔴 Turn OFF Auto-Rename\n"
        "🔹 /setdump - Set a Dump Group ID\n"
        "🔹 /deldump - Disable Dump Group\n"
        "🔹 /extract - Reply to an MKV to extract subtitles\n"
        "🔹 /clear - 🗑 Cancel all running tasks\n\n"
        "💡 **Pro Tips:**\n"
        "• **Batch Sending:** Send MKV & Subtitle together!\n"
        "• **Custom Cover:** Send any `.jpg` image to set it as a Cover."
    )

def help_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="show_start")]
    ])

# --- COMMANDS ---
async def perform_clear(context):
    global current_active_tasks, all_tasks, active_processes
    for key, proc in list(active_processes.items()):
        try: proc.terminate()
        except: pass
    active_processes.clear()
    
    for task in list(all_tasks):
        try: task.cancel()
        except: pass
        
    context.user_data.clear()
    EXTRACT_DATA.clear()
    
    try:
        if os.path.exists("user_thumbs"):
            for f in os.listdir("user_thumbs"):
                if "_task_" in f:
                    os.remove(os.path.join("user_thumbs", f))
    except: pass

    await asyncio.sleep(0.5)
    current_active_tasks = 0
    all_tasks.clear()

async def ui_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "show_help":
        await query.message.edit_text(help_text(), reply_markup=help_keyboard())
    elif data == "show_start":
        await query.message.edit_text(start_text(), reply_markup=start_keyboard())
    elif data == "clear_tasks":
        await perform_clear(context)
        await query.message.edit_text(
            "🗑️ **System Cleared Successfully!**\n_All running processes and queue have been reset._", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="show_start")]])
        )

async def cmd_setdump(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("⚠️ **Incorrect Format!**\n\n**Usage:** `/setdump -100XXXXXXXXX`\n_Please enter a valid Group/Channel ID._")
    try:
        dump_id = int(context.args[0])
        set_dump_id(dump_id)
        await update.message.reply_text(f"✅ **Dump Group Configured!**\n\n🆔 **ID:** `{dump_id}`\n\n⚠️ *Note:* Ensure the bot is an **Admin** in the target group and has **'Manage Topics'** permissions enabled.")
    except ValueError:
        await update.message.reply_text("❌ **Invalid ID Format.**\n_Make sure it is a numeric ID (e.g., -100123456789)._")

async def cmd_deldump(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_dump_id("")
    await update.message.reply_text("🗑️ **Dump Group Removed!**\n_Files will now be sent directly here in the bot._")

async def cmd_startname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    RENAME_PREF[update.effective_user.id] = True
    await update.message.reply_text("✅ **Auto-Rename is now ENABLED!**\n_Your files will be neatly renamed automatically._")

async def cmd_stopname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    RENAME_PREF[update.effective_user.id] = False
    await update.message.reply_text("❌ **Auto-Rename is now DISABLED!**\n_Your files will keep their original names._")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await perform_clear(context)
    await update.message.reply_text("🗑️ **System Cleared Successfully!**\n_All running processes and queue have been reset._")

# --- THUMBNAIL LOGIC ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    os.makedirs("user_thumbs", exist_ok=True)
    thumb_path = f"user_thumbs/{user_id}.jpg"
    bot_msg = await update.message.reply_text("📥 **Saving Custom Cover...**\n_Please wait..._")
    photo_file = await context.bot.get_file(photo.file_id)
    try: shutil.copy(photo_file.file_path, thumb_path)
    except: await photo_file.download_to_drive(thumb_path)
    await bot_msg.edit_text("🖼️ **Custom Cover Saved!**\n_This cover will be applied to your next videos._")

# --- EXTRACTION LOGIC ---
def get_lang_name(code):
    return LANG_MAP.get(code.lower(), code.title())

async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        return await msg.reply_text("⚠️ **Notice:** Reply to an MKV file with `/extract` to begin.")
    user_id = msg.from_user.id
    target = msg.reply_to_message.video or msg.reply_to_message.document
    file_name = getattr(target, 'file_name', None) or "video.mkv"
    if not file_name.lower().endswith('.mkv'): return await msg.reply_text("⚠️ **Format Unsupported.** Only MKV files are supported for extraction.")
    
    bot_msg = await msg.reply_text("📥 **Scanning Video...**\n_Looking for built-in subtitles..._")
    mkv_f = await context.bot.get_file(target.file_id, read_timeout=3600)
    
    cmd =['ffprobe', '-v', 'error', '-select_streams', 's', '-show_entries', 'stream=index,codec_name:stream_tags=language,NUMBER_OF_BYTES', '-of', 'json', mkv_f.file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    
    streams = json.loads(stdout.decode()).get('streams',[]) if stdout else[]
    if not streams: return await bot_msg.edit_text("❌ **No subtitles found in this video.**")
    
    base_name = os.path.splitext(file_name)[0]
    
    if len(streams) == 1:
        await bot_msg.edit_text("⚙️ **Extracting Subtitle...**\n_Please wait a moment._")
        idx, codec = streams[0]['index'], streams[0].get('codec_name', 'subrip')
        ext = ".ass" if codec == "ass" else ".srt"
        out = os.path.abspath(f"{base_name}{ext}")
        try:
            ffmpeg_proc = await asyncio.create_subprocess_exec('ffmpeg', '-y', '-i', mkv_f.file_path, '-map', f"0:{idx}", '-c:s', 'copy', out, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            active_processes[f"ext_{user_id}"] = ffmpeg_proc 
            await ffmpeg_proc.wait()
            if ffmpeg_proc.returncode == 0 and os.path.exists(out):
                await context.bot.send_document(msg.chat_id, document=f"file://{out}", caption="✅ **Extraction Complete!**")
                await bot_msg.delete()
            else: await bot_msg.edit_text("❌ **Failed to extract subtitle.**")
        finally:
            active_processes.pop(f"ext_{user_id}", None)
            if os.path.exists(out): os.remove(out)
        return

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
        
    btns.append([InlineKeyboardButton("❌ Cancel Extraction", callback_data=f"ext_{user_id}_cancel")])
    await bot_msg.edit_text("📂 **Multiple Subtitles Found!**\n_Select a language to extract:_ ", reply_markup=InlineKeyboardMarkup(btns))

async def do_extract_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    
    if len(parts) == 3 and parts[2] == "cancel":
        uid = parts[1]
        if query.from_user.id != int(uid): return await query.answer("Access Denied!", show_alert=True)
        EXTRACT_DATA.pop(int(uid), None)
        return await query.message.edit_text("🛑 **Extraction Process Cancelled.**")
        
    _, uid, idx = parts
    if query.from_user.id != int(uid): return await query.answer("Access Denied!", show_alert=True)
    data = EXTRACT_DATA.get(int(uid))
    if not data: return await query.message.edit_text("❌ **Session Expired.** Please reply and extract again.")
    
    await query.message.edit_text("⚙️ **Extracting Subtitle...**\n_Please wait a moment._")
    ext = data['streams'].get(idx, ".srt")
    out = os.path.abspath(f"{data['name']}_{idx}{ext}")
    try:
        ffmpeg_proc = await asyncio.create_subprocess_exec('ffmpeg', '-y', '-i', data['path'], '-map', f"0:{idx}", '-c:s', 'copy', out, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        active_processes[f"ext_{uid}"] = ffmpeg_proc
        await ffmpeg_proc.wait()
        if ffmpeg_proc.returncode == 0 and os.path.exists(out):
            await context.bot.send_document(query.message.chat_id, document=f"file://{out}", caption="✅ **Extraction Complete!**")
            await query.message.delete()
        else: await query.message.edit_text("❌ **Failed to extract subtitle.**")
    finally:
        active_processes.pop(f"ext_{uid}", None)
        if os.path.exists(out): os.remove(out)

# --- MIDDLEWARES ---
async def check_access(update, context):
    if not update.effective_chat or not update.effective_user: return
    if update.effective_user.id == OWNER_ID: return
    if not is_chat_auth(update.effective_chat.id) and not is_user_auth(update.effective_user.id): raise ApplicationHandlerStop()

async def block_duplicates(update, context):
    if not update.effective_message: return
    key = f"{update.effective_message.chat_id}_{update.effective_message.message_id}"
    if not add_processed_id(key): raise ApplicationHandlerStop()

# --- HANDLERS ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(start_text(), reply_markup=start_keyboard())

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(help_text(), reply_markup=help_keyboard())

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document or update.message.video
    if not doc: return
    file_name = getattr(doc, 'file_name', None) or "video.mkv"
    ext = os.path.splitext(file_name)[1].lower()
    
    if ext == '.mkv':
        context.user_data['mkv_id'] = doc.file_id
        context.user_data['orig_name'] = file_name
        context.user_data['mkv_msg_id'] = update.message.message_id
        if 'sub_id' not in context.user_data:
            await update.message.reply_text("🎬 **Video Received!**\n_Please send the Subtitle file (.srt/.ass) now._")
        
    elif ext in['.srt', '.ass']:
        context.user_data['sub_id'] = doc.file_id
        context.user_data['sub_msg_id'] = update.message.message_id
        if 'mkv_id' not in context.user_data:
            await update.message.reply_text("📝 **Subtitle Received!**\n_Please send the Video file (.mkv) now._")
    else: return
        
    if 'mkv_id' in context.user_data and 'sub_id' in context.user_data:
        user_id = update.effective_user.id
        if RENAME_PREF.get(user_id, True): final_name = auto_rename(context.user_data['orig_name'])
        else: final_name = context.user_data['orig_name']
        await start_task(update, context, final_name)

async def start_task(update, context, final_name):
    global current_active_tasks, all_tasks
    user_id = update.effective_user.id
    msg_list =[context.user_data.get('mkv_msg_id'), context.user_data.get('sub_msg_id')]
    
    # SNAPSHOT THUMBNAIL
    os.makedirs("user_thumbs", exist_ok=True)
    task_id = int(time.time() * 1000)
    main_thumb = f"user_thumbs/{user_id}.jpg"
    task_thumb = f"user_thumbs/{user_id}_task_{task_id}.jpg"
    
    if os.path.exists(main_thumb): shutil.copy(main_thumb, task_thumb)
    else: task_thumb = None
    
    data = {
        'chat_id': update.effective_chat.id, 'user_id': user_id,
        'mkv_id': context.user_data['mkv_id'], 'sub_id': context.user_data['sub_id'], 
        'name': final_name, 'to_delete': msg_list, 'task_thumb': task_thumb
    }
    
    context.user_data.clear()
    current_active_tasks += 1
    
    if current_active_tasks > 1: 
        status = await update.message.reply_text(
            f"⏳ **Task Added to Queue...**\n"
            f"🔢 **Queue Position:** `{current_active_tasks - 1}`\n"
            "_Please wait for your turn._"
        )
    else: 
        status = await update.message.reply_text("🔄 **Initializing Process...**")
        
    task = asyncio.create_task(run_queue(context, data, status))
    all_tasks.add(task)
    task.add_done_callback(lambda t: all_tasks.discard(t))

async def run_queue(context, data, status):
    global current_active_tasks
    try:
        async with global_task_lock:
            try: 
                await status.edit_text(
                    "🎬 **Preparing for Muxing...**\n"
                    "_Gathering resources and verifying files..._"
                )
            except: pass
            
            tmp = os.path.abspath(f"task_{data['chat_id']}_{int(time.time())}")
            os.makedirs(tmp, exist_ok=True)
            out = os.path.join(tmp, data['name'])
            thumb_path = os.path.join(tmp, "thumb.jpg")
            
            custom_thumb = data.get('task_thumb')
            has_thumb = False
            if custom_thumb and os.path.exists(custom_thumb):
                shutil.copy(custom_thumb, thumb_path)
                has_thumb = True
            
            try:
                m_f = await context.bot.get_file(data['mkv_id'], read_timeout=3600)
                s_f = await context.bot.get_file(data['sub_id'], read_timeout=3600)
                success = await mux_video(m_f.file_path, s_f.file_path, out, data['chat_id'], status)
                
                if success:
                    if not has_thumb:
                        await status.edit_text("🖼️ **Generating Video Thumbnail...**\n_Hold on tight!_")
                        has_thumb = await extract_thumbnail(out, thumb_path)
                    
                    await status.edit_text(
                        "📤 **Uploading File to Telegram...**\n"
                        "━━━━━━━━━━━━━━━━━━━\n"
                        "⚡ _Uploading via Ultra-Fast Local API server..._\n"
                        "_This may take some time depending on file size._"
                    )
                    
                    # --- DUMP FOLDER ROUTING LOGIC ---
                    dump_id = get_dump_id()
                    target_chat = data['chat_id']
                    target_thread = None
                    folder_letter = "#"
                    
                    if dump_id:
                        target_chat = dump_id
                        core_name = re.sub(r'\[.*?\]', '', data['name']).replace('@lpxempire', '').strip()
                        match = re.search(r'[A-Za-z0-9]', core_name)
                        if match:
                            char = match.group(0).upper()
                            folder_letter = char if not char.isdigit() else "#"
                            
                        target_thread = get_thread_id(folder_letter)
                        if not target_thread:
                            try:
                                topic = await context.bot.create_forum_topic(chat_id=dump_id, name=folder_letter)
                                target_thread = topic.message_thread_id
                                save_thread_id(folder_letter, target_thread)
                            except Exception as e:
                                print(f"Topic creation failed: {e}")
                                target_thread = None 
                    
                    # --- UPLOADING ---
                    thumb_file = open(thumb_path, 'rb') if has_thumb else None
                    try:
                        try:
                            cap_text = (
                                "✅ **Muxing Complete!**\n"
                                "━━━━━━━━━━━━━━━━━━━\n"
                                f"🎬 **Title:** `{data['name']}`\n"
                                "━━━━━━━━━━━━━━━━━━━\n"
                                "✨ _Powered by Pro Muxing Engine_"
                            )
                            sent_msg = await context.bot.send_document(
                                chat_id=target_chat, message_thread_id=target_thread,
                                document=f"file://{out}", thumbnail=thumb_file,
                                caption=cap_text,
                                read_timeout=7200, write_timeout=7200
                            )
                        except Exception as upload_err:
                            if "thread" in str(upload_err).lower() or "topic" in str(upload_err).lower():
                                if target_thread: delete_thread_id(folder_letter)
                                sent_msg = await context.bot.send_document(
                                    chat_id=target_chat, document=f"file://{out}", thumbnail=thumb_file,
                                    caption=cap_text,
                                    read_timeout=7200, write_timeout=7200
                                )
                            else: raise upload_err
                            
                        if target_chat != data['chat_id']:
                            await context.bot.send_message(
                                chat_id=data['chat_id'],
                                text=(
                                    "✅ **File Uploaded to Dump successfully!**\n"
                                    "━━━━━━━━━━━━━━━━━━━\n"
                                    f"📂 **Directory:** `{folder_letter}`\n"
                                    f"🎬 **File Name:** `{data['name']}`\n"
                                    "━━━━━━━━━━━━━━━━━━━\n"
                                    "✨ _Thank you for using the bot!_"
                                )
                            )
                    finally:
                        if thumb_file: thumb_file.close()
                    
                    await delete_messages(context.bot, data['chat_id'], data['to_delete'])
                    await status.delete()
                else: 
                    await status.edit_text(
                        "❌ **Muxing Process Failed.**\n_Please check your file integrity or try again._", 
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Clear Task", callback_data="clear_tasks")]])
                    )
            
            except asyncio.CancelledError:
                try: await status.edit_text("🚫 **Task Cancelled by User.**")
                except: pass
                raise
            except Exception as e:
                try: await status.edit_text(f"❌ **Error:** `{e}`")
                except: pass
            finally: clean_temp_files(tmp)
                
    finally:
        current_active_tasks = max(0, current_active_tasks - 1)
        custom_thumb = data.get('task_thumb')
        if custom_thumb and os.path.exists(custom_thumb):
            try: os.remove(custom_thumb)
            except: pass

async def cancel_cb(update, context):
    cid = update.effective_chat.id
    if cid in active_processes:
        active_processes[cid].terminate()
        await update.callback_query.edit_message_text(
            "🛑 **Task Cancelled Successfully!**\n_The muxing process has been aborted._",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Clear All Tasks", callback_data="clear_tasks")]])
        )

# --- MAIN ---
def main():
    init_db()
    init_bot_db() 
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), BaseHTTPRequestHandler).serve_forever(), daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).base_url("http://127.0.0.1:8081/bot").local_mode(True).build()
    
    app.add_handler(TypeHandler(Update, check_access), group=-2)
    app.add_handler(TypeHandler(Update, block_duplicates), group=-1)
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help)) 
    app.add_handler(CommandHandler("startname", cmd_startname))
    app.add_handler(CommandHandler("stopname", cmd_stopname))
    app.add_handler(CommandHandler("setdump", cmd_setdump))
    app.add_handler(CommandHandler("deldump", cmd_deldump))
    app.add_handler(CommandHandler("extract", cmd_extract))
    app.add_handler(CommandHandler("clear", cmd_clear))
    
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, handle_docs))
    
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern=r"^cancel_"))
    app.add_handler(CallbackQueryHandler(do_extract_cb, pattern=r"^ext_"))
    app.add_handler(CallbackQueryHandler(ui_cb, pattern=r"^(show_help|show_start|clear_tasks)$"))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
