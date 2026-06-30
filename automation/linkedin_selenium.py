"""
Candidatura via LinkedIn Easy Apply usando Selenium + Firefox visível.
Substitui Playwright que não funciona no Ubuntu 26.04.
"""

import asyncio
import logging
import os
import re
import random

from dotenv import load_dotenv

load_dotenv()

from automation.selenium_browser import (
    nova_pagina, navegar, wait_for_selector, wait_for_selector_visible,
    click, digitar, digitar_com_delay, digitar_robusto, digitar_no_elemento,
    screenshot_base64, fechar, get_driver, get_title, _run_in_thread,
    _wait_for_login_form_visible, clicar_entrar_com_email, forcar_formulario_login,
    _scroll_and_focus_element, _try_convert_selector
)
from automation.browser import notify_browser_step, get_intervention_state, wait_if_paused
from automation.form_filler import responder_pergunta
from selenium.webdriver.common.by import By
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException

logger = logging.getLogger(__name__)


# Campos que nao sao "perguntas customizadas" — ja sao preenchidos automaticamente
_CAMPOS_PADRAO = {
    "telefone", "phone", "email", "nome", "name", "sobrenome", "lastname",
    "primeiro nome", "first name", "cidade", "city", "pais", "country",
    "endereco", "address", "cep", "zip", "estado", "state",
    "país", "primeiro_nome", "ultimo_nome", "last_name", "first_name",
    "numero", "number", "nacionalidade", "nacionalidade",
}


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


async def _aguardar_resolucao_captcha(driver, origem: str = "login") -> bool:
    """
    Pausa a automação e aguarda o usuário resolver um CAPTCHA/checkpoint do LinkedIn.
    Ativa intervenção manual, faz polling da URL e retoma quando o checkpoint sai.
    Retorna True se o usuário resolveu e está numa página válida, False se clicou Parar.
    """
    from automation.browser import set_intervention_state, get_intervention_state

    await set_intervention_state("paused", True)
    await set_intervention_state("intervention_type", "manual")
    await notify_browser_step(
        "selenium_linkedin", "captcha",
        f"⚠️ CAPTCHA/verificação detectado ({origem})! Resolva no browser Firefox e clique ▶️ Continuar no dashboard."
    )
    print(f"[LINKEDIN] CAPTCHA detectado em '{origem}' — aguardando resolução manual...")

    while True:
        # Usuário clicou Parar
        control = await get_intervention_state()
        if control.get("current_action") == "parar":
            print("[LINKEDIN] Usuário clicou Parar durante espera de CAPTCHA")
            return False

        # Verifica se URL saiu do checkpoint (usuário resolveu no browser)
        try:
            cur = await _run_in_thread(lambda: driver.current_url)
            if "checkpoint" not in cur.lower() and "challenge" not in cur.lower():
                # Saiu do checkpoint — verifica se está logado ou numa página válida
                if "linkedin.com" in cur.lower() and "login" not in cur.lower():
                    print(f"[LINKEDIN] CAPTCHA resolvido! URL: {cur}")
                    await notify_browser_step("selenium_linkedin", "retomando", "CAPTCHA resolvido! Retomando automação...")
                    await set_intervention_state("paused", False)
                    await set_intervention_state("intervention_type", None)
                    return True
        except Exception:
            pass

        # Usuário clicou Continuar no dashboard sem ter resolvido ainda —
        # re-ativa intervenção se ainda estiver no checkpoint
        if not control.get("paused") and control.get("intervention_type") != "manual":
            try:
                cur = await _run_in_thread(lambda: driver.current_url)
                if "checkpoint" not in cur.lower() and "challenge" not in cur.lower():
                    print(f"[LINKEDIN] Retomando após Continuar. URL: {cur}")
                    await notify_browser_step("selenium_linkedin", "retomando", "Retomando após intervenção manual...")
                    return True
                else:
                    # Ainda no checkpoint — re-ativa intervenção
                    await set_intervention_state("paused", True)
                    await set_intervention_state("intervention_type", "manual")
                    await notify_browser_step(
                        "selenium_linkedin", "captcha",
                        "⚠️ CAPTCHA ainda ativo. Resolva no browser antes de continuar."
                    )
            except Exception:
                pass

        # Envia screenshot periódico
        try:
            img = await screenshot_base64()
            if img:
                from automation.browser import _send_screenshot_via_sio
                import asyncio as _asyncio
                _asyncio.create_task(_send_screenshot_via_sio({"image": img, "step": "captcha", "action": "aguardando resolução"}))
        except Exception:
            pass

        await asyncio.sleep(2)


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
                print("[LINKEDIN] Login bloqueado por verificação de segurança — aguardando resolução manual")
                await notify_browser_step("linkedin_login", "bloqueio", "Verificação de segurança — resolva no browser")
                resolvido = await _aguardar_resolucao_captcha(driver, "login")
                if not resolvido:
                    return False
                # Após resolução, verifica se realmente está logado
                try:
                    cur = await _run_in_thread(lambda: driver.current_url)
                    ttl = await get_title()
                except Exception:
                    cur, ttl = "", ""
                if _esta_logado(cur, ttl):
                    return True
                # Deu na página de jobs ou feed mas _esta_logado retornou False — tenta navegar para jobs
                if "linkedin.com" in cur.lower():
                    return True
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


async def _get_resumo_curriculo(user_id: str) -> str:
    """Carrega resumo do curriculo do perfil profissional."""
    try:
        from graph.neo4j_client import get_neo4j
        neo4j = get_neo4j()
        return neo4j.get_resumo_curriculo(user_id) or ""
    except Exception:
        return ""


