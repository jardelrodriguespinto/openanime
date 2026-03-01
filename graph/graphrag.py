import re
import logging
from ai.openrouter import openrouter

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """Você é um extrator de conhecimento de textos sobre anime.
A partir do texto fornecido, extraia:
1. Temas principais (humor, melancolia, guerra, amizade, redenção, etc)
2. Atmosfera geral (pesado, leve, épico, slice-of-life, etc)
3. Elementos narrativos (viagem no tempo, harém, isekai, etc)

Retorne APENAS uma lista de palavras-chave separadas por vírgula.
Máximo 10 palavras. Sem explicação.
"""


class GraphRAG:
    """Extrai conhecimento de textos e alimenta Neo4j e Weaviate."""

    def extrair_temas(self, texto: str) -> list[str]:
        """Extrai temas e elementos de um texto."""
        if not texto or len(texto) < 30:
            return []

        messages = [
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user", "content": texto[:1000]},
        ]
        try:
            response = openrouter.search_synthesize(messages)
            temas = [t.strip().lower() for t in response.split(",") if t.strip()]
            logger.info("GraphRAG: %d temas extraídos de texto de %d chars", len(temas), len(texto))
            return temas[:10]
        except Exception as e:
            logger.error("GraphRAG erro na extração: %s", e)
            return []

    def processar_anime(self, anime: dict) -> dict:
        """Enriquece dados de um anime com temas extraídos."""
        synopsis = anime.get("synopsis", "")
        if not synopsis:
            return anime

        temas_extraidos = self.extrair_temas(synopsis)
        temas_existentes = anime.get("temas", [])
        anime["temas"] = list(set(temas_existentes + temas_extraidos))
        logger.debug("GraphRAG: anime=%s temas=%s", anime.get("titulo"), anime["temas"])
        return anime

    def processar_batch(self, animes: list[dict]) -> list[dict]:
        """Processa lista de animes."""
        resultado = []
        for i, anime in enumerate(animes):
            try:
                enriquecido = self.processar_anime(anime)
                resultado.append(enriquecido)
                if (i + 1) % 10 == 0:
                    logger.info("GraphRAG: %d/%d animes processados", i + 1, len(animes))
            except Exception as e:
                logger.error("GraphRAG erro em anime=%s: %s", anime.get("titulo"), e)
                resultado.append(anime)
        return resultado

    def extrair_sentimento(self, texto: str) -> str:
        """Classifica sentimento de uma review."""
        if not texto:
            return "neutro"

        messages = [
            {
                "role": "system",
                "content": "Classifique o sentimento: positivo, negativo ou neutro. Responda com uma palavra.",
            },
            {"role": "user", "content": texto[:500]},
        ]
        try:
            resp = openrouter.search_synthesize(messages).strip().lower()
            if "positivo" in resp:
                return "positivo"
            if "negativo" in resp:
                return "negativo"
            return "neutro"
        except Exception as e:
            logger.error("GraphRAG sentimento erro: %s", e)
            return "neutro"


graphrag = GraphRAG()
