#!/bin/bash
set -e
cd "$(dirname "$0")"

PLAYWRIGHT_HEADLESS=false
DASHBOARD_URL=http://localhost:8082
MODE="local"  # compose | local

usage() {
    echo "Uso: $0 [MODO]"
    echo ""
    echo "Modos:"
    echo "  compose (padrão)  Roda via docker compose up + bot + dashboard"
    echo "  local             Sobe containers (neo4j, redis, postgres) e roda bot/dashboard localmente"
    echo "  headless          Igual compose, mas com PLAYWRIGHT_HEADLESS=true"
    echo ""
    echo "Exemplos:"
    echo "  $0              # docker compose + browser visível"
    echo "  $0 headless     # docker compose + browser headless"
    echo "  $0 local        # containers infra + bot/dashboard local"
    exit 1
}

for arg in "$@"; do
    case "$arg" in
        compose) MODE="compose"; PLAYWRIGHT_HEADLESS=false ;;
        local)   MODE="local";   PLAYWRIGHT_HEADLESS=false ;;
        headless) MODE="compose"; PLAYWRIGHT_HEADLESS=true ;;
        -h|--help) usage ;;
        *) echo "Modo inválido: $arg"; usage ;;
    esac
done

export PLAYWRIGHT_HEADLESS
export DASHBOARD_URL

# Fonte .env se existir
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

wait_for_neo4j() {
    echo "⏳ Aguardando Neo4j ficar disponível em localhost:7687 ..."
    for i in {1..60}; do
        if python3 -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1', 7687)); s.close()" 2>/dev/null; then
            echo "✅ Neo4j disponível"
            return 0
        fi
        sleep 1
    done
    echo "❌ Neo4j não ficou disponível a tempo"
    return 1
}

if [ "$MODE" = "compose" ]; then
    echo "🔧 Subindo serviços Docker (neo4j, redis, postgres, bot, dashboard)..."
    docker compose up -d

    echo "⏳ Aguardando dashboard responder em http://localhost:8082 ..."
    for i in {1..20}; do
        if curl -sf http://localhost:8082/api/status >/dev/null 2>&1; then
            echo "✅ Dashboard online em http://localhost:8082"
            break
        fi
        sleep 1
    done

    echo ""
    echo "🚀 Job Apply Dashboard: http://localhost:8082"
    echo "   Bot Telegram rodando via Docker (anime-bot)"
    echo ""
    echo "Pressione Ctrl+C para parar os containers."
    trap 'echo ""; echo "Parando..."; docker compose down; exit 0' INT TERM
    docker compose logs -f bot dashboard
else
    echo "🔧 Modo LOCAL — subindo todos os containers (neo4j, redis, postgres, bot)..."
    docker compose up -d neo4j redis postgres bot

    wait_for_neo4j

    echo "⏳ Iniciando Dashboard localmente na porta 8082..."
    python3 -m bot.dashboard &
    DASHBOARD_PID=$!
    sleep 2

    cleanup() {
        echo ""
        echo "Parando processos locais..."
        kill $DASHBOARD_PID 2>/dev/null || true
        echo "Parando containers..."
        docker compose stop neo4j redis postgres bot
        exit 0
    }
    trap cleanup INT TERM

    wait
fi