async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "", user_id: str = "admin") -> dict:
    """
    Aplica em vaga LinkedIn Easy Apply via Selenium com multi-step handling.
    Usa IA para responder perguntas customizadas baseadas no resumo do curriculo.
    Sempre responde em ingles.
    """
    if not _get_linkedin_email() or not _get_linkedin_password():
        return {
            "sucesso": False,
            "motivo_falha": "credenciais_ausentes",
            "mensagem": "Configure LINKEDIN_EMAIL e LINKEDIN_PASSWORD no .env.",
        }

    resumo_curriculo = perfil.get("resumo_curriculo", "") or ""
    if not resumo_curriculo:
        resumo_curriculo = await _get_resumo_curriculo(user_id)

    try:
        await notify_browser_step("selenium_linkedin", "iniciando", "Abrindo LinkedIn")
        print(f"[LINKEDIN] Iniciando aplicacao com Selenium para: {vaga_url}")

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

        login_ok = await _garantir_login()
        if not login_ok:
            return {
                "sucesso": False,
                "motivo_falha": "login_falhou",
                "mensagem": "Não foi possível fazer login no LinkedIn. Verifique as credenciais no .env ou se há verificação de segurança.",
            }

        driver = await get_driver()
        if not driver:
            return {"sucesso": False, "mensagem": "Browser não disponível após login."}

        current_url = await _run_in_thread(lambda: driver.current_url) if driver else ""
        print(f"[LINKEDIN] Logado com sucesso. URL: {current_url}")

        vaga_id = _extrair_vaga_id(vaga_url)
        vaga_na_pagina = vaga_id and vaga_id in current_url

        if not vaga_na_pagina:
            collection_url = f"https://www.linkedin.com/jobs/collections/easy-apply/?currentJobId={vaga_id}" if vaga_id else vaga_url
            await notify_browser_step("selenium_linkedin", "navegando", f"Abrindo vaga {vaga_id}")
            print(f"[LINKEDIN] Navegando para: {collection_url}")
            await navegar(collection_url)
        else:
            print(f"[LINKEDIN] Vaga {vaga_id} ja esta na pagina")

        try:
            await _dismissar_cookie_banner(driver)
        except Exception:
            pass

        await notify_browser_step("selenium_linkedin", "easy_apply", "Aguardando pagina carregar...")

        driver = await get_driver()
        for wait_i in range(20):
            await asyncio.sleep(1)
            try:
                n_btns = await _run_in_thread(lambda: driver.execute_script("return document.querySelectorAll('button').length"))
                if n_btns and n_btns > 10:
                    print(f"[LINKEDIN] SPA renderizado ({n_btns} botoes em {wait_i+1}s)")
                    break
            except Exception:
                pass
        else:
            print("[LINKEDIN] SPA demorou para renderizar, tentando mesmo assim...")

        current_url = await _run_in_thread(lambda: driver.current_url) if driver else ""
        if "checkpoint" in current_url.lower() or "challenge" in current_url.lower():
            print("[LINKEDIN] Bloqueio detectado ao navegar para vaga")
            resolvido = await _aguardar_resolucao_captcha(driver, "navegação para vaga")
            if not resolvido:
                b64 = await screenshot_base64()
                return {"sucesso": False, "mensagem": "Verificação de segurança não resolvida.", "screenshot": b64[:100] if b64 else ""}
            # Recarrega URL atual após resolução
            try:
                current_url = await _run_in_thread(lambda: driver.current_url)
            except Exception:
                current_url = ""

        print(f"[LINKEDIN] Vaga carregada: {await get_title()}")

        # Verifica se LinkedIn indica que a vaga já foi candidatada (badge Applied no botão)
        driver = await get_driver()
        if driver and await _verificar_ja_aplicado_na_pagina(driver):
            print(f"[LINKEDIN] Página indica vaga já candidatada — pulando")
            await notify_browser_step("selenium_linkedin", "pulada", "LinkedIn indica vaga já candidatada")
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            await asyncio.sleep(2)
            return {
                "sucesso": False,
                "pulada": True,
                "motivo_falha": "ja_aplicado_pagina",
                "mensagem": "Vaga já candidatada (detectado na página).",
            }

        # Avalia match com currículo e detecta idioma da vaga
        idioma_vaga = "pt"
        if resumo_curriculo:
            driver = await get_driver()
            if driver:
                descricao_vaga = await _extrair_descricao_vaga(driver)
                if descricao_vaga:
                    await notify_browser_step("selenium_linkedin", "avaliando", "Verificando compatibilidade com currículo...")
                    avaliacao = await _avaliar_match_vaga(descricao_vaga, resumo_curriculo)
                    idioma_vaga = avaliacao.get("idioma", "pt")
                    logger.info(
                        "linkedin_selenium: match=%s idioma=%s motivo=%s",
                        avaliacao.get("aplicar"), idioma_vaga, avaliacao.get("motivo")
                    )
                    if not avaliacao.get("aplicar", True):
                        motivo = avaliacao.get("motivo", "sem match")
                        print(f"[LINKEDIN] Pulando vaga — sem match: {motivo}")
                        await notify_browser_step("selenium_linkedin", "pulada", f"Sem match com currículo: {motivo}")
                        await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
                        await asyncio.sleep(2)
                        return {
                            "sucesso": False,
                            "pulada": True,
                            "motivo_falha": "sem_match",
                            "mensagem": f"Vaga ignorada (sem match): {motivo}",
                        }

        await notify_browser_step("selenium_linkedin", "easy_apply", "Procurando Easy Apply...")

        clicou = False
        driver = await get_driver()

        if driver:
            clicou = await _clicar_easy_apply_js(driver)

        if not clicou:
            print("[LINKEDIN] 1a tentativa JS falhou, aguardando mais...")
            await asyncio.sleep(5)
            driver = await get_driver()
            if driver:
                clicou = await _clicar_easy_apply_js(driver)

        if not clicou:
            print("[LINKEDIN] Botao Easy Apply NAO encontrado")
            await notify_browser_step("selenium_linkedin", "erro", "Easy Apply nao encontrado")
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            return {"sucesso": False, "mensagem": "Easy Apply nao encontrado nesta vaga.", "screenshot": b64[:100] if b64 else ""}

        print("[LINKEDIN] Clicou em Easy Apply")
        await asyncio.sleep(3)

        modal = await _aguardar_modal_easy_apply(driver)
        if not modal:
            print("[LINKEDIN] Modal Easy Apply nao apareceu — abortando vaga")
            await notify_browser_step("selenium_linkedin", "erro", "Modal nao apareceu")
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            return {"sucesso": False, "mensagem": "Modal Easy Apply nao apareceu.", "screenshot": b64[:100] if b64 else ""}

        print("[LINKEDIN] Modal aberto")

        await notify_browser_step("selenium_linkedin", "preenchendo", "Preenchendo formulário multi-step")
        resultado = await _processar_formulario_multistep_selenium(driver, perfil, curriculo_path, vaga_url, resumo_curriculo, idioma=idioma_vaga)
        return resultado

    except Exception as e:
        logger.error(f"aplicar_linkedin_selenium erro: {e}")
        await notify_browser_step("selenium_linkedin", "erro", str(e))
        print(f"[LINKEDIN] ERRO: {e}")
        try:
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
        except Exception:
            pass
        return {"sucesso": False, "mensagem": str(e)}


def _detectar_sucesso(html: str, url: str) -> bool:
    """Detecta se a candidatura foi enviada com sucesso."""
    html_lower = (html or "").lower()
    url_lower = (url or "").lower()
    sinais = [
        "application submitted", "candidatura enviada", "your application was sent",
        "applied", "candidatura realizada", "obrigado pela candidatura",
        "thank you for applying", "sua candidatura foi enviada", "candidatura recebida",
        "application sent", "we received your application",
    ]
    return any(s in html_lower or s in url_lower for s in sinais)


async def _processar_formulario_multistep_selenium(driver, perfil: dict, curriculo_path: str, vaga_url: str, resumo_curriculo: str, idioma: str = "pt") -> dict:
    """Processa formulario Easy Apply multi-step via Selenium."""
    from automation.browser import notify_browser_step, wait_if_paused, get_intervention_state, set_intervention_state

    perguntas_customizadas = []
    respostas_geradas = {}
    max_steps = 12

    try:
        vaga_titulo = ""
        try:
            vaga_titulo = driver.title or ""
        except Exception:
            pass

        for step in range(max_steps):
            # 0. Descarta qualquer modal de "Descartar candidatura" que tenha sobrado
            try:
                await _fechar_modal_descarte(driver)
            except Exception:
                pass

            # 1. Aguarda se pausado (sai tambem se 'pular' foi solicitado)
            control = await get_intervention_state()
            if control.get("paused") or control.get("intervention_type") == "manual":
                logger.info("linkedin_selenium: aguardando retomada no step %d", step)
                await notify_browser_step("step_"+str(step), "pausado", "Aguardando retomada pelo usuário...")
                await wait_if_paused(None, "step_"+str(step))

            # 2. Verifica pular (apos pausa E ao iniciar step — consome o sinal aqui)
            control = await get_intervention_state()
            if control.get("current_action") == "pular":
                logger.info("linkedin_selenium: usuario pediu para pular step %d", step)
                await set_intervention_state("current_action", "rodando")
                await notify_browser_step("step_"+str(step), "pulado", "Usuário pediu pular")
                continue

            await asyncio.sleep(random.uniform(0.8, 1.5))
            await notify_browser_step("step_"+str(step), "preenchendo", "Preenchendo campos do formulário")

            await _preencher_step_selenium(driver, perfil, curriculo_path)

            perguntas_step = await _detectar_perguntas_nao_respondidas_selenium(driver)
            pular_agora = False
            if perguntas_step:
                await notify_browser_step("step_"+str(step), "respondendo", f"{len(perguntas_step)} pergunta(s) customizada(s)")
                for pergunta in perguntas_step:
                    if pergunta not in respostas_geradas:
                        await wait_if_paused(None, "step_"+str(step))
                        # Pular pressionado durante resposta: sai sem clicar botao
                        ctrl = await get_intervention_state()
                        if ctrl.get("current_action") == "pular":
                            pular_agora = True
                            break
                        resposta = responder_pergunta(
                            pergunta, perfil,
                            vaga_titulo=vaga_titulo,
                            vaga_empresa="",
                            resumo_curriculo=resumo_curriculo,
                            idioma=idioma,
                        )
                        respostas_geradas[pergunta] = resposta
                        await notify_browser_step("step_"+str(step), "respondendo", f"Pergunta: {pergunta[:40]}...")
                        await _preencher_resposta_customizada_selenium(driver, pergunta, resposta)
                        perguntas_customizadas.append(pergunta)
                        await asyncio.sleep(random.uniform(0.5, 1.0))

            if pular_agora:
                await set_intervention_state("current_action", "rodando")
                await notify_browser_step("step_"+str(step), "pulado", "Usuário pediu pular")
                continue

            btn_text, btn_clicked = await _clicar_botao_primario_modal(driver)

            if not btn_clicked:
                # Pode haver um modal de descarte sobreposto — tenta dispensar e re-clicar
                dispensado = await _fechar_modal_descarte(driver)
                if dispensado:
                    await asyncio.sleep(1)
                    btn_text, btn_clicked = await _clicar_botao_primario_modal(driver)

                if not btn_clicked:
                    print(f"[LINKEDIN] Step {step}: nenhum botão encontrado no modal")
                    break

            print(f"[LINKEDIN] Step {step}: clicou botão '{btn_text}'")
            btn_lower = btn_text.lower()

            is_submit = any(s in btn_lower for s in [
                "submit", "enviar", "send", "finalizar",
            ])
            is_review = any(s in btn_lower for s in [
                "review", "revisar",
            ])

            if is_submit or is_review:
                await asyncio.sleep(3)
                await notify_browser_step("step_"+str(step), "enviando", f"Clicou: {btn_text}")
                try:
                    url_apos = driver.current_url
                except Exception:
                    url_apos = ""
                html = driver.page_source
                if _detectar_sucesso(html, url_apos):
                    logger.info("linkedin_selenium: candidatura enviada com sucesso")
                    await notify_browser_step("step_"+str(step), "sucesso", "Candidatura enviada!")
                    b64 = await screenshot_base64()
                    # Clica em Concluído antes de navegar
                    await _fechar_modal_sucesso(driver)
                    await asyncio.sleep(0.5)
                    await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
                    await asyncio.sleep(2)
                    return {
                        "sucesso": True,
                        "perguntas_respondidas": perguntas_customizadas,
                        "mensagem": "Candidatura enviada com sucesso via LinkedIn Easy Apply!",
                        "screenshot": b64[:100] if b64 else "",
                    }
                if is_review:
                    await asyncio.sleep(1)
                    continue
            else:
                await notify_browser_step("step_"+str(step), "navegando", f"Avançando: {btn_text}")
                await asyncio.sleep(2)
                continue

            break

        html = driver.page_source
        try:
            url_fim = driver.current_url
        except Exception:
            url_fim = ""
        if _detectar_sucesso(html, url_fim):
            b64 = await screenshot_base64()
            await _fechar_modal_sucesso(driver)
            await asyncio.sleep(0.5)
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            await asyncio.sleep(2)
            return {
                "sucesso": True,
                "perguntas_respondidas": perguntas_customizadas,
                "mensagem": "Candidatura enviada com sucesso via LinkedIn Easy Apply!",
                "screenshot": b64[:100] if b64 else "",
            }

        b64 = await screenshot_base64()
        # Tenta fechar qualquer modal aberto antes de navegar
        try:
            await _fechar_modal_sucesso(driver)
        except Exception:
            pass
        await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
        await asyncio.sleep(2)
        return {
            "sucesso": False,
            "motivo_falha": "formulario_incompleto",
            "mensagem": f"Nao consegui completar o formulario. Candidate-se manualmente: {vaga_url}",
            "screenshot": b64[:100] if b64 else "",
        }
    except Exception as e:
        logger.error(f"_processar_formulario_multistep_selenium erro: {e}")
        try:
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            await asyncio.sleep(2)
        except Exception:
            pass
        return {
            "sucesso": False,
            "motivo_falha": "erro_tecnico",
            "mensagem": f"Erro no formulario. Candidate-se manualmente: {vaga_url}",
        }


