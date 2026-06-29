"""
Candidatura via LinkedIn Easy Apply usando Selenium + Firefox visível.
Substitui Playwright que não funciona no Ubuntu 26.04.
"""

import asyncio
import logging
import os
import re

from dotenv import load_dotenv

# Garante que .env é carregado
load_dotenv()

from automation.selenium_browser import (
    nova_pagina, navegar, wait_for_selector, wait_for_selector_visible,
    click, digitar, digitar_com_delay, digitar_robusto, digitar_no_elemento, screenshot_base64, fechar, get_driver, get_title, _run_in_thread, _wait_for_login_form_visible,
    clicar_entrar_com_email, forcar_formulario_login, _scroll_and_focus_element
)
from automation.browser import notify_browser_step, get_intervention_state
from selenium.webdriver.common.by import By
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException

logger = logging.getLogger(__name__)


def _get_linkedin_email() -> str:
    return os.getenv("LINKEDIN_EMAIL", "")


def _get_linkedin_password() -> str:
    return os.getenv("LINKEDIN_PASSWORD", "")


def _find_element_by_text(driver, tag: str, text: str, timeout: int = 10):
    """Encontra elemento por texto usando XPath (compatibilidade Selenium)."""
    xpath = f"//{tag}[contains(., '{text}')]"
    try:
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        el = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
        return el
    except Exception:
        return None


