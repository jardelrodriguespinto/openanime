#!/bin/bash
# Script para rodar browser visível localmente
export PLAYWRIGHT_HEADLESS=false
export DASHBOARD_URL=http://localhost:8082
python -m bot.dashboard &
DASHBOARD_PID=$!
python -m bot.main
kill $DASHBOARD_PID 2>/dev/null