async def _preencher_step_selenium(driver, perfil: dict, curriculo_path: str) -> None:
    """Preenche campos conhecidos no step atual via Selenium.
    Usa find_elements (sem timeout) para não bloquear em campos ausentes."""
    nome_completo = perfil.get("nome", "")
    partes = nome_completo.split()
    primeiro_nome = partes[0] if partes else nome_completo
    ultimo_nome = " ".join(partes[1:]) if len(partes) >= 2 else ""

    # Mapa: lista de seletores CSS → valor
    campos = [
        (["input[name='firstName']", "#first-name", "input[id*='firstName']"], primeiro_nome),
        (["input[name='lastName']", "#last-name", "input[id*='lastName']"], ultimo_nome),
        (["input[name='email']", "#email", "input[type='email'][name*='email']"], perfil.get("email", _get_linkedin_email())),
        (["input[name='phone']", "#phone", "input[type='tel']", "input[aria-label*='Phone Number']"], str(perfil.get("telefone", perfil.get("phone", "")))),
        (["input[aria-label*='City']", "input[aria-label*='Cidade']", "input[name*='city']"], perfil.get("localizacao", "").split(",")[0].strip() if perfil.get("localizacao") else ""),
    ]

    def _preencher_campos():
        for seletores, texto in campos:
            if not texto:
                continue
            for sel in seletores:
                try:
                    els = driver.find_elements(By.CSS_SELECTOR, sel)
                    for el in els:
                        if el.is_displayed() and el.is_enabled():
                            current = el.get_attribute("value") or ""
                            if current.strip():
                                break  # já preenchido
                            el.click()
                            el.clear()
                            el.send_keys(texto[:100])
                            print(f"[LINKEDIN] Preencheu {sel[:40]} = {texto[:30]}")
                            break
                    else:
                        continue
                    break
                except Exception:
                    continue

    await _run_in_thread(_preencher_campos)

    # Seleciona CV em inglês se houver múltiplos CVs já enviados ao LinkedIn
    def _selecionar_cv_ingles():
        """LinkedIn mostra CVs anteriores como radio/checkbox. Prefere o inglês (segundo item)."""
        _en_kw = {"english", "inglês", "ingles", "en_", "_en.", "-en.", "en-", "(en)"}
        # Seletores comuns para escolha de resume no Easy Apply
        for sel in [
            "input[type='radio']",
            "input[type='checkbox']",
        ]:
            opts = driver.find_elements(By.CSS_SELECTOR, sel)
            # Filtra só os que estão dentro de containers de resume/document
            resume_opts = []
            for opt in opts:
                try:
                    opt_id = opt.get_attribute("id") or ""
                    name = (opt.get_attribute("name") or "").lower()
                    # Pega label associada
                    lbl_text = ""
                    if opt_id:
                        lbl_els = driver.find_elements(By.CSS_SELECTOR, f"label[for='{opt_id}']")
                        if lbl_els:
                            lbl_text = (lbl_els[0].text or "").lower()
                    if not lbl_text:
                        try:
                            parent = opt.find_element(By.XPATH, "./..")
                            lbl_text = (parent.text or "").lower()
                        except Exception:
                            pass
                    # Só considera se label tem extensão de arquivo ou palavra "resume"/"cv"
                    is_resume_field = (
                        any(ext in lbl_text for ext in [".pdf", ".doc", ".docx"])
                        or "resume" in lbl_text or "curriculum" in lbl_text
                        or "currículo" in lbl_text or "cv" in lbl_text
                    )
                    if is_resume_field:
                        resume_opts.append((opt, opt_id, lbl_text))
                except Exception:
                    continue

            if len(resume_opts) >= 2:
                # Tenta achar o que tem indicação de inglês
                for opt, opt_id, lbl_text in resume_opts:
                    if any(kw in lbl_text for kw in _en_kw):
                        _clicar_radio_via_label(driver, opt)
                        print(f"[LINKEDIN] Selecionou CV inglês: {lbl_text[:50]}")
                        return True
                # Não achou por label — usa o SEGUNDO (índice 1, presumivelmente EN)
                opt, opt_id, lbl_text = resume_opts[1]
                _clicar_radio_via_label(driver, opt)
                print(f"[LINKEDIN] Selecionou segundo CV (EN presumido): {lbl_text[:50]}")
                return True
        return False

    try:
        await _run_in_thread(_selecionar_cv_ingles)
    except Exception:
        pass

    if curriculo_path:
        try:
            def _upload():
                file_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
                for fi in file_inputs:
                    if fi.is_displayed():
                        fi.send_keys(curriculo_path)
                        print(f"[LINKEDIN] Upload curriculo: {curriculo_path}")
                        return True
                return False
            if await _run_in_thread(_upload):
                await asyncio.sleep(1.5)
        except Exception:
            pass


_SELECT_PLACEHOLDER_TEXTS = {
    "", "select an option", "selecione uma opção", "selecione", "--", "select",
    "choose", "selecione uma resposta", "selecionar uma opção", "escolha uma opção",
    "nenhum selecionado", "select a response", "please select", "escolha",
    "seleccione", "selecionar", "escolher",
}

_DECIMAL_LABEL_KEYWORDS = {
    "salary", "salário", "wage", "remuneração", "taxa", "rate", "compensation",
    "pay", "valor", "price", "preço", "gpa", "nota", "score",
}
_INTEGER_LABEL_KEYWORDS = {
    "years", "months", "anos", "meses", "quantos", "quanto", "how many",
    "number of", "quantidade", "experiencia", "experience",
}


