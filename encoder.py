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

raw_dump = os.getenv("DUMP_ID", "none")
STATUS_MSG_ID = None

# Default Profile Variables
CODEC = "libx264"
CRF = "34"
PRESET = "ultrafast"
POS = "Center"
SIZE = "10"
MARGIN = "10"
RESOLUTION = "original"
ORIG_NAME = RENAME
VIDEO_MSG_ID = None
FONT_IDS =[]

if ":::" in raw_dump:
    parts = raw_dump.split(":::")
    DUMP_ID = parts[0]
    LOGO_ID = parts[1] if len(parts) > 1 else "none"
    STATUS_MSG_ID = parts[2] if len(parts) > 2 else None
    RESOLUTION = parts[3] if len(parts) > 3 else "original"
    ORIG_NAME = parts[4] if len(parts) > 4 else RENAME
    VIDEO_MSG_ID = parts[5] if len(parts) > 5 else None
    
    CODEC = parts[6] if len(parts) > 6 and parts[6] != "none" else CODEC
    CRF = parts[7] if len(parts) > 7 and parts[7] != "none" else CRF
    PRESET = parts[8] if len(parts) > 8 and parts[8] != "none" else PRESET
    POS = parts[9] if len(parts) > 9 and parts[9] != "none" else POS
    SIZE = parts[10] if len(parts) > 10 and parts[10] != "none" else SIZE
    MARGIN = parts[11] if len(parts) > 11 and parts[11] != "none" else MARGIN
    raw_fonts = parts[12] if len(parts) > 12 and parts[12] != "none" else ""
    if raw_fonts: FONT_IDS = raw_fonts.split(",")
else:
    DUMP_ID = raw_dump
    LOGO_ID = "none"

last_edit_time = 0

def get_readable_time(seconds) -> str:
    try:
        seconds = int(float(seconds))
    except:
        seconds = 0
    result = ""
    (days, remainder) = divmod(seconds, 86400)
    if days != 0: result += f"{days}d "
    (hours, remainder) = divmod(remainder, 3600)
    if hours != 0: result += f"{hours}h "
    (minutes, seconds) = divmod(remainder, 60)
    if minutes != 0: result += f"{minutes}m "
    result += f"{seconds}s"
    return result.strip()

async def get_duration(file_path):
    cmd =['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        stdout, _ = await proc.communicate()
        return float(stdout.decode().strip())
    except:
        return 0.0

async def progress_bar(current, total, app, msg_id, action_text, current_file_name, start_time):
    global last_edit_time
    now = time.time()
    if now - last_edit_time > 5 or current == total:
        try:
            perc = (current / total) * 100 if total > 0 else 0
            bar_length = 10
            filled = int((perc / 100) * bar_length)
            bar = "▰" * filled + "▱" * (bar_length - filled)
            
            elapsed = now - start_time
            speed = current / elapsed if elapsed > 0 else 0
            eta_seconds = (total - current) / speed if speed > 0 else 0
            eta_str = get_readable_time(eta_seconds) if eta_seconds > 0 else "0s"
            
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("❌ Cᴀɴᴄᴇʟ"), callback_data="cancel_cloud_task_cloud")]])
            text = (
                f"🎬  " + sc("ɢɪᴛʜᴜʙ ᴄʟᴏᴜᴅ ᴡᴏʀᴋᴇʀ") + "\n"
                "──────────────────────\n"
                f"▸ " + sc("sᴛᴀᴛᴜs :") + f" {action_text}\n"
                f"📊 [{bar}] {perc:.2f}%\n"
                f"🚀 Speed: {speed/(1024*1024):.2f} MB/s\n"
                f"💾 Size: {current/(1024*1024):.1f} MB / {total/(1024*1024):.1f} MB\n"
                f"⏱ ETA: {eta_str}\n"
                "──────────────────────\n"
                "⚙️ Cloud Engine"
            )
            await app.edit_message_text(CHAT_ID, msg_id, text, reply_markup=cancel_kb)
            last_edit_time = now
        except Exception:
            pass

