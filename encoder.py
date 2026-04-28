import os, sys, time, asyncio, traceback
import pyrogram.utils

def patched_get_peer_type(peer_id: int) -> str:
    val = str(peer_id)
    if val.startswith("-100"): return "channel"
    elif val.startswith("-"): return "chat"
    else: return "user"

pyrogram.utils.get_peer_type = patched_get_peer_type

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

TASK_TYPE = os.getenv("TASK_TYPE", "compress")
VIDEO_ID = os.getenv("VIDEO_ID", "")
SUB_ID = os.getenv("SUB_ID", "none")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
THREAD_ID = os.getenv("THREAD_ID", "none")

raw_rename = os.getenv("RENAME", "output.mp4")
if ":::" in raw_rename:
    RESOLUTION, RENAME = raw_rename.split(":::", 1)
else:
    RESOLUTION, RENAME = "Original", raw_rename

raw_dump = os.getenv("DUMP_ID", "none")
STATUS_MSG_ID = None
if ":::" in raw_dump:
    parts = raw_dump.split(":::")
    DUMP_ID = parts[0]
    LOGO_ID = parts[1]
    if len(parts) > 2: STATUS_MSG_ID = parts[2]
else:
    DUMP_ID = raw_dump
    LOGO_ID = "none"

last_edit_time = 0

def get_readable_time(seconds: int) -> str:
    result = ""
    (days, remainder) = divmod(seconds, 86400)
    if int(days) != 0: result += f"{int(days)}d "
    (hours, remainder) = divmod(remainder, 3600)
    if int(hours) != 0: result += f"{int(hours)}h "
    (minutes, seconds) = divmod(remainder, 60)
    if int(minutes) != 0: result += f"{int(minutes)}m "
    result += f"{int(seconds)} sec"
    return result.strip()

