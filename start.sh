#!/bin/bash

# Local API ke liye temporary folder banayein
mkdir -p /app/api_workdir
chmod -R 777 /app/api_workdir

echo "🚀 Starting Local Telegram API Server..."
# Local Server ko background me start karna
telegram-bot-api --api-id=$API_ID --api-hash=$API_HASH --dir=/app/api_workdir --local &

# API server ko start hone ka time dena (3 seconds)
sleep 3

echo "🤖 Starting Python Bot..."
python bot.py
