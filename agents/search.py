import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

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
    r"\b(video|videos|youtube|resumo|recap|review|analise|explicado|explained|"
    r"clipe|clip|mv|lyric|visualizer|official\s+video|live)\b",
    re.IGNORECASE,
)

_KEYWORDS_SCHEDULE = re.compile(
    r"\b(data|datas|episodio|episodios|ep|agenda|quando sai|lancamento)\b",
    re.IGNORECASE,
)

_KEYWORDS_MUSICA = re.compile(
    r"\b(musica|musicas|artista|banda|album|single|turne|show|concerto|"
    r"lancamento musical|novo album|nova musica|spotify|kpop|rock|pop|jazz|rap|funk)\b",
    re.IGNORECASE,
)

_KEYWORDS_LIVRO = re.compile(
    r"\b(livro|livros|autor|autora|romance|novel|ebook|literatura|escritor|"
    r"novo livro|lancamento literario|goodreads|amazon livro|biblioteca)\b",
    re.IGNORECASE,
)

_KEYWORDS_WIKIPEDIA = re.compile(
    r"\b(quem\s+e|quem\s+é|personagem|historia|história|lore|origem|explica|"
    r"resumo\s+da\s+historia|enredo|biografia)\b",
    re.IGNORECASE,
)

_KEYWORDS_EXPLICIT_NON_PT = re.compile(
    r"\b(ingles|ingl[eê]s|english|in english|em english|"
    r"espanhol|spanish|japones|japanese|qualquer idioma|any language)\b",
    re.IGNORECASE,
)

_DDG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://lite.duckduckgo.com/",
}

_LINK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; anime-assistant-linkcheck/1.0)",
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
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
    # Reddit recebe prioridade alta por pedido de "tempo real" e feedback do usuario.
    return reddit.buscar_discussoes(query, limit=12)


def _collector_news(query: str) -> dict:
    rss_results = rss_news.get_latest_news(query=query, limit=8, days=10)
    anilist_results = anilist.get_trending(limit=10) if _KEYWORDS_NEWS.search(query) else []
    wikipedia_results = wikipedia.search_summaries(query=query, limit=3) if _KEYWORDS_WIKIPEDIA.search(query) else []
    tvmaze_results = []
    if _KEYWORDS_SCHEDULE.search(query):
        tvmaze_results = tvmaze.search_with_schedule(
            query=query,
            limit=3,
            days=30,
        )
    return {
        "rss": rss_results,
        "anilist": anilist_results,
        "wikipedia": wikipedia_results,
        "tvmaze": tvmaze_results,
    }


def _collector_youtube(query: str) -> list[dict]:
    if not (_KEYWORDS_YOUTUBE.search(query) or _KEYWORDS_MUSICA.search(query)):
        return []
    yt_query = query
    if _KEYWORDS_MUSICA.search(query) and not _KEYWORDS_YOUTUBE.search(query):
        yt_query = f"{query} clipe oficial lyric"
    return youtube_search.search_summary_videos(query=yt_query, limit=5)


def _collector_musica_livro(query: str) -> dict:
    """Coleta de musica e livro via APIs abertas (opcional por keyword)."""
    if not _KEYWORDS_MUSICA.search(query) and not _KEYWORDS_LIVRO.search(query):
        return {"musica": [], "livro": []}

    musica_results = []
    livro_results = []

    if _KEYWORDS_MUSICA.search(query):
        try:
            from data.musicbrainz import musicbrainz

            musica_results = musicbrainz.buscar_artista(query[:80])[:5]
        except Exception as e:
            logger.debug("collector_musica_livro: musicbrainz erro: %s", e)

    if _KEYWORDS_LIVRO.search(query):
        try:
            from data.openlibrary import openlibrary

            livro_results = openlibrary.buscar_livro(query[:80])[:5]
        except Exception as e:
            logger.debug("collector_musica_livro: openlibrary erro: %s", e)

    return {"musica": musica_results, "livro": livro_results}


