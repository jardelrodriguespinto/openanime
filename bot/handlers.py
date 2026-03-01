import asyncio
import logging

from telegram import Message, Update
from telegram.error import BadRequest, NetworkError, RetryAfter
from telegram.ext import ContextTypes

from agents.orchestrator import processar_mensagem
from ai.assemblyai import get_assemblyai
from bot.formatter import formatar_telegram
from bot.redis_history import get_redis_history

logger = logging.getLogger(__name__)

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
            "Sou seu assistente pessoal multiuso de anime, manga, manhwa, filmes, series, doramas, musica e livros.\n\n"
            "Pode me perguntar sobre qualquer coisa:\n"
            "- Recomendacoes personalizadas no seu estilo\n"
            "- Analise e review de qualquer obra\n"
            "- Noticias, lancamentos e temporada atual\n"
            "- Sites e links para assistir/ler/ouvir\n"
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
            "- \"Noticias de tech hoje\"\n"
            "- \"Tem vaga de dev python remoto?\"\n"
            "- \"Gera meu curriculo ATS\"\n"
            "- \"Me candidata nessa vaga\"\n"
            "- Envie um PDF para analise automatica\n"
            "- Envie audio de voz e eu transcrevo para responder\n\n"
            "<b>Comandos:</b>\n"
            "/start - inicio\n"
            "/help - ajuda\n"
            "/historico - seu historico de midia\n"
            "/stats - suas estatisticas pessoais\n"
            "/maratona &lt;franquia&gt; - ordem de watch\n"
            "/novidades - digest de novidades de anime\n"
            "/noticias [area] - noticias gerais (tech, ia, mercado...)\n"
            "/vagas [query] - busca vagas de emprego\n"
            "/curriculo_ats - gera curriculo ATS personalizado\n"
            "/perfil_pro - seu perfil profissional\n"
            "/candidaturas - pipeline de candidaturas\n"
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
    from bot.notificador import enviar_diario_usuario

    user_id = str(update.effective_user.id)
    logger.info("/novidades: user_id=%s", user_id)

    msg = await _telegram_call_with_retry(
        "reply_text_novidades_processing",
        lambda: update.message.reply_text("Buscando novidades, aguenta ai..."),
    )
    try:
        enviado = await enviar_diario_usuario(context, user_id)
        if not enviado:
            raise RuntimeError("falha ao enviar digest on-demand")
        await _safe_delete_message(msg)
    except Exception as e:
        logger.error("/novidades erro: %s", e)
        await _telegram_call_with_retry(
            "edit_text_novidades_erro",
            lambda: msg.edit_text("Erro ao buscar novidades. Tenta em instantes!"),
        )


