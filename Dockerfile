# Get the official Telegram Local Bot API binary
FROM aiogram/telegram-bot-api:latest AS api-server

# Use Alpine Python to match the binary's architecture
FROM python:3.11-alpine

ENV PYTHONUNBUFFERED=1

# Install FFmpeg and bash
RUN apk update && apk add --no-cache ffmpeg bash

# Copy Local API Server binary from the first stage
COPY --from=api-server /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api

WORKDIR /app

# Install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all code
COPY . .

# Make start script executable
RUN chmod +x start.sh

# Run both servers using the bash script
CMD ["./start.sh"]
