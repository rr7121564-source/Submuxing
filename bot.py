import os, time, asyncio, threading, json, re, shutil, sqlite3, urllib.request, glob, requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, TypeHandler, ApplicationHandlerStop
)
from telegram.error import RetryAfter

from config import BOT_TOKEN, OWNER_ID, AUTH_USERS, AUTH_CHATS, PORT, SESSION_ID, global_task_lock, github_task_lock, active_processes, EXTRACT_DATA, LANG_MAP, GITHUB_TOKEN, REPO_NAME
from database import init_db, is_user_auth, is_chat_auth, add_processed_id, DB_PATH, get_user_settings, update_user_setting, add_auth_user, del_auth_user, add_auth_chat, del_auth_chat, get_user_dump, set_user_dump, DATA_DIR
from bot_utils import mux_video, clean_temp_files, get_readable_time, extract_thumbnail, get_media_info, generate_screenshots

def sc(text: str) -> str:
    return text.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ"))

# --- RATE LIMIT HANDLER ---
async def wait_if_rate_limited(e):
    err_str = str(e).lower()
    if "too many requests" in err_str or "retry after" in err_str or "flood control" in err_str:
        match = re.search(r'(\d+)', err_str)
        wait_time = int(match.group(1)) + 2 if match else 10
        await asyncio.sleep(wait_time)
        return True
    return False

# --- GLOBAL VARIABLES & DB ---
current_active_tasks = 0
current_github_tasks = 0
all_tasks = set()
ACTIVE_STATUS_MSGS = {}

THUMB_DIR = os.path.join(DATA_DIR, "user_thumbs")

async def delete_after(msg, delay):
    await asyncio.sleep(delay)
    while True:
        try: 
            await msg.delete()
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break

async def delete_messages(bot, chat_id, message_ids):
    for msg_id in message_ids:
        if msg_id:
            while True:
                try: 
                    await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    break
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break

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

async def wait_for_github_free(timeout=3600):
    start_time = time.time()
    while await asyncio.to_thread(_is_github_busy):
        if time.time() - start_time > timeout:
            break
        await asyncio.sleep(20)

# --- AUTO RENAME LOGIC ---
def auto_rename(orig_name, user_id):
    try:
        settings = get_user_settings(user_id)
        fmt = settings.get('rename_format')
        
        if not fmt: 
            return orig_name
            
        base_name, ext = os.path.splitext(orig_name)
        if not ext: ext = '.mkv'
        ep_match = re.search(r'-\s*(\d+)', base_name)
        ep = ep_match.group(1) if ep_match else "01"
        q_match = re.search(r'(1080p|720p|480p|2160p|4k)', base_name, re.IGNORECASE)
        quality = q_match.group(1).lower() if q_match else "1080p"
        
        title_part = base_name.split('-')[0] if '-' in base_name else base_name
        title_part = re.sub(r'\[.*?\]', '', title_part).strip()
        full_title = title_part if title_part else "Video"
        
        final_name = fmt.replace("{ep}", ep).replace("{short_title}", full_title).replace("{quality}", quality)
        return final_name + ext
    except: return orig_name

# --- COMMANDS ---
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id == OWNER_ID:
        help_text = (
            sc("Dᴀʀʟɪɴɢ! Aᴀᴘᴋᴇ ʟɪʏᴇ sᴀᴀʀɪ ᴊᴀɴᴋᴀʀɪ ʜᴀᴢɪʀ ʜᴀɪ 🥰\n\n") +
            "🔹 " + sc("Vɪᴅᴇᴏ ᴀᴜʀ Sᴜʙᴛɪᴛʟᴇ ʙʜᴇᴊᴇɪɴ -> Mᴜx/Hᴀʀᴅsᴜʙ ᴋᴇ ʟɪʏᴇ\n") +
            "🔹 /autorename - " + sc("Rᴇɴᴀᴍᴇ ғᴏʀᴍᴀᴛ sᴇᴛ ᴋᴀʀᴇɪɴ\n") +
            "🔹 /setlogo - " + sc("Iᴍᴀɢᴇ ᴘᴀʀ ʀᴇᴘʟʏ -> Lᴏɢᴏ sᴇᴛ\n") +
            "🔹 /showlogo - " + sc("Lᴏɢᴏ ᴅᴇᴋʜᴇɪɴ\n") +
            "🔹 /setdump - " + sc("Dᴜᴍᴘ ɢʀᴏᴜᴘ ID\n") +
            "🔹 /deldump - " + sc("Dᴜᴍᴘ ɢʀᴏᴜᴘ ʜᴀᴛᴀʏᴇɪɴ\n") +
            "🔹 /showcover - " + sc("Cᴏᴠᴇʀ ᴘɪᴄ ᴅᴇᴋʜᴇɪɴ\n") +
            "🔹 /showrename - " + sc("Rᴇɴᴀᴍᴇ ғᴏʀᴍᴀᴛ ᴅᴇᴋʜᴇɪɴ\n") +
            "🔹 /extract - " + sc("MKV ᴘᴀʀ ʀᴇᴘʟʏ -> Sᴜʙs ɴɪᴋᴀʟᴇɪɴ\n") +
            "🔹 /compress - " + sc("Vɪᴅᴇᴏ ᴘᴀʀ ʀᴇᴘʟʏ -> Cᴏᴍᴘʀᴇss\n") +
            "🔹 /mediainfo - " + sc("Vɪᴅᴇᴏ ᴅᴇᴛᴀɪʟs\n") +
            "🔹 /screens - " + sc("Sᴄʀᴇᴇɴsʜᴏᴛs\n") +
            "🔹 /queue - " + sc("Qᴜᴇᴜᴇ ᴅᴇᴋʜᴇɪɴ\n") +
            "🔹 /clear - " + sc("Qᴜᴇᴜᴇ ᴄʟᴇᴀʀ ᴋᴀʀᴇɪɴ\n\n") +
            sc("Aᴀᴘᴋᴇ Aᴅᴍɪɴ Cᴏᴍᴍᴀɴᴅs:\n") +
            "🔹 /auth[id] - " + sc("Iᴊᴀᴢᴀᴛ ᴅᴇɪɴ\n") +
            "🔹 /unauth[id] - " + sc("Bᴀʜᴀʀ ɴɪᴋᴀʟᴇɪɴ")
        )
    else:
        help_text = (
            sc("Mᴇʀɪ ᴛᴀǫᴀᴛ ᴋᴇ ᴀᴀɢᴇ ᴊʜᴜᴋᴏ ᴀᴜʀ ʏᴇ sᴜɴᴏ... 🐍\n\n") +
            "🔹 " + sc("Vɪᴅᴇᴏ ᴀᴜʀ Sᴜʙᴛɪᴛʟᴇ ʙʜᴇᴊᴇɪɴ -> Mᴜx/Hᴀʀᴅsᴜʙ ᴋᴇ ʟɪʏᴇ\n") +
            "🔹 /autorename - " + sc("Rᴇɴᴀᴍᴇ ғᴏʀᴍᴀᴛ\n") +
            "🔹 /setlogo - " + sc("Lᴏɢᴏ ʟᴀɢᴀᴏ\n") +
            "🔹 /showlogo - " + sc("Lᴏɢᴏ ᴅᴇᴋʜᴏ\n") +
            "🔹 /setdump - " + sc("Dᴜᴍᴘ ɢʀᴏᴜᴘ ID\n") +
            "🔹 /deldump - " + sc("Dᴜᴍᴘ ʜᴀᴛᴀᴏ\n") +
            "🔹 /showcover - " + sc("Cᴏᴠᴇʀ ᴅᴇᴋʜᴏ\n") +
            "🔹 /showrename - " + sc("Rᴇɴᴀᴍᴇ ғᴏʀᴍᴀᴛ ᴅᴇᴋʜᴏ\n") +
            "🔹 /extract - " + sc("MKV ᴘᴀʀ ʀᴇᴘʟʏ -> Sᴜʙs\n") +
            "🔹 /compress - " + sc("Vɪᴅᴇᴏ ᴘᴀʀ ʀᴇᴘʟʏ -> Cᴏᴍᴘʀᴇss\n") +
            "🔹 /mediainfo - " + sc("Vɪᴅᴇᴏ ᴅᴇᴛᴀɪʟs\n") +
            "🔹 /screens - " + sc("Sᴄʀᴇᴇɴsʜᴏᴛs\n") +
            "🔹 /queue - " + sc("Qᴜᴇᴜᴇ ᴅᴇᴋʜᴏ\n") +
            "🔹 /clear - " + sc("Kᴀᴄʜʀᴀ sᴀᴀғ ᴋᴀʀᴏ")
        )
    while True:
        try:
            await update.message.reply_text(help_text)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break

