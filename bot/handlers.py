import asyncio
import html
import logging
import unicodedata

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
MAX_TELEGRAM_MESSAGE_LEN = 4096
MAX_TELEGRAM_FORMATTED_CHUNK_LEN = 3900
_SPLIT_FLOOR_RATIO = 0.4


def _find_split_index(text: str, hard_limit: int) -> int:
    if len(text) <= hard_limit:
        return len(text)

    min_idx = int(hard_limit * _SPLIT_FLOOR_RATIO)
    window = text[:hard_limit]
    for sep in ("\n\n", "\n", ". ", " "):
        idx = window.rfind(sep, min_idx)
        if idx != -1:
            return idx + len(sep)
    return hard_limit


def _split_text_chunks(text: str, max_len: int) -> list[str]:
    base = (text or "").strip()
    if not base:
        return ["..."]

    chunks: list[str] = []
    remaining = base
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        cut = _find_split_index(remaining, max_len)
        part = remaining[:cut].rstrip()
        if not part:
            part = remaining[:max_len]
            cut = len(part)

        chunks.append(part)
        remaining = remaining[cut:].lstrip()

    return chunks


def _prepare_response_chunks(response: str) -> list[tuple[str, str]]:
    pending = _split_text_chunks(response, MAX_TELEGRAM_FORMATTED_CHUNK_LEN)
    prepared: list[tuple[str, str]] = []

    while pending:
        plain_chunk = pending.pop(0)
        formatted_chunk = formatar_telegram(plain_chunk)

        if len(formatted_chunk) <= MAX_TELEGRAM_FORMATTED_CHUNK_LEN:
            prepared.append((plain_chunk, formatted_chunk))
            continue

        if len(plain_chunk) <= 1:
            safe_plain = html.escape(plain_chunk or ".", quote=False)
            prepared.append((plain_chunk, safe_plain))
            continue

        split_at = _find_split_index(plain_chunk, max(1, len(plain_chunk) // 2))
        left = plain_chunk[:split_at].rstrip()
        right = plain_chunk[split_at:].lstrip()

        if not left or not right:
            split_at = max(1, len(plain_chunk) // 2)
            left = plain_chunk[:split_at]
            right = plain_chunk[split_at:]

        pending.insert(0, right)
        pending.insert(0, left)

    return prepared


async def _send_plain_text_chunks(update: Update, operation_prefix: str, text: str) -> bool:
    if not update.message:
        return False

    chunks = _split_text_chunks(text, MAX_TELEGRAM_MESSAGE_LEN - 50)
    sent = False
    for idx, chunk in enumerate(chunks, start=1):
        await _telegram_call_with_retry(
            f"{operation_prefix}_{idx}",
            lambda c=chunk: update.message.reply_text(c),
        )
        sent = True
    return sent


async def _send_response_chunks(
    update: Update,
    user_id: str,
    chunks: list[tuple[str, str]],
    start_index: int = 0,
) -> bool:
    if not update.message:
        return False

    for idx in range(start_index, len(chunks)):
        plain_chunk, html_chunk = chunks[idx]
        try:
            await _telegram_call_with_retry(
                f"reply_html_resposta_chunk_{idx + 1}",
                lambda c=html_chunk: update.message.reply_html(c),
            )
        except BadRequest as e:
            logger.warning("Falha reply_html chunk user=%s idx=%d: %s", user_id, idx + 1, e)
            remaining_plain = "\n\n".join(c[0] for c in chunks[idx:])
            try:
                return await _send_plain_text_chunks(
                    update,
                    "reply_text_resposta_chunk_fallback",
                    remaining_plain,
                )
            except Exception as plain_err:
                logger.error(
                    "Falha fallback reply_text chunk user=%s idx=%d: %s",
                    user_id,
                    idx + 1,
                    plain_err,
                    exc_info=True,
                )
                return False
        except Exception as e:
            logger.warning("Falha reply_html chunk user=%s idx=%d: %s", user_id, idx + 1, e)
            remaining_plain = "\n\n".join(c[0] for c in chunks[idx:])
            try:
                return await _send_plain_text_chunks(
                    update,
                    "reply_text_resposta_chunk_fallback",
                    remaining_plain,
                )
            except Exception as plain_err:
                logger.error(
                    "Falha fallback reply_text chunk user=%s idx=%d: %s",
                    user_id,
                    idx + 1,
                    plain_err,
                    exc_info=True,
                )
                return False

    return True


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
    nome = html.escape(user.first_name or "")
    await _telegram_call_with_retry(
        "reply_html_start",
        lambda: update.message.reply_html(
            f"Oi, {nome}!\n\n"
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
            "Como usar:\n\n"
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
            "Comandos:\n"
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
            "/notificacoes - configura horario e tipo de alertas\n"
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

    linhas = ["Suas stats:\n"]

    total_assistidos = stats.get("total_assistidos", 0)
    total_dropados = stats.get("total_dropados", 0)
    total_progresso = stats.get("total_progresso", 0)
    media_notas = stats.get("media_notas")
    drop_rate = stats.get("drop_rate", 0)
    top_generos = stats.get("top_generos", [])
    top_estudios = stats.get("top_estudios", [])

    linhas.append(f"Assistidos: {total_assistidos}")
    linhas.append(f"Em progresso: {total_progresso}")
    linhas.append(f"Dropados: {total_dropados}")
    if media_notas is not None:
        linhas.append(f"Nota media: {media_notas}/10")
    linhas.append(f"Taxa de drop: {drop_rate}%")
    if top_generos:
        linhas.append(f"\nGeneros favoritos: {', '.join(top_generos)}")
    if top_estudios:
        linhas.append(f"Studios favoritos: {', '.join(top_estudios)}")

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
_CANCELAR = {"nao", "cancelar", "cancel", "no", "nope", "desistir"}


def _normalizar_texto(texto: str) -> str:
    base = (texto or "").lower().strip().rstrip("!")
    sem_acentos = unicodedata.normalize("NFD", base)
    return "".join(ch for ch in sem_acentos if unicodedata.category(ch) != "Mn")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler principal - processa toda mensagem de texto."""
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    user_id = str(user.id)
    text = update.message.text.strip()
    text_norm = _normalizar_texto(text)

    logger.info("Mensagem recebida: user_id=%s len=%d preview='%s'", user_id, len(text), text[:50])

    # --- Fluxo de confirmacao de candidatura ---
    cand_pendente = get_redis_history().get_data(f"cand_pendente:{user_id}")
    if cand_pendente:
        if text_norm in _CONFIRMAR:
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
            resp_chunks = _prepare_response_chunks(resp_cand)
            texto_fmt = resp_chunks[0][1]
            try:
                await _telegram_call_with_retry(
                    "edit_text_candidatura_resultado",
                    lambda: msg_exec.edit_text(texto_fmt, parse_mode="HTML"),
                )
                if len(resp_chunks) > 1:
                    sent_extra = await _send_response_chunks(update, user_id, resp_chunks, start_index=1)
                    if not sent_extra:
                        logger.error("Falha ao enviar chunks de candidatura user=%s", user_id)
            except Exception:
                await _send_response_chunks(update, user_id, resp_chunks, start_index=0)
                await _safe_delete_message(msg_exec)
            return
        elif text_norm in _CANCELAR:
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

    response_chunks = _prepare_response_chunks(response)
    texto_formatado = response_chunks[0][1]

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

    # So apaga "Pensando..." se o edit falhou (fallback enviou nova mensagem)
    if not edit_success:
        await _safe_delete_message(msg_processando)
    elif len(response_chunks) > 1:
        sent_extra = await _send_response_chunks(update, user_id, response_chunks, start_index=1)
        if not sent_extra:
            logger.error("Falha ao enviar chunks adicionais user=%s", user_id)

    # Armazena candidatura pendente para confirmacao posterior
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
    _ = texto_formatado  # Mantido na assinatura para compatibilidade com chamadas atuais.
    chunks = _prepare_response_chunks(response)
    sent = await _send_response_chunks(update, user_id, chunks, start_index=0)
    if sent:
        logger.info("Resposta enviada via fallback chunked: user=%s chunks=%d", user_id, len(chunks))
    else:
        logger.error("Falha total ao enviar resposta user=%s", user_id)


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
        caminho = f"/tmp/{user_id}_{doc.file_unique_id}.pdf"
        await tg_file.download_to_drive(caminho)

        nome = doc.file_name or "documento.pdf"
        await _processar_input(update, user_id, f"analisa este PDF: {nome}", msg, pdf_path=caminho)
    except Exception as e:
        logger.error("handle_document erro: user=%s error=%s", user_id, e)
        await _telegram_call_with_retry(
            "edit_text_pdf_erro",
            lambda: msg.edit_text("Erro ao processar o PDF. Tenta novamente!"),
        )


async def handle_notificacoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando /notificacoes - exibe e permite configurar preferencias de notificacao.

    Uso:
      /notificacoes
      /notificacoes digest 7
      /notificacoes digest off
      /notificacoes episodios 21
      /notificacoes vagas 9
      /notificacoes noticias 8
      /notificacoes noticias off
    """
    from graph.neo4j_client import get_neo4j

    user_id = str(update.effective_user.id)
    args = context.args or []

    try:
        neo4j = get_neo4j()
        prefs = neo4j.get_preferencias_notificacao(user_id)
    except Exception as e:
        logger.error("/notificacoes erro Neo4j: %s", e)
        await update.message.reply_text("Erro ao carregar preferencias. Tenta de novo!")
        return

    TIPOS_VALIDOS = {"digest", "episodios", "vagas", "noticias"}
    LABELS = {
        "digest": "Digest de anime/manga",
        "episodios": "Alertas de episodios",
        "vagas": "Vagas de emprego",
        "noticias": "Noticias personalizadas",
    }

    if len(args) >= 2:
        tipo = args[0].lower()
        valor = args[1].lower()
        if tipo not in TIPOS_VALIDOS:
            opcoes = ", ".join(sorted(TIPOS_VALIDOS))
            await update.message.reply_text(
                f"Tipo invalido. Opcoes: {opcoes}"
            )
            return
        if valor == "off":
            prefs[f"{tipo}_ativo"] = False
            msg_ok = f"{LABELS[tipo]} desativado."
        else:
            try:
                hora = int(valor)
                if not 0 <= hora <= 23:
                    raise ValueError
            except ValueError:
                await update.message.reply_text(
                    "Hora invalida. Use um numero de 0 a 23 ou 'off' para desativar."
                )
                return
            prefs[f"{tipo}_ativo"] = True
            prefs[f"{tipo}_hora"] = hora
            msg_ok = f"{LABELS[tipo]} ativado para as {hora:02d}h (horario de Brasilia)."
        try:
            neo4j.salvar_preferencias_notificacao(user_id, prefs)
            await update.message.reply_text(msg_ok)
        except Exception as e:
            logger.error("/notificacoes salvar erro: %s", e)
            await update.message.reply_text("Erro ao salvar preferencias.")
        return

    linhas = ["Suas preferencias de notificacao:\n"]
    for tipo, label in LABELS.items():
        ativo = prefs.get(f"{tipo}_ativo", False)
        hora = prefs.get(f"{tipo}_hora", 0)
        status = f"{hora:02d}:00h" if ativo else "desativado"
        flag = "[ON]" if ativo else "[OFF]"
        linhas.append(f"{flag} {label}: {status}")

    linhas.append(
        "\nPara alterar: /notificacoes [tipo] [hora|off]\n"
        "Tipos: digest | episodios | vagas | noticias\n"
        "Ex: /notificacoes digest 7"
    )
    await _telegram_call_with_retry(
        "reply_text_notificacoes",
        lambda: update.message.reply_text("\n".join(linhas)),
    )

async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handler de erros globais do bot."""
    if isinstance(context.error, NetworkError):
        logger.warning("Telegram NetworkError (transitorio): %s", context.error)
        return
    logger.error("Erro global no bot: %s", context.error, exc_info=context.error)