async def _garantir_login() -> bool:
    """Garante que o browser Selenium está logado no LinkedIn.
    Usa as credenciais do .env. Retorna True se logado com sucesso."""
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By
    
    driver = await get_driver()
    if not driver:
        await nova_pagina("https://www.linkedin.com", reutilizar=False)
        await asyncio.sleep(2)
        driver = await get_driver()

    if not driver:
        print("[LINKEDIN] ERRO: driver é None")
        return False

    try:
        current_url = await _run_in_thread(lambda: driver.current_url)
        title = await get_title()
    except Exception:
        current_url = ""
        title = ""

    print(f"[LINKEDIN] Estado atual: URL={current_url}, Title={title}")

    email = _get_linkedin_email()
    password = _get_linkedin_password()
    if not email or not password:
        print("[LINKEDIN] ERRO: Credenciais não configuradas no .env")
        return False

    if "linkedin.com" in current_url.lower() and "login" not in current_url.lower() and "jobs" not in current_url.lower():
        print("[LINKEDIN] Página principal do LinkedIn - forçando login")
        await navegar("https://www.linkedin.com/login")
        await asyncio.sleep(3)
        current_url = await _run_in_thread(lambda: driver.current_url)
        title = await get_title()

    if _esta_logado(current_url, title):
        print("[LINKEDIN] Já está logado")
        return True

    print(f"[LINKEDIN] Não está logado - fazendo login automático...")
    print(f"[LINKEDIN] Credenciais: email={email}, senha={'***' if password else 'VAZIA'}")
    await notify_browser_step("linkedin_login", "login", "Fazendo login automático no LinkedIn")

    if "login" not in current_url.lower():
        try:
            await navegar("https://www.linkedin.com/login")
            await asyncio.sleep(3)
            current_url = await _run_in_thread(lambda: driver.current_url)
            title = await get_title()
            print(f"[LINKEDIN] Após navegação login: URL={current_url}, Title={title}")
        except Exception as e:
            print(f"[LINKEDIN] Erro ao navegar para login: {e}")

    for tentativa in range(3):
        try:
            el_username = await _run_in_thread(
                lambda: WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR,
                        "#username, input[name='session_key'], input[type='email'], input[autocomplete='username'], "
                        "input[placeholder*='Email or phone'], input[placeholder*='Email'], input[aria-label*='Email']"
                    ))
                )
            )
            el_username = await _run_in_thread(
                lambda: WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR,
                        "#username, input[name='session_key'], input[type='email'], input[autocomplete='username'], "
                        "input[placeholder*='Email or phone'], input[placeholder*='Email'], input[aria-label*='Email']"
                    ))
                )
            )
            print(f"[LINKEDIN] Campo username clicável (tentativa {tentativa+1})")
        except Exception:
            try:
                el_username = await _run_in_thread(
                    lambda: driver.find_element(By.CSS_SELECTOR,
                        "#username, input[name='session_key'], input[type='email'], input[autocomplete='username'], "
                        "input[placeholder*='Email or phone'], input[placeholder*='Email'], input[aria-label*='Email']"
                    )
                )
                try:
                    visivel = await _run_in_thread(lambda e=el_username: e.is_displayed())
                    if not visivel:
                        print(f"[LINKEDIN] Campo username encontrado mas não visível (tentativa {tentativa+1})")
                        try:
                            await _run_in_thread(
                                lambda e=el_username, d=driver: d.execute_script("arguments[0].scrollIntoView({block:'center'});", e)
                            )
                            await asyncio.sleep(0.5)
                            visivel = await _run_in_thread(lambda e=el_username: e.is_displayed())
                            if visivel:
                                print(f"[LINKEDIN] Campo visível após scroll")
                            else:
                                el_username = None
                        except Exception:
                            el_username = None
                    else:
                        print(f"[LINKEDIN] Campo username presente (tentativa {tentativa+1})")
                except Exception:
                    el_username = None
            except Exception:
                el_username = None

        if not el_username:
            form_ok = await forcar_formulario_login(driver)
            if form_ok:
                el_username = await _run_in_thread(
                    lambda: WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR,
                            "#username, input[name='session_key'], input[type='email'], input[autocomplete='username'], "
                            "input[placeholder*='Email or phone'], input[placeholder*='Email'], input[aria-label*='Email']"
                        ))
                    )
                )
                if el_username:
                    print("[LINKEDIN] Campos encontrados após forçar formulário")
                else:
                    print("[LINKEDIN] Formulário forçado mas campos ainda não encontrados")
            
            if not el_username:
                google_btn = await _run_in_thread(
                    lambda: driver.find_elements(By.CSS_SELECTOR,
                        "button[data-litms-control='google_sign_in'], "
                        "button[aria-label*='Google'], "
                        "button[aria-label*='google'], "
                        "a[href*='google'], "
                        "a[href*='google.com']"
                    )
                )
                if google_btn:
                    print("[LINKEDIN] Detectado botão Google — procurando alternativa email/senha")
                    try:
                        await clicar_entrar_com_email(driver)
                        await asyncio.sleep(3)
                        driver = await get_driver()
                        if driver:
                            el_username = await _run_in_thread(
                                lambda: WebDriverWait(driver, 10).until(
                                    EC.element_to_be_clickable((By.CSS_SELECTOR,
                                        "#username, input[name='session_key'], input[type='email'], input[autocomplete='username'], "
                                        "input[placeholder*='Email or phone'], input[placeholder*='Email'], input[aria-label*='Email']"
                                    ))
                                )
                            )
                            if el_username:
                                print("[LINKEDIN] Campos apareceram após clicar 'entrar com email'")
                    except (InvalidSessionIdException, WebDriverException) as e:
                        print(f"[LINKEDIN] Sessão inválida ao clicar 'entrar com email': {e}")
                        await nova_pagina("https://www.linkedin.com/login", reutilizar=False)
                        await asyncio.sleep(3)
                        driver = await get_driver()
            
            if not el_username:
                if tentativa < 2:
                    print(f"[LINKEDIN] Tentativa {tentativa+1} falhou - reiniciando")
                    await navegar("https://www.linkedin.com/login")
                    await asyncio.sleep(2)
                    continue
                print("[LINKEDIN] ERRO: Campo username não ficou disponível após 3 tentativas")
                return False

        try:
            email_ok = await digitar_robusto("#username", email)
            if not email_ok:
                email_ok = await digitar_robusto("input[name='session_key']", email)
            if not email_ok:
                email_ok = await digitar_robusto("input[type='email']", email)
            if not email_ok:
                email_ok = await digitar_robusto("input[placeholder*='Email or phone']", email)
            if not email_ok:
                email_ok = await digitar_robusto("input[placeholder*='Email']", email)
            if not email_ok:
                email_ok = await digitar_robusto("input[aria-label*='Email']", email)
            print(f"[LINKEDIN] Email digitado: {email_ok}")

            await asyncio.sleep(0.5)

            pass_ok = await digitar_robusto("#password", password)
            if not pass_ok:
                pass_ok = await digitar_robusto("input[name='session_password']", password)
            if not pass_ok:
                pass_ok = await digitar_robusto("input[type='password']", password)
            print(f"[LINKEDIN] Senha digitada: {pass_ok}")

            await asyncio.sleep(0.3)

            click_ok = await click("button[type='submit']")
            if not click_ok:
                click_ok = await click("button.sign-in-form__submit-button")
            if not click_ok:
                click_ok = await click("//button[contains(text(), 'Entrar')]")
            if not click_ok:
                click_ok = await click("//button[contains(., 'Sign in')]")
            print(f"[LINKEDIN] Submit clicado: {click_ok}")
            await asyncio.sleep(5)

            if email_ok and pass_ok and click_ok:
                break
        except Exception as e:
            print(f"[LINKEDIN] Erro ao digitar credenciais (tentativa {tentativa+1}): {e}")
            import traceback
            traceback.print_exc()
            if tentativa < 2:
                await navegar("https://www.linkedin.com/login")
                await asyncio.sleep(2)
            continue

    for _ in range(30):
        await asyncio.sleep(1)
        try:
            cur = await _run_in_thread(lambda: driver.current_url)
            ttl = await get_title()
            print(f"[LINKEDIN] Aguardando login... URL={cur}, Title={ttl}")

            try:
                def _find_login_fields():
                    return driver.find_elements(By.ID, "username"), driver.find_elements(By.ID, "password")
                username_el, password_el = await _run_in_thread(_find_login_fields)
                if username_el and password_el and "login" in cur.lower():
                    print("[LINKEDIN] Campos de login ainda visíveis - login pode ter falhado")
            except Exception:
                pass

            if _esta_logado(cur, ttl):
                print("[LINKEDIN] Login OK - redirecionado")
                await notify_browser_step("linkedin_login", "sucesso", "Login realizado com sucesso")
                return True
            if "checkpoint" in cur.lower() or "challenge" in cur.lower():
                print("[LINKEDIN] Login bloqueado por verificação de segurança")
                await notify_browser_step("linkedin_login", "bloqueio", "Verificação de segurança necessária")
                return False
        except Exception as e:
            print(f"[LINKEDIN] Erro ao verificar login: {e}")
            continue

    print("[LINKEDIN] Login não completado em 30s")
    return False