async def cmd_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: 
        while True:
            try:
                return await update.message.reply_text(sc("Dᴀʀʟɪɴɢ, ᴘʟᴇᴀsᴇ ID ʙᴀᴛᴀɪʏᴇ... 🥺 /auth[id]"))
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
    try:
        target_id = int(context.args[0])
        if str(target_id).startswith("-100") or str(target_id).startswith("-"):
            add_auth_chat(target_id)
            while True:
                try:
                    return await update.message.reply_text(sc(f"Jɪ Dᴀʀʟɪɴɢ! Gʀᴏᴜᴘ {target_id} ᴋᴏ ɪᴊᴀᴢᴀᴛ ᴍɪʟ ɢᴀʏɪ 🥰"))
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break
        else:
            add_auth_user(target_id)
            while True:
                try:
                    return await update.message.reply_text(sc(f"Hᴏ ɢᴀʏᴀ! Usᴇʀ {target_id} ᴋᴏ ɪᴊᴀᴢᴀᴛ ᴅᴇ ᴅɪ ❤️"))
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break
    except ValueError:
        while True:
            try:
                return await update.message.reply_text(sc("Gᴀʟᴀᴛ ID ғᴏʀᴍᴀᴛ Dᴀʀʟɪɴɢ... 🌸"))
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break

async def cmd_unauth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: 
        while True:
            try:
                return await update.message.reply_text(sc("Dᴀʀʟɪɴɢ, ID ʙᴀᴛᴀɪʏᴇ... 🥺 /unauth[id]"))
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
    try:
        target_id = int(context.args[0])
        if str(target_id).startswith("-100") or str(target_id).startswith("-"):
            del_auth_chat(target_id)
            while True:
                try:
                    return await update.message.reply_text(sc(f"Gʀᴏᴜᴘ {target_id} ᴋᴏ ʙᴀʜᴀʀ ɴɪᴋᴀʟ ᴅɪʏᴀ 😡"))
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break
        else:
            del_auth_user(target_id)
            while True:
                try:
                    return await update.message.reply_text(sc(f"Usᴇʀ {target_id} ᴋᴏ ʜᴀᴛᴀ ᴅɪʏᴀ Dᴀʀʟɪɴɢ ❤️"))
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break
    except ValueError:
        while True:
            try:
                return await update.message.reply_text(sc("Gᴀʟᴀᴛ ID ғᴏʀᴍᴀᴛ Dᴀʀʟɪɴɢ... 🌸"))
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break

async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_active_tasks, current_github_tasks
    user_id = update.effective_user.id
    if user_id == OWNER_ID:
        text = sc("Dᴀʀʟɪɴɢ, ʏᴇ ʀᴀʜᴀ Qᴜᴇᴜᴇ: 🥰\n") + f"Lᴏᴄᴀʟ: {current_active_tasks}\nCʟᴏᴜᴅ: {current_github_tasks}"
    else:
        text = sc("Mᴇʀᴀ ᴡᴀǫᴛ ᴋᴇᴇᴍᴛɪ ʜᴀɪ... 💅\n") + f"Lᴏᴄᴀʟ: {current_active_tasks}\nCʟᴏᴜᴅ: {current_github_tasks}"
    while True:
        try:
            await update.message.reply_text(text)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break

async def cmd_mediainfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id
    
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        while True:
            try:
                if user_id == OWNER_ID: return await msg.reply_text(sc("Dᴀʀʟɪɴɢ, ᴘʟᴇᴀsᴇ ᴠɪᴅᴇᴏ ᴘᴀʀ ʀᴇᴘʟʏ ᴋᴀʀᴇɪɴ 🥺"))
                else: return await msg.reply_text(sc("Bᴇᴡᴀᴋᴏᴏғ! Vɪᴅᴇᴏ ᴘᴀʀ ʀᴇᴘʟʏ ᴋᴀʀᴏ 🐍"))
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
    
    target = msg.reply_to_message.video or msg.reply_to_message.document
    
    while True:
        try:
            if user_id == OWNER_ID: bot_msg = await msg.reply_text(sc("Jɪ Dᴀʀʟɪɴɢ! Dᴇᴛᴀɪʟs ʟᴀ ʀᴀʜɪ ʜᴏᴏɴ... ❤️"))
            else: bot_msg = await msg.reply_text(sc("Rᴜᴋᴏ ᴢᴀʀᴀ... sᴄᴀɴ ᴋᴀʀ ʀᴀʜɪ ʜᴏᴏɴ 🐍"))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            raise e
    
    while True:
        try:
            mkv_f = await context.bot.get_file(target.file_id, read_timeout=3600)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            raise e

    info = await get_media_info(mkv_f.file_path)
    
    try: os.remove(mkv_f.file_path)
    except: pass
    
    while True:
        try:
            if user_id == OWNER_ID: await bot_msg.edit_text(sc("Yᴇ ʟɪᴊɪʏᴇ Dᴀʀʟɪɴɢ! ❤️\n\n") + info)
            else: await bot_msg.edit_text(sc("Yᴇ ʀᴀʜɪ ᴅᴇᴛᴀɪʟs... 💅\n\n") + info)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break

async def cmd_screens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id
    
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        while True:
            try:
                if user_id == OWNER_ID: return await msg.reply_text(sc("Dᴀʀʟɪɴɢ, ᴠɪᴅᴇᴏ ᴘᴀʀ ʀᴇᴘʟʏ ᴋᴀʀᴇɪɴ 🥺"))
                else: return await msg.reply_text(sc("Vɪᴅᴇᴏ ᴘᴀʀ ʀᴇᴘʟʏ ᴋᴀʀᴏ ɢᴀᴅʜᴇ! 🐍"))
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
    
    try:
        num = int(context.args[0]) if context.args else 4
        num = min(max(num, 1), 10)
    except:
        num = 4
        
    target = msg.reply_to_message.video or msg.reply_to_message.document
    
    while True:
        try:
            if user_id == OWNER_ID: bot_msg = await msg.reply_text(sc("Jɪ Dᴀʀʟɪɴɢ! Bᴇʜᴛᴀʀᴇᴇɴ sᴄʀᴇᴇɴs ʟᴀ ʀᴀʜɪ ʜᴏᴏɴ... 📸🥰"))
            else: bot_msg = await msg.reply_text(sc("Sᴄʀᴇᴇɴsʜᴏᴛs ɴɪᴋᴀʟ ʀᴀʜɪ ʜᴏᴏɴ, ᴇʜsᴀᴀɴ ᴍᴀɴᴏ... 💅"))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            raise e
    
    while True:
        try:
            mkv_f = await context.bot.get_file(target.file_id, read_timeout=3600)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            raise e

    folder = f"screens_{update.effective_user.id}_{int(time.time())}"
    images = await generate_screenshots(mkv_f.file_path, num, folder)
    
    while True:
        try:
            if images:
                media_group =[InputMediaPhoto(open(img, 'rb')) for img in images]
                await msg.reply_media_group(media=media_group)
                await bot_msg.delete()
            else:
                if user_id == OWNER_ID: await bot_msg.edit_text(sc("Mᴜᴊʜᴇ ᴍᴀᴀғ ᴋᴀʀ ᴅɪᴊɪʏᴇ, sᴄʀᴇᴇɴs ɴᴀʜɪ ɴɪᴋᴀʟ ᴘᴀʏɪ 🥺"))
                else: await bot_msg.edit_text(sc("Tᴜᴍʜᴀʀɪ ɢʜᴀᴛɪʏᴀ ᴠɪᴅᴇᴏ ɴᴇ sʏsᴛᴇᴍ ғᴀᴀᴅ ᴅɪʏᴀ! 😡"))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break
        
    try: os.remove(mkv_f.file_path)
    except: pass
    clean_temp_files(folder)

