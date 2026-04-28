import os, time, asyncio, threading, json, re, shutil, sqlite3, urllib.request, glob, requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, TypeHandler, ApplicationHandlerStop
)

from config import BOT_TOKEN, OWNER_ID, PORT, SESSION_ID, global_task_lock, github_task_lock, active_processes, EXTRACT_DATA, LANG_MAP, GITHUB_TOKEN, REPO_NAME
from database import init_db, is_user_auth, is_chat_auth, add_processed_id, DB_PATH, get_user_settings, update_user_setting
from bot_utils import mux_video, clean_temp_files, get_readable_time, extract_thumbnail

# --- GLOBAL VARIABLES & DB ---
current_active_tasks = 0
current_github_tasks = 0
all_tasks = set()
ACTIVE_STATUS_MSGS = {}

async def delete_after(msg, delay):
    await asyncio.sleep(delay)
    try: await msg.delete()
    except: pass

async def delete_messages(bot, chat_id, message_ids):
    for msg_id in message_ids:
        if msg_id:
            try: await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except: pass

def init_bot_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS topics (letter TEXT PRIMARY KEY, thread_id INTEGER)")

def get_thread_id(letter):
    with sqlite3.connect(DB_PATH) as conn:
        res = conn.execute("SELECT thread_id FROM topics WHERE letter=?", (letter,)).fetchone()
        return res[0] if res else None

