import os, time, asyncio, threading, json, re, shutil, sqlite3, urllib.request, glob, requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, TypeHandler, ApplicationHandlerStop
)

from config import BOT_TOKEN, OWNER_ID, AUTH_USERS, AUTH_CHATS, PORT, SESSION_ID, global_task_lock, github_task_lock, active_processes, EXTRACT_DATA, LANG_MAP, GITHUB_TOKEN, REPO_NAME
from database import init_db, is_user_auth, is_chat_auth, add_processed_id, DB_PATH, get_user_settings, update_user_setting, add_auth_user, del_auth_user, add_auth_chat, del_auth_chat, get_user_dump, set_user_dump, DATA_DIR
from bot_utils import mux_video, clean_temp_files, get_readable_time, extract_thumbnail, get_media_info, generate_screenshots

# --- GLOBAL VARIABLES & DB ---
current_active_tasks = 0
current_github_tasks = 0
all_tasks = set()
ACTIVE_STATUS_MSGS = {}

# Thumbnails ko persistent storage me save karne ke liye
THUMB_DIR = os.path.join(DATA_DIR, "user_thumbs")

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
                if run.get("status") in["in_progress", "queued", "requested"]: return True
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
            "Aapke liye saari jankari hazir hai, Darling! 🥰 Aap jo bhi kahenge main wahi karungi!\n\n"
            "🔹 /start - Mujhe aadesh dene ke liye\n"
            "🔹 /autorename - Apna rename format set karein\n"
            "🔹 /setlogo - Kisi bhi image par reply karein logo lagane ke liye\n"
            "🔹 /showlogo - Apna pyara sa logo dekhein\n"
            "🔹 /setdump - Ek pyara sa dump group ID set karein\n"
            "🔹 /deldump - Dump group ko hata dein\n"
            "🔹 /showcover - Apna cover picture dekhein ya hatayein\n"
            "🔹 /showrename - Apna rename format dekhein ya hatayein\n"
            "🔹 /extract - Kisi MKV par reply karke subs nikalwayein\n"
            "🔹 /compress - Video par reply karein compress karne ke liye\n"
            "🔹 /mediainfo - Video details ke liye reply karein\n"
            "🔹 /screens - Screenshots lene ke liye reply karein\n"
            "🔹 /queue - Baki sabke kaam queue me dekhein\n"
            "🔹 /clear - Apna rasta bilkul saaf karein\n\n"
            "Aapke khass Admin Commands:\n"
            "🔹 /auth [id] - Kise andar aane dena hai bataiye\n"
            "🔹 /unauth [id] - Kise bahar nikal fenkna hai bataiye"
        )
    else:
        help_text = (
            "Tum jaise sadharan insaan ko meri madad chahiye? Thik hai, meri khoobsurti aur taqat ke aage jhuko aur ye aadesh suno... 🐍\n\n"
            "🔹 /start - Meri khidmat me hazir hone ke liye\n"
            "🔹 /autorename - Rename format set karne ka tareeqa\n"
            "🔹 /setlogo - Apni koi tasveer par reply karke logo lagao\n"
            "🔹 /showlogo - Apna logo dekhne ya hatane ke liye\n"
            "🔹 /setdump - Apna dump group batao\n"
            "🔹 /deldump - Dump group disable karne ke liye\n"
            "🔹 /showcover - Apna custom cover dekhne ke liye\n"
            "🔹 /showrename - Apna bakwas rename format dekhne ke liye\n"
            "🔹 /extract - Kisi MKV par reply karke subs nikaalo\n"
            "🔹 /compress - Video par reply karke compress karo\n"
            "🔹 /mediainfo - Video ki tuchh details dekhne ke liye\n"
            "🔹 /screens - Screenshots banane ke liye\n"
            "🔹 /queue - Dekho main kitni busy hoon aur queue check karo\n"
            "🔹 /clear - Apna kachra aur queue clear karo yahan se\n\n"
            "Ab inka theek se istemaal karna, mera waqt barbad mat karna! 💅"
        )
    await update.message.reply_text(help_text)

async def cmd_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: return await update.message.reply_text("Darling! please kisi ka ID to bataiye... 🥺 /auth [user_id ya chat_id]")
    try:
        target_id = int(context.args[0])
        if str(target_id).startswith("-100") or str(target_id).startswith("-"):
            add_auth_chat(target_id)
            await update.message.reply_text(f"Ji! Maine is group {target_id} ko ijazat de di hai, sirf aapke kehne par! 🥰")
        else:
            add_auth_user(target_id)
            await update.message.reply_text(f"Ho gaya! Is user {target_id} ko maine izajat de di! Aap kitne dayalu hain! ❤️")
    except ValueError:
        await update.message.reply_text("A-aapne galat ID likh diya... koi baat nahi main intezaar karungi jab tak aap sahi nahi batate! 🌸")

