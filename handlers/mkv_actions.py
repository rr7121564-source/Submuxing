import os
import time
from telegram import Update
from telegram.ext import ContextTypes
from utils.ffmpeg_utils import mux_video, extract_subtitles, active_processes
from utils.helpers import clean_temp_files

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Routes incoming documents. Acknowledges MKVs and catches subtitles."""
    doc = update.message.document
    ext = os.path.splitext(doc.file_name)[1].lower()

    if ext == '.mkv':
        await update.message.reply_text(
            "MKV received! 🎥\n\n"
            "• To mux a subtitle: Reply to this message with /sub\n"
            "• To extract subtitles: Reply to this message with /extract"
        )
    elif ext in ['.srt', '.ass']:
        if context.user_data.get('state') == 'WAITING_FOR_SUB':
            await process_muxing(update, context)

async def cmd_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered when user replies to MKV with /sub."""
    message = update.message
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply_text("Please reply to an MKV file message with /sub.")
        return

    doc = message.reply_to_message.document
    if not doc.file_name.lower().endswith('.mkv'):
        await message.reply_text("The replied message is not an MKV file.")
        return

    # Store MKV context in memory
    context.user_data['mkv_file_id'] = doc.file_id
    context.user_data['mkv_file_name'] = doc.file_name
    context.user_data['state'] = 'WAITING_FOR_SUB'

    await message.reply_text("MKV selected! ✅\nNow upload the subtitle file (.srt or .ass).")

async def process_muxing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Downloads files, muxes, and uploads the result."""
    sub_doc = update.message.document
    chat_id = update.effective_chat.id
    status_msg = await update.message.reply_text("Downloading MKV and Subtitle... 📥")

    mkv_file_id = context.user_data.get('mkv_file_id')
    mkv_name = context.user_data.get('mkv_file_name')
    ts = int(time.time()) # Timestamp prevents collisions

    mkv_path = f"temp_{chat_id}_{ts}.mkv"
    sub_path = f"temp_{chat_id}_{ts}_{sub_doc.file_name}"
    output_mkv = f"muxed_{chat_id}_{ts}.mkv"

    try:
        # Download files from Telegram
        mkv_file = await context.bot.get_file(mkv_file_id)
        await mkv_file.download_to_drive(mkv_path)

        sub_file = await context.bot.get_file(sub_doc.file_id)
        await sub_file.download_to_drive(sub_path)

        await status_msg.edit_text("Starting mux process... ⚙️")

        success = await mux_video(mkv_path, sub_path, output_mkv, chat_id, status_msg)

        if success:
            await status_msg.edit_text("Muxing complete! Uploading... 📤")
            with open(output_mkv, 'rb') as f:
                await context.bot.send_document(chat_id=chat_id, document=f)
            await status_msg.delete()
        else:
            if context.user_data.get('cancelled'):
                await status_msg.edit_text("Process cancelled by user. ❌")
                context.user_data['cancelled'] = False
            else:
                await status_msg.edit_text("An error occurred during muxing. ⚠️")

    except Exception as e:
        await status_msg.edit_text(f"Error: {str(e)}")
    finally:
        clean_temp_files(mkv_path, sub_path, output_mkv)
        context.user_data['state'] = None


async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered when user replies to MKV with /extract."""
    message = update.message
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply_text("Please reply to an MKV file message with /extract.")
        return

    doc = message.reply_to_message.document
    if not doc.file_name.lower().endswith('.mkv'):
        await message.reply_text("The replied message is not an MKV file.")
        return

    chat_id = update.effective_chat.id
    status_msg = await message.reply_text("Downloading MKV for extraction... 📥")
    ts = int(time.time())
    mkv_path = f"extract_{chat_id}_{ts}.mkv"
    extracted_files =[]

    try:
        mkv_file = await context.bot.get_file(doc.file_id)
        await mkv_file.download_to_drive(mkv_path)

        await status_msg.edit_text("Extracting subtitles... ⚙️")
        extracted_files = await extract_subtitles(mkv_path, doc.file_name)

        if not extracted_files:
            await status_msg.edit_text("No subtitle streams found in this MKV. ❌")
            return

        await status_msg.edit_text(f"Found {len(extracted_files)} subtitles. Uploading... 📤")

        for sub_file in extracted_files:
            with open(sub_file, 'rb') as f:
                await context.bot.send_document(chat_id=chat_id, document=f)

        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"Error: {str(e)}")
    finally:
        clean_temp_files(mkv_path, *extracted_files)

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kills the active FFmpeg process if Cancel is clicked."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id

    if chat_id in active_processes:
        proc = active_processes[chat_id]
        proc.terminate() # Issues SIGTERM
        context.user_data['cancelled'] = True
        await query.edit_message_text("Cancelling process... 🛑")
    else:
        await query.answer("No active process to cancel.", show_alert=True)
