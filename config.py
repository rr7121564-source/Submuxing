import os
import uuid
import asyncio

# Settings
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("ADMIN_ID", 0))
PORT = int(os.environ.get("PORT", 10000))

# Global Objects
SESSION_ID = str(uuid.uuid4())[:8]
active_processes = {}
global_task_lock = asyncio.Lock()
EXTRACT_DATA = {} # Extraction session ke liye

# Nika-main wala Language Map
LANG_MAP = {
    'eng': 'English', 'hin': 'Hindi', 'ara': 'Arabic', 'fre': 'French',
    'ger': 'German', 'ita': 'Italian', 'jpn': 'Japanese', 'spa': 'Spanish',
    'rus': 'Russian', 'chi': 'Chinese', 'kor': 'Korean', 'tam': 'Tamil', 'tel': 'Telugu'
}
