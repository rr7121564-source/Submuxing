import asyncio
import json
import os
import time
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

# Global dictionary to track active subprocesses for cancellation
active_processes = {}

async def get_duration(file_path):
    """Uses ffprobe to get the exact duration of the media in seconds."""
    cmd =[
        'ffprobe', '-v', 'error', '-show_entries',
        'format=duration', '-of',
        'default=noprint_wrappers=1:nokey=1', file_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, 
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return 0.0

async def extract_subtitles(mkv_path, original_name):
    """Detects subtitle streams and extracts them natively."""
    cmd =[
        'ffprobe', '-v', 'error', '-select_streams', 's',
        '-show_entries', 'stream=index,codec_name', '-of', 'json', mkv_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    stdout, _ = await proc.communicate()
    
    try:
        data = json.loads(stdout.decode())
    except json.JSONDecodeError:
        return []

    extracted_files =[]
    base_name = os.path.splitext(original_name)[0]

    for stream in data.get('streams',[]):
        index = stream['index']
        codec = stream.get('codec_name', 'subrip')
        # Map codec to extension
        ext = ".ass" if codec == "ass" else ".srt" if codec == "subrip" else ".vtt"

        outfile = f"{base_name}_{index}{ext}"
        ext_cmd =[
            'ffmpeg', '-y', '-i', mkv_path, '-map', f"0:{index}", '-c:s', 'copy', outfile
        ]
        ext_proc = await asyncio.create_subprocess_exec(*ext_cmd, stderr=asyncio.subprocess.DEVNULL)
        await ext_proc.wait()

        if ext_proc.returncode == 0 and os.path.exists(outfile):
            extracted_files.append(outfile)

    return extracted_files

async def mux_video(mkv_path, sub_path, output_path, chat_id, status_msg):
    """Muxes subtitles and dynamically attaches all fonts via FFmpeg stdout progress parsing."""
    duration = await get_duration(mkv_path)
    
    # Dynamically build font attachments
    os.makedirs("fonts", exist_ok=True)
    fonts_dir = "fonts"
    font_args =[]
    font_index = 0

    for font_file in os.listdir(fonts_dir):
        font_path = os.path.join(fonts_dir, font_file)
        ext = os.path.splitext(font_file)[1].lower()
        mimetype = "application/x-truetype-font" if ext in['.ttf', '.ttc'] else "application/vnd.ms-opentype" if ext == '.otf' else ""
        if mimetype:
            font_args.extend([
                "-attach", font_path, 
                f"-metadata:s:t:{font_index}", f"mimetype={mimetype}"
            ])
            font_index += 1

    # Main muxing command (-c copy ensures no re-encoding)
    cmd =[
        'ffmpeg', '-y', '-i', mkv_path, '-i', sub_path,
        '-map', '0', '-map', '1',
        '-c', 'copy', '-c:s', 'copy'
    ] + font_args + [
        '-progress', 'pipe:1', output_path
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL
    )
    active_processes[chat_id] = proc

    start_time = time.time()
    last_update_time = start_time
    speed = "N/A"

    # Live Progress Parsing from pipe:1 stdout
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        line = line.decode('utf-8').strip()

        if line.startswith('speed='):
            speed = line.split('=')[1]

        if line.startswith('out_time_us='):
            out_time_us = line.split('=')[1]
            if out_time_us.isdigit() and duration > 0:
                current_time = int(out_time_us) / 1000000
                percentage = min(100, (current_time / duration) * 100)

                now = time.time()
                # Update Telegram message every 3 seconds to avoid FloodWait limits
                if now - last_update_time > 3:
                    last_update_time = now
                    elapsed = now - start_time
                    if percentage > 0:
                        eta_secs = (elapsed / percentage) * (100 - percentage)
                        eta_str = time.strftime('%H:%M:%S', time.gmtime(eta_secs))
                    else:
                        eta_str = "Calculating..."

                    text = (f"⏳ <b>Muxing Progress</b>\n\n"
                            f"<b>Progress:</b> <code>{percentage:.2f}%</code>\n"
                            f"<b>Speed:</b> <code>{speed}</code>\n"
                            f"<b>ETA:</b> <code>{eta_str}</code>")

                    cancel_kbd = InlineKeyboardMarkup([[
                        InlineKeyboardButton("Cancel", callback_data=f"cancel_{chat_id}")
                    ]])
                    try:
                        await status_msg.edit_text(text, reply_markup=cancel_kbd, parse_mode='HTML')
                    except Exception:
                        pass # Ignore identical text edits

    await proc.wait()
    if chat_id in active_processes:
        del active_processes[chat_id]

    return proc.returncode == 0
