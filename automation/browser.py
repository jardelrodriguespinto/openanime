"""
Gerenciador de browser Playwright — stealth, anti-deteccao, comportamento humano.
Suporte a persistencia de sessao por plataforma (salva/carrega cookies em JSON).
"""

import asyncio
import datetime
import json
import logging
import os
import random
from pathlib import Path

logger = logging.getLogger(__name__)

PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true"
PLAYWRIGHT_TIMEOUT = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "45000"))

# Diretorio onde as sessoes (cookies) sao persistidas por plataforma
_SESSIONS_DIR = Path(os.getenv("SESSIONS_DIR", "/app/data/sessions"))

_browser = None
_playwright_instance = None
_lock = asyncio.Lock()
_active_page = None
_active_page_lock = asyncio.Lock()

_intervention_state = {
    "paused": False,
    "current_action": "idle",
    "manual_input": "",
    "intervention_type": None,
    "intervention_selector": None,
}
_intervention_lock = asyncio.Lock()
_REDIS_INTERVENTION_KEY = "automacao:intervencao"


def _get_redis():
    try:
        import redis
        return redis.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            username=os.getenv("REDIS_USER", "open-anime"),
            password=os.getenv("REDIS_PASSWORD", "open-anime"),
            db=int(os.getenv("REDIS_DB", 0)),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    except Exception:
        return None


_screenshot_loop_task = None
_screenshot_loop_running = False


async def _bot_screenshot_loop():
    """Loop que envia screenshots periodicamente via Socket.IO quando ha pagina ativa."""
    global _screenshot_loop_running
    _screenshot_loop_running = True
    while True:
        try:
            page = await get_active_page()
            if page:
                img = await screenshot_base64(page)
                if img:
                    asyncio.create_task(_send_screenshot_via_sio({
                        "screenshot": img,
                        "url": page.url,
                        "title": await page.title(),
                        "step": "",
                        "action": "rodando",
                    }))
        except Exception:
            pass
        await asyncio.sleep(1)


async def start_bot_screenshot_loop():
    """Garante que o loop de screenshots do bot esta rodando."""
    global _screenshot_loop_task, _screenshot_loop_running
    if not _screenshot_loop_running:
        _screenshot_loop_running = True
        _screenshot_loop_task = asyncio.create_task(_bot_screenshot_loop())

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
                
                # Se firefox for snap, pega o caminho real
                import shutil
                import glob
                chrome_path = shutil.which("google-chrome") or shutil.which("chromium-browser") or shutil.which("chromium")
                firefox_path = shutil.which("firefox")
                
                if firefox_path and "snap" not in firefox_path:
                    snap_firefox = glob.glob("/snap/firefox/*/usr/lib/firefox/firefox")
                    if snap_firefox:
                        firefox_path = snap_firefox[0]
                        logger.info(f"Firefox snap detectado: {firefox_path}")
                
                logger.info("browser: PLAYWRIGHT_HEADLESS=%s, chrome=%s, firefox=%s", 
                           PLAYWRIGHT_HEADLESS, bool(chrome_path), bool(firefox_path))
                print(f"[BROWSER] PLAYWRIGHT_HEADLESS={PLAYWRIGHT_HEADLESS}, chrome={chrome_path}, firefox={firefox_path}")
                
                if firefox_path:
                    logger.info(f"Usando Firefox do sistema: {firefox_path}")
                    print(f"[BROWSER] Lancando Firefox: {firefox_path}")
                    _browser = await _playwright_instance.firefox.launch(
                        headless=PLAYWRIGHT_HEADLESS,
                        executable_path=firefox_path,
                    )
                    logger.info("browser: Firefox lancado com sucesso!")
                    print("[BROWSER] Firefox lancado com sucesso!")
                elif chrome_path:
                    logger.info(f"Usando Chrome do sistema: {chrome_path}")
                    chrome_args = [
                        "--start-maximized",
                        "--no-first-run",
                        "--no-default-browser-check",
                    ]
                    if not PLAYWRIGHT_HEADLESS:
                        chrome_args.append("--start-maximized")
                    _browser = await _playwright_instance.chromium.launch(
                        headless=PLAYWRIGHT_HEADLESS,
                        executable_path=chrome_path,
                        args=chrome_args,
                    )
                    logger.info("browser: Chrome lancado com sucesso!")
                    print("[BROWSER] Chrome lancado com sucesso!")
                else:
                    print("[BROWSER] Nenhum navegador do sistema encontrado!")
                    logger.error("browser: Nenhum navegador do sistema encontrado")
                    raise RuntimeError("Nenhum navegador (Chrome/Firefox) encontrado no sistema")
                logger.info("browser: Playwright iniciado | headless=%s", PLAYWRIGHT_HEADLESS)
            except ImportError:
                raise RuntimeError("playwright nao instalado. Execute: pip install playwright")
            except Exception as e:
                logger.error("browser: erro ao iniciar Playwright: %s", e)
                raise

    return _browser


