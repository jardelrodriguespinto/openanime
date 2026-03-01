import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from bs4 import BeautifulSoup

from agents.orchestrator import State
from ai.openrouter import openrouter
from data.anilist import anilist
from data.reddit import reddit
from data.rss import rss_news
from data.tvmaze import tvmaze
from data.wikipedia import wikipedia
from data.youtube import youtube_search
import prompts.search as search_prompt

logger = logging.getLogger(__name__)

_KEYWORDS_SITES = re.compile(
    r"\b(site|sites|link|links|onde\s+ler|onde\s+ver|onde\s+assistir|onde\s+baixar|"
    r"ler\s+online|assistir\s+online|gratis|de\s+graca|pirat|gratuito)\b",
    re.IGNORECASE,
)

_KEYWORDS_NEWS = re.compile(
    r"\b(novidade|novidades|news|lancamento|temporada|trending|tendencia|top da semana)\b",
    re.IGNORECASE,
)

_KEYWORDS_YOUTUBE = re.compile(
    r"\b(video|videos|youtube|resumo|recap|review|analise|explicado|explained)\b",
    re.IGNORECASE,
)

_KEYWORDS_SCHEDULE = re.compile(
    r"\b(data|datas|episodio|episodios|ep|agenda|quando sai|lancamento)\b",
    re.IGNORECASE,
)

_DDG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://lite.duckduckgo.com/",
}


def _buscar_ddg_lite(query: str, max_results: int = 8) -> list[dict]:
    """Busca via DDG Lite direto com httpx."""
    import urllib.parse

    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            resp = client.post(
                "https://lite.duckduckgo.com/lite/",
                data={"q": query},
                headers=_DDG_HEADERS,
            )
            if resp.status_code != 200:
                logger.warning("DDG Lite: status %d", resp.status_code)
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            seen = set()

            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                title = a.get_text(strip=True)

                if not href or not title:
                    continue
                if "duckduckgo.com/y.js" in href:
                    continue
                if href.startswith("/") or href.startswith("?"):
                    continue
                if "duckduckgo.com/l/" in href:
                    m = re.search(r"uddg=([^&]+)", href)
                    if m:
                        href = urllib.parse.unquote(m.group(1))
                    else:
                        continue

                if href in seen:
                    continue
                seen.add(href)

                snippet = ""
                parent = a.find_parent("td")
                if parent:
                    next_td = parent.find_next_sibling("td")
                    if next_td:
                        snippet = next_td.get_text(strip=True)[:150]

                results.append({"title": title, "href": href, "body": snippet})
                if len(results) >= max_results:
                    break

            logger.info("DDG Lite: %d resultados para '%s'", len(results), query)
            return results
    except Exception as e:
        logger.warning("DDG Lite erro: %s", e)
        return []


def _expandir_query(query: str) -> list[str]:
    """Gera variacoes da query para quando a busca retorna vazio."""
    variacoes = [query]
    mapeamentos = [
        (r"manhwa", "sites ler manhwa gratis portugues"),
        (r"webtoon", "sites ler webtoon gratis"),
        (r"manga", "sites ler manga gratis portugues"),
        (r"anime.*assisti", "sites assistir anime gratis"),
        (r"assisti.*anime", "sites assistir anime gratis"),
    ]
    for pattern, alternativa in mapeamentos:
        if re.search(pattern, query, re.IGNORECASE) and alternativa not in variacoes:
            variacoes.append(alternativa)
    return variacoes[:3]


def _buscar_web(query: str) -> list[dict]:
    """Busca web com fallback e expansao de termos."""
    queries = _expandir_query(query)
    for q in queries:
        results = _buscar_ddg_lite(q)
        if results:
            return results
        time.sleep(2)
    return []


def _collector_web(query: str) -> list[dict]:
    return _buscar_web(query)


def _collector_reddit(query: str) -> list[dict]:
    return reddit.buscar_discussoes(query, limit=6)


def _collector_news(query: str) -> dict:
    rss_results = rss_news.get_latest_news(query=query, limit=8, days=10)
    anilist_results = anilist.get_trending(limit=10) if _KEYWORDS_NEWS.search(query) else []
    wikipedia_results = wikipedia.search_summaries(query=query, limit=3)
    tvmaze_results = tvmaze.search_with_schedule(
        query=query,
        limit=3,
        days=30 if _KEYWORDS_SCHEDULE.search(query) else 14,
    )
    return {
        "rss": rss_results,
        "anilist": anilist_results,
        "wikipedia": wikipedia_results,
        "tvmaze": tvmaze_results,
    }


def _collector_youtube(query: str) -> list[dict]:
    # So consulta YouTube quando a intencao da pergunta bate com video/resumo.
    if not _KEYWORDS_YOUTUBE.search(query):
        return []
    return youtube_search.search_summary_videos(query=query, limit=5)


def search_node(state: State) -> dict:
    """Agente de busca com 4 subagentes paralelos + orquestracao final por LLM."""
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    collectors = {
        "web": _collector_web,
        "reddit": _collector_reddit,
        "news": _collector_news,
        "youtube": _collector_youtube,
    }

    results = {k: [] for k in collectors}
    status = {k: "ok" for k in collectors}

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_map = {
            executor.submit(fn, user_message): name
            for name, fn in collectors.items()
        }
        for fut in as_completed(future_map):
            name = future_map[fut]
            try:
                results[name] = fut.result()
            except Exception as e:
                status[name] = f"erro: {e}"
                results[name] = []

    web_results = results["web"] or []
    reddit_results = results["reddit"] or []
    news = results["news"] if isinstance(results["news"], dict) else {}
    rss_results = (news.get("rss") or []) if isinstance(news, dict) else []
    anilist_results = (news.get("anilist") or []) if isinstance(news, dict) else []
    wikipedia_results = (news.get("wikipedia") or []) if isinstance(news, dict) else []
    tvmaze_results = (news.get("tvmaze") or []) if isinstance(news, dict) else []
    youtube_results = results["youtube"] or []

    logger.info(
        "Busca paralela: user=%s web=%d reddit=%d rss=%d anilist=%d wiki=%d tvmaze=%d youtube=%d",
        user_id,
        len(web_results),
        len(reddit_results),
        len(rss_results),
        len(anilist_results),
        len(wikipedia_results),
        len(tvmaze_results),
        len(youtube_results),
    )

    messages = search_prompt.build_messages(
        user_message=user_message,
        history=history,
        search_results=web_results,
        reddit_results=reddit_results,
        rss_results=rss_results,
        anilist_results=anilist_results,
        wikipedia_results=wikipedia_results,
        tvmaze_results=tvmaze_results,
        youtube_results=youtube_results,
        source_status=status,
        is_sites_query=_KEYWORDS_SITES.search(user_message) is not None,
    )

    logger.info("Agente Busca: orquestrando sintese para user=%s", user_id)
    try:
        response = openrouter.search_synthesize(messages)
    except Exception as e:
        logger.error("Agente Busca: erro OpenRouter: %s", e)
        response = "Nao consegui buscar essa info agora. Tenta em instantes!"

    return {"response": response}