def _pick_url(item: dict, preferred_keys: tuple[str, ...] = ()) -> str:
    for key in preferred_keys:
        value = (item.get(key) or "").strip()
        if value:
            return value
    for key in ("href", "url", "permalink", "show_url"):
        value = (item.get(key) or "").strip()
        if value:
            return value
    return ""


def _is_http_url(url: str) -> bool:
    return isinstance(url, str) and (url.startswith("http://") or url.startswith("https://"))


def _check_link(url: str, timeout: float = 6.0) -> bool:
    if not _is_http_url(url):
        return False

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_LINK_HEADERS) as client:
            resp = client.get(url)
            code = resp.status_code
            if 200 <= code < 400:
                return True
            # Sites com antibot costumam retornar isso para clients.
            if code in {401, 403, 405, 429}:
                return True
            # Erro de gateway/transiente nao deve derrubar link potencialmente valido.
            if 500 <= code <= 599:
                return True
            return False
    except Exception:
        return False


def _validate_items_with_links(
    items: list[dict],
    preferred_keys: tuple[str, ...] = (),
    max_to_check: int = 10,
) -> tuple[list[dict], dict]:
    if not items:
        return [], {"checked": 0, "alive": 0, "removed": 0}

    keep = []
    check_bucket = []

    for item in items:
        url = _pick_url(item, preferred_keys=preferred_keys)
        if not url:
            keep.append(item)
            continue

        if len(check_bucket) < max(1, max_to_check):
            check_bucket.append((item, url))
        else:
            keep.append(item)

    checked = len(check_bucket)
    if checked == 0:
        return keep, {"checked": 0, "alive": 0, "removed": 0}

    alive_items = []
    with ThreadPoolExecutor(max_workers=min(8, checked)) as executor:
        future_map = {executor.submit(_check_link, url): item for item, url in check_bucket}
        for fut in as_completed(future_map):
            item = future_map[fut]
            try:
                if fut.result():
                    alive_items.append(item)
            except Exception:
                pass

    alive = len(alive_items)
    removed = checked - alive
    return keep + alive_items, {"checked": checked, "alive": alive, "removed": removed}


def _prefer_portuguese(query: str) -> bool:
    text = (query or "").strip()
    if not text:
        return True
    return _KEYWORDS_EXPLICIT_NON_PT.search(text) is None


def _is_portuguese_link(item: dict) -> bool:
    fields = [
        str(item.get("href", "")),
        str(item.get("url", "")),
        str(item.get("permalink", "")),
        str(item.get("title", "")),
        str(item.get("body", "")),
        str(item.get("snippet", "")),
    ]
    text = " ".join(fields).lower()
    return (
        ".br" in text
        or "/pt/" in text
        or "pt-br" in text
        or "portugu" in text
        or ".com.br" in text
    )


def _rank_language(items: list[dict], prefer_pt: bool) -> list[dict]:
    if not items:
        return []
    if not prefer_pt:
        return items

    pt_items = [it for it in items if _is_portuguese_link(it)]
    other_items = [it for it in items if not _is_portuguese_link(it)]
    return pt_items + other_items


def _agent_link_validator(payload: dict) -> tuple[dict, str]:
    """Subagente de confiabilidade: remove links quebrados antes da sintese."""
    total_checked = 0
    total_alive = 0

    web_valid, st = _validate_items_with_links(payload.get("web", []), preferred_keys=("href", "url"), max_to_check=10)
    payload["web"] = web_valid
    total_checked += st["checked"]
    total_alive += st["alive"]

    rss_valid, st = _validate_items_with_links(payload.get("rss", []), preferred_keys=("href", "url"), max_to_check=8)
    payload["rss"] = rss_valid
    total_checked += st["checked"]
    total_alive += st["alive"]

    wiki_valid, st = _validate_items_with_links(payload.get("wikipedia", []), preferred_keys=("url",), max_to_check=6)
    payload["wikipedia"] = wiki_valid
    total_checked += st["checked"]
    total_alive += st["alive"]

    tv_valid, st = _validate_items_with_links(payload.get("tvmaze", []), preferred_keys=("url",), max_to_check=6)
    payload["tvmaze"] = tv_valid
    total_checked += st["checked"]
    total_alive += st["alive"]

    yt_valid, st = _validate_items_with_links(payload.get("youtube", []), preferred_keys=("href", "url"), max_to_check=8)
    payload["youtube"] = yt_valid
    total_checked += st["checked"]
    total_alive += st["alive"]

    reddit_valid, st = _validate_items_with_links(payload.get("reddit", []), preferred_keys=("permalink", "url"), max_to_check=8)
    payload["reddit"] = reddit_valid
    total_checked += st["checked"]
    total_alive += st["alive"]

    if total_checked == 0:
        return payload, "n/a"
    return payload, f"ok {total_alive}/{total_checked}"


