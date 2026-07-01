"""
Wrapper Selenium para automacao de browsers — funciona com Firefox snap.
API async para compatibilidade com o codigo existente.
"""

import asyncio
import base64
import contextvars
import glob
import logging
import os
import random
import shutil
import string
import threading
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, InvalidSessionIdException, WebDriverException

from automation.run_context import get_platform

logger = logging.getLogger(__name__)

# Registro de drivers POR PLATAFORMA (chave = run_context.get_platform()).
# Permite LinkedIn e Indeed rodarem em paralelo, cada um no seu Firefox. Callers
# sem plataforma definida (ex.: Gupy, endpoints HTTP) caem na chave "default".
_drivers: dict = {}

GECKODRIVER_PATH = os.getenv("GECKODRIVER_PATH", str(Path(__file__).parent.parent / "geckodriver"))
FIREFOX_BINARY = os.getenv("FIREFOX_BINARY", "/snap/firefox/8568/usr/lib/firefox/firefox")
PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true"

# Perfil Firefox PERSISTENTE: mantém cookies/sessão entre runs. Assim o usuário
# loga uma vez (ex.: Indeed, com código por e-mail) e não precisa passar por
# verificação/CAPTCHA a cada execução — a maior causa do "Additional Verification
# Required" era abrir um perfil zerado toda vez. Desligue com FIREFOX_PERSIST_PROFILE=false.
FIREFOX_PERSIST_PROFILE = os.getenv("FIREFOX_PERSIST_PROFILE", "true").lower() == "true"
FIREFOX_PROFILE_DIR = os.getenv(
    "FIREFOX_PROFILE_DIR",
    str(Path(__file__).parent.parent / "data" / "firefox_profile"),
)

# Firefox "undetected": o geckodriver faz o Firefox expor navigator.webdriver=true,
# que o Cloudflare Turnstile usa pra travar o login do Indeed em loop (o desafio
# nunca "gruda" mesmo resolvido à mão). A solução é usar uma CÓPIA do Firefox com o
# libxul patcheado — o marcador binário 'webdriver' é substituído por bytes
# aleatórios, então navigator.webdriver vira undefined e o Marionette (canal do
# geckodriver) continua funcionando. Validado no snap Firefox local: headful passa
# o Cloudflare e lista vagas. Desligue com FIREFOX_UNDETECTED=false.
FIREFOX_UNDETECTED = os.getenv("FIREFOX_UNDETECTED", "true").lower() == "true"
# Dir gravável onde fica a cópia patcheada (compartilhada por LinkedIn/Indeed; cada
# um usa seu -profile). Fica em data/ como o perfil persistente.
FIREFOX_UNDETECTED_DIR = os.getenv(
    "FIREFOX_UNDETECTED_DIR",
    str(Path(__file__).parent.parent / "data" / "firefox_undetected"),
)

_patch_lock = threading.Lock()
_patched_binary_cache: str | None = None


def _rand_bytes(n: int) -> bytes:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n)).encode()


def _firefox_source_dir() -> Path | None:
    """Dir do Firefox (contém o binário e o libxul.so). Usa o FIREFOX_BINARY
    configurado; se ele sumiu (ex.: snap atualizou de versão e o 8568 hardcoded
    deixou de existir), cai no snap 'current' / maior versão disponível."""
    cand = Path(FIREFOX_BINARY).parent
    if (cand / "libxul.so").exists():
        return cand
    candidatos = ["/snap/firefox/current/usr/lib/firefox"]
    candidatos += sorted(glob.glob("/snap/firefox/*/usr/lib/firefox"), reverse=True)
    candidatos += ["/usr/lib/firefox", "/usr/lib/firefox-esr"]
    for g in candidatos:
        p = Path(g)
        if (p / "libxul.so").exists():
            return p
    return None