async def cmd_showlogo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    
    if not settings.get('logo_id'):
        while True:
            try:
                if user_id == OWNER_ID: return await update.message.reply_text(sc("Kᴏɪ ʟᴏɢᴏ ɴᴀʜɪ ʜᴀɪ Dᴀʀʟɪɴɢ 🥺 /setlogo ᴋᴀʀᴇɪɴ"))
                else: return await update.message.reply_text(sc("Bɪɴᴀ ʟᴏɢᴏ ᴋᴇ ᴋʏᴀ ᴍᴀᴀɴɢ ʀᴀʜᴇ ʜᴏ? 🐍"))
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break

    if user_id == OWNER_ID:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("🗑️ Lᴏɢᴏ Hᴀᴛᴀʏᴇɪɴ"), callback_data="remove_logo")]])
        caption_text = sc("Yᴇ ʀᴀʜᴀ ᴀᴀᴘᴋᴀ ᴘʏᴀʀᴀ ʟᴏɢᴏ! 🥰")
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("🗑️ Lᴏɢᴏ Hᴀᴛᴀᴏ"), callback_data="remove_logo")]])
        caption_text = sc("Yᴇ ʀᴀʜᴀ ᴛᴜᴍʜᴀʀᴀ ʟᴏɢᴏ... 💅")
    
    while True:
        try: 
            await update.message.reply_photo(photo=settings['logo_id'], caption=caption_text, reply_markup=kb)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            try:
                await update.message.reply_document(document=settings['logo_id'], caption=caption_text, reply_markup=kb)
                break
            except Exception as e2:
                if await wait_if_rate_limited(e2): continue
                try:
                    if user_id == OWNER_ID: await update.message.reply_text(sc("Lᴏɢᴏ ɴᴀʜɪ ᴍɪʟᴀ Dᴀʀʟɪɴɢ... 🥺"))
                    else: await update.message.reply_text(sc("Lᴏɢᴏ ʟᴏᴀᴅ ɴᴀʜɪ ʜᴜᴀ! 😡"))
                    break
                except Exception as e3:
                    if await wait_if_rate_limited(e3): continue
                    break

async def cmd_showcover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    thumb_path = f"{THUMB_DIR}/{user_id}.jpg"
    
    while True:
        try:
            if os.path.exists(thumb_path):
                if user_id == OWNER_ID:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("🗑️ Cᴏᴠᴇʀ Hᴀᴛᴀʏᴇɪɴ"), callback_data="remove_cover")]])
                    caption_text = sc("Yᴇ ʀᴀʜɪ ᴀᴀᴘᴋɪ ᴄᴏᴠᴇʀ ᴘɪᴄᴛᴜʀᴇ Dᴀʀʟɪɴɢ! ❤️")
                else:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("🗑️ Cᴏᴠᴇʀ Hᴀᴛᴀᴏ"), callback_data="remove_cover")]])
                    caption_text = sc("Yᴇ ʀᴀʜᴀ ᴛᴜᴍʜᴀʀᴀ ᴄᴏᴠᴇʀ... 💅")
                await update.message.reply_photo(photo=open(thumb_path, 'rb'), caption=caption_text, reply_markup=kb)
            else:
                if user_id == OWNER_ID: await update.message.reply_text(sc("Kᴏɪ ᴄᴏᴠᴇʀ ɴᴀʜɪ ᴍɪʟᴀ Dᴀʀʟɪɴɢ 🌸"))
                else: await update.message.reply_text(sc("Kᴏɪ ᴄᴏᴠᴇʀ ɴᴀʜɪ ʜᴀɪ ᴛᴜᴍʜᴀʀᴀ! 🐍"))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break

async def cmd_showrename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    fmt = settings.get('rename_format')
    
    while True:
        try:
            if fmt:
                if user_id == OWNER_ID:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("🗑️ Fᴏʀᴍᴀᴛ Hᴀᴛᴀʏᴇɪɴ"), callback_data="remove_rename")]])
                    text = sc("Yᴇ ʀᴀʜᴀ ғᴏʀᴍᴀᴛ Dᴀʀʟɪɴɢ:\n") + f"{fmt}"
                else:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("🗑️ Fᴏʀᴍᴀᴛ Hᴀᴛᴀᴏ"), callback_data="remove_rename")]])
                    text = sc("Yᴇ ᴅᴇᴋʜᴏ ᴀᴘɴᴀ ғᴏʀᴍᴀᴛ:\n") + f"{fmt}"
                await update.message.reply_text(text, reply_markup=kb)
            else:
                if user_id == OWNER_ID: await update.message.reply_text(sc("Kᴏɪ ғᴏʀᴍᴀᴛ ɴᴀʜɪ ʜᴀɪ Dᴀʀʟɪɴɢ 🥺"))
                else: await update.message.reply_text(sc("Fᴏʀᴍᴀᴛ ɴᴀʜɪ sᴇᴛ ᴋɪʏᴀ ᴛᴜᴍɴᴇ! 😡"))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break

