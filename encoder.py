import os, sys, time, asyncio
import pyrogram.utils

def patched_get_peer_type(peer_id: int) -> str:
    val = str(peer_id)
    if val.startswith("-100"): return "channel"
    elif val.startswith("-"): return "chat"
    else: return "user"

pyrogram.utils.get_peer_type = patched_get_peer_type
from pyrogram import Client

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

TASK_TYPE = os.getenv("TASK_TYPE")
VIDEO_ID = os.getenv("VIDEO_ID")
SUB_ID = os.getenv("SUB_ID")
RENAME = os.getenv("RENAME", "output.mp4")

# Group/PM routing
USER_ID = int(os.getenv("USER_ID"))
ORIGIN_CHAT = int(os.getenv("ORIGIN_CHAT"))
DUMP_ID = os.getenv("DUMP_ID")
THREAD_ID = os.getenv("THREAD_ID")

# Custom Settings
THUMB_ID = os.getenv("THUMB_ID")
FONT_ID = os.getenv("FONT_ID")

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
            
            text = (
                f"🎬  GITHUB WORKER \n"
                "──────────────────────────\n"
                f"▸ Status    : {action_text}\n"
                f"▸ Progress  : {bar}  {perc:.1f}%\n"
                f"▸ Size      : {current/(1024*1024):.1f} MB / {total/(1024*1024):.1f} MB\n"
                "──────────────────────────\n"
                "⚙ Running on Cloud Engine"
            )
            await app.edit_message_text(ORIGIN_CHAT, msg_id, text)
            last_edit_time = now
        except: pass

async def download_phase():
    app = Client("worker_down", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    
    status_msg = await app.send_message(ORIGIN_CHAT, "⚙️ Worker Triggered: Preparing...")
    msg_id = status_msg.id
    
    os.makedirs("fonts", exist_ok=True)
    video_path = await app.download_media(VIDEO_ID, file_name="video.mkv", progress=progress_bar, progress_args=(app, msg_id, "📥 Downloading Video"))
    
    sub_path = None
    if TASK_TYPE == "hardsub" and SUB_ID != "none":
        sub_path = await app.download_media(SUB_ID, progress=progress_bar, progress_args=(app, msg_id, "📥 Downloading Subtitle"))
        
    if THUMB_ID != "none":
        await app.download_media(THUMB_ID, file_name="thumb.jpg")
        
    if FONT_ID != "none":
        await app.download_media(FONT_ID, file_name=f"fonts/custom_font_{USER_ID}.ttf")
        
    await app.edit_message_text(ORIGIN_CHAT, msg_id, "🔥 Starting FFmpeg Engine...\n*(Connection Paused for Safety)*")
    await app.stop() 
    return video_path, sub_path, msg_id

async def encode_phase(video_path, sub_path, msg_id):
    output = RENAME
    duration = await get_duration(video_path)
    
    font_args =[]
    for idx, f in enumerate(os.listdir("fonts")):
        fp = os.path.join("fonts", f)
        ext = os.path.splitext(f)[1].lower()
        mtype = "application/x-truetype-font" if ext in ['.ttf', '.ttc'] else "application/vnd.ms-opentype" if ext == '.otf' else ""
        if mtype: font_args.extend(["-attach", fp, f"-metadata:s:t:{idx}", f"mimetype={mtype}"])

    if TASK_TYPE == "hardsub":
        abs_sub = os.path.abspath(sub_path).replace('\\', '/').replace(':', '\\:')
        fonts_dir = os.path.abspath("fonts").replace('\\', '/').replace(':', '\\:')
        cmd =[
            'ffmpeg', '-y', '-i', video_path, '-sn', 
            '-vf', f"subtitles='{abs_sub}':fontsdir='{fonts_dir}'", 
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '34', '-c:a', 'copy'
        ] + ['-progress', 'pipe:1', output]
        engine_name = "HARDSUB ENGINE"
    else:
        cmd =[
            'ffmpeg', '-y', '-i', video_path, 
            '-map', '0:v', '-map', '0:a?', '-map', '0:s?', 
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '34', '-c:a', 'copy', '-c:s', 'copy'
        ] + font_args +['-progress', 'pipe:1', output]
        engine_name = "COMPRESSION ENGINE"

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
                    speed = cur / elapsed if elapsed > 0 else 0
                    eta = (duration - cur) / speed if speed > 0 else 0
                    
                    bar_length = 14
                    filled = int((perc / 100) * bar_length)
                    bar = "▓" * filled + "░" * (bar_length - filled)
                    
                    text = (
                        f"🎬  {engine_name} \n"
                        "──────────────────────────\n"
                        f"▸ Status    : Processing Frame...\n"
                        f"▸ Progress  : {bar}  {perc:.2f}%\n"
                        f"▸ Velocity  : {speed:.2f}x\n"
                        f"▸ Remaining : ~{get_readable_time(eta)}\n"
                        "──────────────────────────\n"
                        "⚙ GitHub Cloud Worker"
                    )
                    try: await app.edit_message_text(ORIGIN_CHAT, msg_id, text)
                    except: pass
                    last_up = now
            except: pass
            
    await proc.wait()
    await app.stop()
    return output, proc.returncode

async def extract_thumbnail(video_path, thumb_path):
    cmd =['ffmpeg', '-y', '-ss', '00:00:05', '-i', video_path, '-vf', 'scale=320:-1', '-vframes', '1', thumb_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return os.path.exists(thumb_path)

async def upload_phase(output, returncode, msg_id):
    app = Client("worker_up", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    
    if returncode == 0 and os.path.exists(output):
        thumb_path = "thumb.jpg"
        if not os.path.exists(thumb_path):
            await extract_thumbnail(output, thumb_path)
            
        await app.edit_message_text(ORIGIN_CHAT, msg_id, "▸ Processing Done! Sending to your PM...")
        cap = f"✅ {TASK_TYPE.upper()} COMPLETE"
        
        # 1. Send to User PM
        try:
            await app.send_document(chat_id=USER_ID, document=output, thumb=thumb_path if os.path.exists(thumb_path) else None, caption=cap, progress=progress_bar, progress_args=(app, msg_id, "📤 Uploading Video"))
            if ORIGIN_CHAT != USER_ID:
                await app.send_message(ORIGIN_CHAT, f"{cap}\nFile successfully sent to your Private Messages!")
        except Exception as e:
            await app.send_message(ORIGIN_CHAT, f"❌ I couldn't send the file to your PM. Did you block me or forget to start me? Error: {e}")
            
        # 2. Send to Dump Group
        if DUMP_ID != "none":
            try: await app.send_document(chat_id=int(DUMP_ID), document=output, reply_to_message_id=int(THREAD_ID) if THREAD_ID != "none" else None, thumb=thumb_path if os.path.exists(thumb_path) else None, caption=cap)
            except: pass
            
        await app.delete_messages(ORIGIN_CHAT, msg_id)
    else:
        await app.edit_message_text(ORIGIN_CHAT, msg_id, f"❌ **FFmpeg Error:** Failed to Process Video.")
    
    await app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    vid, sub, mid = loop.run_until_complete(download_phase())
    out, rcode = loop.run_until_complete(encode_phase(vid, sub, mid))
    loop.run_until_complete(upload_phase(out, rcode, mid))
