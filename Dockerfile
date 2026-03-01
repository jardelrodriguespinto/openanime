FROM python:3.12-slim

LABEL maintainer="anime-bot"
LABEL description="Anime Multi-Assistant Telegram Bot"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/logs

CMD ["python", "-m", "bot.main"]