async def download_phase(app):
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("❌ Cᴀɴᴄᴇʟ"), callback_data="cancel_cloud_task_cloud")]])
    
    msg_id = None
    if STATUS_MSG_ID:
        try:
            msg_id = int(STATUS_MSG_ID)
            await app.edit_message_text(CHAT_ID, msg_id, sc("⚙️ Wᴏʀᴋᴇʀ ᴛʀɪɢɢᴇʀᴇᴅ: Pʀᴇᴘᴀʀɪɴɢ...\n"), reply_markup=cancel_kb)
        except Exception:
            msg_id = None

    if not msg_id:
        try:
            reply_id = int(VIDEO_MSG_ID) if VIDEO_MSG_ID and str(VIDEO_MSG_ID) != "None" else None
            status_msg = await app.send_message(CHAT_ID, sc("⚙️ Wᴏʀᴋᴇʀ ᴛʀɪɢɢᴇʀᴇᴅ: Pʀᴇᴘᴀʀɪɴɢ...\n"), reply_markup=cancel_kb, reply_to_message_id=reply_id)
            msg_id = status_msg.id
        except Exception:
            status_msg = await app.send_message(CHAT_ID, sc("⚙️ Wᴏʀᴋᴇʀ ᴛʀɪɢɢᴇʀᴇᴅ: Pʀᴇᴘᴀʀɪɴɢ...\n"), reply_markup=cancel_kb)
            msg_id = status_msg.id
    
    dl_start_time = time.time()
    video_path = await app.download_media(
        VIDEO_ID, file_name="video.mkv", 
        progress=progress_bar, progress_args=(app, msg_id, "Dᴏᴡɴʟᴏᴀᴅɪɴɢ Vɪᴅᴇᴏ", ORIG_NAME, dl_start_time)
    )
    
    sub_path = None
    if TASK_TYPE == "hardsub" and SUB_ID != "none":
        sub_start_time = time.time()
        sub_path = await app.download_media(
            SUB_ID, 
            progress=progress_bar, progress_args=(app, msg_id, "Dᴏᴡɴʟᴏᴀᴅɪɴɢ Sᴜʙᴛɪᴛʟᴇ", ORIG_NAME, sub_start_time)
        )
        
    logo_path = None
    if TASK_TYPE == "hardsub" and LOGO_ID != "none":
        logo_start_time = time.time()
        logo_path = await app.download_media(
            LOGO_ID, 
            progress=progress_bar, progress_args=(app, msg_id, "Dᴏᴡɴʟᴏᴀᴅɪɴɢ Lᴏɢᴏ", ORIG_NAME, logo_start_time)
        )

    os.makedirs("fonts", exist_ok=True)
    if FONT_IDS:
        for idx, f_id in enumerate(FONT_IDS):
            try:
                await app.download_media(f_id, file_name=f"fonts/font_{idx}.ttf")
            except: pass
        
    try:
        await app.edit_message_text(CHAT_ID, msg_id, sc("🔥 Sᴛᴀʀᴛɪɴɢ FFᴍᴘᴇɢ Eɴɢɪɴᴇ...\n"), reply_markup=cancel_kb)
    except:
        pass

    return video_path, sub_path, logo_path, msg_id

