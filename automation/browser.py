"""
Gerenciador de browser Playwright — stealth, anti-deteccao, comportamento humano.
"""

import asyncio
import logging
import os
import random

logger = logging.getLogger(__name__)

PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
PLAYWRIGHT_TIMEOUT = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "45000"))

_browser = None
_playwright_instance = None
_lock = asyncio.Lock()

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 800},
]

_STEALTH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--disable-extensions",
    "--disable-popup-blocking",
    "--disable-gpu",
    "--lang=pt-BR,pt",
    "--no-first-run",
    "--no-default-browser-check",
]


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
                    args=_STEALTH_ARGS,
                )
                logger.info("browser: Playwright iniciado | headless=%s", PLAYWRIGHT_HEADLESS)
            except ImportError:
                raise RuntimeError("playwright nao instalado. Execute: pip install playwright && playwright install chromium")
            except Exception as e:
                logger.error("browser: erro ao iniciar Playwright: %s", e)
                raise

    return _browser


async def nova_pagina(stealth: bool = True):
    """Retorna nova pagina com contexto isolado e stealth opcional."""
    browser = await get_browser()
    user_agent = random.choice(_USER_AGENTS)
    viewport = random.choice(_VIEWPORTS)

    context = await browser.new_context(
        user_agent=user_agent,
        viewport=viewport,
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        permissions=["geolocation"],
        geolocation={"longitude": -46.6388, "latitude": -23.5489},
        color_scheme="light",
        java_script_enabled=True,
    )

    page = await context.new_page()
    page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

    if stealth:
        await _aplicar_stealth(page)

    return page


async def _aplicar_stealth(page) -> None:
    """Injeta scripts para esconder sinais de automacao."""
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                { name: 'Native Client', filename: 'internal-nacl-plugin' }
            ]
        });
        Object.defineProperty(navigator, 'languages', { get: () => ['pt-BR', 'pt', 'en-US', 'en'] });
        window.chrome = {
            runtime: { onConnect: {}, onMessage: {} },
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        Object.defineProperty(screen, 'availHeight', { get: () => screen.height - 40 });
    """)


async def digitar_humano(page, seletor: str, texto: str) -> None:
    """Digita texto com delay humano (30-90ms por tecla)."""
    try:
        el = await page.wait_for_selector(seletor, timeout=10000)
        await el.click()
        await asyncio.sleep(random.uniform(0.1, 0.4))
        await el.triple_click()
        await asyncio.sleep(0.1)
        await el.type(texto, delay=random.randint(30, 90))
    except Exception as e:
        logger.debug("browser: erro digitar '%s': %s", seletor, e)
        try:
            await page.fill(seletor, texto)
        except Exception:
            pass


async def clicar_humano(page, seletor: str, timeout: int = 10000) -> bool:
    """Clica com movimento de mouse simulado. Retorna True se sucesso."""
    try:
        el = await page.wait_for_selector(seletor, timeout=timeout)
        box = await el.bounding_box()
        if box:
            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            await page.mouse.move(x, y, steps=random.randint(3, 8))
            await asyncio.sleep(random.uniform(0.05, 0.2))
            await page.mouse.click(x, y)
        else:
            await el.click()
        return True
    except Exception as e:
        logger.debug("browser: erro clicar '%s': %s", seletor, e)
        return False


async def clicar_qualquer(page, seletores: list, timeout: int = 5000) -> bool:
    """Tenta clicar no primeiro seletor que encontrar."""
    for sel in seletores:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                return True
        except Exception:
            continue
    return False


async def esperar_navegacao(page, timeout: int = 15000) -> None:
    """Aguarda navegacao com fallback gracioso."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            await asyncio.sleep(2)


async def screenshot_debug(page, nome: str = "debug") -> str:
    """Salva screenshot para debug. Retorna caminho."""
    try:
        caminho = f"/tmp/playwright_{nome}.png"
        await page.screenshot(path=caminho, full_page=False)
        logger.info("browser: screenshot -> %s", caminho)
        return caminho
    except Exception as e:
        logger.debug("browser: screenshot falhou: %s", e)
        return ""


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
    if "indeed.com" in url_lower:
        return "indeed"
    if "glassdoor.com" in url_lower:
        return "glassdoor"
    if "vagas.com" in url_lower:
        return "vagas"
    if "catho.com" in url_lower:
        return "catho"
    if "infojobs.com" in url_lower:
        return "infojobs"
    return "desconhecido"


SINAIS_BLOQUEIO = [
    "verify you're a human", "unusual activity", "captcha",
    "security check", "robot", "bot detection", "access denied",
    "forbidden", "cloudflare", "just a moment", "checking your browser",
]


def detectar_bloqueio(html: str) -> bool:
    html_lower = (html or "").lower()
    return any(sinal in html_lower for sinal in SINAIS_BLOQUEIO)
