import os
import time
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from config import global_task_lock
from utils.ffmpeg_utils import mux_video
from utils.helpers import clean_temp_files

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🤖 **Muxing Bot Active!**\n\n1️⃣ Send MKV.\n2️⃣ Send Subtitle.\n3️⃣ Send Name (or /skip).")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"👤 Your ID: `{update.effective_user.id}`\n👥 Chat ID: `{update.effective_chat.id}`", parse_mode='Markdown')

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc: return
    ext = os.path.splitext(doc.file_name)[1].lower()

    if ext == '.mkv':
        context.user_data['mkv_id'] = doc.file_id
        context.user_data['orig_name'] = doc.file_name
        context.user_data['state'] = 'WAIT_SUB'
        await update.message.reply_text("✅ MKV Received!\n📥 Now send the **Subtitle (.srt/.ass)** file.")
    
    elif ext in ['.srt', '.ass']:
        if context.user_data.get('state') == 'WAIT_SUB':
            context.user_data['sub_id'] = doc.file_id
            context.user_data['state'] = 'WAIT_NAME'
            await update.message.reply_text("✅ Subtitle Received!\n✏️ Send a **New Name** or /skip.")
        else:
            await update.message.reply_text("⚠️ Please send the MKV file first.")

async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') == 'WAIT_NAME':
        await start_task(update, context, context.user_data['orig_name'])

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') == 'WAIT_NAME':
        name = update.message.text.strip()
        if not name.lower().endswith('.mkv'): name += '.mkv'
        await start_task(update, context, name)

async def cmd_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = 'WAIT_THUMB'
    await update.message.reply_text("🖼️ Send a photo for thumbnail.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') == 'WAIT_THUMB':
        context.user_data['thumb_id'] = update.message.photo[-1].file_id
        context.user_data['state'] = None
        await update.message.reply_text("✅ Thumbnail saved!")

async def start_task(update: Update, context: ContextTypes.DEFAULT_TYPE, final_name: str):
    data = {
        'chat_id': update.effective_chat.id,
        'mkv_id': context.user_data['mkv_id'],
        'sub_id': context.user_data['sub_id'],
        'name': final_name,
        'thumb': context.user_data.get('thumb_id')
    }
    context.user_data.clear() # Reset state
    status = await update.message.reply_text("⏳ Added to Queue...")
    asyncio.create_task(run_queue(context, data, status))

async def run_queue(context, data, status):
    async with global_task_lock:
        await status.edit_text("⚙️ Initializing...")
        tmp = os.path.abspath(f"task_{data['chat_id']}_{int(time.time())}")
        os.makedirs(tmp, exist_ok=True)
        out = os.path.join(tmp, data['name'])
        
        try:
            m_f = await context.bot.get_file(data['mkv_id'])
            s_f = await context.bot.get_file(data['sub_id'])
            
            t_path = None
            if data['thumb']:
                t_raw = os.path.join(tmp, "t.jpg")
                t_path = os.path.join(tmp, "thumb.jpg")
                tf = await context.bot.get_file(data['thumb'])
                await tf.download_to_drive(t_raw)
                # Resize thumbnail
                os.system(f"ffmpeg -y -i {t_raw} -vf \"crop='min(iw,ih)':'min(iw,ih)',scale=320:320\" {t_path}")

            success = await mux_video(m_f.file_path, s_f.file_path, out, data['chat_id'], status)
            if success:
                await status.edit_text("📤 Uploading...")
                with open(out, 'rb') as f:
                    th = open(t_path, 'rb') if t_path else None
                    await context.bot.send_document(chat_id=data['chat_id'], document=f, thumbnail=th)
                    if th: th.close()
                await status.delete()
            else:
                await status.edit_text("❌ Muxing Failed.")
        except Exception as e:
            await status.edit_text(f"Error: {e}")
        finally:
            clean_temp_files(tmp)