def _resolver_binario_undetected() -> str | None:
    """Garante uma cópia do Firefox com o libxul patcheado e retorna o caminho do
    binário. None se não for possível (o caller cai no Firefox normal). Idempotente:
    só copia/patcheia se ainda não existe ou se a versão de origem mudou."""
    global _patched_binary_cache
    if _patched_binary_cache and os.path.exists(_patched_binary_cache):
        return _patched_binary_cache
    with _patch_lock:
        if _patched_binary_cache and os.path.exists(_patched_binary_cache):
            return _patched_binary_cache
        src_dir = _firefox_source_dir()
        if not src_dir:
            logger.warning("selenium_browser: Firefox de origem não encontrado — sem patch undetected")
            return None
        libxul_src = src_dir / "libxul.so"
        st = libxul_src.stat()
        assinatura = f"{src_dir}:{st.st_size}:{int(st.st_mtime)}"
        dest_dir = Path(FIREFOX_UNDETECTED_DIR)
        marker = dest_dir / ".src"
        dest_bin = dest_dir / Path(FIREFOX_BINARY).name  # normalmente 'firefox'
        try:
            atual = marker.read_text().strip() if marker.exists() else ""
            if not dest_bin.exists() or atual != assinatura:
                if dest_dir.exists():
                    shutil.rmtree(dest_dir, ignore_errors=True)
                shutil.copytree(src_dir, dest_dir, symlinks=True, ignore_dangling_symlinks=True)
                libxul_dest = dest_dir / "libxul.so"
                data = libxul_dest.read_bytes()
                n = data.count(b"webdriver")
                libxul_dest.write_bytes(data.replace(b"webdriver", _rand_bytes(len(b"webdriver"))))
                marker.write_text(assinatura)
                logger.info(
                    "selenium_browser: Firefox undetected preparado (%d marcador(es) patcheado(s)) em %s",
                    n, dest_dir,
                )
            if not dest_bin.exists():
                return None
            _patched_binary_cache = str(dest_bin)
            return _patched_binary_cache
        except Exception as e:
            logger.warning(
                "selenium_browser: falha ao preparar Firefox undetected (%s) — usando Firefox normal", e
            )
            return None


def _matar_orfaos_do_perfil(profile_dir: str) -> None:
    """Mata Firefox órfãos (de runs anteriores) que ainda seguram ESTE perfil.
    Sem isso, o novo Firefox sai na hora com 'Process unexpectedly closed with
    status 0' (perfil em uso) — o clássico "abre e fecha". Reiniciar o
    bot.dashboard deixa o Firefox lançado pelo Selenium órfão, segurando o perfil.

    Casa pelo CAMINHO do perfil no cmdline (ex.: 'firefox_profile/indeed'), que é
    único por plataforma → NUNCA mata o Firefox pessoal do usuário (que não usa
    '-profile data/firefox_profile/*') nem o de outra plataforma."""
    import signal
    import subprocess
    alvo = f"firefox_profile/{Path(profile_dir).name}"
    try:
        out = subprocess.run(["pgrep", "-f", alvo], capture_output=True, text=True, timeout=5)
    except Exception as e:
        logger.debug("selenium_browser: pgrep indisponível (%s) — pulo limpeza de órfãos", e)
        return
    meu = os.getpid()
    for tok in out.stdout.split():
        if not tok.strip().isdigit():
            continue
        pid = int(tok)
        if pid == meu:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            logger.info("selenium_browser: matou Firefox órfão pid=%s do perfil '%s'", pid, alvo)
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.debug("selenium_browser: falha ao matar pid=%s: %s", pid, e)