def _esta_logado(url: str, title: str) -> bool:
    """Verifica se o browser está logado no LinkedIn."""
    url_lower = (url or "").lower().strip()
    title_lower = (title or "").lower().strip()

    # Página de login explicita
    if "login" in url_lower:
        return False

    # Título indica login
    if "sign in" in title_lower:
        return False

    # URLs que indicam login bem-sucedido
    if "feed" in url_lower or "mynetwork" in url_lower:
        return True

    # Páginas de jobs também indicam que está logado
    if "linkedin.com/jobs" in url_lower:
        return True

    # Perfil (https://www.linkedin.com/in/username/)
    if "/in/" in url_lower:
        return True

    # Página principal sem login = não logado
    if url_lower in ("https://www.linkedin.com/", "https://www.linkedin.com"):
        return False

    return False


async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "") -> dict:
    """Aplica em vaga LinkedIn Easy Apply via Selenium."""
    if not _get_linkedin_email() or not _get_linkedin_password():
        return {
            "sucesso": False,
            "motivo_falha": "credenciais_ausentes",
            "mensagem": "Configure LINKEDIN_EMAIL e LINKEDIN_PASSWORD no .env.",
        }

    try:
        await notify_browser_step("selenium_linkedin", "iniciando", "Abrindo LinkedIn")
        print(f"[LINKEDIN] Iniciando aplicacao com Selenium para: {vaga_url}")

        # Verifica sessão existente - reutiliza se válida
        driver = await get_driver()
        if driver and await _driver_session_valida():
            print(f"[LINKEDIN] Reutilizando browser existente: {driver.current_url}")
        else:
            if driver:
                print("[LINKEDIN] Sessão inválida - fechando browser antigo")
                try:
                    await fechar()
                except Exception:
                    pass
            print("[LINKEDIN] Abrindo novo browser")
            await nova_pagina("https://www.linkedin.com", reutilizar=False)
            await asyncio.sleep(2)

        # Garante login antes de prosseguir
        login_ok = await _garantir_login()
        if not login_ok:
            return {
                "sucesso": False,
                "motivo_falha": "login_falhou",
                "mensagem": "Não foi possível fazer login no LinkedIn. Verifique as credenciais no .env ou se há verificação de segurança.",
            }

        driver = await get_driver()
        current_url = driver.current_url if driver else ""
        print(f"[LINKEDIN] Logado com sucesso. URL: {current_url}")

        # Navega para a vaga apenas se não estiver já na página da vaga
        if vaga_url not in current_url:
            await notify_browser_step("selenium_linkedin", "navegando", f"Abrindo vaga")
            await navegar(vaga_url)
            await asyncio.sleep(3)
        else:
            print(f"[LINKEDIN] Já está na página da vaga")

        print(f"[LINKEDIN] Vaga carregada: {await get_title()}")

        # Clica em Easy Apply (ingles ou portugues)
        await notify_browser_step("selenium_linkedin", "easy_apply", "Procurando Easy Apply...")
        easy_btn = await wait_for_selector_visible(
            "button.jobs-apply-button, button[data-control-name='apply_show_modal'], "
            "button[aria-label*='Easy Apply'], button[aria-label*='Candidatura simplificada'], "
            "button:has-text('Easy Apply'), button:has-text('Candidatura simplificada')",
            timeout=10
        )

        if not easy_btn:
            print("[LINKEDIN] Botao Easy Apply NAO encontrado")
            await notify_browser_step("selenium_linkedin", "erro", "Easy Apply nao encontrado")
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            return {"sucesso": False, "mensagem": "Easy Apply nao encontrado nesta vaga.", "screenshot": b64[:100] if b64 else ""}

        await click("button.jobs-apply-button, button[data-control-name='apply_show_modal']")
        print("[LINKEDIN] Clicou em Easy Apply")
        await asyncio.sleep(3)

        # Verifica se modal abriu
        modal = await wait_for_selector_visible(".jobs-easy-apply-modal, .jobs-easy-apply__modal", timeout=10)
        if not modal:
            print("[LINKEDIN] Modal Easy Apply nao apareceu")
            await notify_browser_step("selenium_linkedin", "erro", "Modal nao apareceu")
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            return {"sucesso": False, "mensagem": "Modal Easy Apply nao apareceu.", "screenshot": b64[:100] if b64 else ""}

        print("[LINKEDIN] Modal aberto")

        # Preenche campos do formulario
        campos = 0
        nome_completo = perfil.get("nome", "")
        partes = nome_completo.split()
        if len(partes) >= 2:
            primeiro_nome = partes[0]
            ultimo_nome = " ".join(partes[1:])
        else:
            primeiro_nome = nome_completo
            ultimo_nome = ""

        campos_nomes = [
            ("input[name='firstName'], #first-name, input[id*='firstName']", primeiro_nome),
            ("input[name='lastName'], #last-name, input[id*='lastName']", ultimo_nome),
            ("input[name='email'], #email, input[type='email'], input[placeholder*='Email']", perfil.get("email", _get_linkedin_email())),
            ("input[name='phone'], #phone, input[type='tel'], input[placeholder*='Phone'], input[aria-label*='Phone Number']", str(perfil.get("telefone", perfil.get("phone", "")))),
        ]
        for seletor, texto in campos_nomes:
            if texto and await digitar_com_delay(seletor, str(texto), delay_min=20, delay_max=60):
                campos += 1
                print(f"[LINKEDIN] Preencheu: {seletor[:50]} = {texto[:30]}")

        await notify_browser_step("selenium_linkedin", "preenchido", f"Campos preenchidos: {campos}")

        # Clica em Enviar
        await notify_browser_step("selenium_linkedin", "enviando", "Enviando candidatura...")
        submit = await wait_for_selector_visible(
            "button[aria-label='Enviar candidatura'], button[aria-label='Submit application'], button[type='submit']",
            timeout=10
        )
        if submit:
            await click("button[aria-label='Enviar candidatura'], button[aria-label='Submit application'], button[type='submit']")
            await asyncio.sleep(3)
            print("[LINKEDIN] Clicou em Enviar")

            # Verifica se foi sucesso
            sucesso = await wait_for_selector_visible(".artdeco-toast", timeout=5)
            if sucesso:
                print("[LINKEDIN] Candidatura ENVIADA com sucesso!")
                await notify_browser_step("selenium_linkedin", "sucesso", "Candidatura enviada!")
                b64 = await screenshot_base64()
                await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
                return {"sucesso": True, "mensagem": "Candidatura enviada no LinkedIn!", "screenshot": b64[:100] if b64 else ""}
            else:
                print("[LINKEDIN] Enviou - aguardando confirmacao")
                await notify_browser_step("selenium_linkedin", "sucesso", "Candidatura enviada!")
                b64 = await screenshot_base64()
                await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
                return {"sucesso": True, "mensagem": "Candidatura enviada (aguardando confirmacao).", "screenshot": b64[:100] if b64 else ""}
        else:
            print("[LINKEDIN] Botao enviar nao encontrado")
            await notify_browser_step("selenium_linkedin", "erro", "Botao enviar nao encontrado")
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            return {"sucesso": False, "mensagem": "Botao enviar nao encontrado. Complete manualmente.", "screenshot": b64[:100] if b64 else ""}

    except Exception as e:
        logger.error(f"aplicar_linkedin_selenium erro: {e}")
        await notify_browser_step("selenium_linkedin", "erro", str(e))
        print(f"[LINKEDIN] ERRO: {e}")
        try:
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
        except Exception:
            pass
        return {"sucesso": False, "mensagem": str(e)}


