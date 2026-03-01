import asyncio
import logging

from telegram import Message, Update
from telegram.error import BadRequest, NetworkError, RetryAfter
from telegram.ext import ContextTypes

from agents.orchestrator import processar_mensagem
from ai.assemblyai import get_assemblyai
from bot.formatter import formatar_telegram

logger = logging.getLogger(__name__)

# Historico de conversa por usuario (em memoria)
_historicos: dict[str, list] = {}
MAX_HISTORY = 20
MAX_AUDIO_PREVIEW = 120
MAX_TELEGRAM_RETRIES = 3


async def _telegram_call_with_retry(operation: str, call):
    """Executa chamadas Telegram com retry para flood/network."""
    for attempt in range(1, MAX_TELEGRAM_RETRIES + 1):
        try:
            return await call()
        except RetryAfter as e:
            wait = int(getattr(e, "retry_after", 1)) + 1
            logger.warning(
                "Telegram flood control em %s (tentativa %d/%d). Aguardando %ss",
                operation,
                attempt,
                MAX_TELEGRAM_RETRIES,
                wait,
            )
            await asyncio.sleep(wait)
        except NetworkError as e:
            if attempt >= MAX_TELEGRAM_RETRIES:
                raise
            wait = attempt * 2
            logger.warning(
                "Telegram NetworkError em %s (tentativa %d/%d): %s. Retry em %ss",
                operation,
                attempt,
                MAX_TELEGRAM_RETRIES,
                e,
                wait,
            )
            await asyncio.sleep(wait)

    raise RuntimeError(f"Falha persistente na operacao Telegram: {operation}")


async def _safe_delete_message(message: Message | None):
    if not message:
        return
    try:
        await _telegram_call_with_retry("delete_message", lambda: message.delete())
    except Exception:
        pass


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start."""
    user = update.effective_user
    logger.info("/start: user_id=%s username=%s", user.id, user.username)
    await _telegram_call_with_retry(
        "reply_html_start",
        lambda: update.message.reply_html(
            f"Oi, <b>{user.first_name}</b>!\n\n"
            "Sou seu assistente pessoal de anime, manga e manhwa.\n\n"
            "Pode me perguntar sobre qualquer coisa:\n"
            "- Recomendacoes personalizadas no seu estilo\n"
            "- Analise e review de qualquer obra\n"
            "- Noticias, lancamentos e temporada atual\n"
            "- Sites para ler/assistir\n"
            "- Registrar o que voce assistiu ou leu\n"
            "- Enviar audio para transcricao e resposta\n\n"
            "E so falar naturalmente, sem comandos!"
        ),
    )


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /help."""
    await _telegram_call_with_retry(
        "reply_html_help",
        lambda: update.message.reply_html(
            "<b>Como usar:</b>\n\n"
            "Fale naturalmente. Exemplos:\n\n"
            "- \"Me recomenda algo como Solo Leveling\"\n"
            "- \"Analisa o Attack on Titan pra mim\"\n"
            "- \"Quem e o pai do Eren?\"\n"
            "- \"Tem temporada nova de Chainsaw Man?\"\n"
            "- \"Sites para ler manhwa\"\n"
            "- \"Acabei de assistir Steins;Gate, nota 10\"\n"
            "- Envie audio de voz e eu transcrevo para responder\n\n"
            "<b>Comandos:</b>\n"
            "/start - inicio\n"
            "/help - ajuda\n"
            "/historico - seu historico completo\n"
            "/novidades - digest de novidades\n"
            "/limpar - limpa o historico da conversa"
        ),
    )


async def handle_historico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /historico - mostra historico direto do Neo4j."""
    from bot.formatter import formatar_historico
    from graph.neo4j_client import get_neo4j

    user_id = str(update.effective_user.id)
    logger.info("/historico: user_id=%s", user_id)

    try:
        neo4j = get_neo4j()
        historico = neo4j.get_historico(user_id)
        texto = formatar_historico(
            historico.get("assistidos", []),
            historico.get("dropados", []),
            historico.get("progresso", []),
        )
        await _telegram_call_with_retry("reply_html_historico", lambda: update.message.reply_html(texto))
    except Exception as e:
        logger.error("/historico erro: %s", e)
        await _telegram_call_with_retry(
            "reply_text_historico_erro",
            lambda: update.message.reply_text("Erro ao carregar historico."),
        )


async def handle_novidades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /novidades - gera e envia o digest diario na hora."""
    from bot.notificador import enviar_diario

    user_id = str(update.effective_user.id)
    logger.info("/novidades: user_id=%s", user_id)

    msg = await _telegram_call_with_retry(
        "reply_text_novidades_processing",
        lambda: update.message.reply_text("Buscando novidades, aguenta ai..."),
    )
    try:
        await enviar_diario(context)
        await _safe_delete_message(msg)
    except Exception as e:
        logger.error("/novidades erro: %s", e)
        await _telegram_call_with_retry(
            "edit_text_novidades_erro",
            lambda: msg.edit_text("Erro ao buscar novidades. Tenta em instantes!"),
        )


