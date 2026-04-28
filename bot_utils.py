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

async def extract_thumbnail(video_path, thumb_path):
    cmd =['ffmpeg', '-y', '-ss', '00:00:05', '-i', video_path, '-vf', 'scale=320:-1', '-vframes', '1', thumb_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return os.path.exists(thumb_path)

async def mux_video(mkv_path, sub_path, output_path, chat_id, status_msg, file_name, user_id, mode="mux", task_fonts_dir="fonts"):
    # Note: InlineKeyboardMarkup import ki ab zarurat nahi hai agar button nahi chahiye
    
    duration = await get_duration(mkv_path)
    font_args =[]
    
    if os.path.exists(task_fonts_dir):
        for idx, f in enumerate(os.listdir(task_fonts_dir)):
            fp = os.path.join(task_fonts_dir, f)
            ext = os.path.splitext(f)[1].lower()
            mtype = "application/x-truetype-font" if ext in['.ttf', '.ttc'] else "application/vnd.ms-opentype" if ext == '.otf' else ""
            if mtype: font_args.extend(["-attach", fp, f"-metadata:s:t:{idx}", f"mimetype={mtype}"])

    sub_ext = os.path.splitext(sub_path)[1].lower()
    sub_codec = 'ass' if sub_ext == '.ass' else 'subrip'

    cmd =[
        'ffmpeg', '-y', '-i', mkv_path, '-i', sub_path,
        '-map', '0:v', '-map', '0:a?', '-map', '1:0',
        '-c:v', 'copy', '-c:a', 'copy', f'-c:s', sub_codec,
        '-disposition:s:0', 'default', '-metadata:s:s:0', 'language=eng', '-metadata:s:s:0', 'title=Hinglish'
    ] + font_args +['-progress', 'pipe:1', output_path]
    
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    
    proc_key = f"{chat_id}_{user_id}"
    active_processes[proc_key] = proc
    
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
                
                if duration > 0 and (now - last_up) > 5: 
                    perc = min(100, (cur / duration) * 100)
                    elapsed = now - start_time
                    speed = cur / elapsed if elapsed > 0 else 0
                    eta = (duration - cur) / speed if speed > 0 else 0
                    
                    bar_length = 12
                    filled = int((perc / 100) * bar_length)
                    bar = "▓" * filled + "░" * (bar_length - filled)
                    
                    text = (
                        "🎬 **MUXING IN PROGRESS**\n"
                        "───────────────────\n"
                        f"📦 **File:** `{file_name}`\n"
                        f"📊 **Progress:** {bar} {perc:.1f}%\n"
                        f"🚀 **Speed:** {speed:.2f}x\n"
                        f"⏳ **ETA:** {get_readable_time(eta)}\n"
                        "───────────────────\n"
                        "⚙️ *Engine: FFmpeg Local Engine*"
                    )
                    
                    # Button (reply_markup) wala hissa yahan se hata diya gaya hai
                    try: await status_msg.edit_text(text, parse_mode="Markdown")
                    except: pass
                    last_up = now
            except: pass
            
    await proc.wait()
    if proc_key in active_processes: del active_processes[proc_key]
    return proc.returncode == 0