async def encode_phase(app, video_path, sub_path, logo_path, msg_id):
    output = RENAME
    duration = await get_duration(video_path)
    os.makedirs("fonts", exist_ok=True)
    
    res_map = {"1080p": 1080, "720p": 720, "480p": 480}
    
    if TASK_TYPE == "compress":
        target_h = res_map.get(RESOLUTION, None) if RESOLUTION != "original" else None
        cmd =['ffmpeg', '-y', '-i', video_path, '-map', '0:v', '-map', '0:a?', '-map', '0:s?']
        if target_h:
            cmd.extend(['-vf', f'scale=-2:{target_h}'])
        cmd.extend([
            '-c:v', CODEC, '-preset', PRESET, '-crf', CRF, '-c:a', 'copy', '-c:s', 'copy',
            '-progress', 'pipe:1', output
        ])

    elif TASK_TYPE == "mux":
        font_args =[]
        if os.path.exists("fonts"):
            for idx, f in enumerate(os.listdir("fonts")):
                fp = os.path.join("fonts", f)
                if not os.path.isfile(fp): continue
                ext = os.path.splitext(f)[1].lower()
                mtype = "application/x-truetype-font" if ext in['.ttf', '.ttc'] else "application/vnd.ms-opentype" if ext == '.otf' else ""
                if mtype: font_args.extend(["-attach", fp, f"-metadata:s:t:{idx}", f"mimetype={mtype}"])
        
        sub_codec = 'ass' if (sub_path and sub_path.lower().endswith('.ass')) else 'subrip'
        cmd =[
            'ffmpeg', '-y', '-i', video_path, '-i', sub_path,
            '-map', '0:v', '-map', '0:a?', '-map', '1:0',
            '-c:v', 'copy', '-c:a', 'copy', '-c:s', sub_codec,
            '-disposition:s:0', 'default', '-metadata:s:s:0', 'language=eng', '-metadata:s:s:0', 'title=Hinglish'
        ] + font_args +['-progress', 'pipe:1', output]

    else:
        # hardsub mode
        target_h = res_map.get(RESOLUTION, None) if RESOLUTION != "original" else None
        filter_complex =[]
        current_v = "[0:v]"

        if target_h:
            filter_complex.append(f"{current_v}scale=-2:{target_h}[scaled]")
            current_v = "[scaled]"

        if sub_path:
            abs_sub = os.path.abspath(sub_path).replace('\\', '/').replace("'", r"\'").replace(':', '\\:')
            fonts_dir = os.path.abspath("fonts").replace('\\', '/').replace("'", r"\'").replace(':', '\\:')
            has_fonts = any(f.lower().endswith(('.ttf','.otf')) for f in os.listdir("fonts")) if os.path.exists("fonts") else False
            
            if has_fonts:
                filter_complex.append(f"{current_v}subtitles=filename='{abs_sub}':fontsdir='{fonts_dir}'[subbed]")
            else:
                filter_complex.append(f"{current_v}subtitles=filename='{abs_sub}'[subbed]")
            current_v = "[subbed]"

        if logo_path and LOGO_ID != "none" and POS != "none":
            abs_logo = os.path.abspath(logo_path).replace('\\', '/').replace(':', '\\:')
            s = float(SIZE) / 100
            m = float(MARGIN) / 100

            filter_complex.append(f"[1:v]{current_v}scale2ref=w='iw*{s}':h='ow*ih/iw'[logo][main]")

            if POS == "Top Left": overlay = f"x=W*{m}:y=H*{m}"
            elif POS == "Top Right": overlay = f"x=W-w-(W*{m}):y=H*{m}"
            elif POS == "Bottom Left": overlay = f"x=W*{m}:y=H-h-(H*{m})"
            elif POS == "Bottom Right": overlay = f"x=W-w-(W*{m}):y=H-h-(H*{m})"
            else: overlay = f"x=(W-w)/2:y=(H-h)/2"

            filter_complex.append(f"[main][logo]overlay={overlay}[outv]")
            current_v = "[outv]"

        cmd =['ffmpeg', '-y', '-i', video_path]
        if logo_path and LOGO_ID != "none" and POS != "none":
            cmd.extend(['-i', abs_logo])

        if filter_complex:
            cmd.extend(['-filter_complex', ";".join(filter_complex)])
            cmd.extend(['-map', current_v, '-map', '0:a?'])
        else:
            cmd.extend(['-map', '0:v', '-map', '0:a?'])

        cmd.extend(['-sn', '-c:v', CODEC, '-preset', PRESET, '-crf', CRF, '-c:a', 'copy', '-progress', 'pipe:1', output])

    proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
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
                    speed_bps = cur / elapsed if elapsed > 0 else 0
                    eta = (duration - cur) / speed_bps if speed_bps > 0 else 0
                    
                    bar_length = 10
                    filled = int((perc / 100) * bar_length)
                    bar = "▰" * filled + "▱" * (bar_length - filled)
                    eta_str = get_readable_time(eta) if eta > 0 else "0s"
                    
                    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(sc("❌ Cᴀɴᴄᴇʟ"), callback_data="cancel_cloud_task_cloud")]])
                    text = (
                        f"🎬  " + sc("ɢɪᴛʜᴜʙ ᴄʟᴏᴜᴅ ᴡᴏʀᴋᴇʀ") + "\n"
                        "──────────────────────\n"
                        f"▸ " + sc("sᴛᴀᴛᴜs :") + sc(" Eɴᴄᴏᴅɪɴɢ...\n") +
                        f"📊 [{bar}] {perc:.2f}%\n"
                        f"🚀 Speed: {speed_bps:.2f}x\n"
                        f"💾 Time: {get_readable_time(cur)} / {get_readable_time(duration)}\n"
                        f"⏱ ETA: {eta_str}\n"
                        "──────────────────────\n"
                        "⚙️ Cloud Engine"
                    )
                    try: await app.edit_message_text(CHAT_ID, msg_id, text, reply_markup=cancel_kb)
                    except: pass
                    last_up = now
            except: pass
            
    await proc.wait()
    return output, proc.returncode