async def handle_limpar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Limpa historico de conversa em memoria."""
    user_id = str(update.effective_user.id)
    _historicos.pop(user_id, None)
    logger.info("/limpar: user_id=%s", user_id)
    await _telegram_call_with_retry(
        "reply_text_limpar",
        lambda: update.message.reply_text("Historico de conversa limpo!"),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler principal - processa toda mensagem de texto."""
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    user_id = str(user.id)
    text = update.message.text.strip()

    logger.info("Mensagem recebida: user_id=%s len=%d preview='%s'", user_id, len(text), text[:50])

    msg_processando = await _telegram_call_with_retry(
        "reply_text_processing",
        lambda: update.message.reply_text("Pensando..."),
    )
    await _processar_input(update, user_id, text, msg_processando)


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler de audio/voz: transcreve via AssemblyAI e envia para IA."""
    if not update.message:
        return

    user = update.effective_user
    user_id = str(user.id)

    media = update.message.voice or update.message.audio
    if media is None and update.message.document and (update.message.document.mime_type or "").startswith("audio/"):
        media = update.message.document

    if media is None:
        return

    duration = getattr(media, "duration", None)
    file_id = media.file_id

    logger.info("Audio recebido: user_id=%s duration=%s", user_id, duration)
    msg_processando = await _telegram_call_with_retry(
        "reply_text_transcrevendo",
        lambda: update.message.reply_text("Transcrevendo audio..."),
    )

    try:
        tg_file = await context.bot.get_file(file_id)
        raw = await tg_file.download_as_bytearray()

        assemblyai = get_assemblyai()
        text = assemblyai.transcrever_audio(bytes(raw), duration_seconds=duration)
        if not text:
            raise RuntimeError("Transcricao vazia")

        preview = text[:MAX_AUDIO_PREVIEW].replace("\n", " ")
        suffix = "..." if len(text) > MAX_AUDIO_PREVIEW else ""
        await _telegram_call_with_retry(
            "edit_text_audio_preview",
            lambda: msg_processando.edit_text(f"Entendi: \"{preview}{suffix}\"\n\nPensando..."),
        )
        await _processar_input(update, user_id, text, msg_processando)
    except Exception as e:
        logger.error("handle_audio erro: user=%s error=%s", user_id, e, exc_info=True)
        await _telegram_call_with_retry(
            "edit_text_audio_erro",
            lambda: msg_processando.edit_text("Nao consegui transcrever esse audio agora. Tenta novamente ou manda em texto."),
        )


async def _processar_input(update: Update, user_id: str, text: str, msg_processando: Message) -> None:
    """Roda fluxo principal (orquestrador + resposta)."""
    history = _historicos.get(user_id, [])

    try:
        response = await processar_mensagem(user_id, text, history)
    except Exception as e:
        logger.error("processar_input erro: user=%s error=%s", user_id, e, exc_info=True)
        response = "Algo deu errado. Tenta de novo em instantes!"

    history = history + [
        {"role": "user", "content": text},
        {"role": "assistant", "content": response},
    ]
    _historicos[user_id] = history[-MAX_HISTORY:]

    texto_formatado = formatar_telegram(response)

    # 1) Tenta editar a mensagem "Pensando..."
    try:
        await _telegram_call_with_retry(
            "edit_text_resposta",
            lambda: msg_processando.edit_text(texto_formatado, parse_mode="HTML"),
        )
        logger.info("Resposta enviada via edit: user=%s len=%d", user_id, len(response))
        return
    except BadRequest as e:
        # message is not modified, message to edit not found, parse mode issue etc.
        logger.warning("Falha edit_text (BadRequest) user=%s: %s", user_id, e)
    except Exception as e:
        logger.warning("Falha edit_text user=%s: %s", user_id, e)

    # 2) Fallback: envia nova mensagem (html -> texto puro)
    sent = False
    try:
        await _telegram_call_with_retry(
            "reply_html_resposta",
            lambda: update.message.reply_html(texto_formatado),
        )
        sent = True
        logger.info("Resposta enviada via reply_html: user=%s len=%d", user_id, len(response))
    except BadRequest as e:
        logger.warning("Falha reply_html (BadRequest) user=%s: %s", user_id, e)
    except Exception as e:
        logger.warning("Falha reply_html user=%s: %s", user_id, e)

    if not sent:
        try:
            await _telegram_call_with_retry(
                "reply_text_resposta",
                lambda: update.message.reply_text(response),
            )
            logger.info("Resposta enviada via reply_text: user=%s len=%d", user_id, len(response))
        except Exception as e:
            logger.error("Falha total ao enviar resposta user=%s: %s", user_id, e, exc_info=True)

    await _safe_delete_message(msg_processando)


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handler de erros globais do bot."""
    if isinstance(context.error, NetworkError):
        logger.warning("Telegram NetworkError (transitorio): %s", context.error)
        return
    logger.error("Erro global no bot: %s", context.error, exc_info=context.error)
