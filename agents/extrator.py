"""
Extrator de perfil - roda em background apos toda mensagem.
Usa modelo barato para detectar dados e salva no Neo4j.
Tambem tenta canonicalizar titulo no catalogo para reduzir lixo no grafo.
"""

import json
import logging
import re
from difflib import SequenceMatcher

from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
from graph.weaviate_client import get_weaviate

logger = logging.getLogger(__name__)

SYSTEM = """Analise a mensagem do usuario e extraia dados de perfil de anime/manga/manhwa.

Retorne APENAS um JSON com o formato:
{
  "acoes": [
    {"tipo": "assistido", "titulo": "...", "nota": null_ou_numero},
    {"tipo": "lido", "titulo": "...", "nota": null_ou_numero},
    {"tipo": "drop", "titulo": "...", "episodio": null_ou_numero},
    {"tipo": "quer_ver", "titulo": "..."},
    {"tipo": "gostou_de", "titulo": "..."}
  ]
}

Se nao houver nada relevante, retorne: {"acoes": []}
Retorne APENAS JSON.
"""

_STOPWORDS_TITULO = {
    "anime",
    "manga",
    "manhwa",
    "webtoon",
    "donghua",
    "obra",
    "serie",
    "temporada",
    "episodio",
    "capitulo",
    "etico",
    "sekai",
    "bom",
    "boa",
    "legal",
}


def _titulo_invalido(titulo: str) -> bool:
    text = (titulo or "").strip()
    if len(text) < 3:
        return True

    tokens = [t for t in re.split(r"\s+", text.lower()) if t]
    if not tokens:
        return True

    if len(tokens) == 1 and tokens[0] in _STOPWORDS_TITULO:
        return True

    if all(token in _STOPWORDS_TITULO for token in tokens):
        return True

    return False


def _similaridade(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _resolver_titulo_catalogo(titulo: str) -> tuple[str | None, dict | None]:
    """Tenta resolver para o titulo canonico no Jikan."""
    clean = (titulo or "").strip()
    if _titulo_invalido(clean):
        return None, None

    try:
        from data.jikan import jikan

        resultados = jikan.buscar_anime(clean[:80])
        if not resultados:
            # Se nao encontrou no catalogo, aceita apenas titulos com 2+ tokens
            if len(clean.split()) >= 2:
                return clean, None
            return None, None

        best = resultados[0]
        best_title = (best.get("titulo") or "").strip() or clean
        sim = _similaridade(clean, best_title)

        # Aceita match bom, ou titulo longo com forte sobreposicao
        if sim >= 0.52 or len(clean.split()) >= 2:
            return best_title, best
        return clean, None
    except Exception as e:
        logger.debug("Extrator: resolver titulo falhou para '%s': %s", clean, e)
        return clean, None


async def extrair_e_salvar(user_id: str, user_message: str) -> None:
    """Extrai dados de perfil da mensagem e salva no Neo4j em background."""
    try:
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_message},
        ]

        raw = openrouter.orchestrate(messages)
        data = _parse_json(raw)
        acoes = data.get("acoes", [])
        if not acoes:
            return

        neo4j = get_neo4j()
        neo4j.get_or_create_user(user_id)

        for acao in acoes:
            tipo = (acao.get("tipo") or "").strip()
            titulo_raw = (acao.get("titulo") or "").strip()
            if not titulo_raw:
                continue

            titulo_resolvido, anime_resolvido = _resolver_titulo_catalogo(titulo_raw)
            if not titulo_resolvido:
                logger.debug("Extrator: titulo ignorado por baixa qualidade: '%s'", titulo_raw)
                continue

            if tipo in ("assistido", "lido"):
                nota = acao.get("nota")
                if isinstance(nota, (int, float)) and not (0 <= nota <= 10):
                    nota = None
                neo4j.registrar_assistido(user_id, titulo_resolvido, nota)
                logger.info("Extrator: assistido/lido user=%s titulo=%s nota=%s", user_id, titulo_resolvido, nota)
                await _enriquecer_anime(titulo_resolvido, anime_resolvido)

            elif tipo == "drop":
                episodio = acao.get("episodio")
                neo4j.registrar_drop(user_id, titulo_resolvido, episodio)
                logger.info("Extrator: drop user=%s titulo=%s ep=%s", user_id, titulo_resolvido, episodio)
                await _enriquecer_anime(titulo_resolvido, anime_resolvido)

            elif tipo in ("quer_ver", "gostou_de"):
                neo4j.registrar_quer_ver(user_id, titulo_resolvido)
                logger.info("Extrator: quer_ver user=%s titulo=%s", user_id, titulo_resolvido)
                # Enriquecer quer_ver tambem ajuda a ligar usuario->genero via recomendacao futura
                await _enriquecer_anime(titulo_resolvido, anime_resolvido)

    except Exception as e:
        logger.debug("Extrator: erro (nao critico): %s", e)


async def _enriquecer_anime(titulo: str, anime: dict | None = None) -> None:
    """Enriquece Neo4j + Weaviate com dados do Jikan."""
    try:
        from data.jikan import jikan

        payload = anime
        if not payload:
            resultados = jikan.buscar_anime(titulo)
            if not resultados:
                return
            payload = resultados[0]

        if not payload.get("id"):
            return

        if not payload.get("synopsis") and not payload.get("generos"):
            return

        neo4j = get_neo4j()
        neo4j.upsert_anime(payload)
        logger.info(
            "Extrator: anime enriquecido titulo=%s generos=%s",
            titulo,
            payload.get("generos", []),
        )

        if payload.get("synopsis"):
            weaviate = get_weaviate()
            weaviate.upsert_anime(payload)
            logger.info("Extrator: anime indexado Weaviate titulo=%s", titulo)

    except Exception as e:
        logger.debug("Extrator: enriquecimento falhou para '%s': %s", titulo, e)


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except Exception:
            pass
    return {"acoes": []}
