"""
Agente de noticias — busca RSS + DDG, sintetiza com LLM.
Suporta qualquer topico livre de forma dinamica.
"""

import logging
import re

from ai.openrouter import openrouter
from data.news import buscar_noticias, buscar_por_ddg, buscar_por_rss
from graph.neo4j_client import get_neo4j
import prompts.news as news_prompt

logger = logging.getLogger(__name__)

# Stopwords de comando — palavras que nao fazem parte do topico
_STOPWORDS = {
    "noticias", "noticia", "novidades", "novidade", "me", "da", "de", "do",
    "das", "dos", "sobre", "hoje", "agora", "o", "a", "os", "as", "e",
    "em", "no", "na", "nos", "nas", "por", "para", "com", "sem", "tem",
    "quero", "quais", "qual", "que", "recente", "recentes", "ultimo",
    "ultimas", "ultimos", "novo", "novos", "nova", "novas", "top", "mais",
    "principais", "manda", "traz", "busca", "fala", "conta", "mim",
    "aconteceu", "acontece", "rolou", "rola", "teve", "houve", "sera",
    "vai", "vou", "estou", "esta", "estao", "sao", "foi", "era", "faz",
    "fazendo", "quer", "gostei", "ouvi", "vi", "preciso", "procuro",
    "queria", "ver", "saber", "ouvir",
}

# Mapa de categorias RSS — so para suplementar DDG com fontes confiaveis
_CATEGORIAS_RSS: dict[str, list[str]] = {
    "tech":       ["tech", "tecnologia", "software", "hardware", "apple", "google", "microsoft"],
    "ia":         ["ia", "ai", "inteligencia artificial", "llm", "chatgpt", "machine learning", "openai"],
    "mercado":    ["mercado", "economia", "financeiro", "bolsa", "dolar", "inflacao", "emprego"],
    "games":      ["game", "jogo", "playstation", "xbox", "nintendo", "steam"],
    "ciencia":    ["ciencia", "pesquisa", "estudo", "descoberta", "fisica", "biologia", "espaco"],
    "brasil":     ["brasil", "politica", "governo", "congresso", "stf", "eleicao", "brasilia", "lula", "bolsonaro"],
    "programacao":["programacao", "codigo", "dev", "developer", "python", "javascript"],
    "startup":    ["startup", "unicornio", "venture", "funding"],
    "geral":      ["mundo", "internacional", "global", "geral", "tudo"],
}


def _extrair_query(mensagem: str) -> str:
    """
    Extrai query de busca limpa da mensagem do usuario.
    Remove ruido de comando e mantem o topico real.
    Ex: 'me da noticias do mercado de ti hoje' -> 'mercado ti'
        'o que rolou no futebol' -> 'futebol'
        'noticias' -> 'noticias hoje'  (fallback)
    """
    msg = mensagem.lower().strip()
    msg = re.sub(r"[^\w\s]", " ", msg)
    tokens = [t for t in msg.split() if t not in _STOPWORDS and len(t) > 1]
    query = " ".join(tokens[:6]).strip()
    return query if len(query) > 2 else "noticias hoje"


def _detectar_categorias_rss(query: str) -> list[str]:
    """
    Detecta quais categorias RSS suplementam bem a query.
    Retorna lista vazia se nenhuma categoria se aplica (so DDG).
    """
    q = query.lower()
    encontradas = []
    for cat, keywords in _CATEGORIAS_RSS.items():
        for kw in keywords:
            pattern = r"\b" + re.escape(kw) + r"\b"
            if re.search(pattern, q):
                encontradas.append(cat)
                break
    return list(dict.fromkeys(encontradas))


def news_node(state: dict) -> dict:
    """No LangGraph do agente de noticias."""
    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "")

    query = _extrair_query(mensagem)
    cats_rss = _detectar_categorias_rss(query)

    # Se nenhuma categoria detectada E query e generica, tenta interesses do perfil
    if not cats_rss and user_id and query in ("noticias hoje",):
        try:
            neo4j = get_neo4j()
            interesses = neo4j.get_interesses_noticias(user_id)
            if interesses:
                cats_rss = interesses
                # Reconstroi query a partir dos interesses salvos
                query = " ".join(interesses[:3])
        except Exception as e:
            logger.debug("news: erro ao buscar interesses: %s", e)

    # Salva topico no perfil se nao e query generica
    if query != "noticias hoje" and user_id:
        try:
            neo4j = get_neo4j()
            neo4j.salvar_interesses_noticias(user_id, cats_rss or [query])
        except Exception as e:
            logger.debug("news: erro ao salvar interesses: %s", e)

    logger.info("news: user=%s query=%r cats_rss=%s", user_id, query, cats_rss)

    # DDG e a fonte primaria — dinamica, cobre qualquer topico
    query_ddg = f"{query} noticias"
    noticias_ddg = buscar_por_ddg(query_ddg, limite=6)
    logger.info("news: DDG '%s' -> %d resultados", query_ddg, len(noticias_ddg))

    # RSS como suplemento para categorias com feeds confiaveis
    noticias_rss = []
    if cats_rss:
        noticias_rss = buscar_por_rss(cats_rss, limite=6)
        logger.info("news: RSS cats=%s -> %d resultados", cats_rss, len(noticias_rss))

    # Merge: DDG na frente (mais fresco), RSS como contexto sem duplicatas
    urls_ddg = {n["url"] for n in noticias_ddg if n.get("url")}
    rss_sem_dup = [n for n in noticias_rss if n.get("url") not in urls_ddg]
    noticias = (noticias_ddg + rss_sem_dup)[:10]

    # Fallback se ambos falharam
    if not noticias:
        cats_fallback = cats_rss or ["geral"]
        noticias = buscar_por_rss(cats_fallback, limite=8)
        logger.warning("news: DDG e RSS vazios, fallback RSS cats=%s -> %d", cats_fallback, len(noticias))

    label_cats = cats_rss if cats_rss else [query]
    messages = news_prompt.build_messages(noticias, label_cats, query=mensagem)

    try:
        response = openrouter.search_synthesize(messages)
    except Exception as e:
        logger.error("news: erro LLM: %s", e)
        if noticias:
            linhas = [f"Aqui estao as ultimas noticias de {', '.join(label_cats)}:\n"]
            for n in noticias[:5]:
                linhas.append(f"- {n['titulo']}")
                if n.get("url"):
                    linhas.append(f"  {n['url']}")
            response = "\n".join(linhas)
        else:
            response = f"Nao consegui buscar noticias de {', '.join(label_cats)} agora. Tenta em instantes!"

    logger.info("news: resposta gerada | user=%s query=%r cats=%s", user_id, query, cats_rss)
    return {"response": response}