async def cmd_unauth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: return await update.message.reply_text("Kise bahar nikalna hai, Darling? Mujhe bas ID bata dijiye! 🥺 /unauth [user_id ya chat_id]")
    try:
        target_id = int(context.args[0])
        if str(target_id).startswith("-100") or str(target_id).startswith("-"):
            del_auth_chat(target_id)
            await update.message.reply_text(f"Aapne kaha aur maine is group {target_id} ko hamesha ke liye bahar nikal diya! 😡")
        else:
            del_auth_user(target_id)
            await update.message.reply_text(f"Is badtameez {target_id} ko maine nikal diya! Mujhe sirf aapki zaroorat hai... ❤️")
    except ValueError:
        await update.message.reply_text("Galat ID format hai Darling, please dobara koshish karein... 🌸")

async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_active_tasks, current_github_tasks
    user_id = update.effective_user.id
    if user_id == OWNER_ID:
        text = (
            "Aapke aadesh par saari jankari hazir hai! 🥰\n\n"
            f"Local Tasks: {current_active_tasks}\n"
            f"Cloud Tasks: {current_github_tasks}\n\n"
            "Main in sabko jaldi khatam karungi, bas aapke liye!"
        )
    else:
        text = (
            "Mera waqt bohot keemti hai, phir bhi tumhara ye tuchh queue status yahan hai... 💅\n\n"
            f"Local Tasks: {current_active_tasks}\n"
            f"Cloud Tasks: {current_github_tasks}\n\n"
            "Chupchap apni baari ka intezaar karo aur mujhe pareshaan mat karo! 🐍"
        )
    await update.message.reply_text(text)

async def cmd_mediainfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id
    
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        if user_id == OWNER_ID:
            return await msg.reply_text("Darling... please pehle kisi video par reply kijiye! 🥺")
        else:
            return await msg.reply_text("Bewakoof! Pehle kisi video par reply karna seekho! 😡")
    
    target = msg.reply_to_message.video or msg.reply_to_message.document
    
    if user_id == OWNER_ID:
        bot_msg = await msg.reply_text("Aapke liye video ki jankari la rahi hoon... thoda intezaar kijiye! 🥰")
    else:
        bot_msg = await msg.reply_text("Ruko, main apni sundarta ke saath ye details laa rahi hoon... 💅")
    
    mkv_f = await context.bot.get_file(target.file_id, read_timeout=3600)
    info = await get_media_info(mkv_f.file_path)
    
    try: os.remove(mkv_f.file_path)
    except: pass
    
    if user_id == OWNER_ID:
        await bot_msg.edit_text(f"Ye lijiye aapke video ki saari jankari! ❤️\n\n{info}")
    else:
        await bot_msg.edit_text(f"Dekh lo apne is ghatiya video ki jankari... 🐍\n\n{info}")

async def cmd_screens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id
    
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        if user_id == OWNER_ID:
            return await msg.reply_text("Darling... please kisi video par reply karke bataiye kitne screenshots lene hain... 🥺")
        else:
            return await msg.reply_text("Tumhe kitni baar samjhana padega? Video par reply karke aadesh do! 👠")
    
    try:
        num = int(context.args[0]) if context.args else 4
        num = min(max(num, 1), 10)
    except:
        num = 4
        
    target = msg.reply_to_message.video or msg.reply_to_message.document
    
    if user_id == OWNER_ID:
        bot_msg = await msg.reply_text(f"Ji! Aapke liye {num} behtareen screenshots bana rahi hoon! 📸🥰")
    else:
        bot_msg = await msg.reply_text(f"Hato! Main tumhare liye {num} screenshots nikal rahi hoon. Ehsaan mano mera! 💅")
    
    mkv_f = await context.bot.get_file(target.file_id, read_timeout=3600)
    folder = f"screens_{update.effective_user.id}_{int(time.time())}"
    
    images = await generate_screenshots(mkv_f.file_path, num, folder)
    
    if images:
        media_group =[InputMediaPhoto(open(img, 'rb')) for img in images]
        await msg.reply_media_group(media=media_group)
        await bot_msg.delete()
    else:
        if user_id == OWNER_ID:
            await bot_msg.edit_text("M-mujhe maaf kar dijiye! Main screenshots nahi nikal payi... 🥺")
        else:
            await bot_msg.edit_text("Tumhari video itni ghatiya hai ki mere system ne screenshots lene se inkaar kar diya! 😡")
        
    try: os.remove(mkv_f.file_path)
    except: pass
    clean_temp_files(folder)

async def cmd_showlogo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    if not settings.get('logo_id'):
        if user_id == OWNER_ID:
            return await update.message.reply_text("Aapne abhi tak koi logo set nahi kiya hai Darling... please /setlogo ka istemaal karein! 🥺")
        else:
            return await update.message.reply_text("Bina logo set kiye mujhse kya maang rahe ho? Pehle kisi tasveer par /setlogo reply karo! 🐍")

    if user_id == OWNER_ID:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Logo hata dein?", callback_data="remove_logo")]])
        caption_text = "Ye raha aapka pyara logo! Aapki pasand bohot acchi hai! 🥰"
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Logo hatao", callback_data="remove_logo")]])
        caption_text = "Ye raha tumhara logo. Meri aankhon me chub raha hai par theek hai... 💅"
    
    try: await update.message.reply_photo(photo=settings['logo_id'], caption=caption_text, reply_markup=kb)
    except:
        try: await update.message.reply_document(document=settings['logo_id'], caption=caption_text, reply_markup=kb)
        except: 
            if user_id == OWNER_ID:
                await update.message.reply_text("Main logo dhundh nahi payi... maaf kar dijiye! 🥺")
            else:
                await update.message.reply_text("Tumhara logo load nahi ho raha, isme meri koi galti nahi hai! 😡")