async def extract_thumbnail(video_path, thumb_path):
    cmd =['ffmpeg', '-y', '-ss', '00:00:05', '-i', video_path, '-vf', 'scale=320:-1', '-vframes', '1', thumb_path]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
    except:
        pass
    return os.path.exists(thumb_path)

async def upload_phase(app, output, returncode, msg_id):
    if returncode == 0 and os.path.exists(output):
        thumb_path = "thumb.jpg"
        has_thumb = await extract_thumbnail(output, thumb_path)
        
        try:
            await app.edit_message_text(CHAT_ID, msg_id, sc("▸ Pʀᴏᴄᴇssɪɴɢ Dᴏɴᴇ! Uᴘʟᴏᴀᴅ ᴄʜᴀʟ ʀᴀʜᴀ ʜᴀɪ...\n"))
        except:
            pass

        target_chat = int(DUMP_ID) if DUMP_ID != "none" else CHAT_ID
        thread = int(THREAD_ID) if THREAD_ID != "none" else None
        cap = sc(f"✅ {TASK_TYPE.upper()} Cᴏᴍᴘʟᴇᴛᴇ\n") + f"📦 `{RENAME}`"
        
        reply_id = thread
        if target_chat == CHAT_ID and VIDEO_MSG_ID and str(VIDEO_MSG_ID) != "None":
            try:
                reply_id = int(VIDEO_MSG_ID)
            except:
                reply_id = None
        
        up_start_time = time.time()
        try:
            await app.send_document(
                chat_id=target_chat, document=output, reply_to_message_id=reply_id,
                thumb=thumb_path if has_thumb else None, caption=cap,
                progress=progress_bar, progress_args=(app, msg_id, "Uᴘʟᴏᴀᴅɪɴɢ Vɪᴅᴇᴏ", RENAME, up_start_time)
            )
            if target_chat != CHAT_ID:
                await app.send_message(CHAT_ID, sc("Kᴀᴀᴍ ʜᴏ ɢᴀʏᴀ! Fɪʟᴇ ᴀᴀᴘᴋᴏ ʙʜᴇᴊ ᴅɪ ɢᴀʏɪ ʜᴀɪ! ❤️"), reply_to_message_id=reply_id)
            await app.delete_messages(CHAT_ID,[msg_id])
        except Exception as e:
            try:
                await app.send_document(
                    chat_id=target_chat, document=output,
                    thumb=thumb_path if has_thumb else None, caption=cap,
                    progress=progress_bar, progress_args=(app, msg_id, "Uᴘʟᴏᴀᴅɪɴɢ Vɪᴅᴇᴏ", RENAME, up_start_time)
                )
                if target_chat != CHAT_ID:
                    await app.send_message(CHAT_ID, sc("Kᴀᴀᴍ ʜᴏ ɢᴀʏᴀ! Fɪʟᴇ ᴀᴀᴘᴋᴏ ʙʜᴇᴊ ᴅɪ ɢᴀʏɪ ʜᴀɪ! ❤️"))
                await app.delete_messages(CHAT_ID, [msg_id])
            except Exception as inner_e:
                try:
                    await app.edit_message_text(CHAT_ID, msg_id, sc(f"❌ Uᴘʟᴏᴀᴅ Eʀʀᴏʀ: {str(inner_e)}"))
                except:
                    pass
    else:
        try:
            await app.edit_message_text(CHAT_ID, msg_id, sc("❌ FFᴍᴘᴇɢ Eʀʀᴏʀ: Fᴀɪʟᴇᴅ ᴛᴏ ᴘʀᴏᴄᴇss."))
        except:
            pass

async def main():
    app = Client("worker_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    try:
        vid, sub, logo, mid = await download_phase(app)
        out, rcode = await encode_phase(app, vid, sub, logo, mid)
        await upload_phase(app, out, rcode, mid)
    except Exception as e:
        print(f"Error during GitHub worker execution: {e}")
    finally:
        await app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
