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
    
    app = ApplicationBuilder().token(token).build()

    # Register Handlers
    app.add_handler(CommandHandler("sub", cmd_sub))
    app.add_handler(CommandHandler("extract", cmd_extract))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(cancel_callback, pattern=r"^cancel_"))

    # ==========================================
    # WEBHOOK SETUP FOR RENDER WEB SERVICE
    # ==========================================
    # Render automatically provides the PORT environment variable
    port = int(os.environ.get("PORT", 10000)) 
    
    # You need to set this environment variable in Render Dashboard
    webhook_url = os.environ.get("WEBHOOK_URL") 

    if webhook_url:
        print(f"Starting Webhook on port {port} for URL: {webhook_url}...")
        # Start webhook (Requires for Render Web Service health-check)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            webhook_url=f"{webhook_url}/{token}",
        )
    else:
        print("WEBHOOK_URL not found. Falling back to Polling (Local Mode)...")
        app.run_polling()

if __name__ == "__main__":
    main()
