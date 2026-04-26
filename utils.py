import os
import asyncio
import shutil
import time
import json
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
    if days != 0: result += f"{days}d "
    (hours, remainder) = divmod(remainder, 3600)
    hours = int(hours)
    if hours != 0: result += f"{hours}h "
    (minutes, seconds) = divmod(remainder, 60)
    minutes = int(minutes)
    if minutes != 0: result += f"{minutes}m "
    seconds = int(seconds)
    result += f"{seconds}s"
    return result or "0s"

async def get_duration(file_path):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    try: return float(stdout.decode().strip())
    except: return 0.0

async def extract_thumbnail(video_path, thumb_path):
    # Scale=320 is required for Telegram Document Preview
    cmd = ['ffmpeg', '-y', '-ss', '00:00:05', '-i', video_path, '-vf', 'scale=320:-1', '-vframes', '1', thumb_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return os.path.exists(thumb_path)

async def get_subtitles_info(video_path):
    # Optimized scan with Analyze Duration & Probesize limit
    cmd = [
        'ffprobe', '-v', 'error', '-analyze_duration', '1000000', '-probesize', '1000000',
        '-select_streams', 's', '-show_entries', 'stream=index,codec_name:stream_tags=language,NUMBER_OF_BYTES', 
        '-of', 'json', video_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        return json.loads(stdout.decode()).get('streams', [])
    except: return []

async def extract_sub_logic(video_path, stream_idx, out_path):
    cmd = ['ffmpeg', '-y', '-i', video_path, '-map', f"0:{stream_idx}", '-c:s', 'copy', out_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()
    return os.path.exists(out_path)

async def mux_video(mkv_path, sub_path, output_path, chat_id, status_msg):
    duration = await get_duration(mkv_path)
    sub_ext = os.path.splitext(sub_path)[1].lower()
    sub_codec = 'ass' if sub_ext == '.ass' else 'subrip'
    
    cmd = ['ffmpeg', '-y', '-i', mkv_path, '-i', sub_path, '-map', '0:v', '-map', '0:a?', '-map', '1:0', '-c:v', 'copy', '-c:a', 'copy', f'-c:s', sub_codec, '-disposition:s:0', 'default', '-metadata:s:s:0', 'language=eng', '-metadata:s:s:0', 'title=Hinglish', '-progress', 'pipe:1', output_path]
    
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    active_processes[chat_id] = proc
    start_time, last_up = time.time(), 0
    while True:
        line = await proc.stdout.readline()
        if not line: break
        line = line.decode('utf-8').strip()
        if line.startswith('out_time_us='):
            try:
                cur, now = int(line.split('=')[1]) / 1000000, time.time()
                if duration > 0 and (now - last_up) > 8:
                    perc = min(100, (cur / duration) * 100)
                    speed = cur / (now - start_time) if (now - start_time) > 0 else 0
                    eta = (duration - cur) / speed if speed > 0 else 0
                    bar = "■" * int(perc / 10) + "□" * (10 - int(perc / 10))
                    text = (f"⚙️ **Muxing...**\n\n"
                            f"P: `[{bar}]` {perc:.2f}%\n"
                            f"🚀 Speed: {speed:.2f}x\n"
                            f"⏳ ETA: {get_readable_time(eta)}")
                    await status_msg.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{chat_id}")]]))
                    last_up = now
            except: pass
    await proc.wait()
    if chat_id in active_processes: del active_processes[chat_id]
    return proc.returncode == 0
