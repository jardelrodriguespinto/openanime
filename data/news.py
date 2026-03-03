"""
Coleta de noticias via RSS feeds, Google News RSS e DuckDuckGo.
Sem APIs pagas — tudo gratuito.
"""

import logging
import urllib.parse
from datetime import datetime, timezone

import feedparser
from bs4 import BeautifulSoup
from scrapling import Fetcher, DynamicFetcher

logger = logging.getLogger(__name__)

# RSS curados para categorias conhecidas
CATEGORIAS_RSS: dict[str, list[str]] = {
    "tech": [
        "https://feeds.feedburner.com/TechCrunch",
        "https://www.theverge.com/rss/index.xml",
        "https://tecnoblog.net/feed/",
    ],
    "ia": [
        "https://aiweekly.co/issues.rss",
        "https://www.artificialintelligence-news.com/feed/",
        "https://feeds.feedburner.com/oreilly/radar/atom",
    ],
    "mercado": [
        "https://feeds.folha.uol.com.br/mercado/rss091.xml",
        "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml",
    ],
    "games": [
        "https://www.ign.com/feeds/games.rss",
        "https://kotaku.com/rss",
    ],
    "ciencia": [
        "https://www.newscientist.com/feed/home/",
        "https://feeds.feedburner.com/sciencenews",
    ],
    "brasil": [
        "https://g1.globo.com/rss/g1/",
        "https://feeds.bbci.co.uk/portuguese/rss.xml",
        "https://agenciabrasil.ebc.com.br/rss/geral/feed.xml",
        "https://feeds.folha.uol.com.br/folha/brasil/rss091.xml",
        "https://agenciabrasil.ebc.com.br/rss/politica/feed.xml",
    ],
    "geral": [
        "https://g1.globo.com/rss/g1/",
        "https://feeds.bbci.co.uk/portuguese/rss.xml",
        "https://agenciabrasil.ebc.com.br/rss/geral/feed.xml",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
    ],
    "programacao": [
        "https://feeds.feedburner.com/CssTricks",
        "https://dev.to/feed",
    ],
    "startup": [
        "https://feeds.feedburner.com/TechCrunch/startups",
        "https://www.startuppi.com.br/feed/",
    ],
}

_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
}


def _parse_rss_entry(entry) -> dict:
    """Normaliza entrada do feedparser para formato padrao."""
    titulo = entry.get("title", "").strip()
    url = entry.get("link", "").strip()
    resumo = entry.get("summary", "").strip()
    if resumo:
        try:
            soup = BeautifulSoup(resumo, "html.parser")
            resumo = soup.get_text(separator=" ", strip=True)[:300]
        except Exception:
            resumo = resumo[:300]

    data = ""
    if entry.get("published_parsed"):
        try:
            data = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).strftime("%d/%m/%Y %H:%M")
        except Exception:
            pass

    return {
        "titulo": titulo,
        "url": url,
        "resumo": resumo,
        "data": data,
        "fonte": "rss",
    }


def buscar_por_rss(categorias: list[str], limite: int = 10) -> list[dict]:
    """Busca noticias via RSS feeds das categorias pedidas."""
    resultados: list[dict] = []
    vistos: set[str] = set()

    feeds_usar = []
    for cat in categorias:
        cat_lower = cat.lower().strip()
        feeds = CATEGORIAS_RSS.get(cat_lower, [])
        feeds_usar.extend(feeds)

    if not feeds_usar:
        feeds_usar = CATEGORIAS_RSS["geral"]

    for feed_url in feeds_usar:
        if len(resultados) >= limite:
            break
        try:
            feed = feedparser.parse(feed_url, request_headers={"User-Agent": "Mozilla/5.0"})
            for entry in feed.entries[:8]:
                url = entry.get("link", "").strip()
                if not url or url in vistos:
                    continue
                vistos.add(url)
                item = _parse_rss_entry(entry)
                if item["titulo"]:
                    resultados.append(item)
                if len(resultados) >= limite:
                    break
        except Exception as e:
            logger.debug("news RSS erro para %s: %s", feed_url, e)

    return resultados[:limite]


def buscar_por_google_news(query: str, limite: int = 8) -> list[dict]:
    """
    Busca noticias via Google News RSS — retorna artigos reais, nao portais.
    Funciona para qualquer topico livre (brasil, futebol, cripto, etc).
    """
    q_enc = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q_enc}&hl=pt-BR&gl=BR&ceid=BR:pt-419"

    try:
        feed = feedparser.parse(url, request_headers=_HTTP_HEADERS)
        resultados = []
        vistos: set[str] = set()

        for entry in feed.entries[:limite * 2]:
            titulo = entry.get("title", "").strip()
            if not titulo:
                continue

            # Google News formata como "Titulo da Noticia - Nome do Portal"
            # Remove o sufixo " - Portal" para ficar so com o titulo
            if " - " in titulo:
                titulo = titulo.rsplit(" - ", 1)[0].strip()

            link = entry.get("link", "").strip()
            if not link or link in vistos:
                continue
            vistos.add(link)

            resumo = ""
            raw_summary = entry.get("summary", "")
            if raw_summary:
                try:
                    resumo = BeautifulSoup(raw_summary, "html.parser").get_text(separator=" ", strip=True)[:250]
                    # Remove o titulo duplicado que o Google News coloca no resumo
                    if resumo.startswith(titulo[:30]):
                        resumo = ""
                except Exception:
                    pass

            data = ""
            if entry.get("published_parsed"):
                try:
                    data = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    ).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    pass

            # Filtra titulos muito curtos (provavelmente nao sao manchetes)
            if len(titulo) < 15:
                continue

            resultados.append({
                "titulo": titulo,
                "url": link,
                "resumo": resumo,
                "data": data,
                "fonte": "google_news",
            })
            if len(resultados) >= limite:
                break

        logger.info("news Google News: '%s' -> %d resultados", query, len(resultados))
        return resultados

    except Exception as e:
        logger.warning("news Google News RSS erro: %s", e)
        return []