async def aplicar_vagas_visiveis_na_pagina(perfil: dict, max_vagas: int = 5) -> dict:
    """
    Aplica em vagas visíveis na página atual do LinkedIn.
    Usa a página já aberta (busca, easy-apply, etc.) sem forçar navegação.
    """
    if not _get_linkedin_email() or not _get_linkedin_password():
        return {
            "sucesso": False,
            "motivo_falha": "credenciais_ausentes",
            "mensagem": "Configure LINKEDIN_EMAIL e LINKEDIN_PASSWORD no .env.",
        }

    try:
        await notify_browser_step("selenium_linkedin", "iniciando", "Aplicando nas vagas da página atual")
        driver = await get_driver()

        if not driver:
            await nova_pagina("https://www.linkedin.com", reutilizar=False)
            await asyncio.sleep(2)
            driver = await get_driver()

        if not driver or not await _driver_session_valida():
            print("[LINKEDIN] Sessão inválida - reabrindo browser")
            if driver:
                try:
                    await fechar()
                except Exception:
                    pass
            await nova_pagina("https://www.linkedin.com", reutilizar=False)
            await asyncio.sleep(2)

        # Garante login automático antes de aplicar
        login_ok = await _garantir_login()
        if not login_ok:
            return {
                "sucesso": False,
                "motivo_falha": "login_falhou",
                "mensagem": "Não foi possível fazer login no LinkedIn. Verifique LINKEDIN_EMAIL e LINKEDIN_PASSWORD.",
            }

        driver = await get_driver()
        current_url = driver.current_url
        url_lower = current_url.lower()

        # Se NÃO está em uma página de jobs, navega para easy-apply
        if "jobs" not in url_lower and "feed" not in url_lower:
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            await asyncio.sleep(3)

        await notify_browser_step("selenium_linkedin", "iniciando", f"Página atual: {current_url}")

        resultados = {"sucesso": True, "aplicacoes": [], "falhas": 0}

        for i in range(max_vagas):
            control = await get_intervention_state()
            if control.get("paused"):
                await asyncio.sleep(0.5)
                continue

            await notify_browser_step("selenium_linkedin", f"vaga_{i+1}", f"Procurando vaga {i+1}...")

            vaga_url = await _extrair_primeira_vaga_da_busca()
            if not vaga_url:
                await notify_browser_step("selenium_linkedin", "finalizando", "Nenhuma vaga Easy Apply encontrada")
                break

            await notify_browser_step("selenium_linkedin", f"vaga_{i+1}", f"Aplicando em: {vaga_url[:60]}...")

            resultado = await aplicar(vaga_url, perfil)
            resultados["aplicacoes"].append(resultado)
            if not resultado.get("sucesso"):
                resultados["falhas"] += 1

            # Volta para a página de jobs após cada aplicação
            driver = await get_driver()
            if driver:
                cur = driver.current_url
                if "jobs" not in cur.lower():
                    await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
                    await asyncio.sleep(2)

            await asyncio.sleep(2)

        await notify_browser_step("selenium_linkedin", "finalizando", f"Concluído: {len(resultados['aplicacoes'])} vagas processadas")
        return resultados

    except Exception as e:
        logger.error(f"aplicar_vagas_visiveis erro: {e}")
        return {
            "sucesso": False,
            "mensagem": str(e),
            "falhas": 1,
        }