async def _detectar_perguntas_nao_respondidas_selenium(driver) -> list:
    """Detecta todos os campos de pergunta ainda vazios no step atual via Selenium.
    Inclui text, number (NUMERO/DECIMAL), textarea, select, combobox e radio."""
    perguntas = []
    seen_labels: set = set()

    def _add(pergunta: str):
        # Evita duplicatas pela label base
        base = re.sub(r'^(NUMERO|DECIMAL|SELECT|RADIO|COMBO):', '', pergunta).split(":")[0][:40]
        if base not in seen_labels:
            seen_labels.add(base)
            perguntas.append(pergunta)

    try:
        inputs = driver.find_elements(By.CSS_SELECTOR,
            "input[type='text'], input[type='number'], textarea"
        )
        for inp in inputs:
            if not inp.is_displayed():
                continue
            label = await _get_label_selenium(driver, inp)
            if not label:
                continue
            label_lower = label.lower()
            if any(p in label_lower for p in _CAMPOS_PADRAO):
                continue
            current_val = ""
            try:
                current_val = inp.get_attribute("value") or ""
            except Exception:
                pass
            if current_val:
                continue

            input_type = ""
            step_val = ""
            min_val = ""
            try:
                input_type = (inp.get_attribute("type") or "").lower()
                step_val = (inp.get_attribute("step") or "").strip()
                min_val = (inp.get_attribute("min") or "").strip()
            except Exception:
                pass

            # Detecta se é campo numérico pelo tipo OU palavras-chave na label
            _all_numeric_kw = _DECIMAL_LABEL_KEYWORDS | _INTEGER_LABEL_KEYWORDS
            is_numeric = (
                input_type == "number"
                or any(kw in label_lower for kw in _all_numeric_kw)
            )

            if is_numeric:
                # Distingue decimal de inteiro pelo atributo step/min OU pela label
                step_has_decimal = "." in step_val and step_val not in ("", "any", "1")
                min_has_decimal = "." in min_val
                label_decimal = any(kw in label_lower for kw in _DECIMAL_LABEL_KEYWORDS)
                label_integer = any(kw in label_lower for kw in _INTEGER_LABEL_KEYWORDS)
                if step_has_decimal or min_has_decimal or (label_decimal and not label_integer):
                    _add(f"DECIMAL:{label}")
                else:
                    _add(f"NUMERO:{label}")
            else:
                _add(label)

        # Selects nativos
        selects = driver.find_elements(By.CSS_SELECTOR, "select")
        for sel_elem in selects:
            if not sel_elem.is_displayed():
                continue
            try:
                from selenium.webdriver.support.ui import Select
                sel_obj = Select(sel_elem)
                first_opt = sel_obj.first_selected_option
                selected_text = (first_opt.text or "").strip().lower()
                first_val = (first_opt.get_attribute("value") or "").strip()
                if selected_text in _SELECT_PLACEHOLDER_TEXTS or first_val == "":
                    label = await _get_label_selenium(driver, sel_elem)
                    if label and not any(p in label.lower() for p in _CAMPOS_PADRAO):
                        options_text = [
                            o.text.strip() for o in sel_obj.options
                            if o.text.strip() and o.text.strip().lower() not in _SELECT_PLACEHOLDER_TEXTS
                        ]
                        if options_text:
                            _add(f"SELECT:{label}:{','.join(options_text[:10])}")
            except Exception:
                pass

        # LinkedIn custom comboboxes (role=combobox / aria-haspopup=listbox)
        combo_sels = "[role='combobox'], button[aria-haspopup='listbox'], button[aria-haspopup='true'][aria-expanded]"
        comboboxes = driver.find_elements(By.CSS_SELECTOR, combo_sels)
        for cb in comboboxes:
            if not cb.is_displayed():
                continue
            try:
                # Verifica se já tem valor selecionado
                current_text = (cb.text or cb.get_attribute("aria-label") or "").strip().lower()
                if current_text and current_text not in _SELECT_PLACEHOLDER_TEXTS:
                    continue  # já preenchido
                # Busca label do combobox
                label = await _get_label_selenium(driver, cb)
                if not label:
                    # Tenta via aria-labelledby
                    aria_by = cb.get_attribute("aria-labelledby") or ""
                    for aid in aria_by.split():
                        el = driver.find_elements(By.ID, aid)
                        if el:
                            label = (el[0].text or "").strip()
                            break
                if not label:
                    continue
                if any(p in label.lower() for p in _CAMPOS_PADRAO):
                    continue
                # Coleta opções disponíveis
                options_text = []
                try:
                    opts = driver.find_elements(By.CSS_SELECTOR, "[role='option'], [role='listbox'] li")
                    options_text = [o.text.strip() for o in opts if o.text.strip()]
                except Exception:
                    pass
                suffix = f":{','.join(options_text[:10])}" if options_text else ""
                _add(f"COMBO:{label}{suffix}")
            except Exception:
                continue

        # Fieldsets com radio buttons
        fieldsets = driver.find_elements(By.CSS_SELECTOR, "fieldset")
        for fs in fieldsets:
            if not fs.is_displayed():
                continue
            try:
                legend = fs.find_elements(By.CSS_SELECTOR, "legend, span.fb-dash-form-element__label")
                if not legend:
                    continue
                legend_text = (legend[0].text or "").strip()
                if not legend_text or any(p in legend_text.lower() for p in _CAMPOS_PADRAO):
                    continue
                # LinkedIn esconde os inputs com CSS; não filtra por is_displayed()
                radios = fs.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                if radios:
                    # Verifica se algum já está selecionado (is_selected() funciona mesmo hidden)
                    any_checked = any(r.is_selected() for r in radios)
                    if not any_checked:
                        options_text = []
                        for r in radios:
                            r_label = ""
                            r_id = r.get_attribute("id")
                            if r_id:
                                lbl = driver.find_elements(By.CSS_SELECTOR, f"label[for='{r_id}']")
                                if lbl:
                                    r_label = (lbl[0].text or "").strip()
                            if not r_label:
                                try:
                                    parent = r.find_element(By.XPATH, "./..")
                                    r_label = (parent.text or "").strip()
                                except Exception:
                                    pass
                            if r_label and r_label not in options_text:
                                options_text.append(r_label)
                        if options_text:
                            _add(f"RADIO:{legend_text}:{','.join(options_text[:10])}")
            except Exception:
                continue
    except Exception:
        pass
    return perguntas[:12]


async def _preencher_resposta_customizada_selenium(driver, pergunta: str, resposta: str) -> None:
    """Preenche campo de pergunta customizada pela label via Selenium.
    Suporta text, number, textarea, select, radio, NUMERO (int), DECIMAL (float) e COMBO."""
    try:
        if pergunta.startswith("SELECT:"):
            parts = pergunta.split(":", 2)
            label_text = parts[1] if len(parts) > 1 else ""
            await _preencher_select_selenium(driver, label_text, resposta)
            return

        if pergunta.startswith("RADIO:"):
            parts = pergunta.split(":", 2)
            label_text = parts[1] if len(parts) > 1 else ""
            await _preencher_radio_selenium(driver, label_text, resposta)
            return

        if pergunta.startswith("COMBO:"):
            parts = pergunta.split(":", 2)
            label_text = parts[1] if len(parts) > 1 else ""
            await _preencher_combobox_linkedin(driver, label_text, resposta)
            return

        # NUMERO: inteiro — extrai o primeiro número inteiro da resposta
        is_numero = pergunta.startswith("NUMERO:")
        # DECIMAL: float — extrai número com ponto decimal
        is_decimal = pergunta.startswith("DECIMAL:")

        if is_numero:
            pergunta_busca = pergunta[7:].strip()
            m = re.search(r'\b\d+\b', resposta)
            resposta = m.group() if m else re.sub(r'[^\d]', '', resposta) or "1"
        elif is_decimal:
            pergunta_busca = pergunta[8:].strip()
            m = re.search(r'\d+[.,]\d+|\d+', resposta)
            if m:
                resposta = m.group().replace(',', '.')
            else:
                resposta = re.sub(r'[^\d.]', '', resposta) or "1.0"
        else:
            pergunta_busca = pergunta

        labels = driver.find_elements(By.CSS_SELECTOR, "label")
        for label in labels:
            try:
                label_text = (label.text or "").lower()
            except Exception:
                continue
            if pergunta_busca.lower()[:30] in label_text:
                label_for = label.get_attribute("for")
                if label_for:
                    el = driver.find_elements(By.ID, label_for)
                    if el and el[0].is_displayed():
                        tag = el[0].tag_name.lower()
                        input_type = (el[0].get_attribute("type") or "").lower()
                        if tag == "textarea":
                            el[0].clear()
                            el[0].send_keys(resposta[:500])
                            print(f"[LINKEDIN] Preencheu textarea: {pergunta_busca[:40]} = {resposta[:30]}")
                        elif input_type == "number" or is_numero or is_decimal:
                            # resposta já foi formatada (inteiro ou decimal) antes deste ponto
                            el[0].clear()
                            el[0].send_keys(resposta)
                            print(f"[LINKEDIN] Preencheu number: {pergunta_busca[:40]} = {resposta}")
                        else:
                            el[0].clear()
                            el[0].send_keys(resposta[:200])
                            print(f"[LINKEDIN] Preencheu text: {pergunta_busca[:40]} = {resposta[:30]}")
                        return
    except Exception as e:
        logger.debug("linkedin_selenium: erro ao preencher resposta: %s", e)


