"""
Wrapper Selenium para automacao de browsers — funciona com Firefox snap.
API async para compatibilidade com o codigo existente.
"""

import asyncio
import base64
import logging
import os
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, InvalidSessionIdException

logger = logging.getLogger(__name__)

GECKODRIVER_PATH = os.getenv("GECKODRIVER_PATH", str(Path(__file__).parent.parent / "geckodriver"))
FIREFOX_BINARY = os.getenv("FIREFOX_BINARY", "/snap/firefox/8568/usr/lib/firefox/firefox")
PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true"


def _get_driver():
    """Cria instancia do Firefox com Selenium."""
    options = Options()
    options.binary_location = FIREFOX_BINARY
    options.headless = PLAYWRIGHT_HEADLESS
    if not PLAYWRIGHT_HEADLESS:
        options.set_preference("browser.startup.homepage", "about:blank")
        options.set_preference("browser.startup.page", 0)
    options.set_preference("dom.disable_beforeunload", True)
    options.set_preference("browser.tabs.warnOnClose", False)
    options.set_preference("network.http.use-cache", False)
    options.set_preference("browser.cache.disk.enable", False)
    options.set_preference("browser.cache.memory.enable", False)

    service = Service(GECKODRIVER_PATH)
    driver = webdriver.Firefox(service=service, options=options)
    driver.set_window_size(1920, 1080)
    driver.maximize_window()
    logger.info("selenium_browser: Firefox iniciado")
    return driver


async def _run_in_thread(fn, *args, **kwargs):
    """Executa funcao Selenium (sincrona) em thread separada."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


async def _run_in_thread_no_args(fn):
    """Executa funcao Selenium sem argumentos em thread separada."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn)


async def _driver_session_valida() -> bool:
    """Verifica se o driver Selenium tem uma sessão ativa válida."""
    try:
        driver = await get_driver()
        if driver is None:
            return False
        # Tenta acessar uma propriedade que requer sessão ativa
        await _run_in_thread(lambda: driver.current_url)
        return True
    except Exception:
        return False


async def nova_pagina(url: str = "about:blank", reutilizar: bool = False) -> webdriver.Firefox:
    """Abre uma nova aba/guia no Firefox e navega para a URL.
    Se reutilizar=True e já existe um driver com sessão ativa, apenas navega para a URL na mesma aba.
    """
    driver = getattr(nova_pagina, "_driver", None)
    
    # Verifica se o driver existe e tem sessão válida
    if reutilizar and driver:
        sessao_ok = await _driver_session_valida()
        if sessao_ok:
            await _run_in_thread(driver.get, url)
            return driver
        # Sessão inválida - limpa driver antigo
        nova_pagina._driver = None
        try:
            await _run_in_thread(driver.quit)
        except Exception:
            pass
    
    def _open():
        driver = _get_driver()
        driver.get(url)
        return driver
    driver = await _run_in_thread(_open)
    await _set_driver(driver)
    return driver


async def get_driver() -> webdriver.Firefox | None:
    """Retorna driver existente ou None."""
    return getattr(nova_pagina, "_driver", None)


async def _set_driver(driver):
    """Armazena driver globalmente."""
    nova_pagina._driver = driver


async def navegar(url: str):
    """Navega para URL na pagina atual."""
    driver = await get_driver()
    if not driver:
        raise RuntimeError("Browser nao inicializado")
    await _run_in_thread(driver.get, url)


_HAS_TEXT_PATTERN = r":has-text\(['\"](.+?)['\"]\)"


def _try_convert_selector(selector: str) -> tuple[str, str] | None:
    import re
    # XPath selectors start with //
    if selector.strip().startswith("//"):
        return (By.XPATH, selector.strip())
    match = re.search(_HAS_TEXT_PATTERN, selector)
    if not match:
        return (By.CSS_SELECTOR, selector)
    text = match.group(1)
    base_selector = selector[:match.start()].strip()
    if base_selector and base_selector != "*":
        xpath = f"//{base_selector[1:]}[contains(., '{text}')]"
    else:
        xpath = f"//*[contains(., '{text}')]"
    return (By.XPATH, xpath)


def _iter_selectors(selector: str):
    for part in selector.split(","):
        part = part.strip()
        if not part:
            continue
        yield _try_convert_selector(part)


def _convert_has_text_to_xpath(selector: str) -> tuple[str, str]:
    """Converte selectors com :has-text() para XPath. Retorna (type, value)."""
    return _try_convert_selector(selector)


async def wait_for_selector(selector: str, timeout: int = 10):
    """Aguarda elemento aparecer. Retorna elemento ou None."""
    driver = await get_driver()
    if not driver:
        return None
    for by, value in _iter_selectors(selector):
        try:
            return await _run_in_thread(
                WebDriverWait(driver, timeout).until,
                EC.presence_of_element_located((by, value))
            )
        except TimeoutException:
            continue
    return None


