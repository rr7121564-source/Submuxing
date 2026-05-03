import os, sys, time, asyncio
import pyrogram.utils

def patched_get_peer_type(peer_id: int) -> str:
    val = str(peer_id)
    if val.startswith("-100"): return "channel"
    elif val.startswith("-"): return "chat"
    else: return "user"

pyrogram.utils.get_peer_type = patched_get_peer_type

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def sc(text: str) -> str:
    if not isinstance(text, str): return str(text)
    return text.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ"))

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

TASK_TYPE = os.getenv("TASK_TYPE", "hardsub")
VIDEO_ID = os.getenv("VIDEO_ID", "")
SUB_ID = os.getenv("SUB_ID", "none")
RENAME = os.getenv("RENAME", "output.mp4")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
THREAD_ID = os.getenv("THREAD_ID", "none")

raw_dump = os.getenv("DUMP_ID", "none")
STATUS_MSG_ID = None
RESOLUTION = "original"
ORIG_NAME = RENAME
VIDEO_MSG_ID = None

if ":::" in raw_dump:
    parts = raw_dump.split(":::")
    DUMP_ID = parts[0]
    if len(parts) > 1: LOGO_ID = parts[1]
    else: LOGO_ID = "none"
    if len(parts) > 2: STATUS_MSG_ID = parts[2]
    if len(parts) > 3: RESOLUTION = parts[3]
    if len(parts) > 4: ORIG_NAME = parts[4]
    if len(parts) > 5: VIDEO_MSG_ID = parts[5]
else:
    DUMP_ID = raw_dump
    LOGO_ID = "none"

last_edit_time = 0

def get_readable_time(seconds) -> str:
    try:
        seconds = int(float(seconds))
    except:
        seconds = 0
    result = ""
    (days, remainder) = divmod(seconds, 86400)
    if days != 0: result += f"{days}d "
    (hours, remainder) = divmod(remainder, 3600)
    if hours != 0: result += f"{hours}h "
    (minutes, seconds) = divmod(remainder, 60)
    if minutes != 0: result += f"{minutes}m "
    result += f"{seconds}s"
    return result.strip()

async def get_duration(file_path):
    cmd =['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        stdout, _ = await proc.communicate()
        return float(stdout.decode().strip())
    except:
        return 0.0

async def progress_bar(current, total, app, msg_id, action_text, current_file_name, start_time):
    global last_edit_time
    now = time.time()
    if now - last_edit_time > 5 or current == total:
        try:
            perc = (current / total) * 100 if total > 0 else 0
            bar_length = 10
            filled = int((perc / 100) * bar_length)
            bar = "▰" * filled + "▱" * (bar_length - filled)
            
            elapsed = now - start_time
            speed = current / elapsed if elapsed > 0 else 0
            eta_seconds = (total - current) / speed if speed > 0 else 0
            eta_str = get_readable_time(eta_seconds) if eta_seconds > 0 else "0s"
            
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("❌ Cᴀɴᴄᴇʟ"), callback_data="cancel_cloud_task_cloud")]])
            text = (
                f"🎬  " + sc("ɢɪᴛʜᴜʙ ᴄʟᴏᴜᴅ ᴡᴏʀᴋᴇʀ") + "\n"
                "──────────────────────\n"
                f"▸ " + sc("sᴛᴀᴛᴜs :") + f" {action_text}\n"
                f"📊 [{bar}] {perc:.2f}%\n"
                f"🚀 Speed: {speed/(1024*1024):.2f} MB/s\n"
                f"💾 Size: {current/(1024*1024):.1f} MB / {total/(1024*1024):.1f} MB\n"
                f"⏱ ETA: {eta_str}\n"
                "──────────────────────\n"
                "🐍 " + sc("ʙᴏᴀ ʜᴀɴᴄᴏᴄᴋ ᴄʟᴏᴜᴅ ᴇɴɢɪɴᴇ")
            )
            await app.edit_message_text(CHAT_ID, msg_id, text, reply_markup=cancel_kb)
            last_edit_time = now
        except Exception:
            pass

