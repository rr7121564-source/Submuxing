# Get the official Telegram Local Bot API binary
FROM aiogram/telegram-bot-api:latest AS api-server

# Use Alpine Python
FROM python:3.11-alpine

ENV PYTHONUNBUFFERED=1

# Install FFmpeg, bash and curl
RUN apk update && apk add --no-cache ffmpeg bash curl

# Copy Local API Server binary
COPY --from=api-server /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api

WORKDIR /app

# Install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all files
COPY . .

# Hugging Face Strict Permissions Fix (Bohot zaruri hai)
RUN chmod +x start.sh
RUN chmod -R 777 /app

# Run the startup script
CMD["./start.sh"]
