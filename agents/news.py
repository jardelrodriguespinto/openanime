"""
Agente de noticias — busca RSS + DDG, sintetiza com LLM.
"""

import logging
import re

from ai.openrouter import openrouter
from data.news import buscar_noticias
from graph.neo4j_client import get_neo4j
import prompts.news as news_prompt

logger = logging.getLogger(__name__)

_CATEGORIAS_VALIDAS = {
    "tech", "ia", "mercado", "games", "ciencia",
    "brasil", "geral", "programacao", "startup",
}

_KEYWORDS_CATEGORIA: dict[str, list[str]] = {
    "tech":       ["tech", "tecnologia", "software", "hardware", "apple", "google", "microsoft"],
    "ia":         ["ia", "ai", "inteligencia artificial", "llm", "chatgpt", "machine learning", "openai"],
    "mercado":    ["mercado", "economia", "financeiro", "bolsa", "dolar", "inflacao", "emprego"],
    "games":      ["game", "jogo", "playstation", "xbox", "nintendo", "steam", "fps", "rpg"],
    "ciencia":    ["ciencia", "pesquisa", "estudo", "descoberta", "fisica", "biologia", "espaco"],
    "brasil":     ["brasil", "politica", "governo", "congresso", "stf", "eleicao", "brasilia", "lula", "bolsonaro"],
    "programacao":["programacao", "codigo", "dev", "developer", "python", "javascript", "backend", "frontend"],
    "startup":    ["startup", "unicornio", "venture", "funding", "seed", "serie a"],
    "geral":      ["mundo", "internacional", "global", "geral", "noticias gerais", "tudo", "hoje"],
}


def _detectar_categorias(mensagem: str) -> list[str]:
    """Detecta categorias pedidas na mensagem do usuario."""
    msg = mensagem.lower()
    encontradas = []

    for cat, keywords in _KEYWORDS_CATEGORIA.items():
        for kw in keywords:
            if kw in msg:
                encontradas.append(cat)
                break

    return list(dict.fromkeys(encontradas)) or ["geral"]


def news_node(state: dict) -> dict:
    """No LangGraph do agente de noticias."""
    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "")

    categorias = _detectar_categorias(mensagem)

    # Se tem categorias salvas no perfil e usuario nao especificou nada alem de 'geral'
    if categorias == ["geral"] and user_id:
        try:
            neo4j = get_neo4j()
            interesses = neo4j.get_interesses_noticias(user_id)
            if interesses:
                categorias = interesses
        except Exception as e:
            logger.debug("news: erro ao buscar interesses: %s", e)

    # Salva categorias no perfil se especificadas explicitamente
    if categorias != ["geral"] and user_id:
        try:
            neo4j = get_neo4j()
            neo4j.salvar_interesses_noticias(user_id, categorias)
        except Exception as e:
            logger.debug("news: erro ao salvar interesses: %s", e)

    noticias = buscar_noticias(categorias, query_livre=mensagem, limite=10)
    messages = news_prompt.build_messages(noticias, categorias, query=mensagem)

    try:
        response = openrouter.search_synthesize(messages)
    except Exception as e:
        logger.error("news: erro LLM: %s", e)
        if noticias:
            linhas = [f"Aqui estao as ultimas noticias de {', '.join(categorias)}:\n"]
            for n in noticias[:5]:
                linhas.append(f"- {n['titulo']}")
                if n.get("url"):
                    linhas.append(f"  {n['url']}")
            response = "\n".join(linhas)
        else:
            response = f"Nao consegui buscar noticias de {', '.join(categorias)} agora. Tenta em instantes!"

    logger.info("news: resposta gerada | user=%s categorias=%s", user_id, categorias)
    return {"response": response}
