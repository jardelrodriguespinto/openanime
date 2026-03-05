"""
Leitura de paginas web para resumo/extracao de conteudo.
Prioriza scrapling; fallback para httpx.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

try:
    import trafilatura
except Exception:
    trafilatura = None

try:
    from readability import Document
except Exception:
    Document = None

logger = logging.getLogger(__name__)

try:
    from scrapling import Fetcher
except Exception:
    Fetcher = None


_URL_RE = re.compile(r"https?://[^\s<>\]\[\"'()]+", re.IGNORECASE)
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


def extrair_urls(texto: str, max_urls: int = 3) -> list[str]:
    """Extrai URLs HTTP/HTTPS de uma mensagem."""
    if not texto:
        return []

    seen: set[str] = set()
    urls: list[str] = []
    for raw in _URL_RE.findall(texto):
        url = raw.rstrip(".,;:!?)]}>\"'")
        if not _eh_url_http(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= max(1, max_urls):
            break
    return urls


def ler_pagina(url: str, max_chars: int = 12000, timeout: float = 18.0) -> dict:
    """
    Le pagina e retorna texto principal.
    Retorno:
      {url, resolved_url, title, text, method, status_code, error}
    """
    result = {
        "url": url,
        "resolved_url": url,
        "title": "",
        "text": "",
        "method": "",
        "status_code": 0,
        "error": "",
    }

    if not _eh_url_http(url):
        result["error"] = "URL invalida"
        return result

    html, resolved, method, status, err = _baixar_html(url, timeout=timeout)
    result["resolved_url"] = resolved or url
    result["method"] = method or ""
    result["status_code"] = status or 0
    if err:
        result["error"] = err
        return result
    if not html:
        result["error"] = "Pagina sem HTML legivel"
        return result

    title = _extrair_titulo(html).strip()
    text = _extrair_texto_principal(html, max_chars=max_chars).strip()

    if not text:
        result["error"] = "Nao consegui extrair texto principal da pagina"
        result["title"] = title
        return result

    result["title"] = title
    result["text"] = text
    return result


def _baixar_html(url: str, timeout: float = 18.0) -> tuple[str, str, str, int, str]:
    """
    Retorna (html, resolved_url, method, status_code, error).
    """
    if Fetcher is not None:
        try:
            page = Fetcher.get(
                url,
                timeout=timeout,
                stealthy_headers=True,
                impersonate="chrome124",
            )
            html = (getattr(page, "html", "") or "").strip()
            if html:
                return html, url, "scrapling", 200, ""
        except Exception as e:
            logger.debug("webpage: scrapling falhou url=%s erro=%s", url, e)

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HTTP_HEADERS) as client:
            resp = client.get(url)
            html = (resp.text or "").strip()
            if not html:
                return "", str(resp.url), "httpx", resp.status_code, "Resposta vazia"
            return html, str(resp.url), "httpx", resp.status_code, ""
    except Exception as e:
        return "", url, "httpx", 0, f"Falha ao baixar pagina: {e}"


def _extrair_titulo(html: str) -> str:
    if Document is not None:
        try:
            doc = Document(html)
            short_title = (doc.short_title() or "").strip()
            if short_title:
                return _clean_text(short_title)[:180]
        except Exception:
            pass

    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            return _clean_text(title)[:180]
        except Exception:
            return ""

    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return _clean_text(m.group(1))[:180]


def _extrair_texto_principal(html: str, max_chars: int = 12000) -> str:
    text = ""

    if trafilatura is not None:
        try:
            text = trafilatura.extract(
                html,
                include_links=False,
                include_tables=False,
                favor_precision=True,
                output_format="txt",
            ) or ""
        except Exception:
            text = ""

    if not text and Document is not None and BeautifulSoup is not None:
        try:
            doc = Document(html)
            summary_html = doc.summary()
            soup = BeautifulSoup(summary_html, "html.parser")
            text = soup.get_text(" ", strip=True)
        except Exception:
            text = ""

    if not text:
        if BeautifulSoup is not None:
            try:
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "noscript", "svg"]):
                    tag.decompose()
                container = soup.find("main") or soup.find("article") or soup.body or soup
                text = container.get_text(" ", strip=True)
            except Exception:
                text = ""
        else:
            text = _strip_html_tags(html)

    clean = _clean_text(text)
    if not clean:
        return ""
    return clean[: max(500, max_chars)]


def _clean_text(text: str) -> str:
    txt = (text or "").replace("\u00a0", " ").strip()
    txt = re.sub(r"\s+", " ", txt)
    return txt


def _strip_html_tags(html: str) -> str:
    sem_scripts = re.sub(r"<(script|style|noscript)[^>]*>.*?</\\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    sem_tags = re.sub(r"<[^>]+>", " ", sem_scripts)
    return _clean_text(sem_tags)


def _eh_url_http(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False