async def cmd_showcover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    thumb_path = f"{THUMB_DIR}/{user_id}.jpg"
    
    if os.path.exists(thumb_path):
        if user_id == OWNER_ID:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Cover hata dein?", callback_data="remove_cover")]])
            caption_text = "Ye rahi aapki cover picture! Kitni sundar hai bilkul aapki tarah! ❤️"
        else:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Cover hatao", callback_data="remove_cover")]])
            caption_text = "Ye raha tumhara tuchh sa custom cover. Dekh lo ise. 💅"
        await update.message.reply_photo(photo=open(thumb_path, 'rb'), caption=caption_text, reply_markup=kb)
    else:
        if user_id == OWNER_ID:
            await update.message.reply_text("Koi cover nahi mila Darling, please ek tasveer bhej kar set karein! 🌸")
        else:
            await update.message.reply_text("Koi custom cover nahi hai tumhara. Pehle photo bhejo agar cover chahiye toh! 🐍")

async def cmd_showrename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    fmt = settings.get('rename_format')
    
    if fmt:
        if user_id == OWNER_ID:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Format hata dein?", callback_data="remove_rename")]])
            text = f"Ye raha aapka rename format:\n\n{fmt}\n\nSab ekdum perfect hai! 🥰"
        else:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Format hatao", callback_data="remove_rename")]])
            text = f"Ye dekho apna rename format:\n\n{fmt}\n\nUmeed hai isme tumne koi bewaqoofi nahi ki hogi! 💅"
        await update.message.reply_text(text, reply_markup=kb)
    else:
        if user_id == OWNER_ID:
            await update.message.reply_text("Aapne koi format set nahi kiya hai Darling. please /autorename ka istemaal karein! 🥺")
        else:
            await update.message.reply_text("Tumne koi format set nahi kiya! /autorename use karo aur mera waqt mat barbad karo. 😡")