async def handle_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /stats - estatisticas pessoais do usuario."""
    from graph.neo4j_client import get_neo4j

    user_id = str(update.effective_user.id)
    logger.info("/stats: user_id=%s", user_id)

    try:
        neo4j = get_neo4j()
        stats = neo4j.get_stats_pessoais(user_id)
        texto = _formatar_stats(stats)
        await _telegram_call_with_retry(
            "reply_html_stats",
            lambda: update.message.reply_html(texto),
        )
    except Exception as e:
        logger.error("/stats erro: %s", e)
        await _telegram_call_with_retry(
            "reply_text_stats_erro",
            lambda: update.message.reply_text("Erro ao carregar suas stats."),
        )


def _formatar_stats(stats: dict) -> str:
    if not stats:
        return "Nenhuma estatistica ainda. Registra o que voce assistiu!"

    linhas = ["<b>Suas stats:</b>\n"]

    total_assistidos = stats.get("total_assistidos", 0)
    total_dropados = stats.get("total_dropados", 0)
    total_progresso = stats.get("total_progresso", 0)
    media_notas = stats.get("media_notas")
    drop_rate = stats.get("drop_rate", 0)
    top_generos = stats.get("top_generos", [])
    top_estudios = stats.get("top_estudios", [])

    linhas.append(f"Assistidos: <b>{total_assistidos}</b>")
    linhas.append(f"Em progresso: <b>{total_progresso}</b>")
    linhas.append(f"Dropados: <b>{total_dropados}</b>")
    if media_notas is not None:
        linhas.append(f"Nota media: <b>{media_notas}/10</b>")
    linhas.append(f"Taxa de drop: <b>{drop_rate}%</b>")
    if top_generos:
        linhas.append(f"\nGeneros favoritos: <b>{', '.join(top_generos)}</b>")
    if top_estudios:
        linhas.append(f"Studios favoritos: <b>{', '.join(top_estudios)}</b>")

    return "\n".join(linhas)


async def handle_maratona(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /maratona <franquia> - monta ordem completa de watch."""
    user_id = str(update.effective_user.id)
    args = context.args
    titulo = " ".join(args).strip() if args else ""

    if not titulo:
        await _telegram_call_with_retry(
            "reply_text_maratona_ajuda",
            lambda: update.message.reply_text("Me diz qual franquia! Ex: /maratona Naruto"),
        )
        return

    logger.info("/maratona: user_id=%s titulo=%s", user_id, titulo)
    msg_processando = await _telegram_call_with_retry(
        "reply_text_maratona_processing",
        lambda: update.message.reply_text("Montando guia de maratona..."),
    )
    await _processar_input(update, user_id, f"/maratona {titulo}", msg_processando)


async def handle_limpar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Limpa historico de conversa."""
    user_id = str(update.effective_user.id)
    get_redis_history().delete(user_id)
    logger.info("/limpar: user_id=%s", user_id)
    await _telegram_call_with_retry(
        "reply_text_limpar",
        lambda: update.message.reply_text("Historico de conversa limpo!"),
    )


_CONFIRMAR = {"sim", "sim!", "confirmo", "ok", "yes", "yeah", "claro", "pode", "bora"}
_CANCELAR = {"nao", "não", "nã", "nã!", "cancelar", "cancel", "no", "nope", "desistir"}


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler principal - processa toda mensagem de texto."""
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    user_id = str(user.id)
    text = update.message.text.strip()
    text_lower = text.lower().strip().rstrip("!")

    logger.info("Mensagem recebida: user_id=%s len=%d preview='%s'", user_id, len(text), text[:50])

    # --- Fluxo de confirmação de candidatura ---
    cand_pendente = get_redis_history().get_data(f"cand_pendente:{user_id}")
    if cand_pendente:
        if text_lower in _CONFIRMAR:
            get_redis_history().delete_data(f"cand_pendente:{user_id}")
            msg_exec = await _telegram_call_with_retry(
                "reply_text_candidatura_exec",
                lambda: update.message.reply_text("Executando candidatura..."),
            )
            try:
                from agents.apply import executar_candidatura
                resultado_cand = await executar_candidatura(
                    user_id=user_id,
                    vaga=cand_pendente["vaga"],
                    perfil=cand_pendente["perfil"],
                    plataforma=cand_pendente["plataforma"],
                )
                resp_cand = resultado_cand.get("mensagem", "Candidatura processada!")
            except Exception as e:
                logger.error("executar_candidatura erro user=%s: %s", user_id, e, exc_info=True)
                resp_cand = "Erro ao executar candidatura. Tenta manualmente!"
            texto_fmt = formatar_telegram(resp_cand)
            try:
                await _telegram_call_with_retry(
                    "edit_text_candidatura_resultado",
                    lambda: msg_exec.edit_text(texto_fmt, parse_mode="HTML"),
                )
            except Exception:
                await _telegram_call_with_retry(
                    "reply_text_candidatura_resultado",
                    lambda: update.message.reply_text(resp_cand),
                )
                await _safe_delete_message(msg_exec)
            return
        elif text_lower in _CANCELAR:
            get_redis_history().delete_data(f"cand_pendente:{user_id}")
            await _telegram_call_with_retry(
                "reply_text_candidatura_cancelada",
                lambda: update.message.reply_text("Candidatura cancelada!"),
            )
            return

    # --- Fluxo normal ---
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
        text = await assemblyai.transcrever_audio(bytes(raw), duration_seconds=duration)
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


