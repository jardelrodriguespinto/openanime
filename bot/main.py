import datetime
import logging
import logging.handlers
import os

import pytz
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot.handlers import (
    handle_audio,
    handle_candidaturas,
    handle_comandos,
    handle_curriculo_ats,
    handle_document,
    handle_error,
    handle_help,
    handle_historico,
    handle_limpar,
    handle_maratona,
    handle_message,
    handle_noticias,
    handle_notificacoes,
    handle_novidades,
    handle_perfil_pro,
    handle_start,
    handle_stats,
    handle_vagas,
)

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)-30s %(message)s"
LOG_DATE = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT,
    datefmt=LOG_DATE,
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            "/app/logs/bot.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
    ],
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
logging.getLogger("apscheduler").setLevel(logging.INFO)

logger = logging.getLogger(__name__)


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN nao configurado no .env")

    logger.info("=== Anime Multi-Assistant iniciando ===")
    logger.info("Log level: %s", LOG_LEVEL)

    try:
        from graph.neo4j_client import get_neo4j

        get_neo4j()
        logger.info("Neo4j: conectado")
    except Exception as e:
        logger.error("Neo4j: falha na conexao: %s", e)

    try:
        from graph.weaviate_client import get_weaviate

        weaviate = get_weaviate()
        total = weaviate.total_animes()
        logger.info("Weaviate: conectado | animes indexados=%d", total)
    except Exception as e:
        logger.error("Weaviate: falha na conexao: %s", e)

    try:
        from bot.redis_history import get_redis_history

        get_redis_history().get("__ping__")
        logger.info("Redis: conectado")
    except Exception as e:
        logger.error("Redis: falha na conexao: %s", e)

    try:
        from agents.orchestrator import get_graph

        get_graph()
        logger.info("LangGraph: grafo compilado")
    except Exception as e:
        logger.error("LangGraph: erro ao compilar grafo: %s", e)
        raise

    async def _post_shutdown(_app):
        """Aguarda tasks de background (extrator) terminarem antes de fechar."""
        from agents.orchestrator import _background_tasks
        pending = list(_background_tasks)
        if pending:
            logger.info("Aguardando %d tasks de background finalizarem...", len(pending))
            import asyncio
            try:
                await asyncio.wait(pending, timeout=10)
            except Exception:
                pass

    app = (
        ApplicationBuilder()
        .token(token)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("comandos", handle_comandos))
    app.add_handler(CommandHandler("ajuda", handle_comandos))
    app.add_handler(CommandHandler("historico", handle_historico))
    app.add_handler(CommandHandler("stats", handle_stats))
    app.add_handler(CommandHandler("maratona", handle_maratona))
    app.add_handler(CommandHandler("novidades", handle_novidades))
    app.add_handler(CommandHandler("limpar", handle_limpar))
    app.add_handler(CommandHandler("noticias", handle_noticias))
    app.add_handler(CommandHandler("vagas", handle_vagas))
    app.add_handler(CommandHandler("curriculo_ats", handle_curriculo_ats))
    app.add_handler(CommandHandler("perfil_pro", handle_perfil_pro))
    app.add_handler(CommandHandler("candidaturas", handle_candidaturas))
    app.add_handler(CommandHandler("notificacoes", handle_notificacoes))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))
    # PDF handler — antes do handler de audio generico
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(MessageHandler(filters.ATTACHMENT & ~filters.PHOTO & ~filters.VIDEO & ~filters.Document.PDF, handle_audio))

    app.add_error_handler(handle_error)

    _registrar_jobs(app)

    logger.info("Bot registrado - iniciando polling")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


def _registrar_jobs(app):
    """
    Registra jobs agendados via JobQueue do python-telegram-bot.

    Estrategia: um coordinator roda toda hora e envia notificacoes apenas
    para usuarios cujas preferencias batem com a hora atual.
    Isso torna todos os horarios configuráveis por usuario.
    """
    from bot.notificador import (
        coordinator_notificacoes,
        verificar_lancamentos_culturais,
    )

    tz_br = pytz.timezone("America/Sao_Paulo")

    # Coordinator que roda a cada minuto.
    # Noticias disparam no hora:minuto exato; digest/episodios/vagas so no minuto 0.
    app.job_queue.run_repeating(
        coordinator_notificacoes,
        interval=datetime.timedelta(minutes=1),
        first=10,  # inicia 10s apos o bot subir
        name="coordinator_notificacoes",
    )
    logger.info("Job agendado: coordinator_notificacoes (a cada minuto)")

    # Lancamentos culturais: toda sexta 12h (fixo, independe de prefs individuais)
    app.job_queue.run_daily(
        verificar_lancamentos_culturais,
        time=datetime.time(hour=12, minute=0, tzinfo=tz_br),
        days=(4,),
        name="lancamentos_culturais",
    )
    logger.info("Job agendado: lancamentos_culturais as 12:00 sextas America/Sao_Paulo")


if __name__ == "__main__":
    main()
