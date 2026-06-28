#!/bin/bash
# Executa bot + dashboard localmente usando services do Docker
set -e
cd "$(dirname "$0")"

# Carrega .env automaticamente
set -a
[ -f .env ] && source .env
set +a

# Override para localhost
export NEO4J_URI=bolt://localhost:7687
export REDIS_HOST=localhost
export POSTGRES_HOST=localhost
export DASHBOARD_URL=http://localhost:8082
export PLAYWRIGHT_HEADLESS=false  # Browser visível no desktop
export DISPLAY=${DISPLAY:-:0}

echo ""
echo "⏳ Iniciando Dashboard na porta 8082..."
python3 -m bot.dashboard &
DASHBOARD_PID=$!
sleep 3

echo "⏳ Iniciando Bot..."
python3 -m bot.main &
BOT_PID=$!

cleanup() {
    echo ""
    echo "Parando processos..."
    kill $BOT_PID $DASHBOARD_PID 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

wait $BOT_PID