async def cmd_setdump(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args: 
        if user_id == OWNER_ID:
            return await update.message.reply_text("Darling, please dump group ki ID batayein jaise /setdump -100xxx 🥺")
        else:
            return await update.message.reply_text("Bewakoof! Dump ID kahaan hai? Aise aadesh do: /setdump -100xxx 🐍")
            
    set_user_dump(user_id, context.args[0])
    
    if user_id == OWNER_ID:
        await update.message.reply_text("Ji! Aapka personal dump group set kar diya gaya hai! Main sab wahin bhejungi! ❤️")
    else:
        await update.message.reply_text("Tumhara dump group set kar diya hai maine. Khush raho aur meri khoobsurti ki tareef karo! 👑")

async def cmd_deldump(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_user_dump(user_id, None)
    
    if user_id == OWNER_ID:
        await update.message.reply_text("Aapka dump group hata diya gaya hai! Ab sab kuch seedhe aapko dungi! 🥰")
    else:
        await update.message.reply_text("Tumhara dump group hata diya hai maine! Aage se seedhe bheja jayega. 💅")

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
    
    if user_id == OWNER_ID:
        await update.message.reply_text("Aapka poora rasta saaf kar diya hai maine! Ab sab ekdum naya jaisa hai! ❤️")
    else:
        await update.message.reply_text("Maine tumhara sara kachra aur queue saaf kar diya hai, kyunki main saaf suthri aur khoobsurat hoon! Niklo ab yahan se... 🗑️🐍")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user_id = update.effective_user.id
    
    if user_id == OWNER_ID:
        text = (
            "A-aap yahan hain! 😍 Welcome Darling!\n\n"
            "Main Boa Hancock, aapki seva me hazir hu. Aap bas mujhe ek video aur subtitle bhejiye, baaki saara kaam main sambhal lungi sirf aapke liye! ❤️"
        )
    else:
        text = (
            "Tumhare jaise sadharan insaan ki himmat kaise hui mujhe jagane ki? 🐍\n\n"
            "Main Pirate Empress Boa Hancock hu! Khair... mujhe apni MKV video aur subtitle bhejo aur chupchap meri meherbani ka intezaar karo... 👑"
        )
        
    if os.path.exists("start_img.jpg"):
        await update.message.reply_photo(photo=open("start_img.jpg", 'rb'), caption=text)
    elif os.path.exists("start_img.png"):
        await update.message.reply_photo(photo=open("start_img.png", 'rb'), caption=text)
    else:
        await update.message.reply_text(text)

async def cmd_autorename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        if user_id == OWNER_ID:
            msg = await update.message.reply_text("Darling, please mujhe format bataiye jaise: /autorename [S01 E{ep}] {short_title} [{quality}] 🥺")
        else:
            msg = await update.message.reply_text("Bewakoof! Format kon dega? Aise likho: /autorename[S01 E{ep}] {short_title} [{quality}] 🐍")
        asyncio.create_task(delete_after(update.message, 0))
        asyncio.create_task(delete_after(msg, 5))
        return
        
    format_str = " ".join(context.args)
    update_user_setting(user_id, "rename_format", format_str)
    
    if user_id == OWNER_ID:
        msg = await update.message.reply_text(f"Ji! Aapka auto-rename format bilkul waise hi save kar liya gaya hai! 🥰\nNaya naam kuch aisa dikhega: {format_str}")
    else:
        msg = await update.message.reply_text(f"Tumhara format save ho gaya hai. Ehsaan mano mera! 💅\nNaya naam: {format_str}")
        
    asyncio.create_task(delete_after(update.message, 0))
    asyncio.create_task(delete_after(msg, 5))

async def cmd_setlogo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id
    
    if msg.reply_to_message and (msg.reply_to_message.photo or msg.reply_to_message.document):
        photo_id = msg.reply_to_message.photo[-1].file_id if msg.reply_to_message.photo else msg.reply_to_message.document.file_id
        
        update_user_setting(user_id, "logo_id", photo_id)
        
        await delete_messages(context.bot, msg.chat_id,[msg.message_id])
        if user_id == OWNER_ID:
            await msg.reply_to_message.reply_text("Aapka pyara logo save ho gaya hai Darling! Ise Top Right mein chota sa lagungi! ❤️")
        else:
            await msg.reply_to_message.reply_text("Mera mood accha tha isliye tumhara logo save kar liya. Top Right pe set ho jayega. 💅")
    else:
        if user_id == OWNER_ID:
            await msg.reply_text("Darling... please kisi PNG image par reply karke /setlogo likhiye 🥺")
        else:
            await msg.reply_text("Bewakoof! Bina tasveer par reply kiye logo kaise set karu? PNG file par reply karo! 😡")

async def settings_remove_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    await query.answer()

    if data == "remove_logo":
        update_user_setting(user_id, "logo_id", None)
        try: await query.message.delete()
        except: pass
        if user_id == OWNER_ID:
            msg = await context.bot.send_message(chat_id=query.message.chat_id, text="Aapke aadesh par maine logo hata diya hai! 🥰")
        else:
            msg = await context.bot.send_message(chat_id=query.message.chat_id, text="Tumhara ghatiya logo hata diya gaya hai! 🐍")
        asyncio.create_task(delete_after(msg, 5)) 
    
    elif data == "remove_cover":
        thumb_path = f"{THUMB_DIR}/{user_id}.jpg"
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
        try: await query.message.delete()
        except: pass
        
        if user_id == OWNER_ID:
            msg = await context.bot.send_message(chat_id=query.message.chat_id, text="Ji! Cover picture successfully hata di gayi hai! ❤️")
        else:
            msg = await context.bot.send_message(chat_id=query.message.chat_id, text="Tumhara custom cover mita diya hai maine! 🗑️")
        asyncio.create_task(delete_after(msg, 5))
    
    elif data == "remove_rename":
        update_user_setting(user_id, "rename_format", None)
        try: await query.message.delete()
        except: pass
        
        if user_id == OWNER_ID:
            msg = await context.bot.send_message(chat_id=query.message.chat_id, text="Aapka auto-rename format reset kar diya gaya hai! 🌸")
        else:
            msg = await context.bot.send_message(chat_id=query.message.chat_id, text="Auto-rename format hata diya gaya hai. Ab sadharan naam hi aayenge! 💅")
        asyncio.create_task(delete_after(msg, 5))

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    
    os.makedirs(THUMB_DIR, exist_ok=True)
    thumb_path = f"{THUMB_DIR}/{user_id}.jpg"
    
    photo_file = await context.bot.get_file(photo.file_id)
    try: 
        shutil.copy(photo_file.file_path, thumb_path)
    except: 
        await photo_file.download_to_drive(thumb_path)
    
    try: await update.message.delete()
    except Exception: pass

    if user_id == OWNER_ID:
        text = "Aapki behtareen cover picture save ho gayi hai Darling! Maine purani chat clean kar di hai! ❤️"
    else:
        text = "Tumhara cover save ho gaya hai. Aur main itni sundar aur saaf suthri hu ki maine tumhari chat se tasveer mita di hai! 👑"

    conf_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    asyncio.create_task(delete_after(conf_msg, 5))

async def cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id == OWNER_ID:
        alert_text = "Ji! Main is kaam ko abhi rok rahi hoon aapke liye! 🥰"
    else:
        alert_text = "Tumhari himmat kaise hui mera kaam rokne ki? Thik hai, maine rok diya! 😡"
        
    try: await query.answer(alert_text, show_alert=True)
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
    user_id = update.effective_user.id
    
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        if user_id == OWNER_ID:
            return await msg.reply_text("Darling... please kisi MKV ya MP4 file par reply karke bataiye na compress karna hai kya? 🥺")
        else:
            return await msg.reply_text("Bewakoof! Kisi video par reply karke /compress[resolution] likho! 🐍")
    
    res_arg = context.args[0].lower() if context.args else "original"
    valid_res = {"1080p": "1080", "720p": "720", "480p": "480", "360p": "360"}
    resolution = valid_res.get(res_arg, "original")
    
    target = msg.reply_to_message.video or msg.reply_to_message.document
    file_name = getattr(target, 'file_name', None) or "video.mkv"
    context.user_data['mkv_id'] = target.file_id
    context.user_data['orig_name'] = file_name
    context.user_data['sub_id'] = None 
    context.user_data['resolution'] = resolution
    context.user_data['to_delete'] =[msg.message_id, msg.reply_to_message.message_id]
    
    final_name = auto_rename(file_name, user_id)
    await process_dispatch(update, context, final_name, mode="compress")

def get_lang_name(code): return LANG_MAP.get(code.lower(), code.title())

async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = msg.from_user.id
    
    if not msg.reply_to_message or not (msg.reply_to_message.video or msg.reply_to_message.document):
        if user_id == OWNER_ID:
            return await msg.reply_text("Darling, please pehle ek MKV file par reply kijiye! ❤️")
        else:
            return await msg.reply_text("Extract karne ke liye ek MKV file par reply karna zaroori hai! 🐍")
            
    old_data = EXTRACT_DATA.pop(user_id, None)
    if old_data and 'path' in old_data and os.path.exists(old_data['path']):
        try: os.remove(old_data['path'])
        except: pass

    target = msg.reply_to_message.video or msg.reply_to_message.document
    file_name = getattr(target, 'file_name', None) or "video.mkv"
    
    if user_id == OWNER_ID:
        bot_msg = await msg.reply_text("Aapki file download kar rahi hoon... bas ek pal dijiye! 🥰")
    else:
        bot_msg = await msg.reply_text("Mera qeemti waqt le kar tumhari file download ho rahi hai... chupchap intezaar karo! 💅")
        
    mkv_f = await context.bot.get_file(target.file_id, read_timeout=3600)
    
    if user_id == OWNER_ID:
        await bot_msg.edit_text("Andar kya kya chupa hai wo dhoondh rahi hoon aapke liye... 🌸")
    else:
        await bot_msg.edit_text("Is ghatiya video ki scan chal rahi hai... 👠")
        
    cmd =['ffprobe', '-v', 'error', '-select_streams', 's', '-show_entries', 'stream=index,codec_name:stream_tags=language,NUMBER_OF_BYTES', '-of', 'json', mkv_f.file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    streams = json.loads(stdout.decode()).get('streams', []) if stdout else[]
    
    if not streams:
        if os.path.exists(mkv_f.file_path): os.remove(mkv_f.file_path)
        if user_id == OWNER_ID:
            return await bot_msg.edit_text("Mujhe maaf kar dijiye Darling! Isme koi subtitles nahi mile... 🥺")
        else:
            return await bot_msg.edit_text("Bewakoof! Is file mein koi subtitle hi nahi hai! Mera waqt barbad kiya! 😡")
        
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
        
    btns.append([InlineKeyboardButton("❌ Pura mita do!", callback_data=f"ext_{user_id}_cancel")])
    
    if user_id == OWNER_ID:
        text = "Mujhe ek se zyada subtitles mil gaye hain! Aapko kaunsa chahiye Darling? ❤️"
    else:
        text = "Bohat saare subtitles mile hain. Jaldi se chunav karo warna main inko hata dungi! 🐍"
        
    await bot_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(btns))

async def do_extract_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    
    if len(parts) == 3 and parts[2] == "cancel":
        uid = parts[1]
        if query.from_user.id != int(uid): 
            return await query.answer("Tumhari himmat kaise hui dusre ki file chune ki? 😡", show_alert=True)
            
        data = EXTRACT_DATA.pop(int(uid), None)
        if data and 'path' in data and os.path.exists(data['path']):
            try: os.remove(data['path'])
            except: pass
            
        if int(uid) == OWNER_ID:
            return await query.message.edit_text("Ji! Maine sab saaf kar diya hai aapke kehne par! ❤️")
        else:
            return await query.message.edit_text("Tumhare kehne par maine ye sab mita diya hai. 💅")
        
    _, uid, idx = parts
    data = EXTRACT_DATA.get(int(uid))
    if not data: 
        return await query.message.edit_text("Samay samapt ho chuka hai, dubara shuru karo! 🐍")
    
    if int(uid) == OWNER_ID:
        await query.message.edit_text("Aapke liye subtitles nikal rahi hoon... 🥰")
    else:
        await query.message.edit_text("Subtitles nikalne ka kaam shuru ho gaya hai. Chupchap intezaar karo! 👠")
        
    ext = data['streams'].get(idx, ".srt")
    out = os.path.abspath(f"{data['name']}_{idx}{ext}")
    try:
        ffmpeg_proc = await asyncio.create_subprocess_exec('ffmpeg', '-y', '-i', data['path'], '-map', f"0:{idx}", '-c:s', 'copy', out, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        active_processes[f"ext_{uid}"] = ffmpeg_proc
        await ffmpeg_proc.wait()
        if ffmpeg_proc.returncode == 0 and os.path.exists(out):
            if int(uid) == OWNER_ID:
                await context.bot.send_document(query.message.chat_id, document=f"file://{out}", caption="Ye lijiye aapke subtitles, Darling! ❤️")
            else:
                await context.bot.send_document(query.message.chat_id, document=f"file://{out}", caption="Ye rahe tumhare tuchh subtitles. Jhuk kar shukriya kaho! 👑")
            await query.message.delete()
        else: 
            if int(uid) == OWNER_ID:
                await query.message.edit_text("Mujhe maaf kijiye, main subtitles nahi nikal payi... 🥺")
            else:
                await query.message.edit_text("Kuch gadbad ho gayi, aur ye tumhari bekar file ki galti hai! 😡")
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
    
    # Agar user/group authorized hai, toh aage badhne do
    if user_id == OWNER_ID or user_id in AUTH_USERS or chat_id in AUTH_CHATS: 
        return
        
    # Agar authorized NAHI hai
    if not is_chat_auth(chat_id) and not is_user_auth(user_id): 
        msg = update.effective_message
        
        # Sirf tabhi reply karegi jab user PM me ho, YA group me koi command (/) use kare
        if msg and msg.text:
            if chat_id == user_id or msg.text.startswith('/'):
                denied_text = (
                    "Tumhari himmat kaise hui mujhe aadesh dene ki? 🐍\n\n"
                    "Main Pirate Empress Boa Hancock hoon! Jab tak Darling (Owner) tumhe ijazat nahi dete, "
                    "mujhse baat karne ki koshish bhi mat karna! Dafa ho jao yahan se! 👠\n\n"
                    f"*(Apni aukaat yaad rakhne ke liye ye ID apne paas rakh lo: `{user_id}`)*"
                )
                try:
                    await msg.reply_text(denied_text, parse_mode="Markdown")
                except Exception:
                    pass
                    
        # Uske baad process ko rok do taaki command run na ho
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
    
    if ext == '.mkv' or ext == '.mp4':
        context.user_data['mkv_id'] = doc.file_id
        context.user_data['orig_name'] = file_name
        if 'sub_id' not in context.user_data:
            if user_id == OWNER_ID:
                text = "Video mil gaya Darling! ❤️ Ab jaldi se subtitle bhi bhej dijiye taaki main shuru kar saku..."
            else:
                text = "Video rakh do yahan. Aur subtitle? Kya wo aasman se aayega? Chalo jaldi subtitle bhejo! 💅"
            bot_reply = await update.message.reply_text(text)
            context.user_data['to_delete'].append(bot_reply.message_id)
            
    elif ext in['.srt', '.ass']:
        context.user_data['sub_id'] = doc.file_id
        if 'mkv_id' not in context.user_data:
            if user_id == OWNER_ID:
                text = "Subtitles aa gaye hain Darling! 🥰 Ab bas video file de dijiye..."
            else:
                text = "Subtitles mil gaye, par video ke bina main iska kya aachar dalu? Jaldi video bhejo! 🐍"
            bot_reply = await update.message.reply_text(text)
            context.user_data['to_delete'].append(bot_reply.message_id)
    else: return
        
    if 'mkv_id' in context.user_data and 'sub_id' in context.user_data:
        final_name = auto_rename(context.user_data['orig_name'], user_id)
        context.user_data['final_name'] = final_name
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("☁️ Hardsub (Cloud)", callback_data="mode_hardsub")],[InlineKeyboardButton("💻 Softsub (Local)", callback_data="mode_mux")]
        ])
        
        if user_id == OWNER_ID:
            text = "Dono files mil gayi! Bataiye Darling, main iska kya karu aapke liye? ❤️"
        else:
            text = "Thik hai, dono file mil gayi. Ab chunav karo aur mera zyada waqt barbad mat karna! 🐍"
            
        mode_msg = await update.message.reply_text(text, reply_markup=kb)
        context.user_data['to_delete'].append(mode_msg.message_id)