def _preparar_profile_dir() -> str | None:
    """Garante o diretório do perfil (SEPARADO por plataforma — dois Firefox não
    podem compartilhar o mesmo perfil), mata Firefox órfãos que ainda segurem esse
    perfil e remove locks órfãos de um crash anterior. Só mexe na PRÓPRIA plataforma."""
    if not FIREFOX_PERSIST_PROFILE:
        return None
    try:
        p = Path(FIREFOX_PROFILE_DIR) / get_platform()
        p.mkdir(parents=True, exist_ok=True)
        # 1) Mata órfãos que seguram este perfil (senão o launch sai status 0).
        _matar_orfaos_do_perfil(str(p))
        # 2) Remove locks remanescentes.
        for lock in ("lock", ".parentlock", "parent.lock"):
            f = p / lock
            try:
                if f.exists() or f.is_symlink():
                    f.unlink()
            except Exception:
                pass
        return str(p)
    except Exception as e:
        logger.warning("selenium_browser: falha ao preparar perfil persistente: %s", e)
        return None


def _get_driver():
    """Cria instancia do Firefox com Selenium (perfil persistente por padrão)."""
    options = Options()
    binario = FIREFOX_BINARY
    if FIREFOX_UNDETECTED:
        patched = _resolver_binario_undetected()
        if patched:
            binario = patched
            logger.info("selenium_browser: usando Firefox undetected %s", binario)
    options.binary_location = binario
    options.headless = PLAYWRIGHT_HEADLESS

    profile_dir = _preparar_profile_dir()
    if profile_dir:
        # Usa o perfil em disco EM PLACE (persiste cookies/sessão). Precede as prefs.
        options.add_argument("-profile")
        options.add_argument(profile_dir)
        logger.info("selenium_browser: usando perfil persistente %s", profile_dir)

    if not PLAYWRIGHT_HEADLESS:
        options.set_preference("browser.startup.homepage", "about:blank")
        options.set_preference("browser.startup.page", 0)
    options.set_preference("dom.disable_beforeunload", True)
    options.set_preference("browser.tabs.warnOnClose", False)
    # Com perfil persistente NÃO desligamos o cache — cache/histórico ajudam a
    # parecer um browser real e a reduzir verificações. Sem persistência, mantém
    # o comportamento antigo (cache off) para não crescer disco à toa.
    if not profile_dir:
        options.set_preference("network.http.use-cache", False)
        options.set_preference("browser.cache.disk.enable", False)
        options.set_preference("browser.cache.memory.enable", False)
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference("browser.selfsupport.autostart", False)

    service = Service(GECKODRIVER_PATH)
    driver = webdriver.Firefox(service=service, options=options)
    driver.set_window_size(1920, 1080)
    driver.maximize_window()
    logger.info("selenium_browser: Firefox iniciado")
    return driver


async def _run_in_thread(fn, *args, **kwargs):
    """Executa funcao Selenium (sincrona) em thread separada.

    IMPORTANTE: propaga o contexto (contextvars) da task para a thread. Sem isto, a
    thread do executor roda com contexto ZERADO e `run_context.get_platform()` cai no
    default "default" — o que fazia LinkedIn/Indeed/GeekHunter resolverem o MESMO
    perfil `firefox_profile/default` e um matar o Firefox do outro (bug "abre só uma
    automação por vez"). Com `copy_context().run`, cada plataforma mantém o seu."""
    loop = asyncio.get_event_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(None, lambda: ctx.run(lambda: fn(*args, **kwargs)))


async def _run_in_thread_no_args(fn):
    """Executa funcao Selenium sem argumentos em thread separada.
    Propaga o contexto (contextvars) — ver nota em _run_in_thread."""
    loop = asyncio.get_event_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(None, lambda: ctx.run(fn))


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
    driver = _drivers.get(get_platform())

    # Verifica se o driver existe e tem sessão válida
    if reutilizar and driver:
        sessao_ok = await _driver_session_valida()
        if sessao_ok:
            await _run_in_thread(driver.get, url)
            return driver
        # Sessão inválida - limpa driver antigo
        _drivers.pop(get_platform(), None)
        try:
            await _run_in_thread(driver.quit)
        except Exception:
            pass

    def _open():
        try:
            driver = _get_driver()
        except WebDriverException as e:
            msg = str(e).lower()
            # Perfil ainda preso por um órfão recém-morto (SIGKILL leva um instante
            # pra liberar o lock) → re-limpa e tenta de novo uma vez.
            if "unexpectedly closed" in msg or "status 0" in msg or "profile" in msg:
                logger.warning("selenium_browser: launch falhou (%s) — limpando perfil e re-tentando", e)
                _preparar_profile_dir()
                time.sleep(2)
                driver = _get_driver()
            else:
                raise
        driver.get(url)
        return driver
    driver = await _run_in_thread(_open)
    await _set_driver(driver)
    return driver


