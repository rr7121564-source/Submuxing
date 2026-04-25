#!/bin/bash

if [ -z "$API_ID" ] || [ -z "$API_HASH" ]; then
    echo "ERROR: API_ID or API_HASH is missing in Render Environment Variables!"
    exit 1
fi

# Create a directory for the Telegram API Server to store large files
mkdir -p /app/telegram-data

echo "Starting Telegram Local API Server in background..."
# Run the local server (Bypasses 20MB limit, allows up to 2GB)
/usr/local/bin/telegram-bot-api --local --api-id=$API_ID --api-hash=$API_HASH --dir=/app/telegram-data &

# Wait 5 seconds for the API server to fully start
sleep 5

echo "Starting Python MKV Bot..."
python bot.py
