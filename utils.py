import os
import asyncio
import shutil
import time
import json
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from config import active_processes

def clean_temp_files(path):
    """Temporary files aur folders ko delete karne ke liye"""
    try:
        if os.path.isdir(path): shutil.rmtree(path)
        elif os.path.exists(path): os.remove(path)
    except: pass

def get_readable_time(seconds: int) -> str:
    """Seconds ko 1h 2m 3s format mein badalne ke liye"""
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
    """Video ki total length nikalne ke liye"""
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    try: return float(stdout.decode().strip())
    except: return 0.0

async def extract_thumbnail(video_path, thumb_path):
    """Video se cover image nikalna (Telegram standard 320px width)"""
    cmd = [
        'ffmpeg', '-y', '-ss', '00:00:05', '-i', video_path, 
        '-vf', 'scale=320:-1', 
        '-vframes', '1', thumb_path
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return os.path.exists(thumb_path)

async def get_subtitles_info(video_path):
    """MKV ke andar ke saare subtitle tracks scan karne ke liye (Optimized Fast Scan)"""
    if not os.path.exists(video_path):
        return []

    # Probesize limit ki gayi hai taaki 'Scanning...' par bot na atke
    cmd = [
        'ffprobe', '-v', 'error', 
        '-analyze_duration', '100000000', # 100MB analyze limit
        '-probesize', '100000000',        # 100MB probe limit
        '-select_streams', 's', 
        '-show_entries', 'stream=index,codec_name:stream_tags=language,title,NUMBER_OF_BYTES', 
        '-of', 'json', video_path
    ]
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, 
            stdout=asyncio.subprocess.PIPE, 
            stderr=asyncio.subprocess.PIPE
        )
        # 25 seconds ka hard timeout
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=25)
        
        if proc.returncode != 0:
            return []
            
        data = json.loads(stdout.decode())
        return data.get('streams', [])
    except Exception:
        return []

async def extract_sub_logic(video_path, stream_idx, out_path):
    """Specific track ko extract karne ki logic"""
    cmd = ['ffmpeg', '-y', '-i', video_path, '-map', f"0:{stream_idx}", '-c:s', 'copy', out_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()
    return os.path.exists(out_path)

async def mux_video(mkv_path, sub_path, output_path, chat_id, status_msg):
    """MKV aur Subtitle ko merge karne ki logic with real-time progress bar"""
    duration = await get_duration(mkv_path)
    sub_ext = os.path.splitext(sub_path)[1].lower()
    sub_codec = 'ass' if sub_ext == '.ass' else 'subrip'
    
    # FFmpeg Command
    cmd = [
        'ffmpeg', '-y', '-i', mkv_path, '-i', sub_path, 
        '-map', '0:v', '-map', '0:a?', '-map', '1:0', 
        '-c:v', 'copy', '-c:a', 'copy', f'-c:s', sub_codec, 
        '-disposition:s:0', 'default', 
        '-metadata:s:s:0', 'language=eng', 
        '-metadata:s:s:0', 'title=Hinglish', 
        '-progress', 'pipe:1', output_path
    ]
    
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
                # Har 8 second mein progress update
                if duration > 0 and (now - last_up) > 8:
                    perc = min(100, (cur / duration) * 100)
                    elapsed = now - start_time
                    speed = cur / elapsed if elapsed > 0 else 0
                    eta = (duration - cur) / speed if speed > 0 else 0
                    
                    bar_filled = int(perc / 10)
                    bar = "■" * bar_filled + "□" * (10 - bar_filled)
                    
                    text = (f"⚙️ **Muxing in Progress...**\n\n"
                            f"P: `[{bar}]` {perc:.2f}%\n"
                            f"🚀 Speed: {speed:.2f}x\n"
                            f"⏳ ETA: {get_readable_time(eta)}")
                    
                    await status_msg.edit_text(
                        text, 
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{chat_id}")]])
                    )
                    last_up = now
            except: pass
            
    await proc.wait()
    if chat_id in active_processes: del active_processes[chat_id]
    return proc.returncode == 0