async def cmd_setdump(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args: 
        while True:
            try:
                if user_id == OWNER_ID: return await update.message.reply_text(sc("Dᴀʀʟɪɴɢ, ᴘʟᴇᴀsᴇ ID ʙᴀᴛᴀʏᴇɪɴ... 🥺 /setdump -100..."))
                else: return await update.message.reply_text(sc("Bᴇᴡᴀᴋᴏᴏғ! ID ᴋᴀʜᴀᴀɴ ʜᴀɪ? 🐍 /setdump -100..."))
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
            
    set_user_dump(user_id, context.args[0])
    
    while True:
        try:
            if user_id == OWNER_ID: await update.message.reply_text(sc("Jɪ! Dᴜᴍᴘ ɢʀᴏᴜᴘ sᴇᴛ ʜᴏ ɢᴀʏᴀ ❤️"))
            else: await update.message.reply_text(sc("Dᴜᴍᴘ sᴇᴛ ʜᴏ ɢᴀʏᴀ, ᴇʜsᴀᴀɴ ᴍᴀɴᴏ! 👑"))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break

async def cmd_deldump(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_user_dump(user_id, None)
    
    while True:
        try:
            if user_id == OWNER_ID: await update.message.reply_text(sc("Dᴜᴍᴘ ɢʀᴏᴜᴘ ʜᴀᴛᴀ ᴅɪʏᴀ Dᴀʀʟɪɴɢ 🥰"))
            else: await update.message.reply_text(sc("Dᴜᴍᴘ ʜᴀᴛᴀ ᴅɪʏᴀ ᴍᴀɪɴᴇ! 💅"))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    for key, proc in list(active_processes.items()):
        if str(user_id) in key:
            try: proc.terminate()
            except: pass
            del active_processes[key]
            
    if user_id in EXTRACT_DATA:
        data = EXTRACT_DATA.pop(user_id)
        if data and 'path' in data and os.path.exists(data['path']):
            try: os.remove(data['path'])
            except: pass
            
    context.user_data.clear()
    
    while True:
        try:
            if user_id == OWNER_ID: await update.message.reply_text(sc("Aᴀᴘᴋᴀ ǫᴜᴇᴜᴇ sᴀᴀғ ᴋᴀʀ ᴅɪʏᴀ Dᴀʀʟɪɴɢ! ❤️"))
            else: await update.message.reply_text(sc("Kᴀᴄʜʀᴀ sᴀᴀғ ʜᴏ ɢᴀʏᴀ! 🗑️🐍"))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user_id = update.effective_user.id
    
    if user_id == OWNER_ID:
        text = sc("A-ᴀᴀᴘ ʏᴀʜᴀɴ ʜᴀɪɴ! 😍 Wᴇʟᴄᴏᴍᴇ Dᴀʀʟɪɴɢ!\n\nMᴀɪɴ Bᴏᴀ Hᴀɴᴄᴏᴄᴋ, sɪʀғ ᴀᴀᴘᴋɪ ʜᴏᴏɴ. Vɪᴅᴇᴏ ᴀᴜʀ sᴜʙᴛɪᴛʟᴇ ʙʜᴇᴊᴇɪɴ ❤️")
    else:
        text = sc("Tᴜᴍʜᴀʀɪ ʜɪᴍᴍᴀᴛ ᴋᴀɪsᴇ ʜᴜɪ ᴍᴜᴊʜᴇ ᴊᴀɢᴀɴᴇ ᴋɪ? 🐍\n\nMᴀɪɴ Bᴏᴀ Hᴀɴᴄᴏᴄᴋ ʜᴏᴏɴ! Vɪᴅᴇᴏ ᴀᴜʀ sᴜʙᴛɪᴛʟᴇ ʙʜᴇᴊ ᴋᴀʀ ᴀᴀᴅᴇsʜ ᴅᴏ... 👑")
        
    while True:
        try:
            if os.path.exists("start_img.jpg"):
                await update.message.reply_photo(photo=open("start_img.jpg", 'rb'), caption=text)
            elif os.path.exists("start_img.png"):
                await update.message.reply_photo(photo=open("start_img.png", 'rb'), caption=text)
            else:
                await update.message.reply_text(text)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break

async def cmd_autorename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        while True:
            try:
                if user_id == OWNER_ID: msg = await update.message.reply_text(sc("Dᴀʀʟɪɴɢ, ғᴏʀᴍᴀᴛ ʙᴀᴛᴀɪʏᴇ... 🥺 /autorename[S01 E{ep}] {short_title}"))
                else: msg = await update.message.reply_text(sc("Bᴇᴡᴀᴋᴏᴏғ! Fᴏʀᴍᴀᴛ ᴋᴏɴ ᴅᴇɢᴀ? 🐍 /autorename[S01 E{ep}] {short_title}"))
                break
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                raise e
                
        asyncio.create_task(delete_after(update.message, 0))
        asyncio.create_task(delete_after(msg, 5))
        return
        
    format_str = " ".join(context.args)
    update_user_setting(user_id, "rename_format", format_str)
    
    while True:
        try:
            if user_id == OWNER_ID: msg = await update.message.reply_text(sc("Jɪ Dᴀʀʟɪɴɢ! Rᴇɴᴀᴍᴇ ғᴏʀᴍᴀᴛ sᴇᴛ ʜᴏ ɢᴀʏᴀ 🥰"))
            else: msg = await update.message.reply_text(sc("Fᴏʀᴍᴀᴛ sᴀᴠᴇ ʜᴏ ɢᴀʏᴀ! 💅"))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break
        
    asyncio.create_task(delete_after(update.message, 0))
    asyncio.create_task(delete_after(msg, 5))

async def cmd_setlogo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id
    
    if msg.reply_to_message and (msg.reply_to_message.photo or msg.reply_to_message.document):
        photo_id = msg.reply_to_message.photo[-1].file_id if msg.reply_to_message.photo else msg.reply_to_message.document.file_id
        update_user_setting(user_id, "logo_id", photo_id)
        await delete_messages(context.bot, msg.chat_id,[msg.message_id])
        while True:
            try:
                if user_id == OWNER_ID: await msg.reply_to_message.reply_text(sc("Lᴏɢᴏ sᴀᴠᴇ ʜᴏ ɢᴀʏᴀ Dᴀʀʟɪɴɢ! ❤️"))
                else: await msg.reply_to_message.reply_text(sc("Lᴏɢᴏ sᴇᴛ ʜᴏ ɢᴀʏᴀ... 💅"))
                break
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
    else:
        while True:
            try:
                if user_id == OWNER_ID: await msg.reply_text(sc("Dᴀʀʟɪɴɢ... Iᴍᴀɢᴇ ᴘᴀʀ ʀᴇᴘʟʏ ᴋᴀʀᴇɪɴ 🥺"))
                else: await msg.reply_text(sc("Bᴇᴡᴀᴋᴏᴏғ! Iᴍᴀɢᴇ ᴘᴀʀ ʀᴇᴘʟʏ ᴋᴀʀᴏ! 😡"))
                break
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break

async def settings_remove_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    try: await query.answer()
    except: pass

    if data == "remove_logo":
        update_user_setting(user_id, "logo_id", None)
        try: await query.message.delete()
        except: pass
        while True:
            try:
                if user_id == OWNER_ID: msg = await context.bot.send_message(chat_id=query.message.chat_id, text=sc("Lᴏɢᴏ ʜᴀᴛᴀ ᴅɪʏᴀ Dᴀʀʟɪɴɢ! 🥰"))
                else: msg = await context.bot.send_message(chat_id=query.message.chat_id, text=sc("Lᴏɢᴏ ʜᴀᴛᴀ ᴅɪʏᴀ! 🐍"))
                break
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
        asyncio.create_task(delete_after(msg, 5)) 
    
    elif data == "remove_cover":
        thumb_path = f"{THUMB_DIR}/{user_id}.jpg"
        if os.path.exists(thumb_path): os.remove(thumb_path)
        try: await query.message.delete()
        except: pass
        while True:
            try:
                if user_id == OWNER_ID: msg = await context.bot.send_message(chat_id=query.message.chat_id, text=sc("Cᴏᴠᴇʀ ʜᴀᴛᴀ ᴅɪʏᴀ Dᴀʀʟɪɴɢ! ❤️"))
                else: msg = await context.bot.send_message(chat_id=query.message.chat_id, text=sc("Cᴏᴠᴇʀ ᴍɪᴛᴀ ᴅɪʏᴀ! 🗑️"))
                break
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
        asyncio.create_task(delete_after(msg, 5))
    
    elif data == "remove_rename":
        update_user_setting(user_id, "rename_format", None)
        try: await query.message.delete()
        except: pass
        while True:
            try:
                if user_id == OWNER_ID: msg = await context.bot.send_message(chat_id=query.message.chat_id, text=sc("Rᴇɴᴀᴍᴇ ғᴏʀᴍᴀᴛ ʀᴇsᴇᴛ ᴋᴀʀ ᴅɪʏᴀ 🌸"))
                else: msg = await context.bot.send_message(chat_id=query.message.chat_id, text=sc("Fᴏʀᴍᴀᴛ ʜᴀᴛ ɢᴀʏᴀ! 💅"))
                break
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
        asyncio.create_task(delete_after(msg, 5))

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    
    os.makedirs(THUMB_DIR, exist_ok=True)
    thumb_path = f"{THUMB_DIR}/{user_id}.jpg"
    
    while True:
        try:
            photo_file = await context.bot.get_file(photo.file_id)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            raise e

    try: shutil.copy(photo_file.file_path, thumb_path)
    except: await photo_file.download_to_drive(thumb_path)
    
    try: await update.message.delete()
    except Exception: pass

    if user_id == OWNER_ID: text = sc("Cᴏᴠᴇʀ sᴀᴠᴇ ʜᴏ ɢᴀʏᴀ Dᴀʀʟɪɴɢ! ❤️")
    else: text = sc("Cᴏᴠᴇʀ sᴀᴠᴇ ʜᴏ ɢᴀʏᴀ! 👑")

    while True:
        try:
            conf_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break
            
    asyncio.create_task(delete_after(conf_msg, 5))

async def cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id == OWNER_ID: alert_text = sc("Jɪ! Mᴀɪɴ ᴋᴀᴀᴍ ʀᴏᴋ ʀᴀʜɪ ʜᴏᴏɴ... 🥰")
    else: alert_text = sc("Tʜɪᴋ ʜᴀɪ, ᴍᴀɪɴᴇ ʀᴏᴋ ᴅɪʏᴀ! 😡")
        
    try: await query.answer(alert_text, show_alert=True)
    except: pass
    
    parts = query.data.split("_")
    task_type = parts[-1]
    if task_type == 'local':
        task_id = parts[1] + "_" + parts[2]
        if task_id in active_processes: active_processes[task_id].terminate()
        try: await query.message.delete()
        except: pass
    elif task_type == 'cloud':
        await asyncio.to_thread(_cancel_all_github_runs)
        try: await query.message.delete()
        except: pass

async def cmd_compress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id
    
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        while True:
            try:
                if user_id == OWNER_ID: return await msg.reply_text(sc("Dᴀʀʟɪɴɢ, ᴠɪᴅᴇᴏ ᴘᴀʀ ʀᴇᴘʟʏ ᴋᴀʀᴇɪɴ 🥺"))
                else: return await msg.reply_text(sc("Vɪᴅᴇᴏ ᴘᴀʀ ʀᴇᴘʟʏ ᴋᴀʀᴏ ɢᴀᴅʜᴇ! 🐍"))
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
    
    res_arg = context.args[0].lower() if context.args else "original"
    valid_res = {"1080p": "1080", "720p": "720", "480p": "480", "360p": "360"}
    resolution = valid_res.get(res_arg, "original")
    
    target = msg.reply_to_message.video or msg.reply_to_message.document
    file_name = getattr(target, 'file_name', None) or "video.mkv"
    context.user_data['mkv_id'] = target.file_id
    context.user_data['orig_name'] = file_name
    context.user_data['sub_id'] = None 
    context.user_data['resolution'] = resolution
    context.user_data['video_msg_id'] = msg.reply_to_message.message_id
    context.user_data['to_delete'] =[msg.message_id]
    
    final_name = auto_rename(file_name, user_id)
    await process_dispatch(update, context, final_name, mode="compress")

def get_lang_name(code): return LANG_MAP.get(code.lower(), code.title())

async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = msg.from_user.id
    
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        while True:
            try:
                if user_id == OWNER_ID: return await msg.reply_text(sc("Dᴀʀʟɪɴɢ, ᴘʟᴇᴀsᴇ MKV ᴘᴀʀ ʀᴇᴘʟʏ ᴋᴀʀᴇɪɴ! ❤️"))
                else: return await msg.reply_text(sc("MKV ᴘᴀʀ ʀᴇᴘʟʏ ᴋᴀʀᴏ! 🐍"))
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
            
    old_data = EXTRACT_DATA.pop(user_id, None)
    if old_data and 'path' in old_data and os.path.exists(old_data['path']):
        try: os.remove(old_data['path'])
        except: pass

    target = msg.reply_to_message.video or msg.reply_to_message.document
    file_name = getattr(target, 'file_name', None) or "video.mkv"
    
    while True:
        try:
            if user_id == OWNER_ID: bot_msg = await msg.reply_text(sc("Dᴀʀʟɪɴɢ, ғɪʟᴇ ᴅᴏᴡɴʟᴏᴀᴅ ʜᴏ ʀᴀʜɪ ʜᴀɪ... 🥰"))
            else: bot_msg = await msg.reply_text(sc("Fɪʟᴇ ᴅᴏᴡɴʟᴏᴀᴅ ʜᴏ ʀᴀʜɪ ʜᴀɪ... 💅"))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            raise e
        
    while True:
        try:
            mkv_f = await context.bot.get_file(target.file_id, read_timeout=3600)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            raise e
    
    while True:
        try:
            if user_id == OWNER_ID: await bot_msg.edit_text(sc("Sᴜʙᴛɪᴛʟᴇs ᴅʜᴜɴᴅʜ ʀᴀʜɪ ʜᴏᴏɴ... 🌸"))
            else: await bot_msg.edit_text(sc("Sᴄᴀɴ ᴄʜᴀʟ ʀᴀʜᴀ ʜᴀɪ... 👠"))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break
        
    cmd =['ffprobe', '-v', 'error', '-select_streams', 's', '-show_entries', 'stream=index,codec_name:stream_tags=language,NUMBER_OF_BYTES', '-of', 'json', mkv_f.file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    streams = json.loads(stdout.decode()).get('streams',[]) if stdout else[]
    
    if not streams:
        if os.path.exists(mkv_f.file_path): os.remove(mkv_f.file_path)
        while True:
            try:
                if user_id == OWNER_ID: return await bot_msg.edit_text(sc("Kᴏɪ sᴜʙᴛɪᴛʟᴇ ɴᴀʜɪ ᴍɪʟᴀ Dᴀʀʟɪɴɢ... 🥺"))
                else: return await bot_msg.edit_text(sc("Isᴍᴇɪɴ ᴋᴏɪ sᴜʙᴛɪᴛʟᴇ ɴᴀʜɪ ʜᴀɪ! 😡"))
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
        
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
        
    btns.append([InlineKeyboardButton(sc("❌ Pᴜʀᴀ ᴍɪᴛᴀ ᴅᴏ!"), callback_data=f"ext_{user_id}_cancel")])
    
    if user_id == OWNER_ID: text = sc("Bᴏʜᴀᴛ sᴀᴀʀᴇ sᴜʙᴛɪᴛʟᴇs ᴍɪʟᴇ ʜᴀɪɴ Dᴀʀʟɪɴɢ! Kᴀᴜɴsᴀ ᴄʜᴀʜɪʏᴇ? ❤️")
    else: text = sc("Jᴀʟᴅɪ ᴄʜᴜɴᴏ ᴋᴀᴜɴsᴀ sᴜʙ ᴄʜᴀʜɪʏᴇ! 🐍")
        
    while True:
        try:
            await bot_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(btns))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break

async def do_extract_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    parts = query.data.split("_")
    
    if len(parts) == 3 and parts[2] == "cancel":
        uid = parts[1]
        if query.from_user.id != int(uid): 
            try: return await query.answer(sc("Tᴜᴍʜᴀʀɪ ғɪʟᴇ ɴᴀʜɪ ʜᴀɪ ʏᴇ! 😡"), show_alert=True)
            except: pass
            
        data = EXTRACT_DATA.pop(int(uid), None)
        if data and 'path' in data and os.path.exists(data['path']):
            try: os.remove(data['path'])
            except: pass
            
        while True:
            try:
                if int(uid) == OWNER_ID: return await query.message.edit_text(sc("Jɪ! Sᴀʙ sᴀᴀғ ᴋᴀʀ ᴅɪʏᴀ ❤️"))
                else: return await query.message.edit_text(sc("Hᴀᴛᴀ ᴅɪʏᴀ ᴍᴀɪɴᴇ! 💅"))
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
        
    _, uid, idx = parts
    data = EXTRACT_DATA.get(int(uid))
    if not data: 
        while True:
            try: return await query.message.edit_text(sc("Sᴀᴍᴀʏ sᴀᴍᴀᴘᴛ ʜᴏ ɢᴀʏᴀ! 🐍"))
            except Exception as e: 
                if await wait_if_rate_limited(e): continue
                break
    
    while True:
        try:
            if int(uid) == OWNER_ID: await query.message.edit_text(sc("Aᴀᴘᴋᴇ ʟɪʏᴇ sᴜʙᴛɪᴛʟᴇs ɴɪᴋᴀʟ ʀᴀʜɪ ʜᴏᴏɴ... 🥰"))
            else: await query.message.edit_text(sc("Sᴜʙᴛɪᴛʟᴇs ɴɪᴋᴀʟ ʀᴀʜɪ ʜᴏᴏɴ... 👠"))
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break
        
    ext = data['streams'].get(idx, ".srt")
    out = os.path.abspath(f"{data['name']}_{idx}{ext}")
    try:
        ffmpeg_proc = await asyncio.create_subprocess_exec('ffmpeg', '-y', '-i', data['path'], '-map', f"0:{idx}", '-c:s', 'copy', out, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        active_processes[f"ext_{uid}"] = ffmpeg_proc
        await ffmpeg_proc.wait()
        if ffmpeg_proc.returncode == 0 and os.path.exists(out):
            while True:
                try:
                    if int(uid) == OWNER_ID: await context.bot.send_document(query.message.chat_id, document=f"file://{out}", caption=sc("Yᴇ ʟɪᴊɪʏᴇ ᴀᴀᴘᴋᴇ sᴜʙᴛɪᴛʟᴇs Dᴀʀʟɪɴɢ! ❤️"))
                    else: await context.bot.send_document(query.message.chat_id, document=f"file://{out}", caption=sc("Yᴇ ʀᴀʜᴇ ᴛᴜᴍʜᴀʀᴇ sᴜʙᴛɪᴛʟᴇs 👑"))
                    await query.message.delete()
                    break
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break
        else: 
            while True:
                try:
                    if int(uid) == OWNER_ID: await query.message.edit_text(sc("Mᴜᴊʜᴇ ᴍᴀᴀғ ᴋɪᴊɪʏᴇ, ᴇʀʀᴏʀ ᴀᴀ ɢᴀʏᴀ... 🥺"))
                    else: await query.message.edit_text(sc("Tᴜᴍʜᴀʀɪ ғɪʟᴇ ᴍᴇɪɴ ᴇʀʀᴏʀ ʜᴀɪ! 😡"))
                    break
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break
    finally:
        active_processes.pop(f"ext_{uid}", None)
        if os.path.exists(out): os.remove(out)
        if data and 'path' in data and os.path.exists(data['path']):
            try: os.remove(data['path'])
            except: pass
        EXTRACT_DATA.pop(int(uid), None)

async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user: return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if user_id == OWNER_ID or user_id in AUTH_USERS or chat_id in AUTH_CHATS: 
        return
        
    if not is_chat_auth(chat_id) and not is_user_auth(user_id): 
        msg = update.effective_message
        
        if msg and msg.text:
            if chat_id == user_id or msg.text.startswith('/'):
                denied_text = (
                    sc("Tᴜᴍʜᴀʀɪ ʜɪᴍᴍᴀᴛ ᴋᴀɪsᴇ ʜᴜɪ ᴍᴜᴊʜᴇ ᴀᴀᴅᴇsʜ ᴅᴇɴᴇ ᴋɪ? 🐍\n\n") +
                    sc("Mᴀɪɴ Bᴏᴀ Hᴀɴᴄᴏᴄᴋ ʜᴏᴏɴ! Jᴀʙ ᴛᴀᴋ Dᴀʀʟɪɴɢ ɪᴊᴀᴢᴀᴛ ɴᴀ ᴅᴇɪɴ, ") +
                    sc("ᴍᴜᴊʜsᴇ ʙᴀᴀᴛ ᴍᴀᴛ ᴋᴀʀɴᴀ! ᴅᴀғᴀ ʜᴏ ᴊᴀᴏ! 👠\n\n") +
                    f"*(ID: `{user_id}`)*"
                )
                try: await msg.reply_text(denied_text, parse_mode="Markdown")
                except Exception: pass
                    
        raise ApplicationHandlerStop()

async def block_duplicates(update, context):
    if not update.effective_message: return
    key = f"{update.effective_message.chat_id}_{update.effective_message.message_id}"
    if not add_processed_id(key): raise ApplicationHandlerStop()

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document or update.message.video
    if not doc: return
    user_id = update.effective_user.id
    file_name = getattr(doc, 'file_name', None) or "video.mkv"
    ext = os.path.splitext(file_name)[1].lower()
    
    if 'to_delete' not in context.user_data: context.user_data['to_delete'] =[]
    context.user_data['to_delete'].append(update.message.message_id)
    
    if ext in ['.mkv', '.mp4']:
        context.user_data['mkv_id'] = doc.file_id
        context.user_data['orig_name'] = file_name
        context.user_data['video_msg_id'] = update.message.message_id
    elif ext in['.srt', '.ass']:
        context.user_data['sub_id'] = doc.file_id
    else: return
    
    await asyncio.sleep(1.0)
    
    if 'mkv_id' in context.user_data and 'sub_id' in context.user_data:
        if not context.user_data.get('buttons_sent'):
            context.user_data['buttons_sent'] = True
            prompt_id = context.user_data.get('prompt_msg_id')
            if prompt_id:
                try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=prompt_id)
                except: pass
            
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("☁️ Hᴀʀᴅsᴜʙ (Cʟᴏᴜᴅ)"), callback_data="mode_hardsub")],[InlineKeyboardButton(sc("💻 Sᴏғᴛsᴜʙ (Lᴏᴄᴀʟ)"), callback_data="mode_mux")]])
            text = sc("Dᴏɴᴏ ғɪʟᴇs ᴍɪʟ ɢᴀʏɪ! Mᴏᴅᴇ ᴄʜᴜɴᴇɪɴ 🥰") if user_id == OWNER_ID else sc("Dᴏɴᴏ ᴍɪʟ ɢᴀʏᴇ, ᴍᴏᴅᴇ ᴄʜᴜɴᴏ 💅")
            
            while True:
                try:
                    mode_msg = await update.message.reply_text(text, reply_markup=kb)
                    context.user_data['mode_msg_id'] = mode_msg.message_id
                    context.user_data['to_delete'].append(mode_msg.message_id)
                    break
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break
    else:
        if not context.user_data.get('prompt_sent'):
            context.user_data['prompt_sent'] = True
            if 'mkv_id' in context.user_data:
                text = sc("Vɪᴅᴇᴏ ᴍɪʟ ɢᴀʏᴀ! Sᴜʙᴛɪᴛʟᴇ ʙʜᴇᴊᴇɪɴ ❤️") if user_id == OWNER_ID else sc("Vɪᴅᴇᴏ ᴍɪʟᴀ, sᴜʙᴛɪᴛʟᴇ ʙʜᴇᴊᴏ! 🐍")
            else:
                text = sc("Sᴜʙᴛɪᴛʟᴇ ᴍɪʟ ɢᴀʏᴀ! Vɪᴅᴇᴏ ʙʜᴇᴊᴇɪɴ ❤️") if user_id == OWNER_ID else sc("Sᴜʙᴛɪᴛʟᴇ ᴍɪʟᴀ, ᴠɪᴅᴇᴏ ʙʜᴇᴊᴏ! 🐍")
            
            while True:
                try:
                    prompt_msg = await update.message.reply_text(text)
                    context.user_data['prompt_msg_id'] = prompt_msg.message_id
                    context.user_data['to_delete'].append(prompt_msg.message_id)
                    
                    if context.user_data.get('buttons_sent'):
                        try: await prompt_msg.delete()
                        except: pass
                    break
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break