async def mode_selection_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    await query.answer()
    if 'mkv_id' not in context.user_data: 
        if user_id == OWNER_ID:
            return await query.message.edit_text("Mujhe maaf kijiye, session expire ho gaya... kripya dobara bhejiye! 🥺")
        else:
            return await query.message.edit_text("Time khatam ho gaya! Tumne itni der kyu lagayi? Dobara shuru karo! 😡")
    
    chat_id = update.effective_chat.id
    dump_id = get_user_dump(user_id)
    
    if chat_id != user_id and not dump_id:
        try:
            await context.bot.send_chat_action(chat_id=user_id, action='typing')
        except Exception:
            if user_id == OWNER_ID:
                return await query.message.reply_text(f"A-aapne yahan group me aadesh diya? Par main toh file aapke inbox me hi dungi! Please mujhe PM me start karein Darling... 🥺\nYahan click karein: @{context.bot.username}")
            else:
                return await query.message.reply_text(f"Bewakoof! Group me aadesh de raha hai? Main tumhari ghatiya file sabke saamne nahi dungi. PM me aakar baat karo mujhse! 😡\nYahan aao: @{context.bot.username}")

    mode = query.data.replace("mode_", "")
    final_name = context.user_data.get('final_name', 'video.mkv')
    
    # 💡 YAHAN HAI MAGIC LOGIC 💡
    base_name, ext = os.path.splitext(final_name)
    
    if mode == "hardsub":
        # Hardsub humesha MP4 me nikalna chahiye
        final_name = f"{base_name}.mp4"
    elif mode == "mux":
        # Softsub humesha MKV me hoga! 
        # Agar user ne MP4 diya hai, toh ye use automatically MKV me badal dega.
        final_name = f"{base_name}.mkv"
        
    await query.message.delete()
    await process_dispatch(update, context, final_name, mode=mode)

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

    if mode in["hardsub", "compress"]:
        global current_github_tasks, all_tasks
        actual_sub_id = context.user_data.get('sub_id') or "none"
        resolution = context.user_data.get('resolution', 'original')
        settings = get_user_settings(user_id)
        
        if current_github_tasks > 0: 
            if user_id == OWNER_ID:
                status = await context.bot.send_message(chat_id, f"Aapka kaam Cloud Queue me number #{current_github_tasks} par hai. Main ise jaldi nikalungi! 🥰")
            else:
                status = await context.bot.send_message(chat_id, f"Tumhara kaam cloud queue mein number #{current_github_tasks} pe padha hai. Shanti se wait karo! 💅")
        else: 
            if user_id == OWNER_ID:
                status = await context.bot.send_message(chat_id, "Ji! Cloud Node shuru kiya ja raha hai aapke liye! ❤️")
            else:
                status = await context.bot.send_message(chat_id, "Cloud Node shuru ho raha hai... ehsaan mano mera! 👑")
            
        ACTIVE_STATUS_MSGS[chat_id] = status.message_id
        
        logo_id = settings['logo_id'] or "none"
        dump_id_str = f"{effective_dump}:::{logo_id}:::{status.message_id}:::{resolution}"

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
        current_github_tasks += 1
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
            if current_github_tasks == 0: return 
            
            if is_owner:
                await status.edit_text("Cloud node ka intezaar chal raha hai Darling... 🥰")
            else:
                await status.edit_text("Cloud Node abhi thoda busy hai. Meri khoobsurti niharo jab tak shuru ho... 💅")
                
            await wait_for_github_free()
            
            if is_owner:
                await status.edit_text("Aapka kaam Cloud Engine me bheja ja raha hai... ❤️")
            else:
                await status.edit_text("Tumhara data cloud ko bheja ja raha hai... 🐍")
                
            api_payload = {k: v for k, v in data.items() if k not in["to_delete", "owner"]}
            success, err_msg = await trigger_github(api_payload)
            if success:
                if is_owner:
                    await status.edit_text(f"Kaam safaltapurvak Cloud ko de diya gaya hai! Mode: {data['task_type'].upper()} 🥰\nMain aapki file ka intezaar karungi!")
                else:
                    await status.edit_text(f"Lo ho gaya Cloud Engine par! Mode: {data['task_type'].upper()} 💅\nAb line mein lage raho chupchap.")
                await asyncio.sleep(40)
                await wait_for_github_free()
                
                await delete_messages(context.bot, int(data['chat_id']), data['to_delete'])
                ACTIVE_STATUS_MSGS.pop(int(data['chat_id']), None)
            else: 
                if is_owner:
                    await status.edit_text(f"M-mujhe maaf kar dijiye... Cloud ne error diya: {err_msg} 🥺")
                else:
                    await status.edit_text(f"Cloud server ne tumhara kaam thukra diya! Error: {err_msg} 😡")
    except asyncio.CancelledError: pass
    except Exception as e:
        try: 
            if is_owner:
                await status.edit_text(f"System me kuch gadbad ho gayi Darling: {e} 🥺")
            else:
                await status.edit_text(f"System fat gaya tumhare karan! Error: {e} 😡")
        except: pass
    finally: current_github_tasks = max(0, current_github_tasks - 1)

