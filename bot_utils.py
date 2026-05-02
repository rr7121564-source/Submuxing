import os
import asyncio
import shutil
import time
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from config import active_processes

def sc(text: str) -> str:
    return text.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ"))

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

async def get_media_info(file_path):
    cmd =['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await proc.communicate()
    try:
        import json
        data = json.loads(stdout.decode())
        format_info = data.get('format', {})
        streams = data.get('streams',[])
        
        duration = float(format_info.get('duration', 0))
        size = int(format_info.get('size', 0)) / (1024 * 1024)
        bitrate = int(format_info.get('bit_rate', 0)) / 1000
        
        v_stream = next((s for s in streams if s['codec_type'] == 'video'), None)
        a_stream = next((s for s in streams if s['codec_type'] == 'audio'), None)
        
        res = f"⏱ " + sc("Dᴜʀᴀᴛɪᴏɴ:") + f" {get_readable_time(duration)}\n"
        res += f"💾 " + sc("Sɪᴢᴇ:") + f" {size:.2f} MB\n"
        res += f"⚡ " + sc("Bɪᴛʀᴀᴛᴇ:") + f" {bitrate:.0f} kbps\n\n"
        
        if v_stream:
            res += f"🎬 " + sc("Vɪᴅᴇᴏ:") + f" {v_stream.get('codec_name', 'unknown').upper()} | {v_stream.get('width', '?')}x{v_stream.get('height', '?')}\n"
        if a_stream:
            res += f"🎵 " + sc("Aᴜᴅɪᴏ:") + f" {a_stream.get('codec_name', 'unknown').upper()} | {a_stream.get('sample_rate', '?')} Hz\n"
            
        return res
    except Exception:
        return sc("❌ Fᴀɪʟᴇᴅ ᴛᴏ ᴘᴀʀsᴇ ᴍᴇᴅɪᴀ ɪɴғᴏ.")

async def generate_screenshots(file_path, num_screens, output_folder):
    duration = await get_duration(file_path)
    if duration <= 0: return[]
    interval = duration / (num_screens + 1)
    os.makedirs(output_folder, exist_ok=True)
    images =[]
    
    for i in range(1, num_screens + 1):
        timestamp = interval * i
        out_path = os.path.join(output_folder, f"screen_{i}.jpg")
        cmd =['ffmpeg', '-y', '-ss', str(timestamp), '-i', file_path, '-vframes', '1', '-q:v', '2', out_path]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.communicate()
        if os.path.exists(out_path):
            images.append(out_path)
            
    return images

async def mux_video(mkv_path, sub_path, output_path, chat_id, status_msg, file_name, user_id, mode="mux", task_fonts_dir="fonts"):
    duration = await get_duration(mkv_path)
    font_args =[]
    
    if os.path.exists(task_fonts_dir):
        for idx, f in enumerate(os.listdir(task_fonts_dir)):
            fp = os.path.join(task_fonts_dir, f)
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
    ] + font_args +['-progress', 'pipe:1', output_path]
    
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    
    proc_key = f"{chat_id}_{user_id}"
    active_processes[proc_key] = proc
    
    start_time = time.time()
    last_up = 0
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("❌ Cᴀɴᴄᴇʟ"), callback_data=f"cancel_{proc_key}_local")]])

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
                        "🎬 " + sc("ᴍᴜxɪɴɢ ɪɴ ᴘʀᴏɢʀᴇss") + "\n"
                        "───────────────────\n"
                        f"📦 " + sc("ғɪʟᴇ:") + f" `{file_name}`\n"
                        f"📊 " + sc("ᴘʀᴏɢʀᴇss:") + f" {bar} {perc:.1f}%\n"
                        f"🚀 " + sc("sᴘᴇᴇᴅ:") + f" {speed:.2f}x\n"
                        f"⏳ " + sc("ᴇᴛᴀ:") + f" {get_readable_time(eta)}\n"
                        "───────────────────\n"
                        "🐍 " + sc("ʙᴏᴀ ʜᴀɴᴄᴏᴄᴋ ʟᴏᴄᴀʟ ᴇɴɢɪɴᴇ")
                    )
                    
                    try: await status_msg.edit_text(text, parse_mode="Markdown", reply_markup=cancel_kb)
                    except: pass
                    last_up = now
            except: pass
            
    await proc.wait()
    if proc_key in active_processes: del active_processes[proc_key]
    return proc.returncode == 0