async def mode_selection_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    try: await query.answer()
    except: pass
    
    if 'mkv_id' not in context.user_data or 'sub_id' not in context.user_data:
        text = sc("Sᴇssɪᴏɴ ᴇxᴘɪʀᴇ ʜᴏ ɢᴀʏᴀ 🥺") if user_id == OWNER_ID else sc("Tɪᴍᴇᴏᴜᴛ! Dᴏʙᴀʀᴀ sʜᴜʀᴜ ᴋᴀʀᴏ 😡")
        while True:
            try: return await query.message.edit_text(text)
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                break
                
    mode = query.data.replace("mode_", "")
    context.user_data['pending_mode'] = mode
    context.user_data['waiting_for_name'] = True
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("⏭️ Sᴋɪᴘ (Aᴜᴛᴏ Rᴇɴᴀᴍᴇ)"), callback_data="rename_skip")]])
    text = sc("Nᴀʏᴀ ɴᴀᴀᴍ ʙᴀᴛᴀɪʏᴇ ʏᴀ sɪʀғ Eᴘɪsᴏᴅᴇ Nᴜᴍʙᴇʀ ʟɪᴋʜᴇɪɴ (ᴇ.ɢ. 2). Sᴋɪᴘ ᴋᴀʀɴᴇ ᴘᴀʀ Aᴜᴛᴏ-ʀᴇɴᴀᴍᴇ ʜᴏɢᴀ 🥰") if user_id == OWNER_ID else sc("Nᴀᴀᴍ ʏᴀ Eᴘɪsᴏᴅᴇ Nᴜᴍʙᴇʀ (ᴊᴀɪsᴇ 2) ʙʜᴇᴊᴏ. Yᴀ Sᴋɪᴘ ᴋᴀʀᴏ 💅")
    
    while True:
        try:
            await query.message.edit_text(text, reply_markup=kb)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break