async def _processar_input(
    update: Update,
    user_id: str,
    text: str,
    msg_processando: Message,
    pdf_path: str = "",
) -> None:
    """Roda fluxo principal (orquestrador + resposta)."""
    history = get_redis_history().get(user_id)

    try:
        resultado = await processar_mensagem(user_id, text, history, pdf_path=pdf_path)
        response = resultado.get("response", "Algo deu errado. Tenta de novo!")
    except Exception as e:
        logger.error("processar_input erro: user=%s error=%s", user_id, e, exc_info=True)
        response = "Algo deu errado. Tenta de novo em instantes!"
        resultado = {"response": response, "pdf_bytes": None, "pdf_filename": "", "candidatura_pendente": None}

    history = history + [
        {"role": "user", "content": text},
        {"role": "assistant", "content": response},
    ]
    get_redis_history().set(user_id, history)

    texto_formatado = formatar_telegram(response)

    # 1) Tenta editar a mensagem "Pensando..."
    edit_success = False
    try:
        await _telegram_call_with_retry(
            "edit_text_resposta",
            lambda: msg_processando.edit_text(texto_formatado, parse_mode="HTML"),
        )
        edit_success = True
        logger.info("Resposta enviada via edit: user=%s len=%d", user_id, len(response))
    except BadRequest as e:
        logger.warning("Falha edit_text (BadRequest) user=%s: %s", user_id, e)
        await _enviar_resposta_fallback(update, user_id, response, texto_formatado)
    except Exception as e:
        logger.warning("Falha edit_text user=%s: %s", user_id, e)
        await _enviar_resposta_fallback(update, user_id, response, texto_formatado)

    # Só apaga "Pensando..." se o edit falhou (fallback enviou nova mensagem)
    if not edit_success:
        await _safe_delete_message(msg_processando)

    # Armazena candidatura pendente para confirmação posterior
    candidatura_pendente = resultado.get("candidatura_pendente")
    if candidatura_pendente:
        get_redis_history().set_data(f"cand_pendente:{user_id}", candidatura_pendente, ttl=3600)
        logger.info("Candidatura pendente salva no Redis: user=%s", user_id)

    # Envia PDF gerado se houver
    pdf_bytes = resultado.get("pdf_bytes")
    if pdf_bytes:
        pdf_filename = resultado.get("pdf_filename", "documento.pdf")
        try:
            import io
            await _telegram_call_with_retry(
                "send_document_pdf",
                lambda: update.message.reply_document(
                    document=io.BytesIO(pdf_bytes),
                    filename=pdf_filename,
                ),
            )
            logger.info("PDF enviado: user=%s filename=%s", user_id, pdf_filename)
        except Exception as e:
            logger.error("Falha ao enviar PDF user=%s: %s", user_id, e)


async def _enviar_resposta_fallback(update: Update, user_id: str, response: str, texto_formatado: str) -> None:
    """Fallback: envia nova mensagem quando edit falha."""
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


async def handle_noticias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /noticias [categoria] - busca noticias gerais ou por area."""
    user_id = str(update.effective_user.id)
    args = context.args
    query = " ".join(args).strip() if args else "noticias gerais"

    logger.info("/noticias: user_id=%s query=%s", user_id, query)
    msg = await _telegram_call_with_retry(
        "reply_text_noticias_processing",
        lambda: update.message.reply_text("Buscando noticias..."),
    )
    await _processar_input(update, user_id, query or "noticias gerais", msg)


async def handle_vagas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /vagas [query] - busca vagas de emprego."""
    user_id = str(update.effective_user.id)
    args = context.args
    query = " ".join(args).strip() if args else "vagas para meu perfil"

    logger.info("/vagas: user_id=%s query=%s", user_id, query)
    msg = await _telegram_call_with_retry(
        "reply_text_vagas_processing",
        lambda: update.message.reply_text("Buscando vagas..."),
    )
    await _processar_input(update, user_id, f"busca vagas {query}" if query else "recomenda vagas para meu perfil", msg)