async def get_driver() -> webdriver.Firefox | None:
    """Retorna o driver da plataforma atual (run_context) ou None."""
    return _drivers.get(get_platform())


async def _set_driver(driver):
    """Armazena o driver da plataforma atual (run_context)."""
    _drivers[get_platform()] = driver


async def navegar(url: str):
    """Navega para URL na pagina atual. Reabre o browser se a sessão for inválida."""
    from selenium.common.exceptions import InvalidSessionIdException, WebDriverException
    driver = await get_driver()
    if not driver:
        raise RuntimeError("Browser nao inicializado")
    try:
        await _run_in_thread(driver.get, url)
    except (InvalidSessionIdException, WebDriverException):
        print(f"[SELENIUM] Sessão inválida ao navegar para {url} - necessário reabrir")
        raise


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
        xpath = f"//{base_selector}[contains(., '{text}')]"
    else:
        xpath = f"//*[contains(., '{text}')]"
    return (By.XPATH, xpath)


def _iter_selectors(selector: str):
    selector = selector.strip()
    # XPath selectors (starting with //) should not be split by comma
    if selector.startswith("//"):
        if selector:
            yield (By.XPATH, selector)
        return
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
        except Exception:
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


async def digitar_no_elemento(el, texto: str, clear: bool = True) -> bool:
    """Digita texto em um elemento já encontrado."""
    if not el:
        return False
    try:
        visivel = await _run_in_thread(lambda e=el: e.is_displayed())
        habilitado = await _run_in_thread(lambda e=el: e.is_enabled())
        print(f"[DIGITAR_NO_ELEMENTO] elemento visivel={visivel}, habilitado={habilitado}")
        if not visivel or not habilitado:
            return False
        await _run_in_thread(lambda e=el: e.click())
        await asyncio.sleep(0.2)
        if clear:
            try:
                await _run_in_thread(lambda e=el: e.clear())
            except Exception:
                pass
        await _run_in_thread(lambda e=el, t=texto: e.send_keys(t))
        return True
    except Exception as e:
        print(f"[DIGITAR_NO_ELEMENTO] erro: {e}")
        return False


