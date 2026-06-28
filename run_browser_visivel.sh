#!/bin/bash
# Script para rodar browser visível localmente
set -a
[ -f .env ] && source .env
set +a
export PLAYWRIGHT_HEADLESS=false
export DASHBOARD_URL=http://localhost:8082
python3 -m bot.dashboard &
DASHBOARD_PID=$!
python3 -m bot.main
kill $DASHBOARD_PID 2>/dev/null