async def wait_for_selector_visible(selector: str, timeout: int = 10):
    """Aguarda elemento aparecer E estar visivel."""
    driver = await get_driver()
    if not driver:
        return None
    for by, value in _iter_selectors(selector):
        try:
            return await _run_in_thread(
                WebDriverWait(driver, timeout).until,
                EC.visibility_of_element_located((by, value))
            )
        except TimeoutException:
            continue
    return None


async def click(selector: str, timeout: int = 10) -> bool:
    """Clica em um elemento."""
    driver = await get_driver()
    if not driver:
        return False
    for by, value in _iter_selectors(selector):
        try:
            el = await _run_in_thread(
                WebDriverWait(driver, timeout).until,
                EC.element_to_be_clickable((by, value))
            )
            await _run_in_thread(el.click)
            return True
        except TimeoutException:
            continue
    return False


async def digitar(selector: str, texto: str, clear: bool = True, delay: int = 50):
    """Digita texto em um campo."""
    driver = await get_driver()
    if not driver:
        return False
    try:
        el = await wait_for_selector(selector, timeout=10)
        if not el:
            return False
        if clear:
            await _run_in_thread(el.clear)
        await _run_in_thread(el.send_keys, texto)
        return True
    except Exception:
        return False


async def digitar_com_delay(selector: str, texto: str, delay_min: int = 30, delay_max: int = 90):
    """Digita com delay humano."""
    driver = await get_driver()
    if not driver:
        return False
    try:
        el = await wait_for_selector(selector, timeout=10)
        if not el:
            return False
        await _run_in_thread(el.click)
        await _run_in_thread(el.clear)
        for char in texto:
            await _run_in_thread(el.send_keys, char)
            await asyncio.sleep(delay_min / 1000.0)
        return True
    except Exception:
        return False


async def screenshot(path: str = "/tmp/selenium_screenshot.png") -> str:
    """Captura screenshot. Retorna caminho."""
    driver = await get_driver()
    if not driver:
        return ""
    try:
        await _run_in_thread(driver.save_screenshot, path)
        return path
    except Exception:
        return ""


async def screenshot_base64() -> str:
    """Captura screenshot em base64."""
    driver = await get_driver()
    if not driver:
        return ""
    try:
        return await _run_in_thread(driver.get_screenshot_as_base64)
    except Exception:
        return ""


async def get_url() -> str:
    """Retorna URL atual."""
    driver = await get_driver()
    if not driver:
        return ""
    try:
        return await _run_in_thread(driver.current_url)
    except Exception:
        return ""


async def get_title() -> str:
    driver = await get_driver()
    if not driver:
        return ""
    try:
        return await _run_in_thread(driver.title)
    except Exception:
        return ""


async def avaliar(script: str):
    """Executa JavaScript na pagina."""
    driver = await get_driver()
    if not driver:
        return None
    try:
        return await _run_in_thread(driver.execute_script, script)
    except Exception:
        return None


async def fechar():
    """Fecha browser."""
    driver = await get_driver()
    if driver:
        try:
            await _run_in_thread(driver.quit)
        except (InvalidSessionIdException, Exception):
            pass
        nova_pagina._driver = None
        logger.info("selenium_browser: Firefox fechado")


async def _ensure_driver_alive():
    """Verifica se o driver está ativo e retorna True, ou recria o driver."""
    try:
        driver = await get_driver()
        if driver:
            await _run_in_thread(lambda: driver.current_url)
            return True
    except (InvalidSessionIdException, Exception):
        nova_pagina._driver = None
    return False


async def clicar_entrar_com_email(driver):
    """Tenta clicar em link/botão para usar email/senha ao invés de Google."""
    from selenium.webdriver.common.by import By
    seletores = [
        "a[href*='email-sign-in']",
        "a[href*='sign-in-email']",
        "a[href*='traditional-auth']",
        "button[data-litms-control='sign_in_with_email']",
        "button[aria-label*='Entrar com email']",
        "button[aria-label*='Sign in with email']",
        "a[aria-label*='Entrar com email']",
        "a[aria-label*='Sign in with email']",
        "//a[contains(text(), 'Entrar com email')]",
        "//a[contains(text(), 'Sign in with email')]",
        "//button[contains(text(), 'Entrar com email')]",
        "//button[contains(text(), 'Sign in with email')]",
    ]
    for sel in seletores:
        try:
            el = await _run_in_thread(
                lambda s=sel: driver.find_element(By.CSS_SELECTOR, s) if not s.startswith("//") else driver.find_element(By.XPATH, s)
            )
            if el and await _run_in_thread(lambda e=el: e.is_displayed()):
                await _run_in_thread(lambda e=el: e.click())
                print("[SELENIUM] Clicou em 'Entrar com email'")
                return True
        except Exception:
            continue
    return False