async def _preencher_combobox_linkedin(driver, label_text: str, resposta: str) -> bool:
    """Preenche combobox customizado do LinkedIn (role=combobox / aria-haspopup=listbox).
    Clica no trigger, aguarda a lista de opções e seleciona a que mais combina com a resposta."""
    import time

    def _find_trigger():
        seletores = "[role='combobox'], button[aria-haspopup='listbox'], button[aria-haspopup='true'][aria-expanded]"
        for cb in driver.find_elements(By.CSS_SELECTOR, seletores):
            if not cb.is_displayed():
                continue
            # Busca label via for, aria-labelledby ou container
            cb_id = cb.get_attribute("id") or ""
            label_found = ""
            if cb_id:
                lbs = driver.find_elements(By.CSS_SELECTOR, f"label[for='{cb_id}']")
                if lbs:
                    label_found = (lbs[0].text or "").strip()
            if not label_found:
                aria_by = cb.get_attribute("aria-labelledby") or ""
                for aid in aria_by.split():
                    el = driver.find_elements(By.ID, aid)
                    if el:
                        label_found = (el[0].text or "").strip()
                        break
            if not label_found:
                try:
                    container = cb.find_element(By.XPATH,
                        "./ancestor::div[contains(@class,'fb-') or contains(@class,'easy-apply') or contains(@class,'jobs-')][1]"
                    )
                    lbs = container.find_elements(By.CSS_SELECTOR, "label, legend, span[class*='label']")
                    if lbs:
                        label_found = (lbs[0].text or "").strip()
                except Exception:
                    pass
            if label_text.lower()[:25] in label_found.lower() or label_found.lower()[:25] in label_text.lower():
                return cb
        return None

    def _click_option(resposta_lower: str):
        # Opções visíveis após abrir o combobox
        for sel in [
            "[role='option']", "[role='listbox'] li", ".artdeco-dropdown__item",
            ".basic-typeahead__selectable", "li[data-test-text-selectable-option]",
        ]:
            opts = driver.find_elements(By.CSS_SELECTOR, sel)
            visible = [o for o in opts if o.is_displayed() and (o.text or "").strip()]
            if not visible:
                continue
            # Correspondência exata
            for opt in visible:
                if opt.text.strip().lower() == resposta_lower:
                    opt.click()
                    return True
            # Correspondência parcial
            for opt in visible:
                t = opt.text.strip().lower()
                if resposta_lower in t or t in resposta_lower:
                    opt.click()
                    return True
            # "Sim"/"Não" heurística: se a resposta sugere positivo, pega a 1ª opção
            if resposta_lower in ("sim", "yes", "s", "y") and visible:
                visible[0].click()
                return True
            if resposta_lower in ("não", "nao", "no", "n") and len(visible) >= 2:
                visible[1].click()
                return True
            # Fallback: primeira opção disponível
            visible[0].click()
            return True
        return False

    try:
        trigger = await _run_in_thread(_find_trigger)
        if not trigger:
            # Tenta via <select> como fallback
            await _preencher_select_selenium(driver, label_text, resposta)
            return False

        await _run_in_thread(trigger.click)
        await asyncio.sleep(0.8)

        resultado = await _run_in_thread(lambda: _click_option(resposta.strip().lower()))
        if resultado:
            print(f"[LINKEDIN] Preencheu combobox: {label_text[:40]} = {resposta[:30]}")
        else:
            print(f"[LINKEDIN] Combobox: nenhuma opção encontrada para '{label_text[:30]}'")
        return resultado
    except Exception as e:
        logger.debug("_preencher_combobox_linkedin erro: %s", e)
        return False


async def _preencher_select_selenium(driver, label_text: str, resposta: str) -> None:
    """Preenche um campo select baseado na resposta da IA."""
    try:
        from selenium.webdriver.support.ui import Select
        selects = driver.find_elements(By.CSS_SELECTOR, "select")
        for sel_elem in selects:
            if not sel_elem.is_displayed():
                continue
            sel_label = await _get_label_selenium(driver, sel_elem)
            if not sel_label or label_text.lower()[:30] not in sel_label.lower():
                continue
            sel_obj = Select(sel_elem)
            options = [(o.get_attribute("value"), o.text.strip()) for o in sel_obj.options if o.text.strip()]
            resposta_lower = resposta.strip().lower()
            for val, text in options:
                if resposta_lower == text.lower() or resposta_lower in text.lower() or text.lower() in resposta_lower:
                    sel_obj.select_by_value(val)
                    print(f"[LINKEDIN] Select: {label_text[:30]} = {text}")
                    return
            for val, text in options:
                if text.lower() not in ("", "select an option", "selecione uma opção", "selecione", "--", "select", "choose"):
                    sel_obj.select_by_value(val)
                    print(f"[LINKEDIN] Select fallback: {label_text[:30]} = {text}")
                    return
    except Exception as e:
        logger.debug("linkedin_selenium: erro ao preencher select: %s", e)


def _clicar_radio_via_label(driver, radio_input) -> bool:
    """Clica num radio button via label ou JS — funciona mesmo quando o input está hidden pelo CSS."""
    r_id = radio_input.get_attribute("id")
    if r_id:
        lbls = driver.find_elements(By.CSS_SELECTOR, f"label[for='{r_id}']")
        if lbls:
            try:
                driver.execute_script("arguments[0].click();", lbls[0])
                return True
            except Exception:
                pass
    # Fallback: JS click no próprio input
    try:
        driver.execute_script("arguments[0].click();", radio_input)
        return True
    except Exception:
        pass
    # Último recurso: click normal
    try:
        radio_input.click()
        return True
    except Exception:
        pass
    return False


async def _preencher_radio_selenium(driver, label_text: str, resposta: str) -> None:
    """Preenche um campo radio button baseado na resposta da IA.
    Clica no <label> associado (funciona com inputs hidden do LinkedIn)."""

    def _fill():
        fieldsets = driver.find_elements(By.CSS_SELECTOR, "fieldset")
        for fs in fieldsets:
            if not fs.is_displayed():
                continue
            legend = fs.find_elements(By.CSS_SELECTOR, "legend, span.fb-dash-form-element__label")
            if not legend:
                continue
            legend_text = (legend[0].text or "").strip()
            # Verifica se o fieldset corresponde à label buscada
            if label_text.lower()[:35] not in legend_text.lower() and legend_text.lower()[:35] not in label_text.lower():
                continue
            radios = fs.find_elements(By.CSS_SELECTOR, "input[type='radio']")
            resposta_lower = resposta.strip().lower()

            # Monta mapa radio → texto da label
            radio_opcoes = []
            for r in radios:
                r_label = ""
                r_id = r.get_attribute("id")
                if r_id:
                    lbl = driver.find_elements(By.CSS_SELECTOR, f"label[for='{r_id}']")
                    if lbl:
                        r_label = (lbl[0].text or "").strip()
                if not r_label:
                    try:
                        parent = r.find_element(By.XPATH, "./..")
                        r_label = (parent.text or "").strip()
                    except Exception:
                        pass
                radio_opcoes.append((r, r_label))

            # 1ª tentativa: correspondência exata
            for r, r_label in radio_opcoes:
                if r_label.lower() == resposta_lower:
                    if _clicar_radio_via_label(driver, r):
                        print(f"[LINKEDIN] Radio exato: {legend_text[:30]} = {r_label}")
                        return True

            # 2ª tentativa: correspondência parcial
            for r, r_label in radio_opcoes:
                if r_label and (resposta_lower in r_label.lower() or r_label.lower() in resposta_lower):
                    if _clicar_radio_via_label(driver, r):
                        print(f"[LINKEDIN] Radio parcial: {legend_text[:30]} = {r_label}")
                        return True

            # 3ª tentativa: heurística Sim/Não (Yes=primeiro, No=segundo)
            sim_words = {"sim", "yes", "s", "y", "true", "verdadeiro"}
            nao_words = {"não", "nao", "no", "n", "false", "falso"}
            if resposta_lower in sim_words and radio_opcoes:
                if _clicar_radio_via_label(driver, radio_opcoes[0][0]):
                    print(f"[LINKEDIN] Radio heurística Sim: {legend_text[:30]}")
                    return True
            if resposta_lower in nao_words and len(radio_opcoes) >= 2:
                if _clicar_radio_via_label(driver, radio_opcoes[1][0]):
                    print(f"[LINKEDIN] Radio heurística Não: {legend_text[:30]}")
                    return True

            # Fallback: clica no primeiro
            if radio_opcoes:
                if _clicar_radio_via_label(driver, radio_opcoes[0][0]):
                    print(f"[LINKEDIN] Radio fallback primeiro: {legend_text[:30]}")
                    return True

        return False

    try:
        await _run_in_thread(_fill)
    except Exception as e:
        logger.debug("linkedin_selenium: erro ao preencher radio: %s", e)


