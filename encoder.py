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

CRF = "34"
PRESET = "ultrafast"
RESOLUTION = "original"
ORIG_NAME = RENAME
VIDEO_MSG_ID = None
FONT_IDS =[]
WM_TYPE = None
WM_POS = "top-right"
WM_SIZE = 25

if ":::" in raw_dump:
    parts = raw_dump.split(":::")
    DUMP_ID = parts[0]
    LOGO_ID = parts[1] if len(parts) > 1 else "none"
    STATUS_MSG_ID = parts[2] if len(parts) > 2 else None
    RESOLUTION = parts[3] if len(parts) > 3 else "original"
    ORIG_NAME = parts[4] if len(parts) > 4 else RENAME
    VIDEO_MSG_ID = parts[5] if len(parts) > 5 else None
    CRF = parts[6] if len(parts) > 6 and parts[6] != "none" else "34"
    PRESET = parts[7] if len(parts) > 7 and parts[7] != "none" else "ultrafast"
    raw_fonts = parts[8] if len(parts) > 8 and parts[8] != "none" else ""
    if raw_fonts: FONT_IDS = raw_fonts.split(",")
    
    WM_TYPE = parts[9] if len(parts) > 9 and parts[9] != "none" else None
    WM_POS = parts[11] if len(parts) > 11 and parts[11] != "none" else "top-right"
    try: WM_SIZE = int(parts[12]) if len(parts) > 12 else 25
    except: WM_SIZE = 25
else:
    DUMP_ID = raw_dump
    LOGO_ID = "none"

last_edit_time = 0

def get_readable_time(seconds) -> str:
    try: seconds = int(float(seconds))
    except: seconds = 0
    result = ""
    (days, remainder) = divmod(seconds, 86400)
    if days != 0: result += f"{days}d "
    (hours, remainder) = divmod(remainder, 3600)
    if hours != 0: result += f"{hours}h "
    (minutes, seconds) = divmod(remainder, 60)
    if minutes != 0: result += f"{minutes}m "
    result += f"{seconds}s"
    return result.strip()

async def get_video_dims(file_path):
    cmd =['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0:s=x', file_path]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        w, h = map(int, out.decode().strip().split('x'))
        return w, h
    except:
        return 1280, 720

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
            
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_cloud_task_cloud")]])
            text = (
                f"🎬 GitHub Cloud Worker\n"
                "──────────────────────\n"
                f"▸ Status: {action_text}\n"
                f"📊 [{bar}] {perc:.2f}%\n"
                f"🚀 Speed: {speed/(1024*1024):.2f} MB/s\n"
                f"💾 Size: {current/(1024*1024):.1f} MB / {total/(1024*1024):.1f} MB\n"
                f"⏱ ETA: {eta_str}\n"
                "──────────────────────\n"
                "⚙️ Cloud Engine"
            )
            await app.edit_message_text(CHAT_ID, msg_id, text, reply_markup=cancel_kb)
            last_edit_time = now
        except Exception: pass

async def download_phase(app):
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_cloud_task_cloud")]])
    msg_id = None
    if STATUS_MSG_ID:
        try:
            msg_id = int(STATUS_MSG_ID)
            await app.edit_message_text(CHAT_ID, msg_id, "⚙️ Worker Triggered: Preparing...\n", reply_markup=cancel_kb)
        except: msg_id = None

    if not msg_id:
        try:
            reply_id = int(VIDEO_MSG_ID) if VIDEO_MSG_ID and str(VIDEO_MSG_ID) != "None" else None
            status_msg = await app.send_message(CHAT_ID, "⚙️ Worker Triggered: Preparing...\n", reply_markup=cancel_kb, reply_to_message_id=reply_id)
            msg_id = status_msg.id
        except:
            status_msg = await app.send_message(CHAT_ID, "⚙️ Worker Triggered: Preparing...\n", reply_markup=cancel_kb)
            msg_id = status_msg.id
    
    dl_start_time = time.time()
    video_path = await app.download_media(VIDEO_ID, file_name="video.mkv", progress=progress_bar, progress_args=(app, msg_id, "Downloading Video", ORIG_NAME, dl_start_time))
    
    sub_path = None
    if TASK_TYPE == "hardsub" and SUB_ID != "none":
        sub_start_time = time.time()
        sub_path = await app.download_media(SUB_ID, progress=progress_bar, progress_args=(app, msg_id, "Downloading Subtitle", ORIG_NAME, sub_start_time))
        
    logo_path = None
    if TASK_TYPE == "hardsub" and WM_TYPE == "image" and LOGO_ID != "none":
        logo_start_time = time.time()
        logo_path = await app.download_media(LOGO_ID, progress=progress_bar, progress_args=(app, msg_id, "Downloading Logo", ORIG_NAME, logo_start_time))

    os.makedirs("fonts", exist_ok=True)
    if FONT_IDS:
        for idx, f_id in enumerate(FONT_IDS):
            try: await app.download_media(f_id, file_name=f"fonts/font_{idx}.ttf")
            except: pass
        
    try: await app.edit_message_text(CHAT_ID, msg_id, "🔥 Starting FFmpeg Engine...\n", reply_markup=cancel_kb)
    except: pass
    return video_path, sub_path, logo_path, msg_id