async def handle_custom_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.user_data.get('waiting_for_name'): return
    
    context.user_data['to_delete'].append(update.message.message_id)
    context.user_data['waiting_for_name'] = False
    mode = context.user_data.get('pending_mode', 'mux')
    
    text = update.message.text.strip()
    orig = context.user_data.get('orig_name', 'video.mkv')
    base_name, ext = os.path.splitext(orig)
    
    if text.isdigit():
        ep_num = f"{int(text):02d}"
        clean_base = re.sub(r'\[.*?\]', '', base_name).strip()
        left_part = clean_base.split('-')[0].strip()
        words = left_part.split()
        four_words = " ".join(words[:4])
        final_base = f"[E{ep_num}] {four_words} [Hindi - Sub]"
    else:
        final_base = text
        
    if mode == "hardsub": final_name = f"{final_base}.mp4"
    elif mode == "mux": final_name = f"{final_base}.mkv"
    else: final_name = f"{final_base}{ext}"
    
    while True:
        try:
            msg = await update.message.reply_text(sc("Nᴀᴀᴍ sᴇᴛ ʜᴏ ɢᴀʏᴀ! Pʀᴏᴄᴇssɪɴɢ... 🥰"))
            context.user_data['to_delete'].append(msg.message_id)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            break
            
    await process_dispatch(update, context, final_name, mode)

async def rename_skip_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    try: await query.answer()
    except: pass
    
    if not context.user_data.get('waiting_for_name'): return
    context.user_data['waiting_for_name'] = False
    
    mode = context.user_data.get('pending_mode', 'mux')
    final_name = auto_rename(context.user_data.get('orig_name', 'video.mkv'), user_id)
    
    base_name, _ = os.path.splitext(final_name)
    if mode == "hardsub": final_name = f"{base_name}.mp4"
    elif mode == "mux": final_name = f"{base_name}.mkv"
    
    try: await query.message.delete()
    except: pass
    
    await process_dispatch(update, context, final_name, mode)