async def _aguardar_modal_easy_apply(driver, timeout: int = 12):
    """Aguarda o modal Easy Apply aparecer. Retorna elemento ou None."""
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    seletores = [
        (By.CSS_SELECTOR, "div[role='dialog']"),
        (By.CSS_SELECTOR, ".artdeco-modal"),
        (By.CSS_SELECTOR, ".jobs-easy-apply-modal"),
        (By.CSS_SELECTOR, ".jobs-easy-apply-content"),
    ]
    for by, value in seletores:
        try:
            el = await _run_in_thread(
                lambda b=by, v=value: WebDriverWait(driver, timeout).until(
                    EC.visibility_of_element_located((b, v))
                )
            )
            if el:
                print(f"[LINKEDIN] Modal detectado: {value}")
                return el
        except Exception:
            continue
    return None


async def _clicar_botao_primario_modal(driver) -> tuple:
    """Encontra e clica o botão primário (ação principal) do modal Easy Apply.
    Usa Selenium find_elements diretamente — mais confiável que JS.
    Retorna (texto_botao, clicou_bool)."""

    def _selenium_find_and_click():
        # Localiza o container do modal
        container = driver
        for sel in ['div[role="dialog"]', '.artdeco-modal', '.jobs-easy-apply-modal']:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                container = els[-1]
                break

        # 1. Botão primário do artdeco (azul) — é o "Avançar"/"Enviar" do LinkedIn
        primary_btns = container.find_elements(By.CSS_SELECTOR, "button.artdeco-button--primary")
        for btn in primary_btns:
            try:
                if btn.is_displayed() and btn.is_enabled():
                    text = (btn.text or btn.get_attribute("aria-label") or "").strip()
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    driver.execute_script("arguments[0].click();", btn)
                    return text or "primary_button"
            except Exception:
                continue

        # 2. Botão com data-easy-apply-next-button
        next_btns = container.find_elements(By.CSS_SELECTOR, "button[data-easy-apply-next-button]")
        for btn in next_btns:
            try:
                if btn.is_displayed() and btn.is_enabled():
                    text = (btn.text or "").strip()
                    driver.execute_script("arguments[0].click();", btn)
                    return text or "Next"
            except Exception:
                continue

        # 3. XPath por texto dentro do modal — Avançar / Next / Continuar / Enviar / Submit / Revisar / Review
        is_element = hasattr(container, "find_elements") and container is not driver
        xpath_prefix = ".//" if is_element else "//"
        for kw in ["Avançar", "Next", "Continuar", "Continue", "Enviar candidatura",
                   "Submit application", "Submit", "Revisar", "Review"]:
            try:
                btns = container.find_elements(By.XPATH, f'{xpath_prefix}button[contains(., "{kw}")]')
                for btn in btns:
                    if btn.is_displayed() and btn.is_enabled():
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        driver.execute_script("arguments[0].click();", btn)
                        return kw
            except Exception:
                continue

        return None

    try:
        result = await _run_in_thread(_selenium_find_and_click)
        if result:
            print(f"[LINKEDIN] Clicou botão primário via Selenium: '{result}'")
            return (result, True)
        print("[LINKEDIN] Nenhum botão primário encontrado no modal")
    except Exception as e:
        print(f"[LINKEDIN] _clicar_botao_primario_modal falhou: {e}")

    return ("", False)


async def _clicar_submit_selenium(driver, timeout: int = 5) -> bool:
    """Clica no botao Submit/Review/Enviar."""
    seletores = [
        "button[aria-label='Submit application']",
        "button[aria-label='Enviar candidatura']",
        "button[aria-label='Review your application']",
        "button[aria-label='Revisar sua candidatura']",
        "button[data-control-name='submit_apply']",
        "//button[contains(., 'Enviar candidatura')]",
        "//button[contains(., 'Submit application')]",
        "//button[contains(., 'Revisar')]",
        "//button[contains(., 'Review')]",
    ]
    return await click_qualquer_selenium(driver, seletores, timeout)


async def _clicar_next_selenium(driver, timeout: int = 5) -> bool:
    """Clica no botao Next/Continuar/Avançar."""
    seletores = [
        "button[aria-label='Continue to next step']",
        "button[aria-label='Continuar para a proxima etapa']",
        "button[data-easy-apply-next-button]",
        "button.artdeco-button--primary[data-easy-apply-next-button]",
        "//button[contains(., 'Next')]",
        "//button[contains(., 'Avançar')]",
        "//button[contains(., 'Continuar')]",
        "//button[contains(., 'Continue')]",
    ]
    return await click_qualquer_selenium(driver, seletores, timeout)


async def click_qualquer_selenium(driver, seletores: list, timeout: int = 5) -> bool:
    """Tenta clicar em qualquer um dos seletores fornecidos, esperando o elemento ficar visível."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    def _partes(sel):
        sel = sel.strip()
        if sel.startswith("//"):
            return [sel]
        return [p.strip() for p in sel.split(",") if p.strip()]

    for sel in seletores:
        for parte in _partes(sel):
            try:
                result = _try_convert_selector(parte)
                if result is None:
                    continue
                by, value = result
                el = WebDriverWait(driver, timeout).until(
                    EC.element_to_be_clickable((by, value))
                )
                el.click()
                print(f"[LINKEDIN] Clicou via element_to_be_clickable: {parte[:60]}")
                return True
            except Exception:
                continue
    for sel in seletores:
        for parte in _partes(sel):
            try:
                result = _try_convert_selector(parte)
                if result is None:
                    continue
                by, value = result
                el = WebDriverWait(driver, timeout).until(
                    EC.visibility_of_element_located((by, value))
                )
                driver.execute_script("arguments[0].click();", el)
                print(f"[LINKEDIN] Clicou via JS fallback: {parte[:60]}")
                return True
            except Exception:
                continue
    return False


async def _get_label_selenium(driver, inp) -> str:
    """Retorna label associada a um input via Selenium."""
    try:
        input_id = inp.get_attribute("id")
        if input_id:
            labels = driver.find_elements(By.CSS_SELECTOR, f"label[for='{input_id}']")
            if labels:
                return (labels[0].text or "").strip()
        aria = inp.get_attribute("aria-label")
        if aria:
            return aria.strip()
        placeholder = inp.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()
    except Exception:
        pass
    return ""


async def aplicar_vagas_visiveis_na_pagina(perfil: dict, max_vagas: int = 5, user_id: str = "admin") -> dict:
    """
    Aplica em vagas visíveis na página atual do LinkedIn.
    Usa a página já aberta (busca, easy-apply, etc.) sem forçar navegação.
    Verifica se já se candidatou antes de aplicar.
    Navega de volta para a página de jobs após cada aplicação.
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

        if "jobs" not in url_lower:
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            await asyncio.sleep(3)

        await notify_browser_step("selenium_linkedin", "iniciando", f"Página atual: {current_url}")
        await notify_browser_step("selenium_linkedin", "iniciando", f"Resumo curriculo: {'SIM' if (perfil.get('resumo_curriculo') or await _get_resumo_curriculo(user_id)) else 'NAO'}")

        resultados = {"sucesso": True, "aplicacoes": [], "falhas": 0}
        vagas_tentadas_sessao: set = set()  # evita re-tentar mesma vaga na sessão

        for i in range(max_vagas):
            # Espera se pausado (nao avanca o contador nem pula a vaga)
            await wait_if_paused(None, f"vaga_{i+1}")

            await notify_browser_step("selenium_linkedin", f"vaga_{i+1}", f"Procurando vaga {i+1}...")

            # Garante que o browser está na página de vagas antes de buscar
            try:
                _drv = await get_driver()
                if not _drv or not await _driver_session_valida():
                    print("[LINKEDIN] Sessão do browser inválida — reabrindo")
                    await nova_pagina("https://www.linkedin.com/jobs/collections/easy-apply/", reutilizar=False)
                    await asyncio.sleep(4)
                    if not await _garantir_login():
                        break
                else:
                    _cur = await _run_in_thread(lambda: _drv.current_url)
                    if "jobs" not in _cur.lower() or "checkpoint" in _cur.lower():
                        await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
                        await asyncio.sleep(3)
            except Exception as _nav_e:
                logger.warning("linkedin_selenium: erro ao verificar página: %s", _nav_e)

            vaga_url = await _extrair_proxima_vaga_da_busca(vagas_tentadas_sessao)
            if not vaga_url:
                # Tenta uma vez mais após scroll para o topo (recarrega cards)
                try:
                    _drv = await get_driver()
                    if _drv:
                        await _run_in_thread(lambda: _drv.execute_script("window.scrollTo(0,0)"))
                        await asyncio.sleep(1.5)
                        vaga_url = await _extrair_proxima_vaga_da_busca(vagas_tentadas_sessao)
                except Exception:
                    pass
            if not vaga_url:
                await notify_browser_step("selenium_linkedin", "finalizando", "Nenhuma vaga Easy Apply disponível")
                break

            vaga_id = _extrair_vaga_id(vaga_url)
            vagas_tentadas_sessao.add(vaga_id or vaga_url)

            if vaga_id:
                try:
                    from graph.neo4j_client import get_neo4j
                    neo4j = get_neo4j()
                    ja_aplicou = neo4j.ja_se_candidatou(user_id, vaga_id)
                    if ja_aplicou:
                        print(f"[LINKEDIN] Ja candidatou para {vaga_id} — pulando")
                        await notify_browser_step("selenium_linkedin", f"vaga_{i+1}", "Ja aplicou — pulando")
                        continue
                except Exception:
                    pass

            await notify_browser_step("selenium_linkedin", f"vaga_{i+1}", f"Aplicando em: {vaga_url[:60]}...")

            resultado = await aplicar(vaga_url, perfil, user_id=user_id)
            resultados["aplicacoes"].append(resultado)
            if not resultado.get("sucesso"):
                resultados["falhas"] += 1

            # Registra no Neo4j para evitar re-aplicação (exceto vagas ignoradas por sem_match)
            if vaga_id and not resultado.get("pulada"):
                try:
                    from graph.neo4j_client import get_neo4j
                    _neo4j = get_neo4j()
                    _status = "candidatado" if resultado.get("sucesso") else "tentativa_falhou"
                    _neo4j.registrar_candidatura(
                        user_id=user_id,
                        vaga_id=vaga_id,
                        plataforma="linkedin",
                        status=_status,
                    )
                except Exception as _e:
                    logger.warning("linkedin_selenium: erro ao registrar candidatura: %s", _e)

            driver = await get_driver()
            if driver:
                try:
                    cur = driver.current_url
                    if "jobs" not in cur.lower():
                        await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
                        await asyncio.sleep(2)
                except Exception:
                    pass

                try:
                    body = driver.find_element(By.TAG_NAME, "body")
                    await _scroll_and_focus_element(driver, body)
                    await asyncio.sleep(1)
                except Exception:
                    pass

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


