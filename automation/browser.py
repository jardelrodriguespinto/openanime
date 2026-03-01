"""
Gerenciador de browser Playwright — singleton com cleanup automatico.
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
PLAYWRIGHT_TIMEOUT = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "30000"))

_browser = None
_playwright_instance = None
_lock = asyncio.Lock()


async def get_browser():
    """Retorna instancia do browser Playwright (lazy init)."""
    global _browser, _playwright_instance

    async with _lock:
        if _browser is None or not _browser.is_connected():
            try:
                from playwright.async_api import async_playwright
                _playwright_instance = await async_playwright().start()
                _browser = await _playwright_instance.chromium.launch(
                    headless=PLAYWRIGHT_HEADLESS,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                logger.info("browser: Playwright iniciado | headless=%s", PLAYWRIGHT_HEADLESS)
            except ImportError:
                raise RuntimeError("playwright nao instalado. Execute: pip install playwright && playwright install chromium")
            except Exception as e:
                logger.error("browser: erro ao iniciar Playwright: %s", e)
                raise

    return _browser


async def nova_pagina():
    """Retorna nova pagina do browser."""
    browser = await get_browser()
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        locale="pt-BR",
    )
    page = await context.new_page()
    page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
    return page


async def fechar():
    """Fecha browser graciosamente."""
    global _browser, _playwright_instance
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright_instance:
        try:
            await _playwright_instance.stop()
        except Exception:
            pass
        _playwright_instance = None
    logger.info("browser: Playwright encerrado")


def detectar_plataforma(url: str) -> str:
    """Detecta plataforma de candidatura pela URL."""
    url_lower = (url or "").lower()
    if "linkedin.com" in url_lower:
        return "linkedin"
    if "gupy.io" in url_lower:
        return "gupy"
    if "greenhouse.io" in url_lower:
        return "greenhouse"
    if "lever.co" in url_lower:
        return "lever"
    if "workday" in url_lower or "myworkdayjobs" in url_lower:
        return "workday"
    return "desconhecido"


SINAIS_BLOQUEIO = [
    "verify you're a human",
    "unusual activity",
    "captcha",
    "security check",
    "robot",
    "bot detection",
    "access denied",
]


def detectar_bloqueio(html: str) -> bool:
    """Verifica se a pagina tem sinais de bloqueio anti-bot."""
    html_lower = (html or "").lower()
    return any(sinal in html_lower for sinal in SINAIS_BLOQUEIO)