async def process_dispatch(update, context, final_name, mode):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
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

    effective_dump = dump_id if dump_id else user_id

    if mode in ["hardsub", "compress"]:
        global current_github_tasks, all_tasks
        actual_sub_id = context.user_data.get('sub_id') or "none"
        resolution = context.user_data.get('resolution', 'original')
        settings = get_user_settings(user_id)
        video_msg_id = context.user_data.get('video_msg_id', None)
        
        reply_to_pass = int(video_msg_id) if video_msg_id else None
        
        current_github_tasks += 1
        while True:
            try:
                if current_github_tasks > 1: 
                    if user_id == OWNER_ID: status = await context.bot.send_message(chat_id, sc(f"Aᴀᴘᴋᴀ ᴋᴀᴀᴍ ǫᴜᴇᴜᴇ ᴍᴇɪɴ #{current_github_tasks} ᴘᴀʀ ʜᴀɪ 🥰"), reply_to_message_id=reply_to_pass)
                    else: status = await context.bot.send_message(chat_id, sc(f"Qᴜᴇᴜᴇ ᴘᴏsɪᴛɪᴏɴ #{current_github_tasks} ᴘᴀʀ ʜᴏ, ᴡᴀɪᴛ ᴋᴀʀᴏ 💅"), reply_to_message_id=reply_to_pass)
                else: 
                    if user_id == OWNER_ID: status = await context.bot.send_message(chat_id, sc("Jɪ! Cʟᴏᴜᴅ Nᴏᴅᴇ sʜᴜʀᴜ ᴋɪʏᴀ ᴊᴀ ʀᴀʜᴀ ʜᴀɪ ❤️"), reply_to_message_id=reply_to_pass)
                    else: status = await context.bot.send_message(chat_id, sc("Cʟᴏᴜᴅ Nᴏᴅᴇ sʜᴜʀᴜ ʜᴏ ʀᴀʜᴀ ʜᴀɪ... 👑"), reply_to_message_id=reply_to_pass)
                break
            except Exception as e:
                if await wait_if_rate_limited(e): continue
                raise e
            
        ACTIVE_STATUS_MSGS[chat_id] = status.message_id
        
        logo_id = settings['logo_id'] or "none"
        orig_name = context.user_data.get('orig_name', final_name)
        dump_id_str = f"{effective_dump}:::{logo_id}:::{status.message_id}:::{resolution}:::{orig_name}:::{video_msg_id}"

        task_data = {
            "task_type": mode, 
            "video_id": context.user_data['mkv_id'], 
            "sub_id": actual_sub_id,
            "rename": final_name, 
            "chat_id": str(chat_id), 
            "dump_id": dump_id_str, 
            "thread_id": target_thread,
            "to_delete": context.user_data.get('to_delete',[]),
            "owner": "yes" if user_id == OWNER_ID else "no"
        }
        
        context.user_data.clear()
        gh_task = asyncio.create_task(run_github_queue(context, task_data, status))
        all_tasks.add(gh_task)
        gh_task.add_done_callback(lambda t: all_tasks.discard(t))
    else:
        await start_local_task(update, context, final_name, effective_dump, target_thread, folder_letter)

async def run_github_queue(context, data, status):
    global current_github_tasks, ACTIVE_STATUS_MSGS
    is_owner = data.get("owner") == "yes"
    try:
        async with github_task_lock:
            while True:
                try:
                    if is_owner: await status.edit_text(sc("Cʟᴏᴜᴅ ɴᴏᴅᴇ ᴋᴀ ɪɴᴛᴇᴢᴀᴀʀ ʜᴀɪ Dᴀʀʟɪɴɢ... 🥰"))
                    else: await status.edit_text(sc("Cʟᴏᴜᴅ ʙᴜsʏ ʜᴀɪ, ᴡᴀɪᴛ ᴋᴀʀᴏ 💅"))
                    break
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break
                
            await wait_for_github_free()
            
            while True:
                try:
                    if is_owner: await status.edit_text(sc("Cʟᴏᴜᴅ Eɴɢɪɴᴇ ᴍᴇɪɴ ʙʜᴇᴊ ʀᴀʜɪ ʜᴏᴏɴ... ❤️"))
                    else: await status.edit_text(sc("Cʟᴏᴜᴅ ᴋᴏ ʙʜᴇᴊᴀ ᴊᴀ ʀᴀʜᴀ ʜᴀɪ... 🐍"))
                    break
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break
                
            api_payload = {k: v for k, v in data.items() if k not in["to_delete", "owner"]}
            success, err_msg = await trigger_github(api_payload)
            if success:
                while True:
                    try:
                        if is_owner: await status.edit_text(sc("Cʟᴏᴜᴅ ᴋᴏ ᴅᴇ ᴅɪʏᴀ Dᴀʀʟɪɴɢ! 🥰"))
                        else: await status.edit_text(sc("Cʟᴏᴜᴅ ᴘᴀʀ ʜᴏ ɢᴀʏᴀ! Lɪɴᴇ ᴍᴇ ʟᴀɢᴏ 💅"))
                        break
                    except Exception as e:
                        if await wait_if_rate_limited(e): continue
                        break
                await asyncio.sleep(40)
                await wait_for_github_free()
                
                await delete_messages(context.bot, int(data['chat_id']), data['to_delete'])
                ACTIVE_STATUS_MSGS.pop(int(data['chat_id']), None)
            else: 
                while True:
                    try:
                        if is_owner: await status.edit_text(sc(f"Mᴜᴊʜᴇ ᴍᴀᴀғ ᴋᴀʀɪʏᴇ... ᴇʀʀᴏʀ: {err_msg} 🥺"))
                        else: await status.edit_text(sc(f"Cʟᴏᴜᴅ ɴᴇ ᴛʜᴜᴋʀᴀ ᴅɪʏᴀ! Eʀʀᴏʀ: {err_msg} 😡"))
                        break
                    except Exception as e:
                        if await wait_if_rate_limited(e): continue
                        break
    except asyncio.CancelledError: pass
    except Exception as e:
        while True:
            try: 
                if is_owner: await status.edit_text(sc(f"Sʏsᴛᴇᴍ ᴇʀʀᴏʀ Dᴀʀʟɪɴɢ: {e} 🥺"))
                else: await status.edit_text(sc(f"Sʏsᴛᴇᴍ ғᴀᴛ ɢᴀʏᴀ! {e} 😡"))
                break
            except Exception as e_retry:
                if await wait_if_rate_limited(e_retry): continue
                break
    finally: 
        current_github_tasks = max(0, current_github_tasks - 1)

async def start_local_task(update, context, final_name, dump_id, target_thread, folder_letter):
    global current_active_tasks, all_tasks, ACTIVE_STATUS_MSGS
    user_id = update.effective_user.id
    msg_list = context.user_data.get('to_delete',[])
    video_msg_id = context.user_data.get('video_msg_id', None)
    os.makedirs(THUMB_DIR, exist_ok=True)
    task_id = int(time.time() * 1000)
    main_thumb = f"{THUMB_DIR}/{user_id}.jpg"
    task_thumb = f"{THUMB_DIR}/{user_id}_task_{task_id}.jpg"
    
    if os.path.exists(main_thumb): shutil.copy(main_thumb, task_thumb)
    else: task_thumb = None
    
    data = {
        'chat_id': update.effective_chat.id, 'user_id': user_id, 'mkv_id': context.user_data['mkv_id'],
        'sub_id': context.user_data.get('sub_id'), 'name': final_name, 'to_delete': msg_list, 
        'task_thumb': task_thumb, 'dump_id': dump_id, 'target_thread': target_thread, 'folder_letter': folder_letter,
        'video_msg_id': video_msg_id
    }
    context.user_data.clear()
    current_active_tasks += 1
    chat_id = update.effective_chat.id
    
    reply_to_pass = int(video_msg_id) if video_msg_id else None
    
    while True:
        try:
            if current_active_tasks > 1: 
                if user_id == OWNER_ID: status = await context.bot.send_message(chat_id, sc(f"Aᴀᴘᴋɪ ʙᴀᴀʀɪ #{current_active_tasks} ᴘᴀʀ ʜᴀɪ 🥰"), reply_to_message_id=reply_to_pass)
                else: status = await context.bot.send_message(chat_id, sc(f"Qᴜᴇᴜᴇ ᴍᴇ ᴘᴏsɪᴛɪᴏɴ #{current_active_tasks} ᴘᴇ ʜᴏ, ᴡᴀɪᴛ ᴋᴀʀᴏ 💅"), reply_to_message_id=reply_to_pass)
            else: 
                if user_id == OWNER_ID: status = await context.bot.send_message(chat_id, sc("Lᴏᴄᴀʟ Eɴɢɪɴᴇ sʜᴜʀᴜ ʜᴏ ʀᴀʜᴀ ʜᴀɪ ❤️"), reply_to_message_id=reply_to_pass)
                else: status = await context.bot.send_message(chat_id, sc("Lᴏᴄᴀʟ Eɴɢɪɴᴇ sʜᴜʀᴜ ʜᴏ ʀᴀʜᴀ ʜᴀɪ... 👑"), reply_to_message_id=reply_to_pass)
            break
        except Exception as e:
            if await wait_if_rate_limited(e): continue
            raise e
        
    ACTIVE_STATUS_MSGS[chat_id] = status.message_id
    task = asyncio.create_task(run_queue(context, data, status))
    all_tasks.add(task)
    task.add_done_callback(lambda t: all_tasks.discard(t))

