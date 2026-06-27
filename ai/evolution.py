import os
import logging
import httpx
from io import BytesIO

logger = logging.getLogger(__name__)

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL")
EVOLUTION_API_TOKEN = os.getenv("EVOLUTION_API_TOKEN")

MAX_EVOLUTION_MESSAGE_LEN = 4096
MAX_EVOLUTION_TEXT_LEN = 4000


def _headers() -> dict:
    if not EVOLUTION_API_URL or not EVOLUTION_API_TOKEN:
        raise RuntimeError("EVOLUTION_API_URL or EVOLUTION_API_TOKEN not configured")
    return {"Authorization": f"Bearer {EVOLUTION_API_TOKEN}"}


async def send_text(to: str, text: str) -> dict:
    """Send a text message via Evolution API to a WhatsApp number."""
    if not to or not text:
        return {"ok": True}

    chunks = _chunk_text(text)
    results = []
    for chunk in chunks:
        url = EVOLUTION_API_URL.rstrip("/") + "/messages"
        headers = {**_headers(), "Content-Type": "application/json"}
        payload = {
            "to": to,
            "type": "text",
            "text": {"body": chunk},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload, headers=headers)
            try:
                resp.raise_for_status()
            except Exception as e:
                logger.error("Evolution send_text failed: %s %s", resp.status_code, resp.text)
                raise
            results.append(resp.json())
    return results[-1] if results else {"ok": True}


async def send_document(to: str, filename: str, file_bytes: bytes, caption: str = "") -> dict:
    """Send a document/file via Evolution API (multipart form)."""
    if not to or not file_bytes:
        return {"ok": True}

    url = EVOLUTION_API_URL.rstrip("/") + "/messages"
    headers = _headers()
    data = {
        "to": to,
        "type": "document",
    }
    if caption:
        data["text"] = {"body": caption[:MAX_EVOLUTION_MESSAGE_LEN]}

    files = {
        "file": (filename, BytesIO(file_bytes), "application/pdf"),
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, data=data, files=files, headers=headers)
        try:
            resp.raise_for_status()
        except Exception as e:
            logger.error("Evolution send_document failed: %s %s", resp.status_code, resp.text)
            raise
        return resp.json()


async def send_audio(to: str, audio_bytes: bytes, mimetype: str = "audio/ogg") -> dict:
    """Send an audio voice note via Evolution API (multipart form)."""
    if not to or not audio_bytes:
        return {"ok": True}

    url = EVOLUTION_API_URL.rstrip("/") + "/messages"
    headers = _headers()
    data = {
        "to": to,
        "type": "audio",
    }

    ext = mimetype.split("/")[-1] if mimetype else "ogg"
    files = {
        "file": (f"audio.{ext}", BytesIO(audio_bytes), mimetype),
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, data=data, files=files, headers=headers)
        try:
            resp.raise_for_status()
        except Exception as e:
            logger.error("Evolution send_audio failed: %s %s", resp.status_code, resp.text)
            raise
        return resp.json()


async def download_media(media_url: str) -> bytes:
    """Download media bytes from Evolution API media endpoint."""
    headers = _headers()
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(media_url, headers=headers)
        resp.raise_for_status()
        return resp.content


def _chunk_text(text: str, max_len: int = MAX_EVOLUTION_TEXT_LEN) -> list[str]:
    """Split long text into Evolution-safe chunks."""
    base = (text or "").strip()
    if not base:
        return [""]
    if len(base) <= max_len:
        return [base]

    chunks = []
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


def _find_split_index(text: str, hard_limit: int) -> int:
    if len(text) <= hard_limit:
        return len(text)
    min_idx = int(hard_limit * 0.4)
    window = text[:hard_limit]
    for sep in ("\n\n", "\n", ". ", " "):
        idx = window.rfind(sep, min_idx)
        if idx != -1:
            return idx + len(sep)
    return hard_limit
