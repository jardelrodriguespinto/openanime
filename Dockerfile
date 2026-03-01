FROM python:3.12-slim

LABEL maintainer="anime-bot"
LABEL description="Anime Multi-Assistant Telegram Bot"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    wget \
    # weasyprint dependencies (PDF generation)
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright Chromium para auto-candidatura (falha silenciosa se nao disponivel)
RUN playwright install chromium --with-deps 2>/dev/null || echo "Playwright: instalacao ignorada"

COPY . .

RUN mkdir -p /app/logs /tmp

CMD ["python", "-m", "bot.main"]
