#!/bin/bash

# 1. Local API Server start karein background mein
# --local flag zaroori hai badi files ke liye
# --api-id aur --api-hash environment variables se uthayega
telegram-bot-api --local --api-id=$API_ID --api-hash=$API_HASH --http-port=8081 &

# 2. 5 second wait karein taaki server puri tarah start ho jaye
sleep 5

# 3. Ab apna Python bot start karein
python bot.py