async def download_phase():
    app = Client("worker_down", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("❌ Cᴀɴᴄᴇʟ"), callback_data="cancel_cloud_task_cloud")]])
    
    msg_id = None
    if STATUS_MSG_ID:
        try:
            msg_id = int(STATUS_MSG_ID)
            await app.edit_message_text(CHAT_ID, msg_id, sc("⚙️ Wᴏʀᴋᴇʀ ᴛʀɪɢɢᴇʀᴇᴅ: Pʀᴇᴘᴀʀɪɴɢ...\n"), reply_markup=cancel_kb)
        except Exception:
            msg_id = None

    if not msg_id:
        try:
            reply_id = int(VIDEO_MSG_ID) if VIDEO_MSG_ID and str(VIDEO_MSG_ID) != "None" else None
            status_msg = await app.send_message(CHAT_ID, sc("⚙️ Wᴏʀᴋᴇʀ ᴛʀɪɢɢᴇʀᴇᴅ: Pʀᴇᴘᴀʀɪɴɢ...\n"), reply_markup=cancel_kb, reply_to_message_id=reply_id)
            msg_id = status_msg.id
        except Exception:
            status_msg = await app.send_message(CHAT_ID, sc("⚙️ Wᴏʀᴋᴇʀ ᴛʀɪɢɢᴇʀᴇᴅ: Pʀᴇᴘᴀʀɪɴɢ...\n"), reply_markup=cancel_kb)
            msg_id = status_msg.id
    
    dl_start_time = time.time()
    video_path = await app.download_media(
        VIDEO_ID, file_name="video.mkv", 
        progress=progress_bar, progress_args=(app, msg_id, "Dᴏᴡɴʟᴏᴀᴅɪɴɢ Vɪᴅᴇᴏ", ORIG_NAME, dl_start_time)
    )
    
    sub_path = None
    if TASK_TYPE == "hardsub" and SUB_ID != "none":
        sub_start_time = time.time()
        sub_path = await app.download_media(
            SUB_ID, 
            progress=progress_bar, progress_args=(app, msg_id, "Dᴏᴡɴʟᴏᴀᴅɪɴɢ Sᴜʙᴛɪᴛʟᴇ", ORIG_NAME, sub_start_time)
        )
        
    logo_path = None
    if TASK_TYPE == "hardsub" and LOGO_ID != "none":
        logo_start_time = time.time()
        logo_path = await app.download_media(
            LOGO_ID, 
            progress=progress_bar, progress_args=(app, msg_id, "Dᴏᴡɴʟᴏᴀᴅɪɴɢ Lᴏɢᴏ", ORIG_NAME, logo_start_time)
        )
        
    try:
        await app.edit_message_text(CHAT_ID, msg_id, sc("🔥 Sᴛᴀʀᴛɪɴɢ FFᴍᴘᴇɢ Eɴɢɪɴᴇ...\n"), reply_markup=cancel_kb)
    except:
        pass

    await app.stop() 
    return video_path, sub_path, logo_path, msg_id