async def _driver_session_valida() -> bool:
    """Verifica se o driver Selenium tem uma sessão ativa válida."""
    try:
        driver = await get_driver()
        if driver is None:
            return False
        await _run_in_thread(lambda: driver.current_url)
        return True
    except Exception:
        return False


async def _extrair_primeira_vaga_da_busca() -> str | None:
    """Extrai URL da primeira vaga com Easy Apply visível na página de busca."""
    from selenium.webdriver.common.by import By

    driver = await get_driver()
    if not driver:
        return None

    try:
        def _procurar():
            current_url = driver.current_url.lower()
            is_easy_apply_page = "easy-apply" in current_url or "collections" in current_url
            is_jobs_search = "jobs/search" in current_url

            # Estratégia para página /jobs/collections/easy-apply/
            if is_easy_apply_page:
                try:
                    cards = driver.find_elements(By.CSS_SELECTOR,
                        ".jobs-easy-apply__card, .job-card--easy-apply, .job-card, "
                        ".job-card-container, .jobs-search__result-card"
                    )
                    for card in cards[:10]:
                        try:
                            link_elem = card.find_element(By.XPATH, ".//a[contains(@href, '/jobs/view/')]")
                            href = link_elem.get_attribute("href")
                            if href:
                                return href
                        except Exception:
                            continue
                except Exception:
                    pass

            # Estratégia 1: Procura por botão Easy Apply e obtém o link da vaga associada
            try:
                buttons = driver.find_elements(By.XPATH, "//button[contains(@class,'jobs-apply-button') and contains(@aria-label,'Easy Apply')]")
                for btn in buttons[:5]:
                    try:
                        link_elem = btn.find_element(By.XPATH, "./ancestor::a[contains(@href,'/jobs/view/')]")
                        href = link_elem.get_attribute("href")
                        if href:
                            return href
                    except Exception:
                        continue
            except Exception:
                pass

            # Estratégia 2: Cards de vaga com botão apply visível
            try:
                job_cards = driver.find_elements(By.CSS_SELECTOR, ".job-card-container, .jobs-search__result-card, .base-card, .job-card")
                for card in job_cards[:15]:
                    try:
                        apply_btn = card.find_element(By.CSS_SELECTOR, "button.jobs-apply-button, button[data-control-name='apply_show_modal']")
                        link_elem = card.find_element(By.XPATH, ".//a[contains(@href, '/jobs/view/')]")
                        href = link_elem.get_attribute("href")
                        if href:
                            return href
                    except Exception:
                        continue
            except Exception:
                pass

            # Estratégia 3: Primeiro link de vaga visível (fallback)
            try:
                links = driver.find_elements(By.XPATH, "//a[@href and contains(@href, '/jobs/view/')]")
                if links:
                    return links[0].get_attribute("href")
            except Exception:
                pass

            return None

        return await _run_in_thread(_procurar)
    except Exception as e:
        logger.debug(f"Erro ao extrair vaga da busca: {e}")
    return None


