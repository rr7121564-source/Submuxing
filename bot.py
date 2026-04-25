import os
import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from handlers.mkv_actions import handle_docs, cmd_sub, cmd_extract, cancel_callback

# Setup basic logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("Error: BOT_TOKEN environment variable not set.")
        return

    # Ensure fonts directory exists on startup
    os.makedirs("fonts", exist_ok=True)

    # ---------------------------------------------------------
    # FOR LARGE FILES (>20MB) UNCOMMENT AND USE A LOCAL BOT API:
    # app = ApplicationBuilder().token(token).base_url("http://YOUR_LOCAL_IP:8081/bot").build()
    # ---------------------------------------------------------
    
    app = ApplicationBuilder().token(token).build()

    # Register Handlers
    app.add_handler(CommandHandler("sub", cmd_sub))
    app.add_handler(CommandHandler("extract", cmd_extract))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(cancel_callback, pattern=r"^cancel_"))

    print("Bot is up and running...")
    app.run_polling()

if __name__ == "__main__":
    main()