def search_node(state: State) -> dict:
    """Agente de busca com coletores paralelos + validador de links + sintese final."""
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    collectors = {
        "web": _collector_web,
        "reddit": _collector_reddit,
        "news": _collector_news,
        "youtube": _collector_youtube,
        "cultural": _collector_musica_livro,
    }

    results = {k: [] for k in collectors}
    status = {k: "ok" for k in collectors}

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_map = {executor.submit(fn, user_message): name for name, fn in collectors.items()}
        for fut in as_completed(future_map):
            name = future_map[fut]
            try:
                results[name] = fut.result()
            except Exception as e:
                status[name] = f"erro: {e}"
                results[name] = []

    news = results["news"] if isinstance(results["news"], dict) else {}
    cultural = results["cultural"] if isinstance(results["cultural"], dict) else {}

    payload = {
        "web": results["web"] or [],
        "reddit": results["reddit"] or [],
        "rss": (news.get("rss") or []) if isinstance(news, dict) else [],
        "anilist": (news.get("anilist") or []) if isinstance(news, dict) else [],
        "wikipedia": (news.get("wikipedia") or []) if isinstance(news, dict) else [],
        "tvmaze": (news.get("tvmaze") or []) if isinstance(news, dict) else [],
        "youtube": results["youtube"] or [],
        "musica": (cultural.get("musica") or []) if isinstance(cultural, dict) else [],
        "livro": (cultural.get("livro") or []) if isinstance(cultural, dict) else [],
    }

    payload, linkcheck_status = _agent_link_validator(payload)

    prefer_pt = _prefer_portuguese(user_message)
    for key in ("web", "rss", "youtube", "wikipedia", "tvmaze", "reddit"):
        payload[key] = _rank_language(payload.get(key, []), prefer_pt=prefer_pt)
    status["linkcheck"] = linkcheck_status
    status["collected_at"] = datetime.now(timezone.utc).isoformat()
    status["language_mode"] = "pt-first" if prefer_pt else "user-requested-language"

    logger.info(
        "Busca paralela: user=%s web=%d reddit=%d rss=%d anilist=%d wiki=%d tvmaze=%d youtube=%d musica=%d livro=%d linkcheck=%s",
        user_id,
        len(payload["web"]),
        len(payload["reddit"]),
        len(payload["rss"]),
        len(payload["anilist"]),
        len(payload["wikipedia"]),
        len(payload["tvmaze"]),
        len(payload["youtube"]),
        len(payload["musica"]),
        len(payload["livro"]),
        linkcheck_status,
    )

    messages = search_prompt.build_messages(
        user_message=user_message,
        history=history,
        search_results=payload["web"],
        reddit_results=payload["reddit"],
        rss_results=payload["rss"],
        anilist_results=payload["anilist"],
        wikipedia_results=payload["wikipedia"],
        tvmaze_results=payload["tvmaze"],
        youtube_results=payload["youtube"],
        musica_results=payload["musica"],
        livro_results=payload["livro"],
        source_status=status,
        is_sites_query=_KEYWORDS_SITES.search(user_message) is not None,
        prefer_portuguese=prefer_pt,
    )

    logger.info("Agente Busca: orquestrando sintese para user=%s", user_id)
    try:
        response = openrouter.search_synthesize(messages)
    except Exception as e:
        logger.error("Agente Busca: erro OpenRouter: %s", e)
        response = "Nao consegui buscar essa info agora. Tenta em instantes!"

    return {"response": response}