async def handle_curriculo_ats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /curriculo_ats - gera curriculo ATS personalizado."""
    user_id = str(update.effective_user.id)
    logger.info("/curriculo_ats: user_id=%s", user_id)
    msg = await _telegram_call_with_retry(
        "reply_text_curriculo_processing",
        lambda: update.message.reply_text("Gerando seu curriculo ATS..."),
    )
    await _processar_input(update, user_id, "gera meu curriculo ats personalizado", msg)


async def handle_perfil_pro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /perfil_pro - mostra perfil profissional."""
    user_id = str(update.effective_user.id)
    logger.info("/perfil_pro: user_id=%s", user_id)
    msg = await _telegram_call_with_retry(
        "reply_text_perfil_pro_processing",
        lambda: update.message.reply_text("Carregando seu perfil profissional..."),
    )
    await _processar_input(update, user_id, "me mostra meu perfil profissional", msg)


async def handle_candidaturas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /candidaturas - mostra pipeline de candidaturas."""
    user_id = str(update.effective_user.id)
    logger.info("/candidaturas: user_id=%s", user_id)
    msg = await _telegram_call_with_retry(
        "reply_text_candidaturas_processing",
        lambda: update.message.reply_text("Carregando suas candidaturas..."),
    )
    await _processar_input(update, user_id, "minhas candidaturas", msg)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para documentos PDF recebidos pelo Telegram."""
    if not update.message or not update.message.document:
        return

    doc = update.message.document
    mime = (doc.mime_type or "").lower()

    if "pdf" not in mime:
        await _telegram_call_with_retry(
            "reply_text_doc_nao_pdf",
            lambda: update.message.reply_text("Por enquanto so aceito PDFs. Manda um arquivo .pdf!"),
        )
        return

    user_id = str(update.effective_user.id)
    logger.info("PDF recebido: user_id=%s filename=%s size=%s", user_id, doc.file_name, doc.file_size)

    # Verifica tamanho (max 20MB)
    max_bytes = int(__import__("os").getenv("PDF_MAX_SIZE_MB", "20")) * 1024 * 1024
    if doc.file_size and doc.file_size > max_bytes:
        await _telegram_call_with_retry(
            "reply_text_doc_grande",
            lambda: update.message.reply_text(f"Esse PDF e muito grande (max {max_bytes // 1024 // 1024}MB). Tenta um menor!"),
        )
        return

    msg = await _telegram_call_with_retry(
        "reply_text_pdf_processing",
        lambda: update.message.reply_text("Recebi o PDF! Analisando..."),
    )

    try:
        import os
        tg_file = await doc.get_file()
        caminho = f"/tmp/{doc.file_unique_id}.pdf"
        await tg_file.download_to_drive(caminho)

        nome = doc.file_name or "documento.pdf"
        await _processar_input(update, user_id, f"analisa este PDF: {nome}", msg, pdf_path=caminho)
    except Exception as e:
        logger.error("handle_document erro: user=%s error=%s", user_id, e)
        await _telegram_call_with_retry(
            "edit_text_pdf_erro",
            lambda: msg.edit_text("Erro ao processar o PDF. Tenta novamente!"),
        )


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handler de erros globais do bot."""
    if isinstance(context.error, NetworkError):
        logger.warning("Telegram NetworkError (transitorio): %s", context.error)
        return
    logger.error("Erro global no bot: %s", context.error, exc_info=context.error)
