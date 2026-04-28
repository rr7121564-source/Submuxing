import os
import uuid
import asyncio

# Settings
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("ADMIN_ID", 0))
PORT = int(os.environ.get("PORT", 7860))

# GitHub Actions Credentials (For Hardsub & Compress)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME") # Format: username/reponame

# Pyrogram API (GitHub Worker ke liye)
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")

# Global Objects
SESSION_ID = str(uuid.uuid4())[:8]
active_processes = {}

# --- QUEUE SYSTEM LOCKS ---
global_task_lock = asyncio.Lock()   # Local Muxing ke liye
github_task_lock = asyncio.Lock()   # Cloud Hardsub/Compress ke liye

# --- QUEUE COUNTERS ---
current_github_tasks = 0            # Cloud Queue tracking

EXTRACT_DATA = {} 

LANG_MAP = {
    'eng': 'English', 'hin': 'Hindi', 'ara': 'Arabic', 'fre': 'French',
    'ger': 'German', 'ita': 'Italian', 'jpn': 'Japanese', 'spa': 'Spanish',
    'rus': 'Russian', 'chi': 'Chinese', 'kor': 'Korean', 'tam': 'Tamil', 'tel': 'Telugu'
}