def buscar_por_playwright(query: str, limite: int = 5) -> list[dict]:
    """
    Fallback com DynamicFetcher (Scrapling/Playwright): scrapa Google News quando feedparser falha.
    Mais lento mas mais robusto para queries exoticas.
    """
    try:
        q_enc = urllib.parse.quote(query)
        search_url = f"https://news.google.com/search?q={q_enc}&hl=pt-BR&gl=BR&ceid=BR:pt-419"

        page = DynamicFetcher.get(search_url, timeout=20000)
        soup = BeautifulSoup(page.html, "html.parser")
        resultados = []
        vistos: set[str] = set()

        for article in soup.find_all("article"):
            a = article.find("a", href=True)
            if not a:
                continue
            titulo = a.get_text(strip=True)
            href = a["href"]
            if href.startswith("./"):
                href = "https://news.google.com" + href[1:]

            if not titulo or len(titulo) < 15 or href in vistos:
                continue
            vistos.add(href)

            # Remove sufixo de portal
            if " - " in titulo:
                titulo = titulo.rsplit(" - ", 1)[0].strip()

            resultados.append({
                "titulo": titulo,
                "url": href,
                "resumo": "",
                "data": "",
                "fonte": "playwright",
            })
            if len(resultados) >= limite:
                break

        logger.info("news DynamicFetcher: '%s' -> %d resultados", query, len(resultados))
        return resultados

    except Exception as e:
        logger.warning("news DynamicFetcher erro: %s", e)
        return []


def buscar_por_ddg(query: str, limite: int = 5) -> list[dict]:
    """
    DDG como ultimo fallback — filtra aggressivamente homepages e portais.
    Prefira buscar_por_google_news para resultados de qualidade.
    """
    resultados: list[dict] = []
    try:
        page = Fetcher.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": query},
            stealthy_headers=True,
            impersonate='chrome124',
            timeout=20,
        )
        soup = BeautifulSoup(page.html, "html.parser")
        seen: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            titulo = a.get_text(strip=True)
            if not href or not titulo:
                continue
            if "duckduckgo.com/y.js" in href or href.startswith("/") or href.startswith("?"):
                continue
            if "duckduckgo.com/l/" in href:
                m_url = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [])
                href = m_url[0] if m_url else ""
                if not href:
                    continue
            if href in seen:
                continue

            # Filtra homepages: URL com path muito raso (ex: https://g1.globo.com/ ou /categoria/)
            try:
                path = urllib.parse.urlparse(href).path.rstrip("/")
                segments = [s for s in path.split("/") if s]
                if len(segments) < 2:
                    continue  # Homepage ou categoria raiz
            except Exception:
                continue

            # Filtra titulos curtos que parecem nomes de portal (< 30 chars)
            if len(titulo) < 30:
                continue

            seen.add(href)
            resumo = ""
            parent = a.find_parent("td")
            if parent:
                next_td = parent.find_next_sibling("td")
                if next_td:
                    resumo = next_td.get_text(strip=True)[:200]

            resultados.append({
                "titulo": titulo,
                "url": href,
                "resumo": resumo,
                "data": "",
                "fonte": "duckduckgo",
            })
            if len(resultados) >= limite:
                break
    except Exception as e:
        logger.warning("news DDG erro: %s", e)

    return resultados


def buscar_noticias(categorias: list[str], query_livre: str = "", limite: int = 10) -> list[dict]:
    """
    Ponto de entrada principal.
    Prioridade: RSS curado > Google News RSS > Playwright > DDG.
    """
    resultados = buscar_por_rss(categorias, limite=limite)
    vistos = {n.get("url") for n in resultados if n.get("url")}

    if len(resultados) < limite:
        q = query_livre if query_livre and len(query_livre) > 5 else (
            " ".join(categorias[:2]) if categorias else "noticias"
        )

        faltam = limite - len(resultados)
        gn = buscar_por_google_news(q, limite=faltam)
        if gn:
            novos = [n for n in gn if n.get("url") and n["url"] not in vistos]
            resultados.extend(novos)
            vistos.update(n["url"] for n in novos)

        faltam = limite - len(resultados)
        if faltam > 0:
            pw = buscar_por_playwright(q, limite=faltam)
            if pw:
                novos = [n for n in pw if n.get("url") and n["url"] not in vistos]
                resultados.extend(novos)
                vistos.update(n["url"] for n in novos)

        faltam = limite - len(resultados)
        if faltam > 0:
            ddg = buscar_por_ddg(f"{q} noticias", limite=faltam)
            if ddg:
                novos = [n for n in ddg if n.get("url") and n["url"] not in vistos]
                resultados.extend(novos)
                vistos.update(n["url"] for n in novos)

    logger.info("news: %d noticias coletadas para categorias=%s", len(resultados), categorias)
    return resultados[:limite]