async def encode_phase(video_path, sub_path, logo_path, msg_id):
    output = RENAME
    duration = await get_duration(video_path)
    os.makedirs("fonts", exist_ok=True)
    
    if TASK_TYPE == "hardsub":
        abs_sub = os.path.abspath(sub_path).replace('\\', '/').replace(':', '\\:') if sub_path else ""
        fonts_dir = os.path.abspath("fonts").replace('\\', '/').replace(':', '\\:')
        sub_filter = f"subtitles='{abs_sub}':fontsdir='{fonts_dir}'" if abs_sub else ""

        if logo_path:
            abs_logo = os.path.abspath(logo_path).replace('\\', '/').replace(':', '\\:')
            scale_val = "120:-1"
            pos_val = "main_w-overlay_w-15:15"
            
            if sub_filter:
                filter_complex = f"[1:v]scale={scale_val}[logo];[0:v]{sub_filter}[subbed];[subbed][logo]overlay={pos_val}[outv]"
            else:
                filter_complex = f"[1:v]scale={scale_val}[logo];[0:v][logo]overlay={pos_val}[outv]"
            
            cmd =[
                'ffmpeg', '-y', '-i', video_path, '-i', abs_logo,
                '-filter_complex', filter_complex,
                '-map', '[outv]', '-map', '0:a?', '-sn',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '34', '-c:a', 'copy',
                '-progress', 'pipe:1', output
            ]
        else:
            cmd =[
                'ffmpeg', '-y', '-i', video_path, '-sn', 
                '-vf', sub_filter, 
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '34', '-c:a', 'copy',
                '-progress', 'pipe:1', output
            ] if sub_filter else[
                'ffmpeg', '-y', '-i', video_path, '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '34', '-c:a', 'copy', '-progress', 'pipe:1', output
            ]
    else:
        if RESOLUTION != "original":
            vf_scale = f"scale=-2:{RESOLUTION}"
            cmd =[
                'ffmpeg', '-y', '-i', video_path, 
                '-map', '0:v', '-map', '0:a?', '-map', '0:s?', 
                '-vf', vf_scale,
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '34', '-c:a', 'copy', '-c:s', 'copy',
                '-progress', 'pipe:1', output
            ]
        else:
            cmd =[
                'ffmpeg', '-y', '-i', video_path, 
                '-map', '0:v', '-map', '0:a?', '-map', '0:s?', 
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '34', '-c:a', 'copy', '-c:s', 'copy',
                '-progress', 'pipe:1', output
            ]

    app = Client("worker_enc", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    start_time = time.time()
    last_up = 0

    while True:
        line = await proc.stdout.readline()
        if not line: break
        line = line.decode('utf-8').strip()
        if line.startswith('out_time_us='):
            try:
                time_str = line.split('=')[1]
                if time_str.lower() == 'n/a': continue 
                cur = int(time_str) / 1000000
                now = time.time()
                
                if duration > 0 and (now - last_up) > 10:
                    perc = min(100, (cur / duration) * 100)
                    elapsed = now - start_time
                    speed_bps = cur / elapsed if elapsed > 0 else 0
                    eta = (duration - cur) / speed_bps if speed_bps > 0 else 0
                    
                    bar_length = 10
                    filled = int((perc / 100) * bar_length)
                    bar = "▰" * filled + "▱" * (bar_length - filled)
                    eta_str = get_readable_time(eta) if eta > 0 else "0s"
                    
                    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("❌ Cᴀɴᴄᴇʟ"), callback_data="cancel_cloud_task_cloud")]])
                    text = (
                        f"🎬  " + sc("ɢɪᴛʜᴜʙ ᴄʟᴏᴜᴅ ᴡᴏʀᴋᴇʀ") + "\n"
                        "──────────────────────\n"
                        f"▸ " + sc("sᴛᴀᴛᴜs :") + sc(" Eɴᴄᴏᴅɪɴɢ...\n") +
                        f"📊 [{bar}] {perc:.2f}%\n"
                        f"🚀 Speed: {speed_bps:.2f}x\n"
                        f"💾 Time: {get_readable_time(cur)} / {get_readable_time(duration)}\n"
                        f"⏱ ETA: {eta_str}\n"
                        "──────────────────────\n"
                        "🐍 " + sc("ʙᴏᴀ ʜᴀɴᴄᴏᴄᴋ ᴄʟᴏᴜᴅ ᴇɴɢɪɴᴇ")
                    )
                    try: await app.edit_message_text(CHAT_ID, msg_id, text, reply_markup=cancel_kb)
                    except: pass
                    last_up = now
            except: pass
            
    await proc.wait()
    await app.stop()
    return output, proc.returncode