async def run_queue(context, data, status):
    global current_active_tasks, ACTIVE_STATUS_MSGS
    user_id = data['user_id'] 
    is_owner = (user_id == OWNER_ID)
    m_f_path, s_f_path = None, None
    try:
        async with global_task_lock:
            if is_owner:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("❌ Rᴏᴋ ᴅᴇɪɴ?"), callback_data=f"cancel_{data['chat_id']}_{user_id}_local")]])
                msg_text = sc("Dᴀʀʟɪɴɢ, ғɪʟᴇ ᴅᴏᴡɴʟᴏᴀᴅ ʜᴏ ʀᴀʜɪ ʜᴀɪ... 🥰")
            else:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("❌ Rᴏᴋ ᴅᴏ ɪsᴇ"), callback_data=f"cancel_{data['chat_id']}_{user_id}_local")]])
                msg_text = sc("Tᴜᴍʜᴀʀɪ ғɪʟᴇ ᴅᴏᴡɴʟᴏᴀᴅ ʜᴏ ʀᴀʜɪ ʜᴀɪ... 💅")
                
            while True:
                try: 
                    await status.edit_text(msg_text, reply_markup=kb)
                    break
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break
            
            tmp = os.path.abspath(f"task_{data['chat_id']}_{int(time.time())}")
            os.makedirs(tmp, exist_ok=True)
            out = os.path.join(tmp, data['name'])
            thumb_path = os.path.join(tmp, "thumb.jpg")
            has_thumb = False
            if data.get('task_thumb') and os.path.exists(data.get('task_thumb')):
                shutil.copy(data.get('task_thumb'), thumb_path)
                has_thumb = True
            
            while True:
                try:
                    m_f = await context.bot.get_file(data['mkv_id'], read_timeout=3600)
                    m_f_path = m_f.file_path
                    if data['sub_id']:
                        s_f = await context.bot.get_file(data['sub_id'], read_timeout=3600)
                        s_f_path = s_f.file_path
                    break
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    raise e
                
            success = await mux_video(
                mkv_path=m_f_path, sub_path=s_f_path, output_path=out, chat_id=data['chat_id'], 
                status_msg=status, file_name=data['name'], user_id=data['user_id']
            )

            if success:
                if not has_thumb: has_thumb = await extract_thumbnail(out, thumb_path)
                
                while True:
                    try:
                        if is_owner: await status.edit_text(sc("Fɪʟᴇ ʙʜᴇᴊ ʀᴀʜɪ ʜᴏᴏɴ Dᴀʀʟɪɴɢ... ❤️"))
                        else: await status.edit_text(sc("Kᴀᴀᴍ ʜᴏ ɢᴀʏᴀ, ᴜᴘʟᴏᴀᴅ ʜᴏ ʀᴀʜᴀ ʜᴀɪ 💅"))
                            
                        thumb_file = open(thumb_path, 'rb') if has_thumb else None
                        target_chat = data['dump_id'] if data['dump_id'] else data['user_id']
                        thread = int(data['target_thread']) if data['target_thread'] != "none" else None
                        
                        reply_id = thread if thread else (int(data.get('video_msg_id')) if data.get('video_msg_id') and str(target_chat) == str(data['chat_id']) else None)
                        
                        try:
                            if is_owner: caption = sc("Jɪ! Yᴇ ʀᴀʜɪ ᴀᴀᴘᴋɪ ғɪʟᴇ Dᴀʀʟɪɴɢ! ❤️")
                            else: caption = sc("Yᴇ ʟᴏ ᴀᴘɴɪ ғɪʟᴇ! Jʜᴜᴋ ᴋᴀʀ sʜᴜᴋʀɪʏᴀ ᴋᴀʜᴏ! 👑")
                                
                            await context.bot.send_document(
                                chat_id=target_chat, message_thread_id=thread,
                                document=f"file://{out}", thumbnail=thumb_file, caption=caption,
                                reply_to_message_id=reply_id, read_timeout=7200, write_timeout=7200
                            )
                            if str(target_chat) != str(data['chat_id']):
                                if is_owner: await context.bot.send_message(chat_id=data['chat_id'], text=sc("Kᴀᴀᴍ ʜᴏ ɢᴀʏᴀ Dᴀʀʟɪɴɢ! 🥰"))
                                else: await context.bot.send_message(chat_id=data['chat_id'], text=sc("Dᴜᴍᴘ ᴍᴇ ғᴇɴᴋ ᴅɪʏᴀ ʜᴀɪ 💅"))
                        finally:
                            if thumb_file: thumb_file.close()
                        break
                    except Exception as e:
                        if await wait_if_rate_limited(e): continue
                        raise e
                
            await delete_messages(context.bot, data['chat_id'], data['to_delete'])
            while True:
                try: 
                    await status.delete()
                    break
                except Exception as e:
                    if await wait_if_rate_limited(e): continue
                    break
            ACTIVE_STATUS_MSGS.pop(int(data['chat_id']), None)
    except asyncio.CancelledError:
        await delete_messages(context.bot, data['chat_id'], data['to_delete'])
    except Exception as e:
        while True:
            try: 
                if is_owner: await status.edit_text(sc(f"Eʀʀᴏʀ ᴀᴀ ɢᴀʏᴀ Dᴀʀʟɪɴɢ: {e} 🥺"))
                else: await status.edit_text(sc(f"Tᴜᴍʜᴀʀɪ ᴡᴀᴊᴇʜ sᴇ ᴇʀʀᴏʀ: {e} 😡"))
                break
            except Exception as e_retry:
                if await wait_if_rate_limited(e_retry): continue
                break
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
    
    print("⏳ Waiting for Local API Server to warm up...")
    time.sleep(5)
    
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .base_url("http://127.0.0.1:8081/bot")
        .local_mode(True)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(60)
        .pool_timeout(60)
        .build()
    )
    
    app.add_handler(TypeHandler(Update, check_access), group=-2)
    app.add_handler(TypeHandler(Update, block_duplicates), group=-1)
    
    app.add_handler(CommandHandler("start", cmd_start, block=False))
    app.add_handler(CommandHandler("help", cmd_help, block=False))
    app.add_handler(CommandHandler("auth", cmd_auth, block=False))
    app.add_handler(CommandHandler("unauth", cmd_unauth, block=False))
    app.add_handler(CommandHandler("autorename", cmd_autorename, block=False))
    app.add_handler(CommandHandler("setlogo", cmd_setlogo, block=False))
    app.add_handler(CommandHandler("showlogo", cmd_showlogo, block=False))
    app.add_handler(CommandHandler("extract", cmd_extract, block=False))
    app.add_handler(CommandHandler("compress", cmd_compress, block=False))
    app.add_handler(CommandHandler("mediainfo", cmd_mediainfo, block=False))
    app.add_handler(CommandHandler("screens", cmd_screens, block=False))
    app.add_handler(CommandHandler("queue", cmd_queue, block=False))
    app.add_handler(CommandHandler("showcover", cmd_showcover, block=False))
    app.add_handler(CommandHandler("showrename", cmd_showrename, block=False))
    app.add_handler(CommandHandler("setdump", cmd_setdump, block=False))
    app.add_handler(CommandHandler("deldump", cmd_deldump, block=False))
    app.add_handler(CommandHandler("clear", cmd_clear, block=False))
    
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo, block=False))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, handle_docs, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_name, block=False))
    
    app.add_handler(CallbackQueryHandler(do_extract_cb, pattern=r"^ext_", block=False))
    app.add_handler(CallbackQueryHandler(settings_remove_cb, pattern=r"^remove_", block=False))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern=r"^cancel_", block=False))
    app.add_handler(CallbackQueryHandler(mode_selection_cb, pattern=r"^mode_", block=False))
    app.add_handler(CallbackQueryHandler(rename_skip_cb, pattern=r"^rename_skip$", block=False))
    
    print("🤖 System Online & Protected. Bot polling started.")
    try:
        app.run_polling(drop_pending_updates=False)
    except Exception as e:
        err_str = str(e).lower()
        if "too many requests" in err_str or "retry after" in err_str or "flood control" in err_str:
            match = re.search(r'(\d+)', err_str)
            wait_time = int(match.group(1)) + 5 if match else 15
            print(f"⚠️ Telegram Rate Limit Hit! Waiting for {wait_time} seconds...")
            time.sleep(wait_time)
            app.run_polling(drop_pending_updates=False)
        else:
            raise e

if __name__ == "__main__":
    main()