def _extrair_vaga_id(url: str) -> str:
    if not url:
        return ""
    if "/jobs/view/" in url:
        return url.split("/jobs/view/")[1].split("/")[0].split("?")[0]
    return url


def _limpar_url_vaga(url: str) -> str:
    if not url:
        return url
    vaga_id = _extrair_vaga_id(url)
    if vaga_id:
        return f"https://www.linkedin.com/jobs/view/{vaga_id}/"
    return url


async def _dismissar_cookie_banner(driver) -> None:
    try:
        seletores = [
            'button[data-control-name="ga_cookie_consent.accept"]',
            'button[data-control-name="cookie_banner_dismiss"]',
            'button.artdeco-button--primary[data-control-name="cookie_banner_dismiss"]',
            'button[aria-label*="Aceitar"]',
            'button[aria-label*="Accept"]',
            '[data-test-id="cookie-banner-accept"]',
            '.cookie-banner-accept',
        ]
        for sel in seletores:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                if btn.is_displayed() and btn.is_enabled():
                    await _run_in_thread(btn.click)
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                continue
    except Exception:
        pass


async def _fechar_modal_sucesso(driver) -> bool:
    """Clica em 'Concluído'/'Done' no modal de confirmação pós-candidatura do LinkedIn.
    Retorna True se o botão foi encontrado e clicado."""
    done_labels = [
        "concluído", "concluido", "done", "fechar", "close", "ok", "dismiss",
    ]

    def _click_done():
        # Tenta encontrar o botão Concluído em qualquer dialog visível
        for sel in ["div[role='dialog']", ".artdeco-modal", ".jobs-easy-apply-modal", "body"]:
            containers = driver.find_elements(By.CSS_SELECTOR, sel)
            for container in containers:
                try:
                    btns = container.find_elements(By.TAG_NAME, "button")
                    for btn in btns:
                        t = (btn.text or btn.get_attribute("aria-label") or "").strip().lower()
                        if any(k in t for k in done_labels) and btn.is_displayed() and btn.is_enabled():
                            btn.click()
                            return True
                except Exception:
                    continue
        return False

    try:
        result = await _run_in_thread(_click_done)
        if result:
            await asyncio.sleep(0.8)
            print("[LINKEDIN] Clicou em Concluído após candidatura enviada")
        return result
    except Exception:
        return False


async def _verificar_ja_aplicado_na_pagina(driver) -> bool:
    """Verifica se o LinkedIn indica que a vaga já foi candidatada (badge/botão Applied)."""
    def _check():
        # Verifica texto de botões — LinkedIn troca "Easy Apply" por "Applied" após candidatura
        for btn in driver.find_elements(By.CSS_SELECTOR, "button"):
            try:
                if not btn.is_displayed():
                    continue
                t = (btn.text or btn.get_attribute("aria-label") or "").strip().lower()
                if t in ("applied", "candidatou-se", "já candidatou", "already applied"):
                    return True
            except Exception:
                continue
        # Verifica badges/avisos na página
        for sel in ["[class*='applied-badge']", "[class*='already-applied']",
                    "span[data-test-job-insight-item-with-icon]"]:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    t = (el.text or "").lower()
                    if "applied" in t or "candidatou" in t:
                        return True
            except Exception:
                continue
        return False

    try:
        return await _run_in_thread(_check)
    except Exception:
        return False


async def _fechar_modal_descarte(driver) -> bool:
    """Detecta e fecha modal 'Descartar candidatura' clicando em Continuar editando.
    Retorna True se um modal de descarte foi encontrado e dispensado."""
    keep_labels = [
        "continue applying", "continuar candidatura", "continuar editando",
        "keep editing", "manter candidatura", "go back", "voltar",
        "não, continuar", "no, continue",
    ]
    discard_markers = ["discard", "descartar", "abandon"]

    def _dismiss():
        try:
            dialogs = driver.find_elements(
                By.CSS_SELECTOR,
                "div[role='alertdialog'], div[role='dialog']"
            )
            for dialog in dialogs:
                try:
                    text = (dialog.text or "").lower()
                except Exception:
                    continue
                if not any(k in text for k in discard_markers):
                    continue
                # Found a discard dialog — look for the "keep" button
                btns = dialog.find_elements(By.TAG_NAME, "button")
                for btn in btns:
                    try:
                        t = (btn.text or "").strip().lower()
                        if any(k in t for k in keep_labels):
                            if btn.is_displayed() and btn.is_enabled():
                                btn.click()
                                return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False

    try:
        result = await _run_in_thread(_dismiss)
        if result:
            await asyncio.sleep(0.8)
        return result
    except Exception:
        return False


async def _extrair_descricao_vaga(driver) -> str:
    """Extrai texto da descrição da vaga na página atual."""
    def _extract():
        seletores = [
            ".jobs-description__content",
            ".jobs-description-content__text",
            "div[class*='job-description']",
            ".description__text",
            "#job-details",
            "article.jobs-description",
            ".jobs-box__html-content",
            ".jobs-unified-top-card__job-insight",
        ]
        for sel in seletores:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].is_displayed():
                    text = (els[0].text or "").strip()
                    if len(text) > 100:
                        return text[:3000]
            except Exception:
                continue
        return ""

    try:
        return await _run_in_thread(_extract)
    except Exception:
        return ""


async def _avaliar_match_vaga(descricao: str, curriculo: str) -> dict:
    """
    Avalia se a vaga é compatível com o currículo via LLM barato.
    Retorna: {"aplicar": bool, "idioma": "pt"|"en", "motivo": str}
    """
    if not descricao or not curriculo:
        return {"aplicar": True, "idioma": "pt", "motivo": "sem dados para avaliar"}

    import json as _json
    from ai.openrouter import openrouter

    prompt = f"""You are evaluating if a job description matches a candidate's resume.

JOB DESCRIPTION (first 2000 chars):
{descricao[:2000]}

CANDIDATE RESUME (first 1500 chars):
{curriculo[:1500]}

Respond with ONLY valid JSON, no markdown, no explanation:
{{"aplicar": true, "idioma": "pt", "motivo": "brief reason"}}

Rules:
- "aplicar": true if the job is reasonably compatible (be lenient — only set false for clearly unrelated roles like developer resume for nursing/accounting/unrelated field)
- "idioma": language of the job description — "pt" for Portuguese, "en" for English
- "motivo": one sentence explaining the decision"""

    try:
        resp = openrouter.converse([{"role": "user", "content": prompt}])
        resp = resp.strip()
        json_match = re.search(r'\{[^{}]+\}', resp, re.DOTALL)
        if json_match:
            data = _json.loads(json_match.group())
            return {
                "aplicar": bool(data.get("aplicar", True)),
                "idioma": str(data.get("idioma", "pt")),
                "motivo": str(data.get("motivo", "")),
            }
    except Exception as e:
        logger.warning("_avaliar_match_vaga erro LLM: %s", e)

    return {"aplicar": True, "idioma": "pt", "motivo": "fallback"}