async def get_duration(file_path):
    cmd =['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    try: return float(stdout.decode().strip())
    except: return 0.0

async def progress_bar(current, total, app, msg_id, action_text):
    global last_edit_time
    now = time.time()
    if now - last_edit_time > 5 or current == total:
        try:
            perc = (current / total) * 100 if total > 0 else 0
            bar_length = 14
            filled = int((perc / 100) * bar_length)
            bar = "▓" * filled + "░" * (bar_length - filled)
            
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_cloud_task_cloud")]])
            text = (
                "━━━━━━━━━━━━━━━━━━━\n"
                "☁️ 𝗚𝗜𝗧𝗛𝗨𝗕 𝗪𝗢𝗥𝗞𝗘𝗥\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"📦 𝗙𝗶𝗹𝗲: `{RENAME}`\n"
                f"▸ 𝗦𝘁𝗮𝘁𝘂𝘀: {action_text}\n\n"
                f"📊 𝗣𝗿𝗼𝗴𝗿𝗲𝘀𝘀: [{bar}] {perc:.1f}%\n"
                f"💾 𝗦𝗶𝘇𝗲: {current/(1024*1024):.1f} MB / {total/(1024*1024):.1f} MB\n\n"
                "⚙ 𝗘𝗻𝗴𝗶𝗻𝗲: Cloud Engine\n"
                "━━━━━━━━━━━━━━━━━━━"
            )
            await app.edit_message_text(CHAT_ID, msg_id, text, reply_markup=cancel_kb)
            last_edit_time = now
        except: pass

async def download_phase(app):
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_cloud_task_cloud")]])
    
    if STATUS_MSG_ID:
        msg_id = int(STATUS_MSG_ID)
        try: await app.edit_message_text(CHAT_ID, msg_id, f"⚙️ Worker Triggered: Preparing...\n📦 File: `{RENAME}`", reply_markup=cancel_kb)
        except:
            status_msg = await app.send_message(CHAT_ID, f"⚙️ Worker Triggered: Preparing...\n📦 File: `{RENAME}`", reply_markup=cancel_kb)
            msg_id = status_msg.id
    else:
        status_msg = await app.send_message(CHAT_ID, f"⚙️ Worker Triggered: Preparing...\n📦 File: `{RENAME}`", reply_markup=cancel_kb)
        msg_id = status_msg.id
    
    video_path = await app.download_media(VIDEO_ID, file_name="video.mkv", progress=progress_bar, progress_args=(app, msg_id, "📥 Downloading Video"))
    sub_path = None
    if TASK_TYPE == "hardsub" and SUB_ID != "none":
        sub_path = await app.download_media(SUB_ID, progress=progress_bar, progress_args=(app, msg_id, "📥 Downloading Subtitle"))
        
    logo_path = None
    if TASK_TYPE == "hardsub" and LOGO_ID != "none":
        logo_path = await app.download_media(LOGO_ID, progress=progress_bar, progress_args=(app, msg_id, "📥 Downloading Logo"))
        
    await app.edit_message_text(CHAT_ID, msg_id, f"🔥 Starting FFmpeg Engine...\n📦 File: `{RENAME}`\n*(Connection Paused for Safety)*", reply_markup=cancel_kb)
    return video_path, sub_path, logo_path, msg_id

async def encode_phase(app, video_path, sub_path, logo_path, msg_id):
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
            
            filter_complex = f"[1:v]scale={scale_val}[logo];[0:v]{sub_filter}[subbed];[subbed][logo]overlay={pos_val}" if sub_filter else f"[1:v]scale={scale_val}[logo];[0:v][logo]overlay={pos_val}"
            
            cmd =[
                'ffmpeg', '-y', '-i', video_path, '-i', abs_logo,
                '-filter_complex', filter_complex,
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
        engine_name = "HARDSUB ENGINE"
    else:
        vf_scale =[]
        if RESOLUTION == "720p": vf_scale = ['-vf', 'scale=-1:720']
        elif RESOLUTION == "480p": vf_scale = ['-vf', 'scale=-1:480']
        
        cmd =[
            'ffmpeg', '-y', '-i', video_path, 
            '-map', '0:v:0', '-map', '0:a?', '-map', '0:s?'
        ]
        if output.lower().endswith('.mkv'):
            cmd.extend(['-map', '0:t?'])
            
        cmd.extend(vf_scale)
        
        # Explicitly defining codecs to avoid -c copy conflict
        cmd.extend([
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '34',
            '-c:a', 'copy', '-c:s', 'copy'
        ])
        if output.lower().endswith('.mkv'):
            cmd.extend(['-c:t', 'copy'])
            
        cmd.extend(['-progress', 'pipe:1', output])
        engine_name = f"COMPRESS ENGINE ({RESOLUTION})"

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
                    speed = cur / elapsed if elapsed > 0 else 0
                    eta = (duration - cur) / speed if speed > 0 else 0
                    
                    bar_length = 14
                    filled = int((perc / 100) * bar_length)
                    bar = "▓" * filled + "░" * (bar_length - filled)
                    
                    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_cloud_task_cloud")]])
                    text = (
                        "━━━━━━━━━━━━━━━━━━━\n"
                        f"🎬 {engine_name}\n"
                        "━━━━━━━━━━━━━━━━━━━\n"
                        f"📦 𝗙𝗶𝗹𝗲: `{RENAME}`\n"
                        f"▸ 𝗦𝘁𝗮𝘁𝘂𝘀: Processing Frame...\n\n"
                        f"📊 𝗣𝗿𝗼𝗴𝗿𝗲𝘀𝘀: [{bar}] {perc:.2f}%\n"
                        f"⚡ 𝗩𝗲𝗹𝗼𝗰𝗶𝘁𝘆: {speed:.2f}x\n"
                        f"⏱ 𝗥𝗲𝗺𝗮𝗶𝗻𝗶𝗻𝗴: ~{get_readable_time(eta)}\n\n"
                        "⚙ 𝗘𝗻𝗴𝗶𝗻𝗲: GitHub Cloud Worker\n"
                        "━━━━━━━━━━━━━━━━━━━"
                    )
                    try: await app.edit_message_text(CHAT_ID, msg_id, text, reply_markup=cancel_kb)
                    except: pass
                    last_up = now
            except: pass
            
    await proc.wait()
    return output, proc.returncode

async def extract_thumbnail(video_path, thumb_path):
    cmd =['ffmpeg', '-y', '-ss', '00:00:05', '-i', video_path, '-vf', 'scale=320:-1', '-vframes', '1', thumb_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return os.path.exists(thumb_path)

async def upload_phase(app, output, returncode, msg_id):
    if returncode == 0 and os.path.exists(output):
        thumb_path = "thumb.jpg"
        has_thumb = await extract_thumbnail(output, thumb_path)
        
        await app.edit_message_text(CHAT_ID, msg_id, f"▸ Processing Done! Starting Fresh Upload...\n📦 File: `{RENAME}`")
        
        target_chat = int(DUMP_ID) if DUMP_ID != "none" else CHAT_ID
        thread = int(THREAD_ID) if THREAD_ID and THREAD_ID != "none" else None
        cap = f"✅ {TASK_TYPE.upper()} COMPLETE\n📦 File: `{RENAME}`"
        
        try:
            await app.send_document(
                chat_id=target_chat, document=output, reply_to_message_id=thread,
                thumb=thumb_path if has_thumb else None, caption=cap,
                progress=progress_bar, progress_args=(app, msg_id, "📤 Uploading Video")
            )
            if target_chat != CHAT_ID:
                await app.send_message(CHAT_ID, f"{cap}\n\nFile successfully dumped to Group!")
            await app.delete_messages(CHAT_ID, msg_id)
        except Exception as e:
            await app.edit_message_text(CHAT_ID, msg_id, f"❌ Upload Error: {str(e)}")
    else:
        await app.edit_message_text(CHAT_ID, msg_id, f"❌ **FFmpeg Error:** Failed to Process Video. (Exit Code: {returncode})")

async def main():
    app = Client("github_worker", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    msg_id = int(STATUS_MSG_ID) if STATUS_MSG_ID else None
    try:
        vid, sub, logo, msg_id = await download_phase(app)
        out, rcode = await encode_phase(app, vid, sub, logo, msg_id)
        await upload_phase(app, out, rcode, msg_id)
    except Exception as e:
        err = traceback.format_exc()
        print(err)
        if msg_id:
            try: await app.edit_message_text(CHAT_ID, msg_id, f"❌ **CRITICAL WORKER ERROR:**\n\n`{str(e)}`")
            except: pass
    finally:
        await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
