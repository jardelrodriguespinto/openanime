import os
import logging
import asyncio

from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agents.orchestrator import processar_mensagem
from ai.evolution import send_text, send_document, send_audio, download_media
from ai.assemblyai import get_assemblyai
from bot.redis_history import get_redis_history
from bot.notificador import (
    enviar_diario,
    verificar_novos_episodios,
    coordinator_notificacoes,
    verificar_lancamentos_culturais,
)

logger = logging.getLogger(__name__)
app = FastAPI()
scheduler = AsyncIOScheduler()


def _extract_sender(payload: dict) -> str | None:
    sender = payload.get("from")
    if sender:
        return sender
    data = payload.get("data", payload)
    key = data.get("key", {}) if isinstance(data, dict) else {}
    remote_jid = key.get("remoteJid", "")
    if remote_jid:
        return remote_jid.split("@")[0]
    return None


def _extract_text(payload: dict) -> str:
    text = payload.get("text") or payload.get("message") or ""
    if text:
        return text
    data = payload.get("data", payload)
    if isinstance(data, dict):
        msg = data.get("message", {})
        if isinstance(msg, dict):
            text = msg.get("conversation") or msg.get("extendedTextMessage", {}).get("text") or ""
            if text:
                return text
    return ""


def _extract_media(payload: dict) -> dict | None:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    msg = data.get("message", {})
    if not isinstance(msg, dict):
        return None

    audio = msg.get("audioMessage")
    if audio:
        return {
            "type": "audio",
            "url": audio.get("url"),
            "mimetype": audio.get("mimetype") or "audio/ogg",
        }

    document = msg.get("documentMessage")
    if document:
        return {
            "type": "document",
            "url": document.get("url"),
            "mimetype": document.get("mimetype") or "application/pdf",
            "fileName": document.get("fileName") or "documento.pdf",
        }

    return None


@app.get("/")
async def health():
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()

    sender = _extract_sender(payload)
    if not sender:
        return {"ok": False, "error": "no_sender"}

    text = _extract_text(payload)
    media = _extract_media(payload)

    user_id = sender
    history = get_redis_history().get(user_id)

    async def process_and_reply():
        try:
            if media:
                if media["type"] == "audio":
                    await _handle_audio(user_id, media, history)
                elif media["type"] == "document":
                    await _handle_document(user_id, media, history)
                else:
                    await _handle_text(user_id, text, history)
            else:
                await _handle_text(user_id, text, history)
        except Exception as e:
            logger.error("Failed to process message user=%s: %s", user_id, e, exc_info=True)
            try:
                await send_text(user_id, "Algo deu errado. Tenta de novo em instantes!")
            except Exception:
                pass

    background_tasks.add_task(process_and_reply)
    return {"ok": True}


async def _handle_text(user_id: str, text: str, history: list) -> None:
    text_lower = (text or "").lower().strip()

    if text_lower in {"/pausar", "pausar", "pausa"}:
        try:
            from automation.browser import set_intervention_state
            asyncio.create_task(set_intervention_state("paused", True))
            asyncio.create_task(set_intervention_state("current_action", "pausado"))
            await send_text(user_id, "⏸ Automacao pausada. Use /continuar para retomar.")
        except Exception as e:
            await send_text(user_id, f"Erro ao pausar: {e}")
        return

    if text_lower in {"/continuar", "continuar", "continuar automacao", "resumir"}:
        try:
            from automation.browser import set_intervention_state
            asyncio.create_task(set_intervention_state("paused", False))
            asyncio.create_task(set_intervention_state("current_action", "rodando"))
            asyncio.create_task(set_intervention_state("intervention_type", None))
            asyncio.create_task(set_intervention_state("intervention_selector", None))
            await send_text(user_id, "▶ Automacao retomada!")
        except Exception as e:
            await send_text(user_id, f"Erro ao continuar: {e}")
        return

    if text_lower in {"/pular", "pular", "pula", "skip"}:
        try:
            from automation.browser import set_intervention_state
            asyncio.create_task(set_intervention_state("current_action", "pular"))
            await send_text(user_id, "⏭ Step atual serah pulado.")
        except Exception as e:
            await send_text(user_id, f"Erro ao pular: {e}")
        return

    result = await processar_mensagem(user_id, text, history)
    response = result.get("response", "")
    history2 = (history or []) + [
        {"role": "user", "content": text},
        {"role": "assistant", "content": response},
    ]
    get_redis_history().set(user_id, history2)

    candidatura_pendente = result.get("candidatura_pendente")
    if candidatura_pendente:
        get_redis_history().set_data(f"cand_pendente:{user_id}", candidatura_pendente, ttl=3600)

    await send_text(user_id, response)

    pdf_bytes = result.get("pdf_bytes")
    if pdf_bytes:
        pdf_filename = result.get("pdf_filename", "documento.pdf")
        await send_document(user_id, pdf_filename, pdf_bytes)


async def _handle_audio(user_id: str, media: dict, history: list) -> None:
    await send_text(user_id, "Transcrevendo audio...")
    try:
        raw = await download_media(media["url"])
        assemblyai = get_assemblyai()
        duration = getattr(media, "duration_seconds", None)
        text = await assemblyai.transcrever_audio(raw, duration_seconds=duration)
        if not text:
            raise RuntimeError("Transcricao vazia")
        await _handle_text(user_id, text, history)
    except Exception as e:
        logger.error("handle_audio erro user=%s: %s", user_id, e)
        await send_text(user_id, "Nao consegui transcrever esse audio. Tenta enviar em texto.")


async def _handle_document(user_id: str, media: dict, history: list) -> None:
    if "pdf" not in (media.get("mimetype") or "").lower():
        await send_text(user_id, "Por enquanto so aceito PDFs. Manda um arquivo .pdf!")
        return

    await send_text(user_id, "Recebi o PDF! Analisando...")
    try:
        raw = await download_media(media["url"])
        filename = media.get("fileName", "documento.pdf")
        max_bytes = int(os.getenv("PDF_MAX_SIZE_MB", "20")) * 1024 * 1024
        if len(raw) > max_bytes:
            await send_text(user_id, f"Esse PDF e muito grande (max {max_bytes // 1024 // 1024}MB). Tenta um menor!")
            return

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(raw)
            pdf_path = f.name

        await _handle_text(user_id, f"analisa este PDF: {filename}", history, pdf_path=pdf_path)
    except Exception as e:
        logger.error("handle_document erro user=%s: %s", user_id, e)
        await send_text(user_id, "Erro ao processar o PDF. Tenta novamente!")


def _schedule_jobs():
    from bot.notificador import enviar_diario, verificar_novos_episodios, coordinator_notificacoes, verificar_lancamentos_culturais

    scheduler.add_job(
        coordinator_notificacoes,
        "cron",
        minute="*",
        id="coordinator_notificacoes",
        replace_existing=True,
    )
    scheduler.add_job(
        verificar_lancamentos_culturais,
        "cron",
        day_of_week="fri",
        hour=12,
        minute=0,
        id="lancamentos_culturais",
        replace_existing=True,
    )
    scheduler.add_job(
        verificar_novos_episodios,
        "cron",
        hour="20",
        minute="0",
        id="verificar_novos_episodios",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("APScheduler iniciado com jobs de notificacao")


def run_server():
    port = int(os.getenv("EVOLUTION_PORT", "8081"))
    _schedule_jobs()
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    run_server()