async def extrair_vagas_da_busca(perfil: dict, max_vagas: int = 20) -> dict:
    """
    Extrai vagas da página de busca aberta do LinkedIn.
    Usa a página ativa se já estiver no LinkedIn (qualquer página de jobs).
    Tenta Playwright primeiro, senão Selenium.
    Não força navegação se já houver uma página de jobs aberta.
    """
    if not _get_linkedin_email() or not _get_linkedin_password():
        return {
            "sucesso": False,
            "vagas": [],
            "mensagem": "Configure LINKEDIN_EMAIL e LINKEDIN_PASSWORD no .env para candidatura automatica.",
        }

    search_url = "https://www.linkedin.com/jobs/search/?keywords=desenvolvedor&location=Brasil"
    easy_apply_url = "https://www.linkedin.com/jobs/collections/easy-apply/"

    # Tenta usar página ativa do Playwright primeiro (browser já aberto pelo usuário)
    try:
        from automation.browser import get_active_page
        page = await get_active_page()
        if page and "linkedin.com" in (page.url or ""):
            print(f"[LINKEDIN] Usando página Playwright ativa: {page.url}")
            # Se já está em uma página de jobs, não navega - usa a atual
            url = page.url or ""
            if "jobs" not in url and "feed" not in url:
                await page.goto(easy_apply_url)
                await asyncio.sleep(3)
            resultado = await _extrair_vagas_playwright(page, max_vagas)
            if resultado.get("vagas"):
                return resultado
    except Exception as e:
        print(f"[LINKEDIN] Falha ao usar Playwright ativo: {e}")

    driver = await get_driver()

    if not driver:
        await nova_pagina(easy_apply_url, reutilizar=False)
        await asyncio.sleep(3)
        driver = await get_driver()

    if not await _driver_session_valida():
        print("[LINKEDIN] Sessão inválida na extração - reabrindo browser")
        await fechar()
        await nova_pagina(easy_apply_url, reutilizar=False)
        await asyncio.sleep(3)
        driver = await get_driver()

    # Garante login automático antes de acessar
    login_ok = await _garantir_login()
    if not login_ok:
        return {
            "sucesso": False,
            "vagas": [],
            "mensagem": "Não foi possível fazer login no LinkedIn. Verifique LINKEDIN_EMAIL e LINKEDIN_PASSWORD no .env.",
        }

    current_url = driver.current_url
    # Se já está em uma página de jobs (search, easy-apply, etc), não navega
    url_lower = current_url.lower()
    if "jobs" not in url_lower and "feed" not in url_lower:
        await navegar(easy_apply_url)
        await asyncio.sleep(3)

    await notify_browser_step("linkedin_extracao", "iniciando", "Extraindo vagas da página atual")

    cards = await _extrair_cards_vaga(max_vagas)

    await notify_browser_step("linkedin_extracao", "finalizando", f"Extraídas {len(cards)} vagas")
    return {"sucesso": True, "vagas": cards, "total": len(cards)}


