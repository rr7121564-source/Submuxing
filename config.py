import os
import uuid
import asyncio

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("ADMIN_ID", 0))
PORT = int(os.environ.get("PORT", 10000))

# Global Objects
SESSION_ID = str(uuid.uuid4())[:8]
active_processes = {}
global_task_lock = asyncio.Lock()