_JS_CLICK_EASY_APPLY = """
return (() => {
    function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim().toLowerCase(); }

    const keywords = ['candidatura simplificada', 'easy apply'];
    const seletor = 'button, a, [role="button"]';
    const elementos = document.querySelectorAll(seletor);

    for (const el of elementos) {
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0 || el.disabled) continue;

        const text = norm(el.textContent || el.innerText || '');
        const aria = norm(el.getAttribute('aria-label') || '');

        for (const kw of keywords) {
            if (text.includes(kw) || aria.includes(kw)) {
                el.scrollIntoView({block: 'center'});
                el.click();
                return 'clicou:' + kw;
            }
        }
    }

    // Fallback: match just "candidatura" or "easy apply" separately
    for (const el of elementos) {
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0 || el.disabled) continue;

        const text = norm(el.textContent || el.innerText || '');
        const aria = norm(el.getAttribute('aria-label') || '');
        const cls = norm(el.className || '');

        if (cls.includes('jobs-apply-button') ||
            text.includes('candidatura') || aria.includes('candidatura') ||
            (text.includes('apply') && !text.includes('applied')) ||
            (aria.includes('apply') && !aria.includes('applied'))) {
            el.scrollIntoView({block: 'center'});
            el.click();
            return 'clicou:fallback:' + text.substring(0, 40);
        }
    }

    return null;
})()
"""


async def _clicar_easy_apply_js(driver) -> bool:
    try:
        resultado = await _run_in_thread(driver.execute_script, _JS_CLICK_EASY_APPLY)
        if resultado and 'clicou:' in resultado:
            print(f"[LINKEDIN] Clicou via JS: {resultado}")
            return True
        else:
            print(f"[LINKEDIN] JS nao encontrou botao Easy Apply")
    except Exception as e:
        print(f"[LINKEDIN] JS click falhou: {e}")
    return False


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
    return await _extrair_proxima_vaga_da_busca(set())


async def _extrair_proxima_vaga_da_busca(ja_tentadas: set) -> str | None:
    """Extrai URL da próxima vaga não tentada na página de busca.
    Pula URLs que já estão em ja_tentadas (controle de sessão)."""
    from selenium.webdriver.common.by import By

    driver = await get_driver()
    if not driver:
        return None

    def _extrair_id_de_href(href: str) -> str:
        if not href:
            return ""
        if "/jobs/view/" in href:
            return href.split("/jobs/view/")[1].split("/")[0].split("?")[0]
        return href

    try:
        def _procurar():
            current_url = driver.current_url.lower()
            is_easy_apply_page = "easy-apply" in current_url or "collections" in current_url

            candidatos = []

            # Estratégia 1: cards da página de coleção easy-apply
            if is_easy_apply_page:
                try:
                    cards = driver.find_elements(By.CSS_SELECTOR,
                        ".jobs-easy-apply__card, .job-card--easy-apply, .job-card, "
                        ".job-card-container, .jobs-search__result-card"
                    )
                    for card in cards[:20]:
                        try:
                            link_elem = card.find_element(By.XPATH, ".//a[contains(@href, '/jobs/view/')]")
                            href = link_elem.get_attribute("href")
                            if href:
                                candidatos.append(href)
                        except Exception:
                            continue
                except Exception:
                    pass

            # Estratégia 2: todos os links de vagas na página
            if not candidatos:
                try:
                    links = driver.find_elements(By.XPATH, "//a[@href and contains(@href, '/jobs/view/')]")
                    for lnk in links[:30]:
                        href = lnk.get_attribute("href")
                        if href:
                            candidatos.append(href)
                except Exception:
                    pass

            # Retorna o primeiro que ainda não foi tentado
            for href in candidatos:
                vaga_id = _extrair_id_de_href(href)
                chave = vaga_id or href
                if chave not in ja_tentadas:
                    return href

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
        try:
            await fechar()
        except Exception:
            pass
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

    # Após login, sempre navega para a aba de vagas Easy Apply
    await navegar(easy_apply_url)
    await asyncio.sleep(4)

    await notify_browser_step("linkedin_extracao", "iniciando", f"Extraindo vagas de {easy_apply_url}")

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
                        "button[aria-label*='Easy Apply'], button[aria-label*='Candidatura simplificada'], "
                        ".jobs-apply-button, [data-control-name='apply_show_modal'], "
                        "[aria-label*='Easy Apply'], [aria-label*='Candidatura simplificada']"
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
    import time
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    driver = await get_driver()
    if not driver:
        return []

    def _procurar():
        resultados = []

        # Aguarda aparecer pelo menos um card (LinkedIn renderiza via JS)
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "li.scaffold-layout__list-item, .job-card-container, "
                    ".jobs-search-results__list-item, [class*='job-card']"
                ))
            )
        except Exception:
            pass
        time.sleep(2)

        # Scroll para disparar lazy-load de cards adicionais
        last_h = driver.execute_script("return document.body.scrollHeight")
        for _ in range(4):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)
            new_h = driver.execute_script("return document.body.scrollHeight")
            if new_h == last_h:
                break
            last_h = new_h
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)

        # Seletores em ordem de prioridade para diferentes versões do LinkedIn
        cards = []
        for sel in [
            "li.scaffold-layout__list-item",
            ".jobs-search-results__list-item",
            ".job-card-container",
            "[class*='job-card-container']",
            ".jobs-search__result-card",
            ".base-card",
            "[class*='job-card']",
        ]:
            try:
                found = driver.find_elements(By.CSS_SELECTOR, sel)
                if found:
                    print(f"[LINKEDIN] {len(found)} cards via: {sel}")
                    cards = found
                    break
            except Exception:
                continue

        # Fallback: todos os links /jobs/view/ visíveis na página
        if not cards:
            print("[LINKEDIN] Nenhum card — usando links /jobs/view/ como fallback")
            links = driver.find_elements(By.XPATH, "//a[contains(@href, '/jobs/view/')]")
            seen = set()
            for link in links:
                try:
                    url = link.get_attribute("href") or ""
                    if not url or url in seen or "/jobs/view/" not in url:
                        continue
                    seen.add(url)
                    titulo = link.text.strip() or link.get_attribute("aria-label") or ""
                    vaga_id = url.split("/jobs/view/")[1].split("/")[0].split("?")[0]
                    if vaga_id:
                        resultados.append({
                            "id": f"linkedin-{vaga_id}",
                            "titulo": titulo or f"Vaga {vaga_id}",
                            "empresa": "",
                            "url": url,
                            "fonte": "LinkedIn",
                            "easy_apply": True,
                            "salario": "", "modalidade": "", "descricao": "", "local": "",
                        })
                    if len(resultados) >= max_vagas:
                        break
                except Exception:
                    continue
            return resultados

        seen_urls: set = set()
        for card in cards[:max_vagas * 2]:
            try:
                titulo = ""
                url = ""
                empresa = ""

                # URL — link que aponta para /jobs/view/
                try:
                    link_el = card.find_element(By.XPATH, ".//a[contains(@href, '/jobs/view/')]")
                    url = link_el.get_attribute("href") or ""
                    candidate = link_el.text.strip() or link_el.get_attribute("aria-label") or ""
                    if candidate:
                        titulo = candidate
                except Exception:
                    pass

                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                # Título — seletores alternativos
                if not titulo:
                    for sel in [
                        ".job-card-list__title--link",
                        ".job-card-list__title",
                        "a.job-card-container__link",
                        "[class*='job-card'][class*='title']",
                        "h3", "h2",
                    ]:
                        try:
                            el = card.find_element(By.CSS_SELECTOR, sel)
                            t = el.text.strip() or el.get_attribute("aria-label") or ""
                            if t:
                                titulo = t
                                break
                        except Exception:
                            pass

                # Empresa
                for sel in [
                    ".job-card-container__primary-description",
                    ".job-card-container__company-name",
                    "[class*='company-name']",
                    ".artdeco-entity-lockup__subtitle",
                    ".base-search-card__subtitle",
                ]:
                    try:
                        el = card.find_element(By.CSS_SELECTOR, sel)
                        t = el.text.strip()
                        if t:
                            empresa = t
                            break
                    except Exception:
                        pass

                vaga_id = url.split("/jobs/view/")[1].split("/")[0].split("?")[0] if "/jobs/view/" in url else ""
                if not vaga_id:
                    continue

                resultados.append({
                    "id": f"linkedin-{vaga_id}",
                    "titulo": titulo or f"Vaga {vaga_id}",
                    "empresa": empresa,
                    "url": url,
                    "fonte": "LinkedIn",
                    "easy_apply": True,
                    "salario": "", "modalidade": "", "descricao": "", "local": "",
                })

                if len(resultados) >= max_vagas:
                    break
            except Exception:
                continue

        return resultados

    return await _run_in_thread(_procurar)
