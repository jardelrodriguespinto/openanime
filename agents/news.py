"""
Agente de noticias — busca RSS + DDG, sintetiza com LLM.
Suporta busca por nicho livre (futebol, saude, cripto, etc.)
"""

import logging
import re

from ai.openrouter import openrouter
from data.news import buscar_noticias, buscar_por_ddg
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

# Palavras de ruido que nao sao o topico em si
_STOPWORDS_NICHO = {
    "noticias", "noticia", "novidades", "novidade", "me", "da", "de", "do",
    "das", "dos", "sobre", "hoje", "agora", "o", "a", "os", "as", "e",
    "em", "no", "na", "nos", "nas", "por", "para", "com", "sem", "tem",
    "quero", "quais", "qual", "que", "recente", "recentes", "ultimo",
    "ultimas", "ultimos", "novo", "novos", "nova", "novas", "top", "mais",
    "principais", "manda", "traz", "busca", "fala", "conta", "mim",
    # verbos e expressoes de acao que nao sao topico
    "aconteceu", "acontece", "rolou", "rola", "teve", "tem", "teve",
    "houve", "sera", "vai", "vou", "estou", "esta", "estao", "sao",
    "foi", "era", "faz", "fazendo", "quer", "gostei", "ouvi", "vi",
    "preciso", "procuro", "queria",
}


def _detectar_categorias(mensagem: str) -> list[str]:
    """Detecta categorias predefinidas na mensagem."""
    msg = mensagem.lower()
    encontradas = []

    for cat, keywords in _KEYWORDS_CATEGORIA.items():
        for kw in keywords:
            # Word boundary para evitar falso positivo (ex: "ia" em "noticias")
            pattern = r"\b" + re.escape(kw) + r"\b"
            if re.search(pattern, msg):
                encontradas.append(cat)
                break

    return list(dict.fromkeys(encontradas)) or ["geral"]


def _extrair_topico_nicho(mensagem: str, categorias: list[str]) -> str:
    """
    Extrai o topico de nicho da mensagem para busca DDG.
    Retorna string vazia se a mensagem nao tem topico especifico alem das categorias.
    Ex: 'noticias de futebol' -> 'futebol'
        'noticias de seguranca da informacao' -> 'seguranca da informacao'
        'noticias do brasil' -> '' (ja coberto por categoria)
    """
    msg = mensagem.lower()

    # Remove pontuacao
    msg = re.sub(r"[^\w\s]", " ", msg)

    # Todas as keywords das categorias detectadas — sao o tema, nao nicho livre
    keywords_cats: set[str] = set()
    for cat in categorias:
        for kw in _KEYWORDS_CATEGORIA.get(cat, []):
            keywords_cats.update(kw.lower().split())

    tokens = [
        t for t in msg.split()
        if t not in _STOPWORDS_NICHO and t not in keywords_cats and len(t) > 2
    ]

    topico = " ".join(tokens[:5]).strip()
    # Considera nicho valido se sobrou algo alem do ruido
    return topico if len(topico) > 3 else ""


def news_node(state: dict) -> dict:
    """No LangGraph do agente de noticias."""
    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "")

    categorias = _detectar_categorias(mensagem)
    topico_nicho = _extrair_topico_nicho(mensagem, categorias)

    # Se apenas "geral" e sem nicho especifico, tenta interesses salvos no perfil
    if categorias == ["geral"] and not topico_nicho and user_id:
        try:
            neo4j = get_neo4j()
            interesses = neo4j.get_interesses_noticias(user_id)
            if interesses:
                categorias = interesses
        except Exception as e:
            logger.debug("news: erro ao buscar interesses: %s", e)

    # Salva categorias/nicho no perfil se especificados explicitamente
    cats_para_salvar = categorias if categorias != ["geral"] else (
        [topico_nicho] if topico_nicho else []
    )
    if cats_para_salvar and user_id:
        try:
            neo4j = get_neo4j()
            neo4j.salvar_interesses_noticias(user_id, cats_para_salvar)
        except Exception as e:
            logger.debug("news: erro ao salvar interesses: %s", e)

    logger.info("news: user=%s cats=%s nicho=%r", user_id, categorias, topico_nicho)

    # Busca principal via RSS das categorias
    noticias = buscar_noticias(categorias, query_livre=mensagem, limite=8)

    # Se tem nicho livre, busca DDG especificamente para ele e prioriza
    if topico_nicho:
        query_ddg = f"{topico_nicho} noticias hoje"
        ddg_nicho = buscar_por_ddg(query_ddg, limite=6)
        if ddg_nicho:
            # Nicho DDG vai na frente; RSS vai por tras como contexto
            urls_ddg = {n["url"] for n in ddg_nicho}
            rss_sem_dup = [n for n in noticias if n.get("url") not in urls_ddg]
            noticias = ddg_nicho + rss_sem_dup
            logger.info("news: nicho '%s' -> %d DDG + %d RSS", topico_nicho, len(ddg_nicho), len(rss_sem_dup))

    noticias = noticias[:10]
    label_cats = [topico_nicho] if topico_nicho and categorias == ["geral"] else categorias
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

    logger.info("news: resposta gerada | user=%s cats=%s nicho=%r", user_id, categorias, topico_nicho)
    return {"response": response}
