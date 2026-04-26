import os
import asyncio
import shutil
import time
import math
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from config import active_processes

def clean_temp_files(path):
    try:
        if os.path.isdir(path): shutil.rmtree(path)
        elif os.path.exists(path): os.remove(path)
    except: pass

def get_readable_time(seconds: int) -> str:
    result = ""
    (days, remainder) = divmod(seconds, 86400)
    days = int(days)
    if days != 0:
        result += f"{days}d "
    (hours, remainder) = divmod(remainder, 3600)
    hours = int(hours)
    if hours != 0:
        result += f"{hours}h "
    (minutes, seconds) = divmod(remainder, 60)
    minutes = int(minutes)
    if minutes != 0:
        result += f"{minutes}m "
    seconds = int(seconds)
    result += f"{seconds}s"
    return result

async def get_duration(file_path):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    try: return float(stdout.decode().strip())
    except: return 0.0

async def mux_video(mkv_path, sub_path, output_path, chat_id, status_msg):
    duration = await get_duration(mkv_path)
    os.makedirs("fonts", exist_ok=True)
    font_args = []
    for idx, f in enumerate(os.listdir("fonts")):
        fp = os.path.join("fonts", f)
        ext = os.path.splitext(f)[1].lower()
        mtype = "application/x-truetype-font" if ext in ['.ttf', '.ttc'] else "application/vnd.ms-opentype" if ext == '.otf' else ""
        if mtype: font_args.extend(["-attach", fp, f"-metadata:s:t:{idx}", f"mimetype={mtype}"])

    sub_ext = os.path.splitext(sub_path)[1].lower()
    sub_codec = 'ass' if sub_ext == '.ass' else 'subrip'

    cmd = [
        'ffmpeg', '-y', '-i', mkv_path, '-i', sub_path,
        '-map', '0:v', '-map', '0:a?', '-map', '1:0',
        '-c:v', 'copy', '-c:a', 'copy', f'-c:s', sub_codec,
        '-disposition:s:0', 'default', '-metadata:s:s:0', 'language=eng', '-metadata:s:s:0', 'title=Hinglish'
    ] + font_args + ['-progress', 'pipe:1', output_path]
    
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    active_processes[chat_id] = proc
    
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
                if duration > 0 and (now - last_up) > 6: # Har 6 second me update
                    perc = min(100, (cur / duration) * 100)
                    elapsed_time = now - start_time
                    speed = cur / elapsed_time if elapsed_time > 0 else 0
                    eta = (duration - cur) / speed if speed > 0 else 0
                    
                    # Progress Bar Design
                    filled = int(perc / 10)
                    bar = "■" * filled + "□" * (10 - filled)
                    
                    msg = (
                        f"⚙️ **Muxing in Progress...**\n\n"
                        f"P: `[{bar}]` {perc:.2f}%\n"
                        f"🚀 Speed: {speed:.2f}x\n"
                        f"⏳ ETA: {get_readable_time(eta)}"
                    )
                    await status_msg.edit_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{chat_id}")]]))
                    last_up = now
            except: pass
            
    await proc.wait()
    if chat_id in active_processes: del active_processes[chat_id]
    return proc.returncode == 0