async def extract_thumbnail(video_path, thumb_path):
    cmd =['ffmpeg', '-y', '-ss', '00:00:05', '-i', video_path, '-vf', 'scale=320:-1', '-vframes', '1', thumb_path]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
    except:
        pass
    return os.path.exists(thumb_path)

async def upload_phase(output, returncode, msg_id):
    app = Client("worker_up", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    
    if returncode == 0 and os.path.exists(output):
        thumb_path = "thumb.jpg"
        has_thumb = await extract_thumbnail(output, thumb_path)
        
        try:
            await app.edit_message_text(CHAT_ID, msg_id, sc("▸ Pʀᴏᴄᴇssɪɴɢ Dᴏɴᴇ! Uᴘʟᴏᴀᴅ ᴄʜᴀʟ ʀᴀʜᴀ ʜᴀɪ...\n"))
        except:
            pass

        target_chat = int(DUMP_ID) if DUMP_ID != "none" else CHAT_ID
        thread = int(THREAD_ID) if THREAD_ID != "none" else None
        cap = sc(f"✅ {TASK_TYPE.upper()} Cᴏᴍᴘʟᴇᴛᴇ\n") + f"📦 `{RENAME}`"
        
        reply_id = thread
        if target_chat == CHAT_ID and VIDEO_MSG_ID and str(VIDEO_MSG_ID) != "None":
            try:
                reply_id = int(VIDEO_MSG_ID)
            except:
                reply_id = None
        
        up_start_time = time.time()
        try:
            await app.send_document(
                chat_id=target_chat, document=output, reply_to_message_id=reply_id,
                thumb=thumb_path if has_thumb else None, caption=cap,
                progress=progress_bar, progress_args=(app, msg_id, "Uᴘʟᴏᴀᴅɪɴɢ Vɪᴅᴇᴏ", RENAME, up_start_time)
            )
            if target_chat != CHAT_ID:
                await app.send_message(CHAT_ID, sc("Kᴀᴀᴍ ʜᴏ ɢᴀʏᴀ! Fɪʟᴇ ᴀᴀᴘᴋᴏ ʙʜᴇᴊ ᴅɪ ɢᴀʏɪ ʜᴀɪ! ❤️"), reply_to_message_id=reply_id)
            await app.delete_messages(CHAT_ID,[msg_id])
        except Exception as e:
            try:
                # Fallback upload in case reply_to_message_id is invalid/deleted
                await app.send_document(
                    chat_id=target_chat, document=output,
                    thumb=thumb_path if has_thumb else None, caption=cap,
                    progress=progress_bar, progress_args=(app, msg_id, "Uᴘʟᴏᴀᴅɪɴɢ Vɪᴅᴇᴏ", RENAME, up_start_time)
                )
                if target_chat != CHAT_ID:
                    await app.send_message(CHAT_ID, sc("Kᴀᴀᴍ ʜᴏ ɢᴀʏᴀ! Fɪʟᴇ ᴀᴀᴘᴋᴏ ʙʜᴇᴊ ᴅɪ ɢᴀʏɪ ʜᴀɪ! ❤️"))
                await app.delete_messages(CHAT_ID, [msg_id])
            except Exception as inner_e:
                try:
                    await app.edit_message_text(CHAT_ID, msg_id, sc(f"❌ Uᴘʟᴏᴀᴅ Eʀʀᴏʀ: {str(inner_e)}"))
                except:
                    pass
    else:
        try:
            await app.edit_message_text(CHAT_ID, msg_id, sc("❌ FFᴍᴘᴇɢ Eʀʀᴏʀ: Fᴀɪʟᴇᴅ ᴛᴏ ᴘʀᴏᴄᴇss."))
        except:
            pass
    
    await app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        vid, sub, logo, mid = loop.run_until_complete(download_phase())
        out, rcode = loop.run_until_complete(encode_phase(vid, sub, logo, mid))
        loop.run_until_complete(upload_phase(out, rcode, mid))
    except Exception as e:
        print(f"Error during GitHub worker execution: {e}")