def save_thread_id(letter, thread_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO topics (letter, thread_id) VALUES (?, ?)", (letter, thread_id))
        conn.commit()

# --- GITHUB TRIGGER & STATUS LOGIC ---
def _send_to_github(task):
    url = f"https://api.github.com/repos/{REPO_NAME}/actions/workflows/encode.yml/dispatches"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    payload = {"ref": "main", "inputs": task}
    try:
        r = requests.post(url, headers=headers, json=payload)
        return r.status_code == 204, r.text
    except Exception as e: return False, str(e)

async def trigger_github(task):
    return await asyncio.to_thread(_send_to_github, task)

def _is_github_busy():
    url = f"https://api.github.com/repos/{REPO_NAME}/actions/runs"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            for run in r.json().get("workflow_runs",[]):
                if run.get("status") in ["in_progress", "queued", "requested"]: return True
        return False
    except: return False

def _cancel_all_github_runs():
    url = f"https://api.github.com/repos/{REPO_NAME}/actions/runs"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            for run in r.json().get("workflow_runs",[]):
                if run.get("status") in["in_progress", "queued", "requested", "waiting"]:
                    requests.post(f"{url}/{run.get('id')}/cancel", headers=headers)
            return True
        return False
    except: return False

async def wait_for_github_free():
    while await asyncio.to_thread(_is_github_busy):
        await asyncio.sleep(20)

# --- AUTO RENAME LOGIC ---
def auto_rename(orig_name, user_id):
    try:
        settings = get_user_settings(user_id)
        fmt = settings.get('rename_format')
        if not fmt: fmt = "[E{ep}] {short_title}[{quality}] @lpxempire"
        
        base_name, ext = os.path.splitext(orig_name)
        if not ext: ext = '.mkv'
        ep_match = re.search(r'-\s*(\d+)', base_name)
        ep = ep_match.group(1) if ep_match else "01"
        q_match = re.search(r'(1080p|720p|480p|2160p|4k)', base_name, re.IGNORECASE)
        quality = q_match.group(1).lower() if q_match else "1080p"
        title_part = base_name.split('-')[0] if '-' in base_name else base_name
        title_part = re.sub(r'\[.*?\]', '', title_part).strip()
        words = title_part.split()
        short_title = " ".join(words[:4]) if len(words) > 0 else "Video"
        
        final_name = fmt.replace("{ep}", ep).replace("{short_title}", short_title).replace("{quality}", quality)
        return final_name + ext
    except: return orig_name

# --- COMMANDS ---
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛠 **Bot Commands & Features** 🛠\n\n"
        "🔹 /start - Check bot status\n"
        "🔹 /autorename - Set Rename Format (e.g., `/autorename [E{ep}] {short_title}`)\n"
        "🔹 /setlogo - **Reply** to an image to set Hardsub Logo\n"
        "🔹 /showlogo - Check currently set logo\n"
        "🔹 /setdump - Set a Dump Group ID\n"
        "🔹 /deldump - Disable Dump Group\n"
        "🔹 /dthumb - Delete Custom Cover Picture\n"
        "🔹 /extract - Reply to MKV to extract subs\n"
        "🔹 /compress - Reply to video to Compress (CRF 34 via Cloud)\n"
        "🔹 /clear - 🗑 Cancel active tasks & Clean Memory"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cmd_dthumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    thumb_path = f"user_thumbs/{user_id}.jpg"
    if os.path.exists(thumb_path):
        os.remove(thumb_path)
        await update.message.reply_text("🗑️ Custom cover deleted.")
    else: await update.message.reply_text("⚠️ No custom cover found.")

async def cmd_setdump(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("Usage: `/setdump -100xxx`")
    from database import set_user_dump
    set_user_dump(update.effective_user.id, context.args[0])
    await update.message.reply_text("✅ Personal dump group set!")

async def cmd_deldump(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from database import set_user_dump
    set_user_dump(update.effective_user.id, None)
    await update.message.reply_text("🗑️ Personal dump removed.")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await perform_clear(context)
    await update.message.reply_text("🗑️ Task queues, active tasks & extra memory cleared.\n*(Logo, Rename format & Cover are safe!)*")

# --- CORE LOGIC ---
async def perform_clear(context):
    global current_active_tasks, current_github_tasks, all_tasks, active_processes, ACTIVE_STATUS_MSGS
    await asyncio.to_thread(_cancel_all_github_runs)
    for key, proc in list(active_processes.items()):
        try: proc.terminate()
        except: pass
    active_processes.clear()
    for chat_id, msg_id in list(ACTIVE_STATUS_MSGS.items()):
        try: await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except: pass
    ACTIVE_STATUS_MSGS.clear()
    context.user_data.clear()
    
    try:
        for uid, data in list(EXTRACT_DATA.items()):
            if data and 'path' in data and os.path.exists(data['path']):
                try: os.remove(data['path'])
                except: pass
        EXTRACT_DATA.clear()
        
        # NOTE: Only deleting task thumbnails. Base thumbnail `user_id.jpg` is SAFE!
        if os.path.exists("user_thumbs"):
            for f in os.listdir("user_thumbs"):
                if "_task_" in f: os.remove(os.path.join("user_thumbs", f))
        for folder in glob.glob("task_*"):
            if os.path.isdir(folder): shutil.rmtree(folder)
    except: pass
    current_active_tasks, current_github_tasks = 0, 0
    all_tasks.clear()

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text = "🎬 Welcome to Pro SubMuxer Bot!\n\n📌 How to Use:\n▸ Send an MKV video file.\n▸ Send a Subtitle file (.srt/.ass).\n▸ Relax while I do the magic!"
    if os.path.exists("start_img.jpg"):
        await update.message.reply_photo(photo=open("start_img.jpg", 'rb'), caption=text)
    elif os.path.exists("start_img.png"):
        await update.message.reply_photo(photo=open("start_img.png", 'rb'), caption=text)
    else:
        await update.message.reply_text(text)

async def cmd_autorename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        msg = await update.message.reply_text("⚠️ Usage: `/autorename[S01 E{ep}] {short_title} [{quality} ⌯ Sub]`")
        asyncio.create_task(delete_after(update.message, 0))
        asyncio.create_task(delete_after(msg, 5))
        return
    format_str = " ".join(context.args)
    update_user_setting(update.effective_user.id, "rename_format", format_str)
    msg = await update.message.reply_text(f"✅ Auto-Rename format saved successfully!\nOutput: `{format_str}`")
    asyncio.create_task(delete_after(update.message, 0))
    asyncio.create_task(delete_after(msg, 4))

async def cmd_setlogo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.reply_to_message and (msg.reply_to_message.photo or msg.reply_to_message.document):
        user_id = update.effective_user.id
        photo_id = msg.reply_to_message.photo[-1].file_id if msg.reply_to_message.photo else msg.reply_to_message.document.file_id
        
        update_user_setting(user_id, "logo_id", photo_id)
        
        await delete_messages(context.bot, msg.chat_id,[msg.message_id])
        await msg.reply_to_message.reply_text("✅ Logo saved successfully!\n(Position: Top Right, Size: Small)")
    else:
        await msg.reply_text("⚠️ Please **Reply** to a PNG Image with `/setlogo` to set your logo.")

async def cmd_showlogo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    if not settings.get('logo_id'):
        msg = await update.message.reply_text("⚠️ No logo set! Reply to a PNG with /setlogo")
        asyncio.create_task(delete_after(update.message, 0))
        asyncio.create_task(delete_after(msg, 5))
        return

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Close", callback_data="logo_close")]])
    
    try: await update.message.reply_photo(photo=settings['logo_id'], caption="🖼 **Current Logo**", reply_markup=kb)
    except:
        try: await update.message.reply_document(document=settings['logo_id'], caption="🖼 **Current Logo**", reply_markup=kb)
        except: await update.message.reply_text("⚠️ Failed to load logo.")
        
    try: await update.message.delete()
    except: pass

async def logo_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "logo_close":
        try: await query.message.delete()
        except: pass

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    os.makedirs("user_thumbs", exist_ok=True)
    thumb_path = f"user_thumbs/{user_id}.jpg"
    photo_file = await context.bot.get_file(photo.file_id)
    try: shutil.copy(photo_file.file_path, thumb_path)
    except: await photo_file.download_to_drive(thumb_path)
    await update.message.reply_text("🖼️ Custom Cover Saved!")

async def cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer("❌ Task Cancelling...", show_alert=True)
    except: pass
    parts = query.data.split("_")
    task_type = parts[-1]
    if task_type == 'local':
        task_id = parts[1] + "_" + parts[2]
        if task_id in active_processes:
            active_processes[task_id].terminate()
        try: await query.message.delete()
        except: pass
    elif task_type == 'cloud':
        await asyncio.to_thread(_cancel_all_github_runs)
        try: await query.message.delete()
        except: pass

async def cmd_compress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        return await msg.reply_text("⚠️ Reply to an MKV/MP4 file with `/compress` to begin.")
    target = msg.reply_to_message.video or msg.reply_to_message.document
    file_name = getattr(target, 'file_name', None) or "video.mkv"
    context.user_data['mkv_id'] = target.file_id
    context.user_data['orig_name'] = file_name
    context.user_data['sub_id'] = None 
    context.user_data['to_delete'] =[msg.message_id, msg.reply_to_message.message_id]
    user_id = update.effective_user.id
    final_name = auto_rename(file_name, user_id)
    await process_dispatch(update, context, final_name, mode="compress")

def get_lang_name(code): return LANG_MAP.get(code.lower(), code.title())

async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        return await msg.reply_text("⚠️ Reply to an MKV file with `/extract` to begin.")
    user_id = msg.from_user.id
    
    old_data = EXTRACT_DATA.pop(user_id, None)
    if old_data and 'path' in old_data and os.path.exists(old_data['path']):
        try: os.remove(old_data['path'])
        except: pass

    target = msg.reply_to_message.video or msg.reply_to_message.document
    file_name = getattr(target, 'file_name', None) or "video.mkv"
    bot_msg = await msg.reply_text("📥 Downloading file for extraction...")
    mkv_f = await context.bot.get_file(target.file_id, read_timeout=3600)
    
    await bot_msg.edit_text("▸ Scanning Streams...")
    cmd =['ffprobe', '-v', 'error', '-select_streams', 's', '-show_entries', 'stream=index,codec_name:stream_tags=language,NUMBER_OF_BYTES', '-of', 'json', mkv_f.file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    streams = json.loads(stdout.decode()).get('streams', []) if stdout else[]
    
    if not streams:
        if os.path.exists(mkv_f.file_path): os.remove(mkv_f.file_path)
        return await bot_msg.edit_text("❌ No subtitles found.")
        
    base_name = os.path.splitext(file_name)[0]
    EXTRACT_DATA[user_id] = {'path': mkv_f.file_path, 'name': base_name, 'streams': {}}
    btns =[]
    for s in streams:
        idx, codec = s['index'], s.get('codec_name', 'subrip')
        tags = s.get('tags', {})
        lang = get_lang_name(tags.get('language', 'und'))
        size = tags.get('NUMBER_OF_BYTES')
        text = f"{lang}"
        if size: text += f" ({(int(size)/1024)/1024:.2f} MB)" if (int(size)/1024) > 1024 else f" ({int(size)/1024:.0f} KB)"
        EXTRACT_DATA[user_id]['streams'][str(idx)] = ".ass" if codec == "ass" else ".srt"
        btns.append([InlineKeyboardButton(text, callback_data=f"ext_{user_id}_{idx}")])
        
    btns.append([InlineKeyboardButton("❌ Cancel & Cleanup Disk", callback_data=f"ext_{user_id}_cancel")])
    await bot_msg.edit_text("📂 Multiple Subtitles Found!\n▸ Select a language to extract:", reply_markup=InlineKeyboardMarkup(btns))

async def do_extract_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    
    if len(parts) == 3 and parts[2] == "cancel":
        uid = parts[1]
        if query.from_user.id != int(uid): return await query.answer("Access Denied!", show_alert=True)
        data = EXTRACT_DATA.pop(int(uid), None)
        if data and 'path' in data and os.path.exists(data['path']):
            try: os.remove(data['path'])
            except: pass
        return await query.message.edit_text("❌ Extraction Canceled & Disk Cleaned.")
        
    _, uid, idx = parts
    data = EXTRACT_DATA.get(int(uid))
    if not data: return await query.message.edit_text("❌ Session Expired.")
    
    await query.message.edit_text("▸ Extracting Subtitles...")
    ext = data['streams'].get(idx, ".srt")
    out = os.path.abspath(f"{data['name']}_{idx}{ext}")
    try:
        ffmpeg_proc = await asyncio.create_subprocess_exec('ffmpeg', '-y', '-i', data['path'], '-map', f"0:{idx}", '-c:s', 'copy', out, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        active_processes[f"ext_{uid}"] = ffmpeg_proc
        await ffmpeg_proc.wait()
        if ffmpeg_proc.returncode == 0 and os.path.exists(out):
            await context.bot.send_document(query.message.chat_id, document=f"file://{out}", caption="✅ Extraction Complete!")
            await query.message.delete()
        else: await query.message.edit_text("❌ Extraction Failed.")
    finally:
        active_processes.pop(f"ext_{uid}", None)
        if os.path.exists(out): os.remove(out)
        if data and 'path' in data and os.path.exists(data['path']):
            try: os.remove(data['path'])
            except: pass
        EXTRACT_DATA.pop(int(uid), None)

async def check_access(update, context):
    if not update.effective_chat or not update.effective_user: return
    if update.effective_user.id == OWNER_ID: return
    if not is_chat_auth(update.effective_chat.id) and not is_user_auth(update.effective_user.id): raise ApplicationHandlerStop()

async def block_duplicates(update, context):
    if not update.effective_message: return
    key = f"{update.effective_message.chat_id}_{update.effective_message.message_id}"
    if not add_processed_id(key): raise ApplicationHandlerStop()

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document or update.message.video
    if not doc: return
    file_name = getattr(doc, 'file_name', None) or "video.mkv"
    ext = os.path.splitext(file_name)[1].lower()
    
    if 'to_delete' not in context.user_data: context.user_data['to_delete'] =[]
    context.user_data['to_delete'].append(update.message.message_id)
    
    if ext == '.mkv' or ext == '.mp4':
        context.user_data['mkv_id'] = doc.file_id
        context.user_data['orig_name'] = file_name
        if 'sub_id' not in context.user_data:
            bot_reply = await update.message.reply_text("🎬 Video Received\n▸ Now send the subtitle file (.srt/.ass)")
            context.user_data['to_delete'].append(bot_reply.message_id)
    elif ext in['.srt', '.ass']:
        context.user_data['sub_id'] = doc.file_id
        if 'mkv_id' not in context.user_data:
            bot_reply = await update.message.reply_text("📝 Subtitle Received\n▸ Now send the MKV video file")
            context.user_data['to_delete'].append(bot_reply.message_id)
    else: return
        
    if 'mkv_id' in context.user_data and 'sub_id' in context.user_data:
        user_id = update.effective_user.id
        final_name = auto_rename(context.user_data['orig_name'], user_id)
        context.user_data['final_name'] = final_name
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔥 Hardsub (Cloud)", callback_data="mode_hardsub")],[InlineKeyboardButton("⚡ Softsub (Local)", callback_data="mode_mux")]])
        mode_msg = await update.message.reply_text("🛠 Choose Processing Mode:", reply_markup=kb)
        context.user_data['to_delete'].append(mode_msg.message_id)

async def mode_selection_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if 'mkv_id' not in context.user_data: return await query.message.edit_text("❌ Session expired.")
    mode = query.data.replace("mode_", "")
    final_name = context.user_data.get('final_name', 'video.mkv')
    await query.message.delete()
    await process_dispatch(update, context, final_name, mode=mode)

async def process_dispatch(update, context, final_name, mode):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    from database import get_user_dump
    dump_id = get_user_dump(user_id)
    target_thread = "none"
    folder_letter = "#" 
    
    if dump_id:
        try: dump_id = int(dump_id)
        except: dump_id = None

    if dump_id:
        core_name = re.sub(r'\[.*?\]', '', final_name).replace('@lpxempire', '').strip()
        match = re.search(r'[A-Za-z0-9]', core_name)
        if match: folder_letter = match.group(0).upper() if not match.group(0).isdigit() else "#"
        thread = get_thread_id(folder_letter)
        if not thread:
            try:
                topic = await context.bot.create_forum_topic(chat_id=dump_id, name=folder_letter)
                thread = topic.message_thread_id
                save_thread_id(folder_letter, thread)
            except: pass
        target_thread = str(thread) if thread else "none"

    if mode in["hardsub", "compress"]:
        global current_github_tasks, all_tasks
        actual_sub_id = context.user_data.get('sub_id') or "none"
        settings = get_user_settings(user_id)
        
        if current_github_tasks > 0: status = await context.bot.send_message(chat_id, f"  TASK QUEUED (Cloud Node)\n  Position: #{current_github_tasks}")
        else: status = await context.bot.send_message(chat_id, "  Initializing Cloud Node...")
            
        ACTIVE_STATUS_MSGS[chat_id] = status.message_id
        
        logo_id = settings['logo_id'] or "none"
        # Only sending dump_id, logo_id and msg_id (Size/Pos not needed anymore)
        dump_id_str = f"{dump_id if dump_id else 'none'}:::{logo_id}:::{status.message_id}"

        task_data = {
            "task_type": mode, "video_id": context.user_data['mkv_id'], "sub_id": actual_sub_id,
            "rename": final_name, "chat_id": str(chat_id), "dump_id": dump_id_str, "thread_id": target_thread,
            "to_delete": context.user_data.get('to_delete',[])
        }
        
        context.user_data.clear()
        current_github_tasks += 1
        gh_task = asyncio.create_task(run_github_queue(context, task_data, status))
        all_tasks.add(gh_task)
        gh_task.add_done_callback(lambda t: all_tasks.discard(t))
    else:
        await start_local_task(update, context, final_name, dump_id, target_thread, folder_letter)

async def run_github_queue(context, data, status):
    global current_github_tasks, ACTIVE_STATUS_MSGS
    try:
        async with github_task_lock:
            if current_github_tasks == 0: return 
            
            await status.edit_text("⏳ SYSTEM: Checking Cloud Node availability...")
            await wait_for_github_free()
            
            await status.edit_text("⏳ SYSTEM: Sending payload to Cloud Engine...")
            api_payload = {k: v for k, v in data.items() if k != "to_delete"}
            success, err_msg = await trigger_github(api_payload)
            if success:
                await status.edit_text(f"✅ SENT TO CLOUD ENGINE\n◈ Mode: {data['task_type'].upper()}\n\n(Lock active until task completes)")
                await asyncio.sleep(40)
                await wait_for_github_free()
                
                await delete_messages(context.bot, int(data['chat_id']), data['to_delete'])
                ACTIVE_STATUS_MSGS.pop(int(data['chat_id']), None)
            else: await status.edit_text(f"❌ CLOUD ERROR: {err_msg}")
    except asyncio.CancelledError: pass
    except Exception as e:
        try: await status.edit_text(f"❌ System Error: {e}")
        except: pass
    finally: current_github_tasks = max(0, current_github_tasks - 1)

async def start_local_task(update, context, final_name, dump_id, target_thread, folder_letter):
    global current_active_tasks, all_tasks, ACTIVE_STATUS_MSGS
    user_id = update.effective_user.id
    msg_list = context.user_data.get('to_delete',[])
    os.makedirs("user_thumbs", exist_ok=True)
    task_id = int(time.time() * 1000)
    main_thumb = f"user_thumbs/{user_id}.jpg"
    task_thumb = f"user_thumbs/{user_id}_task_{task_id}.jpg"
    
    if os.path.exists(main_thumb): shutil.copy(main_thumb, task_thumb)
    else: task_thumb = None
    
    data = {
        'chat_id': update.effective_chat.id, 'user_id': user_id, 'mkv_id': context.user_data['mkv_id'],
        'sub_id': context.user_data.get('sub_id'), 'name': final_name, 'to_delete': msg_list, 
        'task_thumb': task_thumb, 'dump_id': dump_id, 'target_thread': target_thread, 'folder_letter': folder_letter
    }
    context.user_data.clear()
    current_active_tasks += 1
    chat_id = update.effective_chat.id
    
    if current_active_tasks > 1: status = await context.bot.send_message(chat_id, f"⏳ Local Queue Position : {current_active_tasks - 1}")
    else: status = await context.bot.send_message(chat_id, "▸ Preparing Local Engine")
        
    ACTIVE_STATUS_MSGS[chat_id] = status.message_id
    task = asyncio.create_task(run_queue(context, data, status))
    all_tasks.add(task)
    task.add_done_callback(lambda t: all_tasks.discard(t))

async def run_queue(context, data, status):
    global current_active_tasks, ACTIVE_STATUS_MSGS
    user_id = data['user_id'] 
    m_f_path, s_f_path = None, None
    try:
        async with global_task_lock:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{data['chat_id']}_{user_id}_local")]])
            try: await status.edit_text(f"📥 Downloading files to Local Engine... (Please wait)\n📦 File: `{data['name']}`", reply_markup=kb)
            except: pass
            
            tmp = os.path.abspath(f"task_{data['chat_id']}_{int(time.time())}")
            os.makedirs(tmp, exist_ok=True)
            out = os.path.join(tmp, data['name'])
            thumb_path = os.path.join(tmp, "thumb.jpg")
            has_thumb = False
            if data.get('task_thumb') and os.path.exists(data.get('task_thumb')):
                shutil.copy(data.get('task_thumb'), thumb_path)
                has_thumb = True
            
            try:
                m_f = await context.bot.get_file(data['mkv_id'], read_timeout=3600)
                m_f_path = m_f.file_path
                if data['sub_id']:
                    s_f = await context.bot.get_file(data['sub_id'], read_timeout=3600)
                    s_f_path = s_f.file_path
                
                success = await mux_video(
                    mkv_path=m_f_path, sub_path=s_f_path, output_path=out, chat_id=data['chat_id'], 
                    status_msg=status, file_name=data['name'], user_id=data['user_id']
                )

                if success:
                    if not has_thumb: has_thumb = await extract_thumbnail(out, thumb_path)
                    await status.edit_text("📤 Uploading file to Telegram...")
                    thumb_file = open(thumb_path, 'rb') if has_thumb else None
                    target_chat = data['dump_id'] if data['dump_id'] else data['chat_id']
                    thread = int(data['target_thread']) if data['target_thread'] != "none" else None
                    
                    try:
                        await context.bot.send_document(
                            chat_id=target_chat, message_thread_id=thread,
                            document=f"file://{out}", thumbnail=thumb_file, caption=f"✅ MUXING COMPLETE",
                            read_timeout=7200, write_timeout=7200
                        )
                        if str(target_chat) != str(data['chat_id']):
                            await context.bot.send_message(chat_id=data['chat_id'], text=f"✅ Muxing Complete!\n\nFile dumped to `{data['folder_letter']}` folder.")
                    finally:
                        if thumb_file: thumb_file.close()
                    
                await delete_messages(context.bot, data['chat_id'], data['to_delete'])
                try: await status.delete()
                except: pass
                ACTIVE_STATUS_MSGS.pop(int(data['chat_id']), None)
            except asyncio.CancelledError:
                await delete_messages(context.bot, data['chat_id'], data['to_delete'])
            except Exception as e:
                try: await status.edit_text(f"❌ Error: {e}")
                except: pass
    finally:
        current_active_tasks = max(0, current_active_tasks - 1)
        if data.get('task_thumb') and os.path.exists(data.get('task_thumb')):
            try: os.remove(data.get('task_thumb'))
            except: pass
        clean_temp_files(tmp)
        if m_f_path and os.path.exists(m_f_path):
            try: os.remove(m_f_path)
            except: pass
        if s_f_path and os.path.exists(s_f_path):
            try: os.remove(s_f_path)
            except: pass

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is awake!")
    def log_message(self, format, *args): pass

def run_dummy_server():
    try: HTTPServer(('0.0.0.0', PORT), PingHandler).serve_forever()
    except: pass

def main():
    init_db()
    init_bot_db() 
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    app = ApplicationBuilder().token(BOT_TOKEN).base_url("http://127.0.0.1:8081/bot").local_mode(True).build()
    app.add_handler(TypeHandler(Update, check_access), group=-2)
    app.add_handler(TypeHandler(Update, block_duplicates), group=-1)
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("autorename", cmd_autorename))
    app.add_handler(CommandHandler("setlogo", cmd_setlogo))
    app.add_handler(CommandHandler("showlogo", cmd_showlogo))
    app.add_handler(CommandHandler("extract", cmd_extract))
    app.add_handler(CommandHandler("compress", cmd_compress))
    app.add_handler(CommandHandler("dthumb", cmd_dthumb))
    app.add_handler(CommandHandler("setdump", cmd_setdump))
    app.add_handler(CommandHandler("deldump", cmd_deldump))
    app.add_handler(CommandHandler("clear", cmd_clear))
    
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, handle_docs))
    
    app.add_handler(CallbackQueryHandler(mode_selection_cb, pattern=r"^mode_"))
    app.add_handler(CallbackQueryHandler(do_extract_cb, pattern=r"^ext_"))
    app.add_handler(CallbackQueryHandler(logo_cb, pattern=r"^logo_"))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern=r"^cancel_"))
    
    print("🤖 System Online & Protected. Bot polling started.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
