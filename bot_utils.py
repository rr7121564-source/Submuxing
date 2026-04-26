import os
import asyncio
import shutil
import time
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
    if days != 0: result += f"{days}d "
    (hours, remainder) = divmod(remainder, 3600)
    hours = int(hours)
    if hours != 0: result += f"{hours}h "
    (minutes, seconds) = divmod(remainder, 60)
    minutes = int(minutes)
    if minutes != 0: result += f"{minutes}m "
    seconds = int(seconds)
    result += f"{seconds} sec"
    return result.strip()

async def get_duration(file_path):
    cmd =['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    try: return float(stdout.decode().strip())
    except: return 0.0

async def extract_thumbnail(video_path, thumb_path):
    """Video se cover nikalna aur use 320px scale karna"""
    cmd =[
        'ffmpeg', '-y', '-ss', '00:00:05', '-i', video_path, 
        '-vf', 'scale=320:-1', 
        '-vframes', '1', thumb_path
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return os.path.exists(thumb_path)

async def mux_video(mkv_path, sub_path, output_path, chat_id, status_msg):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    
    duration = await get_duration(mkv_path)
    os.makedirs("fonts", exist_ok=True)
    font_args =[]
    for idx, f in enumerate(os.listdir("fonts")):
        fp = os.path.join("fonts", f)
        ext = os.path.splitext(f)[1].lower()
        mtype = "application/x-truetype-font" if ext in ['.ttf', '.ttc'] else "application/vnd.ms-opentype" if ext == '.otf' else ""
        if mtype: font_args.extend(["-attach", fp, f"-metadata:s:t:{idx}", f"mimetype={mtype}"])

    sub_ext = os.path.splitext(sub_path)[1].lower()
    sub_codec = 'ass' if sub_ext == '.ass' else 'subrip'

    cmd =[
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
                time_str = line.split('=')[1]
                if time_str.lower() == 'n/a': continue 
                
                cur = int(time_str) / 1000000
                now = time.time()
                
                if duration > 0 and (now - last_up) > 3:
                    perc = min(100, (cur / duration) * 100)
                    elapsed = now - start_time
                    speed = cur / elapsed if elapsed > 0 else 0
                    eta = (duration - cur) / speed if speed > 0 else 0
                    
                    bar_length = 14
                    filled_blocks = int((perc / 100) * bar_length)
                    bar = "▓" * filled_blocks + "░" * (bar_length - filled_blocks)
                    
                    text = (
                        "🎬  SUBTITLE SYNC ENGINE \n"
                        "──────────────────────────\n"
                        f"▸ Status    : Muxing Subtitle...\n"
                        f"▸ Progress  : {bar}  {perc:.2f}%\n"
                        f"▸ Velocity  : {speed:.2f}x\n"
                        f"▸ Remaining : ~{get_readable_time(eta)}\n"
                        "──────────────────────────\n"
                        "⚙ Running silently in background"
                    )
                    
                    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Process", callback_data=f"cancel_{chat_id}")]])
                    
                    try: await status_msg.edit_text(text, reply_markup=cancel_markup)
                    except: pass
                    last_up = now
            except: pass
            
    await proc.wait()
    if chat_id in active_processes: del active_processes[chat_id]
    return proc.returncode == 0
