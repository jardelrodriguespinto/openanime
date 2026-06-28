FROM python:3.12-slim

LABEL maintainer="anime-bot"
LABEL description="OpenAnime Bot - Telegram + Dashboard + Job Apply Automation"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    wget \
    libpq-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev \
    shared-mime-info \
    xvfb \
    x11-utils \
    libasound2 \
    libgtk-3-0 \
    libxss1 \
    libx11-xcb1 \
    libxcb1 \
    libxrender1 \
    libxtst6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install chromium --with-deps 2>/dev/null || echo "Playwright: instalacao ignorada"

COPY . .

RUN mkdir -p /app/logs /tmp

CMD ["xvfb-run", "--auto-servernum", "--server-num=1", "-a", "python", "-m", "bot.main"]