async def start_local_task(update, context, final_name, dump_id, target_thread, folder_letter):
    global current_active_tasks, all_tasks, ACTIVE_STATUS_MSGS
    user_id = update.effective_user.id
    msg_list = context.user_data.get('to_delete',[])
    os.makedirs(THUMB_DIR, exist_ok=True)
    task_id = int(time.time() * 1000)
    main_thumb = f"{THUMB_DIR}/{user_id}.jpg"
    task_thumb = f"{THUMB_DIR}/{user_id}_task_{task_id}.jpg"
    
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
    
    if current_active_tasks > 1: 
        if user_id == OWNER_ID:
            status = await context.bot.send_message(chat_id, f"Aapki baari Local Queue mein number {current_active_tasks - 1} par hai! Main jaldi aapka kaam karungi! 🥰")
        else:
            status = await context.bot.send_message(chat_id, f"Local Queue mein number {current_active_tasks - 1} pe khade ho tum. Chupchap intezaar karo! 💅")
    else: 
        if user_id == OWNER_ID:
            status = await context.bot.send_message(chat_id, "Local Engine shuru ho raha hai sirf aapke liye! ❤️")
        else:
            status = await context.bot.send_message(chat_id, "Mera Local Engine tumhara kaam karne ke liye taiyaar ho raha hai... ehsaan mano. 👑")
        
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
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Rok dein?", callback_data=f"cancel_{data['chat_id']}_{user_id}_local")]])
                msg_text = f"Darling, aapki file download ho rahi hai... bas ek pal!\nFile: {data['name']} 🥰"
            else:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Rok do is ghatiya kaam ko", callback_data=f"cancel_{data['chat_id']}_{user_id}_local")]])
                msg_text = f"Mera qeemti waqt le kar tumhari file download ho rahi hai...\nFile: {data['name']} 💅"
                
            try: await status.edit_text(msg_text, reply_markup=kb)
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
                    
                    if is_owner:
                        await status.edit_text("Aapki file Telegram par bhej rahi hoon... ❤️")
                    else:
                        await status.edit_text("Tumhara kaam ban gaya hai, ab upload ho raha hai. 💅")
                        
                    thumb_file = open(thumb_path, 'rb') if has_thumb else None
                    target_chat = data['dump_id'] if data['dump_id'] else data['user_id']
                    thread = int(data['target_thread']) if data['target_thread'] != "none" else None
                    
                    try:
                        if is_owner:
                            caption = "Ji! Muxing pura ho gaya! Ye rahi aapki file Darling! ❤️"
                        else:
                            caption = "Ye lo apni file. Mera local engine tumhara kaam kar diya, ab jhuk kar shukriya kaho! 👑"
                            
                        await context.bot.send_document(
                            chat_id=target_chat, message_thread_id=thread,
                            document=f"file://{out}", thumbnail=thumb_file, caption=caption,
                            read_timeout=7200, write_timeout=7200
                        )
                        if str(target_chat) != str(data['chat_id']):
                            if is_owner:
                                await context.bot.send_message(chat_id=data['chat_id'], text="Aapka kaam ho gaya aur file wahan bhej di gayi hai Darling! 🥰")
                            else:
                                await context.bot.send_message(chat_id=data['chat_id'], text="Kaam karke Dump me fenk diya hai. Ab pareshan mat karna. 💅")
                    finally:
                        if thumb_file: thumb_file.close()
                    
                await delete_messages(context.bot, data['chat_id'], data['to_delete'])
                try: await status.delete()
                except: pass
                ACTIVE_STATUS_MSGS.pop(int(data['chat_id']), None)
            except asyncio.CancelledError:
                await delete_messages(context.bot, data['chat_id'], data['to_delete'])
            except Exception as e:
                try: 
                    if is_owner:
                        await status.edit_text(f"Mujhe maaf kijiye, kuch error aa gaya: {e} 🥺")
                    else:
                        await status.edit_text(f"Tumhari wajeh se error aaya hai: {e} 😡")
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
    app.add_handler(CommandHandler("auth", cmd_auth))
    app.add_handler(CommandHandler("unauth", cmd_unauth))
    app.add_handler(CommandHandler("autorename", cmd_autorename))
    app.add_handler(CommandHandler("setlogo", cmd_setlogo))
    app.add_handler(CommandHandler("showlogo", cmd_showlogo))
    app.add_handler(CommandHandler("extract", cmd_extract))
    app.add_handler(CommandHandler("compress", cmd_compress))
    app.add_handler(CommandHandler("mediainfo", cmd_mediainfo))
    app.add_handler(CommandHandler("screens", cmd_screens))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("showcover", cmd_showcover))
    app.add_handler(CommandHandler("showrename", cmd_showrename))
    app.add_handler(CommandHandler("showlogo", cmd_showlogo))
    app.add_handler(CommandHandler("setdump", cmd_setdump))
    app.add_handler(CommandHandler("deldump", cmd_deldump))
    app.add_handler(CommandHandler("clear", cmd_clear))
    
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, handle_docs))
    
    app.add_handler(CallbackQueryHandler(mode_selection_cb, pattern=r"^mode_"))
    app.add_handler(CallbackQueryHandler(do_extract_cb, pattern=r"^ext_"))
    app.add_handler(CallbackQueryHandler(settings_remove_cb, pattern=r"^remove_"))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern=r"^cancel_"))
    
    print("🤖 System Online & Protected. Bot polling started.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
