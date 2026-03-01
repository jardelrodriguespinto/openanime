import os
import logging
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

logger = logging.getLogger(__name__)


class OpenRouterClient:
    """Cliente unificado para todos os modelos via OpenRouter."""

    def __init__(self):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY não configurada")

        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
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
        logger.info(
            "OpenRouterClient inicializado | orquestrador=%s | chat=%s | search=%s",
            self.model_orchestrator,
            self.model_chat,
            self.model_search,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def chat(self, model: str, messages: list, temperature: float = 0.7, **kwargs) -> str:
        logger.debug(
            "OpenRouter request | model=%s | messages=%d", model, len(messages)
        )
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

    def orchestrate(self, messages: list) -> str:
        """Classifica intenção — usa modelo barato."""
        return self.chat(self.model_orchestrator, messages, temperature=0.1)

    def converse(self, messages: list) -> str:
        """Conversa e recomendação — usa melhor modelo."""
        return self.chat(self.model_chat, messages, temperature=0.8)

    def search_synthesize(self, messages: list) -> str:
        """Síntese de buscas — usa modelo intermediário."""
        return self.chat(self.model_search, messages, temperature=0.5)


# Singleton
openrouter = OpenRouterClient()