async def _extrair_vagas_playwright(page, max_vagas: int = 20) -> dict:
    """Extrai vagas usando uma página Playwright já ativa."""
    try:
        # Tenta múltiplos seletores para cobrir /jobs/search e /jobs/collections/easy-apply
        cards = await page.query_selector_all(
            ".job-card-container, .jobs-search__result-card, .base-card, "
            ".jobs-easy-apply__card, .job-card--easy-apply, .job-card"
        )
        if not cards:
            return {"sucesso": False, "vagas": [], "mensagem": "Nenhum card de vaga encontrado"}

        resultados = []
        for card in cards[:max_vagas]:
            try:
                titulo = ""
                titulo_el = await card.query_selector(
                    ".job-card-list__title, .base-search-card__title, h3, "
                    "a[data-control-name='job_card_title'], .job-card-title"
                )
                if titulo_el:
                    titulo = (await titulo_el.inner_text()).strip()
                if not titulo:
                    link_el = await card.query_selector("a[href*='/jobs/view/']")
                    if link_el:
                        titulo = (await link_el.inner_text()).strip()

                empresa = ""
                emp_el = await card.query_selector(
                    ".job-card-container__primary-description, .base-search-card__subtitle, "
                    ".job-card-container__company-name, .job-card-company-name"
                )
                if emp_el:
                    empresa = (await emp_el.inner_text()).strip()

                url = ""
                link_el = await card.query_selector("a[href*='/jobs/view/']")
                if link_el:
                    url = await link_el.get_attribute("href") or ""

                modalidade = ""
                mod_el = await card.query_selector(
                    ".job-card-container__footer, .job-search-card__benefits, "
                    ".job-card-properties, .job-card-benefits"
                )
                if mod_el:
                    modalidade = (await mod_el.inner_text()).strip()

                local = ""
                loc_el = await card.query_selector(
                    ".job-card-container__metadata, .job-search-card__location, "
                    ".base-search-card__metadata, .job-card-location"
                )
                if loc_el:
                    local = (await loc_el.inner_text()).strip()

                easy_apply = False
                try:
                    ea_btn = await card.query_selector(
                        "button.jobs-apply-button, button[data-control-name='apply_show_modal'], "
                        "button[aria-label*='Easy Apply'], button[aria-label*='Candidatura simplificada']"
                    )
                    if ea_btn:
                        ea_text = (await ea_btn.inner_text()).strip().lower()
                        if "easy apply" in ea_text or "candidatura simplificada" in ea_text or "apply" in ea_text:
                            easy_apply = True
                except Exception:
                    pass

                if titulo and url:
                    vaga_id = url.split("/jobs/view/")[1].split("/")[0].split("?")[0] if "/jobs/view/" in url else url
                    resultados.append({
                        "id": f"linkedin-{vaga_id}",
                        "titulo": titulo,
                        "empresa": empresa,
                        "url": url,
                        "fonte": "LinkedIn",
                        "salario": "",
                        "modalidade": modalidade,
                        "descricao": "",
                        "local": local,
                        "easy_apply": easy_apply,
                    })
            except Exception:
                continue

        return {"sucesso": True, "vagas": resultados, "total": len(resultados)}
    except Exception as e:
        return {"sucesso": False, "vagas": [], "mensagem": str(e)}


