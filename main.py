import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, TypeHandler
)
from config import BOT_TOKEN, PORT, SESSION_ID, OWNER_ID
from database.db_handler import init_db
import middlewares
from handlers import admin, user, callbacks

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(f"Session {SESSION_ID} OK".encode())
    def log_message(self, *args): pass

def main():
    if not BOT_TOKEN: return
    init_db()
    
    # Start Health Check Server
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever(), daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).base_url("http://127.0.0.1:8081/bot").base_file_url("http://127.0.0.1:8081/file/bot").local_mode(True).build()

    # Middlewares
    app.add_handler(TypeHandler(Update, middlewares.check_access), group=-2)
    app.add_handler(TypeHandler(Update, middlewares.block_duplicates), group=-1)
    
    # Handlers
    app.add_handler(CommandHandler("start", user.cmd_start))
    app.add_handler(CommandHandler("id", user.cmd_id))
    app.add_handler(CommandHandler(["add_user", "rem_user", "add_chat", "rem_chat"], admin.admin_auth))
    app.add_handler(CommandHandler("skip", user.cmd_skip))
    app.add_handler(CommandHandler("thumbnail", user.cmd_thumbnail))
    
    app.add_handler(MessageHandler(filters.PHOTO, user.handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user.handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, user.handle_docs))
    app.add_handler(CallbackQueryHandler(callbacks.cancel_cb, pattern=r"^cancel_"))

    print(f"--- BOT STARTED (ADMIN: {OWNER_ID}) ---")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