async def set_active_page(page):
    """Define a pagina ativa para monitoramento do dashboard."""
    global _active_page
    async with _active_page_lock:
        _active_page = page
    if page:
        await start_bot_screenshot_loop()


async def get_active_page():
    """Retorna a pagina ativa para captura de screenshot."""
    async with _active_page_lock:
        return _active_page


def _get_dashboard_url() -> str:
    return os.getenv("DASHBOARD_URL", "http://anime-dashboard:8082")


async def notify_browser_step(step: str = "", action: str = "", detail: str = ""):
    """Envia atualizacao de passo do browser para o dashboard via HTTP."""
    try:
        import httpx
        payload = {
            "step": step or "",
            "action": action or "",
            "detail": detail or "",
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            await client.post(f"{_get_dashboard_url()}/api/browser/step", json=payload)
    except Exception:
        pass


async def wait_if_paused(page=None, step_name: str = ""):
    """Aguarda se o usuario pausou a automacao. Retorna True se deve continuar."""
    while True:
        control = await get_intervention_state()
        if control.get("current_action") == "pular":
            await set_intervention_state("current_action", "rodando")
            return True

        if not control.get("paused") and control.get("intervention_type") != "manual":
            return True

        if page:
            try:
                img = await screenshot_base64(page)
                if img:
                    # Usa Socket.IO existente via HTTP para evitar import circular
                    asyncio.create_task(_send_screenshot_via_sio({
                        "screenshot": img,
                        "url": page.url,
                        "title": await page.title(),
                        "step": step_name,
                        "status": "paused" if control.get("paused") else "manual",
                    }))
            except Exception:
                pass

        await asyncio.sleep(0.5)


async def _send_screenshot_via_sio(data: dict):
    """Envia screenshot via Socket.IO para o dashboard."""
    try:
        import socketio
        sio_client = socketio.AsyncClient()
        await sio_client.connect(_get_dashboard_url(), wait_timeout=1)
        await sio_client.emit("browser_screenshot", data)
        await sio_client.disconnect()
    except Exception:
        pass


async def sio_emit_browser_screenshot(data: dict):
    """Emite screenshot via Socket.IO diretamente para o dashboard."""
    try:
        import socketio
        sio_int = socketio.AsyncClient()
        await sio_int.connect(_get_dashboard_url(), wait_timeout=1)
        await sio_int.emit("browser_screenshot", data)
        await sio_int.disconnect()
    except Exception:
        pass


_global_step_info = {"step": "", "action": "", "detail": "", "updated_at": ""}
_step_lock = asyncio.Lock()


async def set_current_step(step: str = "", action: str = "", detail: str = ""):
    """Atualiza step atual em memoria e notifica dashboard."""
    global _global_step_info
    async with _step_lock:
        _global_step_info = {
            "step": step,
            "action": action,
            "detail": detail,
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }


async def get_current_step() -> dict:
    async with _step_lock:
        return dict(_global_step_info)


async def set_intervention_state(key: str, value):
    """Atualiza estado de intervencao (pausa, acao manual) — tenta Redis, fallback memoria."""
    async with _intervention_lock:
        _intervention_state[key] = value
    try:
        r = _get_redis()
        if r:
            full = await get_intervention_state()
            r.setex(_REDIS_INTERVENTION_KEY, 3600, json.dumps(full, default=str))
    except Exception:
        pass


async def get_intervention_state() -> dict:
    """Retorna copia do estado de intervencao — prioriza Redis, fallback memoria."""
    try:
        r = _get_redis()
        if r:
            raw = r.get(_REDIS_INTERVENTION_KEY)
            if raw:
                parsed = json.loads(raw)
                async with _intervention_lock:
                    _intervention_state.update(parsed)
                return dict(_intervention_state)
    except Exception:
        pass
    async with _intervention_lock:
        return dict(_intervention_state)


async def nova_pagina(stealth: bool = True):
    """Retorna nova pagina com contexto isolado e stealth opcional."""
    browser = await get_browser()
    user_agent = random.choice(_USER_AGENTS)
    viewport = random.choice(_VIEWPORTS)
    print(f"[BROWSER] Nova pagina criada | stealth={stealth}")

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


async def screenshot_base64(page, full_page: bool = False) -> str:
    """Captura screenshot e retorna como base64 para streaming."""
    try:
        bytes_data = await page.screenshot(full_page=full_page, type="png")
        import base64
        return base64.b64encode(bytes_data).decode("utf-8")
    except Exception as e:
        logger.debug("browser: screenshot base64 falhou: %s", e)
        return ""


async def get_page_state(page) -> dict:
    """Retorna estado atual da pagina (url, titulo, html resumido)."""
    try:
        url = page.url
        title = await page.title()
        content = await page.content()
        return {
            "url": url,
            "title": title,
            "html_length": len(content),
            "html_preview": content[:2000],
        }
    except Exception as e:
        logger.debug("browser: get_page_state falhou: %s", e)
        return {"url": "", "title": "", "html_length": 0, "html_preview": ""}


async def nova_pagina_com_sessao(plataforma: str, stealth: bool = True):
    """
    Retorna pagina com contexto que ja carrega cookies salvos da plataforma.
    Se nao houver sessao salva, retorna pagina normal (vai precisar de login).
    """
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

    cookies = _carregar_cookies(plataforma)
    if cookies:
        try:
            await context.add_cookies(cookies)
            logger.info("browser: sessao carregada para '%s' (%d cookies)", plataforma, len(cookies))
        except Exception as e:
            logger.debug("browser: erro ao carregar cookies de '%s': %s", plataforma, e)

    page = await context.new_page()
    page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

    if stealth:
        await _aplicar_stealth(page)

    return page, context


async def salvar_sessao(context, plataforma: str) -> None:
    """
    Salva cookies do contexto atual para a plataforma.
    Chame apos login bem-sucedido.
    """
    try:
        cookies = await context.cookies()
        if not cookies:
            return
        _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        caminho = _SESSIONS_DIR / f"{plataforma}.json"
        caminho.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("browser: sessao salva para '%s' (%d cookies -> %s)", plataforma, len(cookies), caminho)
    except Exception as e:
        logger.warning("browser: falha ao salvar sessao de '%s': %s", plataforma, e)


def _carregar_cookies(plataforma: str) -> list:
    """Carrega cookies salvos da plataforma. Retorna [] se nao houver."""
    caminho = _SESSIONS_DIR / f"{plataforma}.json"
    if not caminho.exists():
        return []
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return dados if isinstance(dados, list) else []
    except Exception as e:
        logger.debug("browser: erro ao ler cookies de '%s': %s", plataforma, e)
        return []


def sessao_existe(plataforma: str) -> bool:
    """Retorna True se existe sessao salva para a plataforma."""
    return (_SESSIONS_DIR / f"{plataforma}.json").exists()


def limpar_sessao(plataforma: str) -> None:
    """Remove sessao salva de uma plataforma (ex: apos logout ou erro de login)."""
    caminho = _SESSIONS_DIR / f"{plataforma}.json"
    if caminho.exists():
        caminho.unlink()
        logger.info("browser: sessao de '%s' removida", plataforma)


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
    if "indeed.com" in url_lower or "br.indeed.com" in url_lower:
        return "indeed"
    if "gupy.io" in url_lower:
        return "gupy"
    if "greenhouse.io" in url_lower or "jobs.greenhouse" in url_lower:
        return "greenhouse"
    if "lever.co" in url_lower or "jobs.lever" in url_lower:
        return "lever"
    if "workday" in url_lower or "myworkdayjobs" in url_lower:
        return "workday"
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
