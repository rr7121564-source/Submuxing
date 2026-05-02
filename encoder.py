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

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

TASK_TYPE = os.getenv("TASK_TYPE")
VIDEO_ID = os.getenv("VIDEO_ID")
SUB_ID = os.getenv("SUB_ID")
RENAME = os.getenv("RENAME", "output.mp4")
CHAT_ID = int(os.getenv("CHAT_ID"))
THREAD_ID = os.getenv("THREAD_ID")

raw_dump = os.getenv("DUMP_ID", "none")
STATUS_MSG_ID = None
RESOLUTION = "original"
LOGO_ID = "none"
THUMB_ID = "none"
ORIG_NAME = "video.mkv"

if ":::" in raw_dump:
    parts = raw_dump.split(":::")
    DUMP_ID = parts[0]
    LOGO_ID = parts[1]
    STATUS_MSG_ID = parts[2]
    RESOLUTION = parts[3]
    if len(parts) > 4: THUMB_ID = parts[4]
    if len(parts) > 5: ORIG_NAME = parts[5]
else:
    DUMP_ID = raw_dump

last_edit_time = 0
start_time_manual = time.time()

def get_readable_time(seconds: int) -> str:
    if seconds < 0: return "0s"
    (days, remainder) = divmod(int(seconds), 86400)
    (hours, remainder) = divmod(remainder, 3600)
    (minutes, seconds) = divmod(remainder, 60)
    res = ""
    if days: res += f"{days}d, "
    if hours: res += f"{hours}h, "
    if minutes: res += f"{minutes}m, "
    res += f"{seconds}s"
    return res

async def get_duration(file_path):
    cmd =['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    try: return float(stdout.decode().strip())
    except: return 0.0

async def progress_bar(current, total, app, msg_id, action_text, file_display_name):
    global last_edit_time, start_time_manual
    now = time.time()
    if now - last_edit_time > 5 or current == total:
        try:
            perc = (current / total) * 100 if total > 0 else 0
            # Custom 10-char bar: [▱▱▱▱▱▱▱▱▱▱]
            filled = int(perc / 10)
            bar = "▰" * filled + "▱" * (10 - filled)
            
            elapsed = now - start_time_manual
            speed = current / elapsed if elapsed > 0 else 0
            eta = (total - current) / speed if speed > 0 else 0
            
            speed_str = f"{speed/(1024*1024):.2f} MB/s" if speed > 1024*1024 else f"{speed/1024:.2f} KB/s"
            size_cur = f"{current/(1024*1024):.1f} MB"
            size_tot = f"{total/(1024*1024):.1f} MB"

            text = (
                f"📥 **{action_text}...**\n\n"
                f"📄 **File:** `{file_display_name}`\n\n"
                f"📊 [{bar}] {perc:.2f}%\n"
                f"🚀 **Speed:** {speed_str}\n"
                f"💾 **Size:** {size_cur} / {size_tot}\n"
                f"⏱ **ETA:** {get_readable_time(eta)}"
            )
            await app.edit_message_text(CHAT_ID, msg_id, text)
            last_edit_time = now
        except: pass

async def download_phase():
    global start_time_manual
    app = Client("worker_down", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    
    msg_id = int(STATUS_MSG_ID) if STATUS_MSG_ID else (await app.send_message(CHAT_ID, "Preparing Worker...")).id
    
    start_time_manual = time.time()
    # Phase 1: Use ORIG_NAME
    video_path = await app.download_media(VIDEO_ID, file_name="video.mkv", progress=progress_bar, progress_args=(app, msg_id, "Downloading", ORIG_NAME))
    
    sub_path = None
    if TASK_TYPE == "hardsub" and SUB_ID != "none":
        start_time_manual = time.time()
        sub_path = await app.download_media(SUB_ID, progress=progress_bar, progress_args=(app, msg_id, "Downloading Sub", "Subtitle File"))
        
    logo_path = None
    if LOGO_ID != "none":
        logo_path = await app.download_media(LOGO_ID)
        
    custom_thumb = None
    if THUMB_ID != "none":
        custom_thumb = await app.download_media(THUMB_ID, file_name="thumb.jpg")
        
    await app.stop() 
    return video_path, sub_path, logo_path, msg_id, custom_thumb

async def encode_phase(video_path, sub_path, logo_path, msg_id):
    output = RENAME
    duration = await get_duration(video_path)
    
    # VF construction
    abs_sub = os.path.abspath(sub_path).replace('\\', '/').replace(':', '\\:') if sub_path else ""
    sub_filter = f"subtitles='{abs_sub}'" if abs_sub else ""
    
    if logo_path:
        abs_logo = os.path.abspath(logo_path).replace('\\', '/').replace(':', '\\:')
        vf = f"[1:v]scale=120:-1[logo];[0:v]{sub_filter}[subbed];[subbed][logo]overlay=main_w-overlay_w-15:15" if sub_filter else f"[1:v]scale=120:-1[logo];[0:v][logo]overlay=main_w-overlay_w-15:15"
        cmd = ['ffmpeg', '-y', '-i', video_path, '-i', abs_logo, '-filter_complex', vf, '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '32', '-c:a', 'copy', '-progress', 'pipe:1', output]
    else:
        cmd = ['ffmpeg', '-y', '-i', video_path, '-vf', sub_filter, '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '32', '-c:a', 'copy', '-progress', 'pipe:1', output] if sub_filter else ['ffmpeg', '-y', '-i', video_path, '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '32', '-c:a', 'copy', '-progress', 'pipe:1', output]

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
                cur = int(line.split('=')[1]) / 1000000
                now = time.time()
                if duration > 0 and (now - last_up) > 8:
                    perc = min(100, (cur / duration) * 100)
                    filled = int(perc / 10)
                    bar = "▰" * filled + "▱" * (10 - filled)
                    elapsed = now - start_time
                    speed = cur / elapsed if elapsed > 0 else 0
                    eta = (duration - cur) / speed if speed > 0 else 0
                    
                    text = (
                        f"⚙️ **Encoding Video...**\n\n"
                        f"📄 **File:** `{RENAME}`\n\n"
                        f"📊 [{bar}] {perc:.2f}%\n"
                        f"🚀 **Velocity:** {speed:.2f}x\n"
                        f"⏱ **ETA:** {get_readable_time(eta)}"
                    )
                    await app.edit_message_text(CHAT_ID, msg_id, text)
                    last_up = now
            except: pass
    await proc.wait()
    await app.stop()
    return output

async def upload_phase(output, msg_id, custom_thumb):
    global start_time_manual
    app = Client("worker_up", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    
    if os.path.exists(output):
        start_time_manual = time.time()
        target = int(DUMP_ID) if DUMP_ID != "none" else CHAT_ID
        thread = int(THREAD_ID) if THREAD_ID != "none" else None
        
        await app.send_document(
            chat_id=target, document=output, caption=f"✅ Processed: `{RENAME}`",
            thumb=custom_thumb if custom_thumb else None,
            message_thread_id=thread,
            progress=progress_bar, progress_args=(app, msg_id, "Uploading", RENAME)
        )
        await app.delete_messages(CHAT_ID, msg_id)
    else:
        await app.edit_message_text(CHAT_ID, msg_id, "FFmpeg Error!")
    await app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    vid, sub, logo, mid, thumb = loop.run_until_complete(download_phase())
    out = loop.run_until_complete(encode_phase(vid, sub, logo, mid))
    loop.run_until_complete(upload_phase(out, mid, thumb))
