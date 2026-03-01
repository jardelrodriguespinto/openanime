import logging
import os
import time

from openai import OpenAI
from openai import APIConnectionError, APITimeoutError, APIStatusError, InternalServerError, RateLimitError

logger = logging.getLogger(__name__)


def _parse_model_list(primary: str, fallbacks_raw: str) -> list[str]:
    models = []
    for item in [primary, *(fallbacks_raw or "").split(",")]:
        model = (item or "").strip()
        if model and model not in models:
            models.append(model)
    return models


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)):
        return True
    if isinstance(exc, APIStatusError):
        code = getattr(exc, "status_code", None)
        return isinstance(code, int) and (code == 429 or code >= 500)
    return False


class OpenRouterClient:
    """Cliente unificado para todos os modelos via OpenRouter."""

    def __init__(self):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY nao configurada")

        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            max_retries=0,
            default_headers={
                "HTTP-Referer": "https://github.com/anime-bot",
                "X-Title": "Anime Multi-Assistant",
            },
        )

        self.model_orchestrator = os.getenv(
            "MODEL_ORCHESTRATOR", "meta-llama/llama-3-8b-instruct"
        )
        self.model_chat = os.getenv("MODEL_CHAT", "anthropic/claude-sonnet-4-6")
        self.model_search = os.getenv(
            "MODEL_SEARCH", "meta-llama/llama-3-70b-instruct"
        )

        self.fallback_orchestrator = _parse_model_list(
            self.model_orchestrator,
            os.getenv("MODEL_ORCHESTRATOR_FALLBACKS", "meta-llama/llama-3.1-8b-instruct,openai/gpt-4o-mini"),
        )
        self.fallback_chat = _parse_model_list(
            self.model_chat,
            os.getenv("MODEL_CHAT_FALLBACKS", "openai/gpt-4o-mini,meta-llama/llama-3.1-70b-instruct"),
        )
        self.fallback_search = _parse_model_list(
            self.model_search,
            os.getenv("MODEL_SEARCH_FALLBACKS", "openai/gpt-4o-mini,meta-llama/llama-3.1-70b-instruct"),
        )

        self.per_model_attempts = max(1, int(os.getenv("OPENROUTER_PER_MODEL_ATTEMPTS", "2")))
        self.retry_base_seconds = max(0.1, float(os.getenv("OPENROUTER_RETRY_BASE_SECONDS", "1.0")))
        self.retry_max_seconds = max(0.5, float(os.getenv("OPENROUTER_RETRY_MAX_SECONDS", "4.0")))

        logger.info(
            "OpenRouterClient inicializado | orquestrador=%s | chat=%s | search=%s",
            self.model_orchestrator,
            self.model_chat,
            self.model_search,
        )

    def _chat_once(self, model: str, messages: list, temperature: float = 0.7, **kwargs) -> str:
        logger.debug("OpenRouter request | model=%s | messages=%d", model, len(messages))
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            **kwargs,
        )
        content = response.choices[0].message.content or ""
        logger.debug(
            "OpenRouter response | model=%s | tokens=%s | preview=%s...",
            model,
            response.usage.total_tokens if response.usage else "?",
            content[:80],
        )
        return content

    def _chat_with_fallback(
        self,
        models: list[str],
        messages: list,
        temperature: float = 0.7,
        **kwargs,
    ) -> str:
        last_exc = None

        for model in models:
            for attempt in range(1, self.per_model_attempts + 1):
                try:
                    return self._chat_once(model, messages, temperature=temperature, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    transient = _is_transient_error(exc)

                    if transient and attempt < self.per_model_attempts:
                        sleep_s = min(self.retry_base_seconds * (2 ** (attempt - 1)), self.retry_max_seconds)
                        logger.warning(
                            "OpenRouter transient error model=%s attempt=%d/%d: %s | retry em %.1fs",
                            model,
                            attempt,
                            self.per_model_attempts,
                            exc,
                            sleep_s,
                        )
                        time.sleep(sleep_s)
                        continue

                    logger.warning(
                        "OpenRouter falha model=%s attempt=%d/%d transient=%s: %s",
                        model,
                        attempt,
                        self.per_model_attempts,
                        transient,
                        exc,
                    )
                    break

            logger.info("OpenRouter: tentando fallback de modelo apos falha em %s", model)

        if last_exc:
            raise last_exc
        raise RuntimeError("OpenRouter sem modelos disponiveis")

    def chat(self, model: str, messages: list, temperature: float = 0.7, **kwargs) -> str:
        return self._chat_with_fallback([model], messages, temperature=temperature, **kwargs)

    def orchestrate(self, messages: list) -> str:
        """Classifica intencao — usa modelos baratos com fallback."""
        return self._chat_with_fallback(self.fallback_orchestrator, messages, temperature=0.1)

    def converse(self, messages: list) -> str:
        """Conversa e recomendacao — usa melhor modelo com fallback."""
        return self._chat_with_fallback(self.fallback_chat, messages, temperature=0.8)

    def search_synthesize(self, messages: list) -> str:
        """Sintese de buscas — usa modelo intermediario com fallback."""
        return self._chat_with_fallback(self.fallback_search, messages, temperature=0.5)


# Singleton
openrouter = OpenRouterClient()
