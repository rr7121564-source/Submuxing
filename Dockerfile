FROM aiogram/telegram-bot-api:latest AS api-server
FROM python:3.11-alpine

ENV PYTHONUNBUFFERED=1
RUN apk update && apk add --no-cache ffmpeg bash curl

COPY --from=api-server /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api

WORKDIR /app

# Ensure persistent directory exists for Hugging Face Free Tier
RUN mkdir -p /data && chmod -R 777 /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

RUN chmod +x start.sh
RUN chmod -R 777 /app

# Space is mandatory between CMD and the command
CMD ["bash", "start.sh"]
