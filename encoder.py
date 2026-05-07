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

def sc(text: str) -> str:
    if not isinstance(text, str): return str(text)
    return text.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ"))

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

TASK_TYPE = os.getenv("TASK_TYPE", "hardsub")
VIDEO_ID = os.getenv("VIDEO_ID", "")
SUB_ID = os.getenv("SUB_ID", "none")
RENAME = os.getenv("RENAME", "output.mp4")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
THREAD_ID = os.getenv("THREAD_ID", "none")

# User Settings from Payload
CRF = os.getenv("CRF", "34")
PRESET = os.getenv("PRESET", "ultrafast")

raw_dump = os.getenv("DUMP_ID", "none")
STATUS_MSG_ID = None
RESOLUTION = "original"
ORIG_NAME = RENAME
VIDEO_MSG_ID = None

if ":::" in raw_dump:
    parts = raw_dump.split(":::")
    DUMP_ID = parts[0]
    if len(parts) > 1: LOGO_ID = parts[1]
    else: LOGO_ID = "none"
    if len(parts) > 2: STATUS_MSG_ID = parts[2]
    if len(parts) > 3: RESOLUTION = parts[3]
    if len(parts) > 4: ORIG_NAME = parts[4]
    if len(parts) > 5: VIDEO_MSG_ID = parts[5]
else:
    DUMP_ID, LOGO_ID = raw_dump, "none"

last_edit_time = 0

def get_readable_time(seconds) -> str:
    try: seconds = int(float(seconds))
    except: seconds = 0
    res = ""
    d, r = divmod(seconds, 86400)
    if d: res += f"{d}d "
    h, r = divmod(r, 3600)
    if h: res += f"{h}h "
    m, s = divmod(r, 60)
    if m: res += f"{m}m "
    res += f"{s}s"
    return res.strip()

async def get_duration(file_path):
    cmd =['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        return float(stdout.decode().strip())
    except: return 0.0

async def progress_bar(current, total, app, msg_id, action_text, current_file_name, start_time):
    global last_edit_time
    now = time.time()
    if now - last_edit_time > 5 or current == total:
        try:
            perc = (current / total) * 100 if total > 0 else 0
            bar = "▰" * int(perc / 10) + "▱" * (10 - int(perc / 10))
            elapsed = now - start_time
            speed = current / elapsed if elapsed > 0 else 0
            eta = get_readable_time((total - current) / speed) if speed > 0 else "0s"
            text = (f"🎬 " + sc("ᴄʟᴏᴜᴅ ᴡᴏʀᴋᴇʀ") + f"\n▸ {action_text}\n📊 [{bar}] {perc:.2f}%\n🚀 {speed/(1024*1024):.2f} MB/s\n⏱ ETA: {eta}")
            await app.edit_message_text(CHAT_ID, msg_id, text)
            last_edit_time = now
        except: pass

async def download_phase():
    app = Client("worker_down", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    msg_id = int(STATUS_MSG_ID) if STATUS_MSG_ID else (await app.send_message(CHAT_ID, sc("⚙️ Wᴏʀᴋᴇʀ Pʀᴇᴘᴀʀɪɴɢ..."))).id
    v_path = await app.download_media(VIDEO_ID, file_name="video.mkv", progress=progress_bar, progress_args=(app, msg_id, "Dᴏᴡɴʟᴏᴀᴅɪɴɢ Vɪᴅᴇᴏ", ORIG_NAME, time.time()))
    s_path = await app.download_media(SUB_ID, progress=progress_bar, progress_args=(app, msg_id, "Dᴏᴡɴʟᴏᴀᴅɪɴɢ Sᴜʙ", ORIG_NAME, time.time())) if SUB_ID != "none" else None
    l_path = await app.download_media(LOGO_ID, progress=progress_bar, progress_args=(app, msg_id, "Dᴏᴡɴʟᴏᴀᴅɪɴɢ Lᴏɢᴏ", ORIG_NAME, time.time())) if LOGO_ID != "none" else None
    await app.stop()
    return v_path, s_path, l_path, msg_id

async def encode_phase(video_path, sub_path, logo_path, msg_id):
    duration = await get_duration(video_path)
    output = RENAME
    if TASK_TYPE == "hardsub":
        abs_sub = os.path.abspath(sub_path).replace('\\', '/').replace(':', '\\:') if sub_path else ""
        sub_filter = f"subtitles='{abs_sub}'" if abs_sub else ""
        if logo_path:
            abs_logo = os.path.abspath(logo_path).replace('\\', '/').replace(':', '\\:')
            f_complex = f"[1:v]scale=120:-1[l];[0:v]{sub_filter or 'null'}[v];[v][l]overlay=main_w-overlay_w-15:15[outv]"
            cmd = ['ffmpeg', '-y', '-i', video_path, '-i', abs_logo, '-filter_complex', f_complex, '-map', '[outv]', '-map', '0:a?', '-c:v', 'libx264', '-preset', PRESET, '-crf', CRF, '-c:a', 'copy', '-progress', 'pipe:1', output]
        else:
            cmd = ['ffmpeg', '-y', '-i', video_path, '-vf', sub_filter or "copy", '-c:v', 'libx264', '-preset', PRESET, '-crf', CRF, '-c:a', 'copy', '-progress', 'pipe:1', output]
    else:
        vf = f"scale=-2:{RESOLUTION}" if RESOLUTION != "original" else "copy"
        cmd = ['ffmpeg', '-y', '-i', video_path, '-vf', vf, '-c:v', 'libx264', '-preset', PRESET, '-crf', CRF, '-c:a', 'copy', '-c:s', 'copy', '-progress', 'pipe:1', output]

    app = Client("worker_enc", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE)
    while True:
        line = await proc.stdout.readline()
        if not line: break
        line = line.decode('utf-8').strip()
        if line.startswith('out_time_us='):
            cur = int(line.split('=')[1]) / 1000000
            if duration > 0 and (time.time() - last_edit_time) > 10:
                perc = (cur / duration) * 100
                await app.edit_message_text(CHAT_ID, msg_id, f"🎬 " + sc("ᴇɴᴄᴏᴅɪɴɢ") + f" {perc:.2f}%\n🚀 {PRESET} | CRF {CRF}")
    await proc.wait()
    await app.stop()
    return output, proc.returncode

async def upload_phase(output, returncode, msg_id):
    if returncode != 0 or not os.path.exists(output): return
    app = Client("worker_up", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    target_chat = int(DUMP_ID) if DUMP_ID != "none" else CHAT_ID
    await app.send_document(chat_id=target_chat, document=output, caption=sc("Pʀᴏᴄᴇss Cᴏᴍᴘʟᴇᴛᴇ! 👑"), progress=progress_bar, progress_args=(app, msg_id, "Uᴘʟᴏᴀᴅɪɴɢ", RENAME, time.time()))
    await app.delete_messages(CHAT_ID, [msg_id])
    await app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    v, s, l, mid = loop.run_until_complete(download_phase())
    out, r = loop.run_until_complete(encode_phase(v, s, l, mid))
    loop.run_until_complete(upload_phase(out, r, mid))