async def digitar_robusto(selector: str, texto: str, clear: bool = True) -> bool:
    """Digita texto com garantias: espera visível, dá foco, usa JS como fallback."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    driver = await get_driver()
    if not driver:
        return False

    is_xpath = selector.strip().startswith("//")
    if is_xpath:
        partes = [selector.strip()]
    else:
        partes = [p.strip() for p in selector.split(",") if p.strip()]
    
    el = None
    for parte in partes:
        try:
            if is_xpath or parte.startswith("//"):
                by, value = (By.XPATH, parte)
            else:
                by, value = _try_convert_selector(parte)
            el = await _run_in_thread(
                lambda b=by, v=value: WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((b, v))
                )
            )
            if el:
                break
        except Exception:
            continue

    if not el:
        try:
            el = await _run_in_thread(lambda: driver.find_element(By.CSS_SELECTOR, partes[0]))
        except Exception:
            return False

    try:
        visivel = await _run_in_thread(lambda e=el: e.is_displayed())
        habilitado = await _run_in_thread(lambda e=el: e.is_enabled())
        print(f"[DIGITAR_ROBUSTO] elemento visivel={visivel}, habilitado={habilitado}, selector={selector[:60]}")
        
        if not visivel:
            try:
                await _run_in_thread(
                    lambda e=el, d=driver: d.execute_script(
                        "arguments[0].scrollIntoView({block:'center', inline:'nearest'});", e
                    )
                )
                await asyncio.sleep(0.5)
                visivel = await _run_in_thread(lambda e=el: e.is_displayed())
                print(f"[DIGITAR_ROBUSTO] após scroll visivel={visivel}")
            except Exception:
                pass
        
        if not habilitado:
            try:
                await _run_in_thread(
                    lambda e=el, d=driver: d.execute_script("arguments[0].removeAttribute('disabled');", e)
                )
                await asyncio.sleep(0.2)
            except Exception:
                pass

        await _run_in_thread(lambda e=el: e.click())
        await asyncio.sleep(0.2)

        if clear:
            try:
                await _run_in_thread(lambda e=el: e.clear())
            except Exception:
                try:
                    await _run_in_thread(lambda e=el: driver.execute_script("arguments[0].value='';", e))
                except Exception:
                    pass

        await _run_in_thread(lambda e=el, t=texto: e.send_keys(t))

        valor_final = await _run_in_thread(lambda e=el: e.get_attribute("value") or "")
        sucesso = valor_final == texto or valor_final.strip() == texto.strip()
        print(f"[DIGITAR_ROBUSTO] digitado='{texto[:30]}' | valor_final='{valor_final[:30]}' | sucesso={sucesso}")
        return sucesso
    except Exception as e:
        print(f"[DIGITAR_ROBUSTO] excecao: {e}")
        try:
            await _run_in_thread(
                lambda e=el, t=texto, d=driver: d.execute_script(
                    "arguments[0].scrollIntoView({block:'center'}); arguments[0].focus(); arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', {bubbles:true}));",
                    e, t
                )
            )
            return True
        except Exception as e2:
            print(f"[DIGITAR_ROBUSTO] fallback JS falhou: {e2}")
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
        _drivers.pop(get_platform(), None)
        logger.info("selenium_browser: Firefox fechado (%s)", get_platform())


async def _ensure_driver_alive():
    """Verifica se o driver está ativo e retorna True, ou recria o driver."""
    try:
        driver = await get_driver()
        if driver:
            await _run_in_thread(lambda: driver.current_url)
            return True
    except (InvalidSessionIdException, Exception):
        _drivers.pop(get_platform(), None)
    return False


async def _wait_for_login_form_visible(driver, timeout: int = 10):
    """Aguarda o formulário de login de email ficar visível (não apenas presente)."""
    try:
        el = await _run_in_thread(
            lambda d=driver: WebDriverWait(d, timeout).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR,
                    "#username, input[name='session_key'], input[type='email'], input[autocomplete='username'], "
                    "input[placeholder*='Email or phone'], input[placeholder*='Email'], input[aria-label*='Email'], "
                    "input[placeholder*='Phone'], input[aria-label*='Phone Number']"
                ))
            )
        )
        return el
    except Exception:
        return None


async def _scroll_and_focus_element(driver, el):
    """Rola a página para fazer elemento visível e chama scrollIntoView."""
    try:
        await _run_in_thread(
            lambda e=el: driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'nearest'});", e)
        )
        await asyncio.sleep(0.5)
    except Exception:
        pass


async def clicar_entrar_com_email(driver):
    """Tenta clicar em link/botão para usar email/senha ao invés de Google."""
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import InvalidSessionIdException, WebDriverException, NoSuchElementException, StaleElementReferenceException
    
    seletores = [
        "a[href*='email-sign-in']",
        "a[href*='sign-in-email']",
        "a[href*='traditional-auth']",
        "button[data-litms-control='sign_in_with_email']",
        "button[data-litms-control='email_sign_in']",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'sign in with email')]",
        "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'sign in with email')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'entre com email')]",
        "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'entre com email')]",
        "//button[.//span[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'email')] and not(contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'google'))]",
        "//a[.//span[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'email')] and not(contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'google'))]",
        "button[type='email']",
        ".sign-in-form__sign-in-button",
        "[data-litms-control='google_sign_in']",
        "//button[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'sign in with email')]",
        "//button[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'email') and not(contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'google'))]",
        ".sign-in-alternate-button",
        ".sign-in-form__sign-in-button",
    ]
    for sel in seletores:
        try:
            if not driver:
                return False
            el = await _run_in_thread(
                lambda s=sel: driver.find_element(By.CSS_SELECTOR, s) if not s.startswith("//") else driver.find_element(By.XPATH, s)
            )
            if el:
                visivel = await _run_in_thread(lambda e=el: e.is_displayed())
                if visivel:
                    texto = await _run_in_thread(lambda e=el: e.text or "")
                    aria = await _run_in_thread(lambda e=el: e.get_attribute("aria-label") or "")
                    href = await _run_in_thread(lambda e=el: e.get_attribute("href") or "")
                    if "google" not in texto.lower() and "google" not in aria.lower() and "google" not in href.lower():
                        await _run_in_thread(lambda e=el: e.click())
                        print(f"[SELENIUM] Clicou em 'Entrar com email': {sel}")
                        await asyncio.sleep(2)
                        return True
                elif await _run_in_thread(lambda e=el: e.is_enabled()):
                    await _run_in_thread(lambda e=el: driver.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", el))
                    print(f"[SELENIUM] Clicou via JS em 'Entrar com email': {sel}")
                    await asyncio.sleep(2)
                    return True
        except (InvalidSessionIdException, WebDriverException) as e:
            print(f"[SELENIUM] Sessão inválida ao clicar em {sel}: {type(e).__name__}")
            _drivers.pop(get_platform(), None)
            raise
        except StaleElementReferenceException:
            continue
        except NoSuchElementException:
            continue
        except Exception:
            continue
    return False


async def forcar_formulario_login(driver):
    """Força a exibição do formulário de login usando navegação direta."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    selectors = [
        "https://www.linkedin.com/login",
        "https://www.linkedin.com/login/?trk=sign_in_with-email",
        "https://www.linkedin.com/uas/login",
        "https://www.linkedin.com/checkpoint/lg/sign-in-unique-email",
    ]
    for url in selectors:
        try:
            await _run_in_thread(driver.get, url)
            await asyncio.sleep(3)
            
            try:
                await _run_in_thread(
                    lambda d=driver: d.execute_script("window.scrollTo(0, 200);")
                )
            except Exception:
                pass
            
            google_btn = await _run_in_thread(
                lambda d=driver: d.find_elements(By.CSS_SELECTOR,
                    "button[data-litms-control='google_sign_in'], "
                    "button[aria-label*='Google'], "
                    "button[aria-label*='google'], "
                    "a[href*='google'], "
                    "a[href*='google.com']"
                )
            )
            if google_btn:
                print(f"[SELENIUM] Google button detectado em {url}, tentando entrar com email")
                await clicar_entrar_com_email(driver)
                await asyncio.sleep(2)
            el = await _run_in_thread(
                lambda d=driver: WebDriverWait(d, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR,
                        "#username, input[name='session_key'], input[type='email'], input[autocomplete='username'], "
                        "input[placeholder*='Email or phone'], input[placeholder*='Email'], input[aria-label*='Email']"
                    ))
                )
            )
            if el:
                try:
                    visivel = await _run_in_thread(lambda e=el: e.is_displayed())
                    if not visivel:
                        await _run_in_thread(
                            lambda d=driver: d.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        )
                        await asyncio.sleep(0.5)
                        visivel = await _run_in_thread(lambda e=el: e.is_displayed())
                    if visivel:
                        print(f"[SELENIUM] Formulário visível após navegar para {url}")
                        return True
                except Exception:
                    pass
        except Exception:
            continue
    return False
