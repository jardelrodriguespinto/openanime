"""
Sub-agente de Analise - review profundo, comparador e explicador de final.
"""
import logging
import re

from agents.orchestrator import State
from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
from graph.weaviate_client import get_weaviate
from data.jikan import jikan
from data.reddit import reddit
import prompts.analysis as analysis_prompt

logger = logging.getLogger(__name__)


_VS_PATTERNS = [
    re.compile(r"(.+?)\s+vs\.?\s+(.+)", re.IGNORECASE),
    re.compile(r"compar(ar|acao|ação)\s+(.+?)\s+(com|vs)\s+(.+)", re.IGNORECASE),
]


def analysis_node(state: State) -> dict:
    """Analise profunda, comparacao e explicacao de final de obra."""
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    user_profile = {}
    try:
        neo4j = get_neo4j()
        neo4j.get_or_create_user(user_id)
        user_profile = neo4j.get_user_profile(user_id)
    except Exception as e:
        logger.warning("Analise: erro Neo4j: %s", e)

    titles = _extrair_titulos_para_comparacao(user_message)
    compare_mode = len(titles) >= 2

    jikan_data = []
    try:
        if compare_mode:
            for t in titles[:2]:
                results = jikan.buscar_anime(t[:80])
                if results:
                    jikan_data.append(results[0])
        else:
            jikan_data = jikan.buscar_anime(user_message[:80])
        logger.info("Analise: %d resultados Jikan para user=%s", len(jikan_data), user_id)
    except Exception as e:
        logger.warning("Analise: erro Jikan: %s", e)

    character_data = []
    try:
        for anime in jikan_data[:2]:
            anime_id = anime.get("id")
            if anime_id and str(anime_id).isdigit():
                chars = jikan.get_anime_characters(int(anime_id), limit=10)
                if chars:
                    character_data.append(
                        {
                            "titulo": anime.get("titulo", "?"),
                            "personagens": chars,
                        }
                    )
    except Exception as e:
        logger.debug("Analise: erro personagens Jikan: %s", e)

    weaviate_data = []
    try:
        wc = get_weaviate()
        weaviate_data = wc.busca_semantica(user_message, limit=5)
        logger.debug("Analise: %d resultados Weaviate", len(weaviate_data))
    except Exception as e:
        logger.warning("Analise: erro Weaviate: %s", e)

    reddit_data = []
    try:
        reddit_data = reddit.buscar_discussoes(user_message[:90], limit=6)
        logger.info("Analise: %d posts Reddit", len(reddit_data))
    except Exception as e:
        logger.warning("Analise: erro Reddit: %s", e)

    messages = analysis_prompt.build_messages(
        user_message=user_message,
        history=history,
        jikan_data=jikan_data,
        weaviate_data=weaviate_data,
        reddit_data=reddit_data,
        user_profile=user_profile,
        character_data=character_data,
        compare_mode=compare_mode,
    )

    logger.info("Agente Analise: gerando review para user=%s", user_id)
    try:
        response = openrouter.converse(messages)
    except Exception as e:
        logger.error("Agente Analise: erro OpenRouter: %s", e)
        response = "Nao consegui buscar os dados dessa obra agora. Tenta de novo?"

    return {"response": response, "user_profile": user_profile}


def _extrair_titulos_para_comparacao(text: str) -> list[str]:
    src = (text or "").strip()
    if not src:
        return []

    for pat in _VS_PATTERNS:
        m = pat.search(src)
        if not m:
            continue
        groups = [g for g in m.groups() if g and len(g) > 2]
        if pat.pattern.startswith("(.+?)"):
            left = m.group(1).strip()
            right = m.group(2).strip()
            return [left[:90], right[:90]]
        if len(groups) >= 3:
            return [groups[1][:90], groups[2][:90]]

    return []

