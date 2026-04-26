#!/bin/bash
# Start Telegram Local API in background
telegram-bot-api --local --api-id=YOUR_API_ID --api-hash=YOUR_API_HASH & 

# Start the Python bot
python bot.py