async def _extrair_cards_vaga(max_vagas: int = 20) -> list[dict]:
    """Extrai dados dos cards de vaga visíveis na página de busca."""
    from selenium.webdriver.common.by import By

    driver = await get_driver()
    if not driver:
        return []

    def _procurar():
        resultados = []
        cards = driver.find_elements(By.CSS_SELECTOR,
            ".job-card-container, .jobs-search__result-card, .base-card, "
            ".jobs-easy-apply__card, .job-card--easy-apply, .job-card"
        )
        if not cards:
            return resultados

        for card in cards[:max_vagas]:
            try:
                titulo = ""
                try:
                    el = card.find_element(By.CSS_SELECTOR,
                        ".job-card-list__title, .base-search-card__title, h3, "
                        "a[data-control-name='job_card_title'], .job-card-title"
                    )
                    titulo = el.text.strip()
                except Exception:
                    pass
                if not titulo:
                    try:
                        el = card.find_element(By.XPATH, ".//a[contains(@href, '/jobs/view/')]")
                        titulo = el.text.strip()
                    except Exception:
                        pass

                empresa = ""
                try:
                    el = card.find_element(By.CSS_SELECTOR,
                        ".job-card-container__primary-description, .base-search-card__subtitle, "
                        ".job-card-container__company-name, .job-card-company-name"
                    )
                    empresa = el.text.strip()
                except Exception:
                    pass

                url = ""
                try:
                    el = card.find_element(By.XPATH, ".//a[contains(@href, '/jobs/view/')]")
                    url = el.get_attribute("href") or ""
                except Exception:
                    pass

                local = ""
                try:
                    el = card.find_element(By.CSS_SELECTOR,
                        ".job-card-container__metadata, .job-search-card__location, "
                        ".base-search-card__metadata, .job-card-location"
                    )
                    local = el.text.strip()
                except Exception:
                    pass

                modalidade = ""
                try:
                    el = card.find_element(By.CSS_SELECTOR,
                        ".job-card-container__footer, .job-search-card__benefits, "
                        ".job-card-properties, .job-card-benefits"
                    )
                    modalidade = el.text.strip()
                except Exception:
                    pass

                easy_apply = False
                try:
                    ea_btn = card.find_element(By.CSS_SELECTOR,
                        "button.jobs-apply-button, button[data-control-name='apply_show_modal'], "
                        "button[aria-label*='Easy Apply'], button[aria-label*='Candidatura simplificada']"
                    )
                    if ea_btn and ea_btn.is_displayed():
                        ea_text = (ea_btn.text or "").strip().lower()
                        if "easy apply" in ea_text or "candidatura simplificada" in ea_text or "apply" in ea_text:
                            easy_apply = True
                except Exception:
                    pass

                if titulo and url:
                    vaga_id = url.split("/jobs/view/")[1].split("/")[0].split("?")[0] if "/jobs/view/" in url else url
                    resultados.append({
                        "id": f"linkedin-{vaga_id}",
                        "titulo": titulo,
                        "empresa": empresa,
                        "url": url,
                        "fonte": "LinkedIn",
                        "salario": "",
                        "modalidade": modalidade,
                        "descricao": "",
                        "local": local,
                        "easy_apply": easy_apply,
                    })
            except Exception:
                continue
        return resultados

    return await _run_in_thread(_procurar)
