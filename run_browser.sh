#!/bin/bash
# Run browser automation visível localmente (não Docker)
set -e
cd "$(dirname "$0")"

# Export env vars
export PLAYWRIGHT_HEADLESS=false
export DASHBOARD_URL=http://localhost:8082

echo "⏳ Iniciando Dashboard na porta 8082..."
python -m bot.dashboard &
DASHBOARD_PID=$!
sleep 2

echo "⏳ Iniciando Bot..."
python -m bot.main &
BOT_PID=$!

cleanup() {
    echo "Parando processos..."
    kill $BOT_PID $DASHBOARD_PID 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

wait $BOT_PID
