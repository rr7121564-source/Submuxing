import os
import logging
import uuid
import asyncio

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

SESSION_ID = str(uuid.uuid4())[:8]
OWNER_ID = int(os.getenv("ADMIN_ID", 0))
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))

# Global objects
active_processes = {}
global_task_lock = asyncio.Lock()