async def encode_phase(app, video_path, sub_path, logo_path, msg_id):
    outputs =[]
    duration = await get_duration(video_path)
    vid_w, vid_h = await get_video_dims(video_path)
    os.makedirs("fonts", exist_ok=True)
    
    res_map = {"1080p": 1080, "720p": 720, "480p": 480, "360p": 360}
    res_list = RESOLUTION.split(",") if RESOLUTION else ["original"]
    base_name, ext = os.path.splitext(RENAME)
    
    has_image_wm = (TASK_TYPE == "hardsub" and WM_TYPE == 'image' and logo_path and LOGO_ID != "none")
    target_wm_h = int((vid_h * WM_SIZE) / 100) if WM_SIZE else int((vid_h * 10) / 100)

    font_args =[]
    for idx, f in enumerate(os.listdir("fonts")):
        fp = os.path.join("fonts", f)
        if not os.path.isfile(fp): continue
        fext = os.path.splitext(f)[1].lower()
        mtype = "application/x-truetype-font" if fext in['.ttf', '.ttc'] else "application/vnd.ms-opentype" if fext == '.otf' else ""
        if mtype: font_args.extend(["-attach", fp, f"-metadata:s:t:{idx}", f"mimetype={mtype}"])

    cmd = ['ffmpeg', '-y', '-fflags', '+genpts', '-i', video_path]
    if has_image_wm: cmd.extend(['-i', os.path.abspath(logo_path)])

    filter_complex =[]
    current_v = "[0:v]"

    if has_image_wm:
        if WM_POS == "top-left": x, y = "10", "10"
        elif WM_POS == "top-center": x, y = "(main_w-overlay_w)/2", "10"
        elif WM_POS == "top-right": x, y = "main_w-overlay_w-10", "10"
        elif WM_POS == "center-left": x, y = "10", "(main_h-overlay_h)/2"
        elif WM_POS == "center-right": x, y = "main_w-overlay_w-10", "(main_h-overlay_h)/2"
        elif WM_POS == "bottom-left": x, y = "10", "main_h-overlay_h-10"
        elif WM_POS == "bottom-center": x, y = "(main_w-overlay_w)/2", "main_h-overlay_h-10"
        elif WM_POS == "bottom-right": x, y = "main_w-overlay_w-10", "main_h-overlay_h-10"
        else: x, y = "main_w-overlay_w-10", "10"
        filter_complex.append(f"[1:v]scale=-1:{target_wm_h}[wm];{current_v}[wm]overlay={x}:{y}[wm_out]")
        current_v = "[wm_out]"

    if TASK_TYPE == "compress":
        if len(res_list) > 1:
            split_out = "".join([f"[v{i}]" for i in range(len(res_list))])
            filter_complex.append(f"{current_v}split={len(res_list)}{split_out}")
            out_streams =[]
            for i, res in enumerate(res_list):
                target_h = res_map.get(res)
                if target_h:
                    filter_complex.append(f"[v{i}]scale=-2:{target_h}[out_{res}]")
                    out_streams.append(f"[out_{res}]")
                else: out_streams.append(f"[v{i}]")
        else:
            target_h = res_map.get(res_list[0])
            if target_h:
                filter_complex.append(f"{current_v}scale=-2:{target_h}[out_{res_list[0]}]")
                out_streams = [f"[out_{res_list[0]}]"]
            else: out_streams = [current_v]

        if filter_complex: cmd.extend(['-filter_complex', ";".join(filter_complex)])
        cmd.extend(['-progress', 'pipe:1'])

        for i, res in enumerate(res_list):
            out_file = f"{base_name} {res}{ext}"
            cmd.extend(['-map', out_streams[i], '-map', '0:a?', '-map', '0:s?'])
            cmd.extend(['-c:v', 'libx264', '-preset', PRESET, '-crf', str(CRF), '-c:a', 'copy', '-c:s', 'copy'])
            cmd.append(out_file)
            outputs.append(out_file)

    elif TASK_TYPE == "hardsub":
        if sub_path:
            abs_sub = os.path.abspath(sub_path).replace('\\', '/').replace("'", r"\'").replace(':', '\\:')
            fonts_dir = os.path.abspath("fonts").replace('\\', '/').replace("'", r"\'").replace(':', '\\:')
            has_fonts = any(f.lower().endswith(('.ttf','.otf')) for f in os.listdir("fonts")) if os.path.exists("fonts") else False
            if has_fonts: filter_complex.append(f"{current_v}subtitles=filename='{abs_sub}':fontsdir='{fonts_dir}'[subbed]")
            else: filter_complex.append(f"{current_v}subtitles=filename='{abs_sub}'[subbed]")
            current_v = "[subbed]"

        if filter_complex: cmd.extend(['-filter_complex', ";".join(filter_complex)])
        cmd.extend(['-map', current_v, '-map', '0:a?'])
        out_file = RENAME
        cmd.extend(['-sn', '-c:v', 'libx264', '-preset', PRESET, '-crf', str(CRF), '-c:a', 'copy', '-progress', 'pipe:1', out_file])
        outputs.append(out_file)

    elif TASK_TYPE == "mux":
        sub_codec = 'ass' if (sub_path and sub_path.lower().endswith('.ass')) else 'subrip'
        cmd = ['ffmpeg', '-y', '-fflags', '+genpts', '-i', video_path, '-i', sub_path]
        if filter_complex: cmd.extend(['-filter_complex', filter_complex[0]])
        v_map = "[wm_out]" if filter_complex else "0:v"
        cmd.extend(['-map', v_map, '-map', '0:a?', '-map', '1:0', '-c:v', 'copy', '-c:a', 'copy', '-c:s', sub_codec])
        cmd.extend(['-disposition:s:0', 'default', '-metadata:s:s:0', 'language=eng', '-metadata:s:s:0', 'title=Hinglish'])
        cmd.extend(font_args)
        out_file = RENAME
        cmd.extend(['-progress', 'pipe:1', out_file])
        outputs.append(out_file)

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
                    
                    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_cloud_task_cloud")]])
                    text = (
                        f"🎬 GitHub Cloud Worker\n"
                        "──────────────────────\n"
                        f"▸ Status: Encoding...\n"
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
    return outputs, proc.returncode

async def upload_phase(app, outputs, returncode, msg_id):
    if returncode == 0 and outputs:
        for out in outputs:
            if not os.path.exists(out): continue
            thumb_path = f"thumb_{int(time.time())}.jpg"
            has_thumb = await extract_thumbnail(out, thumb_path)
            try: await app.edit_message_text(CHAT_ID, msg_id, f"▸ Uploading: {out}\n")
            except: pass

            target_chat = int(DUMP_ID) if DUMP_ID != "none" else CHAT_ID
            thread = int(THREAD_ID) if THREAD_ID != "none" else None
            
            quality = "ORIGINAL"
            for q in ["1080p", "720p", "480p", "360p"]:
                if q in out: quality = q; break
                
            cap = f"✅ {TASK_TYPE.upper()} Complete\n📦 `{out}`\n🎥 Quality: {quality}"
            
            reply_id = thread
            if target_chat == CHAT_ID and VIDEO_MSG_ID and str(VIDEO_MSG_ID) != "None":
                try: reply_id = int(VIDEO_MSG_ID)
                except: reply_id = None
            
            up_start_time = time.time()
            try:
                await app.send_document(
                    chat_id=target_chat, document=out, reply_to_message_id=reply_id,
                    thumb=thumb_path if has_thumb else None, caption=cap,
                    progress=progress_bar, progress_args=(app, msg_id, "Uploading Video", out, up_start_time)
                )
                if target_chat != CHAT_ID:
                    await app.send_message(CHAT_ID, "Task completed! The file has been sent to the dump.", reply_to_message_id=reply_id)
            except Exception as e:
                try: await app.edit_message_text(CHAT_ID, msg_id, f"❌ Upload Error: {str(e)}")
                except: pass
            finally:
                if os.path.exists(thumb_path): os.remove(thumb_path)
        
        try: await app.delete_messages(CHAT_ID,[msg_id])
        except: pass
    else:
        try: await app.edit_message_text(CHAT_ID, msg_id, "❌ FFmpeg Error: Failed to process.")
        except: pass

async def main():
    app = Client("worker_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    await app.start()
    try:
        vid, sub, logo, mid = await download_phase(app)
        outs, rcode = await encode_phase(app, vid, sub, logo, mid)
        await upload_phase(app, outs, rcode, mid)
    except Exception as e:
        print(f"Error during GitHub worker execution: {e}")
    finally:
        await app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
