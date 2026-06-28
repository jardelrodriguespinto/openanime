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
export PLAYWRIGHT_HEADLESS=false  # Browser visível no desktop

echo ""
echo "✅ Iniciando bot (browser visível)..."
exec python3 -m bot.main
