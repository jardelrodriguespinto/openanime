import logging
import os
import asyncio

import httpx

logger = logging.getLogger(__name__)


class AssemblyAIClient:
    """Cliente minimo para upload + transcricao no AssemblyAI."""

    def __init__(self):
        self.api_key = (
            os.getenv("ASSEMBLY_IA_KEY")
            or os.getenv("ASSEMBLYAI_API_KEY")
            or ""
        )
        if not self.api_key:
            raise ValueError("ASSEMBLY_IA_KEY nao configurada no .env")

        self.base_url = "https://api.assemblyai.com/v2"
        self.headers = {"authorization": self.api_key}
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=60.0,
        )

    async def transcrever_audio(
        self,
        audio_bytes: bytes,
        duration_seconds: int | None = None,
        language_code: str = "pt",
        min_duration_seconds: int = 10,
        timeout_seconds: int = 90,
    ) -> str:
        payload = self._pad_short_audio(
            audio_bytes,
            duration_seconds=duration_seconds,
            min_duration_seconds=min_duration_seconds,
        )
        audio_url = await self._upload(payload)
        transcript_id = await self._create_transcript(audio_url, language_code=language_code)
        return await self._wait_transcript(transcript_id, timeout_seconds=timeout_seconds)

    async def _upload(self, audio_bytes: bytes) -> str:
        resp = await self.client.post(
            "/upload",
            data=audio_bytes,
        )
        resp.raise_for_status()
        data = resp.json()
        upload_url = data.get("upload_url")
        if not upload_url:
            raise RuntimeError("AssemblyAI nao retornou upload_url")
        return upload_url

    async def _create_transcript(self, audio_url: str, language_code: str = "pt") -> str:
        payload = {
            "audio_url": audio_url,
            "language_code": language_code,
            "punctuate": True,
            "format_text": True,
        }
        resp = await self.client.post(
            "/transcript",
            headers={**self.headers, "content-type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        transcript_id = data.get("id")
        if not transcript_id:
            raise RuntimeError(f"AssemblyAI nao retornou id: {data}")
        return transcript_id

    async def _wait_transcript(self, transcript_id: str, timeout_seconds: int = 90) -> str:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            resp = await self.client.get(f"/transcript/{transcript_id}")
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")

            if status == "completed":
                text = (data.get("text") or "").strip()
                if not text:
                    raise RuntimeError("AssemblyAI completou sem texto")
                return text

            if status == "error":
                raise RuntimeError(data.get("error") or "Erro desconhecido no AssemblyAI")

            await asyncio.sleep(2)

        raise TimeoutError("Timeout aguardando transcricao do AssemblyAI")

    def _pad_short_audio(
        self,
        audio_bytes: bytes,
        duration_seconds: int | None,
        min_duration_seconds: int = 10,
    ) -> bytes:
        """
        Alguns audios muito curtos falham em provedores de STT.
        Se vier com duracao < min_duration_seconds, adiciona padding de bytes.
        """
        if duration_seconds is None or duration_seconds >= min_duration_seconds:
            return audio_bytes

        missing_seconds = max(min_duration_seconds - duration_seconds, 1)
        pad_size = missing_seconds * 16000
        sausage = (b"LINGUICA" + b"\x00") * ((pad_size // 9) + 1)
        padded = audio_bytes + sausage[:pad_size]
        logger.info(
            "AssemblyAI: audio curto (%ss), aplicado padding de %d bytes",
            duration_seconds,
            pad_size,
        )
        return padded


_assemblyai_client: AssemblyAIClient | None = None


def get_assemblyai() -> AssemblyAIClient:
    global _assemblyai_client
    if _assemblyai_client is None:
        _assemblyai_client = AssemblyAIClient()
    return _assemblyai_client
