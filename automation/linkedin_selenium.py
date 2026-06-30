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
from selenium.common.exceptions import (
    InvalidSessionIdException, WebDriverException, StaleElementReferenceException,
)

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


# Frases ESPECÍFICAS do modal de confirmação pós-envio. NÃO inclui "applied"
# (genérico demais — aparece no fundo da listagem de vagas já candidatadas e
# causava falso-positivo de sucesso já no 1º 'Avançar').
_FRASES_SUCESSO = (
    "candidatura enviada", "sua candidatura foi enviada", "candidatura foi enviada",
    "application submitted", "application sent", "your application was sent",
    "we received your application", "candidatura recebida", "candidatura realizada",
    "obrigado pela candidatura", "thank you for applying", "applicationsubmitted",
)


async def _candidatura_enviada(driver) -> bool:
    """Confirma envio SÓ pelo modal de confirmação visível (não pela página de
    fundo). Evita falso-positivo do 'applied' que aparece na listagem atrás do
    modal e fazia o bot pular para outra vaga sem preencher/enviar."""
    def _check():
        for sel in ["div[role='dialog']", ".artdeco-modal", ".artdeco-toast-item"]:
            for dlg in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if not dlg.is_displayed():
                        continue
                    low = (dlg.text or "").lower()
                    if any(f in low for f in _FRASES_SUCESSO):
                        return True
                except Exception:
                    continue
        return False
    try:
        return await _run_in_thread(_check)
    except Exception:
        return False


def _detectar_sucesso(html: str, url: str) -> bool:
    """[legado] Detecta sucesso por texto da página inteira — use
    _candidatura_enviada(driver) (escopo no modal) para evitar falso-positivo."""
    html_lower = (html or "").lower()
    url_lower = (url or "").lower()
    return any(s in html_lower or s in url_lower for s in _FRASES_SUCESSO)


async def _detectar_erros_validacao(driver) -> list:
    """Detecta mensagens de erro de validação visíveis no step atual.

    Quando o LinkedIn rejeita um clique em 'Avançar' por campos obrigatórios
    vazios/inválidos, ele renderiza mensagens inline. Retorna a lista de textos
    de erro (vazia = sem erro = step avançou). É o sinal definitivo de que o
    clique foi recusado e não adianta clicar de novo no mesmo botão.
    """
    def _coletar():
        erros = []
        # 1. Mensagens inline conhecidas do artdeco + roles de alerta
        sels = [
            ".artdeco-inline-feedback--error",
            "[role='alert']",
            ".fb-dash-form-element__error-text",
            "[data-test-form-element-error-messages]",
            ".artdeco-text-input--error",
            "[aria-invalid='true']",
        ]
        for sel in sels:
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    if not el.is_displayed():
                        continue
                    txt = (el.text or "").strip()
                    if txt and txt not in erros:
                        erros.append(txt)
            except Exception:
                continue
        # 2. Fallback estrutura-agnóstico: varre o texto do modal por frases de erro.
        #    LinkedIn varia as classes/idiomas; o texto é mais estável.
        FRASES = [
            "obrigatório", "obrigatorio", "insira uma resposta válida",
            "insira uma resposta valida", "required", "please enter",
            "please select", "this field is required", "enter a valid",
            "campo obrigatório", "selecione uma opção",
        ]
        try:
            for dlg in driver.find_elements(By.CSS_SELECTOR, "div[role='dialog'], .artdeco-modal"):
                if not dlg.is_displayed():
                    continue
                low = (dlg.text or "").lower()
                for fr in FRASES:
                    if fr in low and fr not in [e.lower() for e in erros]:
                        erros.append(fr)
                break
        except Exception:
            pass
        return erros

    try:
        return await _run_in_thread(_coletar)
    except Exception:
        return []


def _dump_form_debug_sync(driver) -> str:
    """Escreve o HTML e um inventário dos controles do modal Easy Apply atual
    num arquivo de debug. Permite inspecionar a estrutura REAL do formulário
    (tipos de campo, labels, aria) quando o preenchimento falha — fim do chute.
    Retorna o caminho do arquivo, ou "" em falha."""
    import os
    try:
        modal = None
        for sel in ["div[role='dialog']", ".artdeco-modal", ".jobs-easy-apply-modal"]:
            els = [e for e in driver.find_elements(By.CSS_SELECTOR, sel) if e.is_displayed()]
            if els:
                modal = els[0]
                break
        scope = modal if modal is not None else driver
        html = ""
        try:
            html = modal.get_attribute("outerHTML") if modal is not None else driver.page_source
        except Exception:
            html = driver.page_source

        linhas = []
        controles = scope.find_elements(
            By.CSS_SELECTOR,
            "input, select, textarea, [role='combobox'], [role='radio'], "
            "button[aria-haspopup], [contenteditable='true']",
        )
        for c in controles:
            try:
                if not c.is_displayed():
                    continue
                tag = c.tag_name
                attrs = {
                    k: (c.get_attribute(k) or "")
                    for k in ("type", "id", "name", "role", "aria-label",
                              "aria-labelledby", "aria-required", "aria-invalid",
                              "placeholder", "value")
                }
                lbl = ""
                cid = attrs.get("id")
                if cid:
                    lblels = driver.find_elements(By.CSS_SELECTOR, f"label[for='{cid}']")
                    if lblels:
                        lbl = (lblels[0].text or "").strip().replace("\n", " ")
                opts = ""
                if tag == "select":
                    try:
                        opts = " | OPTIONS=" + ",".join(
                            (o.text or "").strip() for o in c.find_elements(By.TAG_NAME, "option")
                        )[:300]
                    except Exception:
                        pass
                linhas.append(
                    f"<{tag}> label='{lbl}' "
                    + " ".join(f"{k}='{v}'" for k, v in attrs.items() if v)
                    + opts
                )
            except Exception:
                continue

        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_linkedin_form_debug.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("=== INVENTÁRIO DE CONTROLES ===\n")
            f.write("\n".join(linhas))
            f.write("\n\n=== HTML DO MODAL ===\n")
            f.write(html or "")
        print(f"[LINKEDIN] DOM do formulário salvo em: {path}")
        return path
    except Exception as e:
        print(f"[LINKEDIN] _dump_form_debug falhou: {e}")
        return ""


async def _obter_progresso(driver) -> str:
    """Assinatura do estado do step para detectar não-avanço de forma
    estrutura-agnóstica: progresso (aria-valuenow) + nº de campos vazios.
    Se a assinatura não muda após clicar 'Avançar', o step travou."""
    def _sig():
        prog = ""
        try:
            for pb in driver.find_elements(By.CSS_SELECTOR, "[role='progressbar'], progress"):
                v = pb.get_attribute("aria-valuenow") or pb.get_attribute("value") or ""
                if v:
                    prog = v
                    break
        except Exception:
            pass
        vazios = 0
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, "input, select, textarea"):
                try:
                    if el.is_displayed() and not (el.get_attribute("value") or "").strip():
                        vazios += 1
                except Exception:
                    continue
        except Exception:
            pass
        return f"p={prog};vazios={vazios}"

    try:
        return await _run_in_thread(_sig)
    except Exception:
        return ""


async def _processar_formulario_multistep_selenium(driver, perfil: dict, curriculo_path: str, vaga_url: str, resumo_curriculo: str, idioma: str = "pt") -> dict:
    """Processa formulario Easy Apply multi-step via Selenium."""
    from automation.browser import notify_browser_step, wait_if_paused, get_intervention_state, set_intervention_state

    # Garante que o preenchimento (ex.: extração de cidade) enxergue o currículo,
    # mesmo quando o perfil veio sem o campo e o resumo foi resolvido à parte.
    if resumo_curriculo and not perfil.get("resumo_curriculo"):
        perfil = {**perfil, "resumo_curriculo": resumo_curriculo}

    perguntas_customizadas = []
    respostas_geradas = {}
    max_steps = 12

    try:
        vaga_titulo = ""
        try:
            vaga_titulo = driver.title or ""
        except Exception:
            pass

        async def _finalizar_se_sucesso(step_idx):
            """Se a candidatura foi enviada, finaliza e devolve o dict de sucesso;
            senão None. Chamado após QUALQUER clique de botão primário — o botão de
            enviar às vezes volta com texto vazio e não casa is_submit."""
            if await _candidatura_enviada(driver):
                logger.info("linkedin_selenium: candidatura enviada com sucesso")
                _registrar_run_log(f"FORM step {step_idx}: SUCESSO detectado")
                await notify_browser_step("step_"+str(step_idx), "sucesso", "Candidatura enviada!")
                _b64 = await screenshot_base64()
                await _finalizar_sucesso(driver, motivo="sucesso-step")
                return {
                    "sucesso": True,
                    "perguntas_respondidas": perguntas_customizadas,
                    "mensagem": "Candidatura enviada com sucesso via LinkedIn Easy Apply!",
                    "screenshot": _b64[:100] if _b64 else "",
                }
            return None

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

            # Preenchimento resiliente a StaleElementReference: o typeahead da cidade
            # e a seleção de CV/radio re-renderizam o DOM, invalidando refs de
            # elementos. Em vez de abortar o form (e deixar 'Descartar' sobreposto),
            # espera o DOM assentar e re-tenta o preenchimento do step.
            for _tent in range(3):
                try:
                    await _preencher_step_selenium(driver, perfil, curriculo_path)
                    perguntas_step = await _detectar_perguntas_nao_respondidas_selenium(driver)
                    break
                except StaleElementReferenceException:
                    _registrar_run_log(f"FORM step {step}: stale no preenchimento (tent {_tent+1}) — re-tentando")
                    await asyncio.sleep(1.0)
                    perguntas_step = []
            else:
                perguntas_step = []
            # DIAGNÓSTICO: quantas perguntas foram detectadas e quais (prefixo+label)
            _registrar_run_log(
                f"FORM step {step}: {len(perguntas_step)} pergunta(s) detectada(s)"
                + ("" if not perguntas_step else " -> " + " | ".join(p[:45] for p in perguntas_step[:4]))
            )
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
                        try:
                            resposta = responder_pergunta(
                                pergunta, perfil,
                                vaga_titulo=vaga_titulo,
                                vaga_empresa="",
                                resumo_curriculo=resumo_curriculo,
                                idioma=idioma,
                            )
                        except Exception as _rpe:
                            _registrar_run_log(f"FORM step {step}: responder_pergunta ERRO: {_rpe}")
                            resposta = ""
                        respostas_geradas[pergunta] = resposta
                        await notify_browser_step("step_"+str(step), "respondendo", f"Pergunta: {pergunta[:40]}...")
                        _registrar_run_log(f"FORM step {step}: resp '{pergunta[:35]}' = '{str(resposta)[:25]}'")
                        await _preencher_resposta_customizada_selenium(driver, pergunta, resposta)
                        perguntas_customizadas.append(pergunta)
                        await asyncio.sleep(random.uniform(0.5, 1.0))

            if pular_agora:
                await set_intervention_state("current_action", "rodando")
                await notify_browser_step("step_"+str(step), "pulado", "Usuário pediu pular")
                continue

            # Assinatura do step antes de clicar — para detectar não-avanço
            sig_antes = await _obter_progresso(driver)

            try:
                btn_text, btn_clicked = await _clicar_botao_primario_modal(driver)
            except StaleElementReferenceException:
                _registrar_run_log(f"FORM step {step}: stale ao clicar botão — re-tentando step")
                await asyncio.sleep(1.0)
                continue

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
            _registrar_run_log(f"FORM step {step}: clicou botão '{btn_text[:40]}'")
            btn_lower = btn_text.lower()

            is_submit = any(s in btn_lower for s in [
                "submit", "enviar", "send", "finalizar",
            ])
            is_review = any(s in btn_lower for s in [
                "review", "revisar",
            ])

            # Espera o DOM reagir ao clique (submit/review precisam de mais tempo).
            await asyncio.sleep(3 if (is_submit or is_review) else 2)
            await notify_browser_step("step_"+str(step),
                                      "enviando" if is_submit else "navegando", f"Botão: {btn_text[:40]}")

            # Checagem de sucesso INCONDICIONAL: cobre o caso do botão 'Enviar
            # candidatura' vir com texto vazio (não casa is_submit) — sem isto a
            # candidatura enviada seria tratada como falha e descartada.
            _res = await _finalizar_se_sucesso(step)
            if _res:
                return _res

            if is_review:
                await asyncio.sleep(1)
                continue

            if is_submit:
                # Clicou enviar mas sucesso ainda não confirmado — dá mais tempo e re-checa.
                await asyncio.sleep(2.5)
                _res = await _finalizar_se_sucesso(step)
                if _res:
                    return _res
                _registrar_run_log(f"FORM step {step}: submit clicado, sucesso NÃO confirmado — encerrando")
                break

            # Navegando (Avançar/Continuar):
            # Keystone (estrutura-agnóstico): o clique em 'Avançar' pode ter sido
            # recusado por campo obrigatório vazio/inválido. Detecta de DUAS formas
            # independentes da estrutura exata do DOM:
            #   1) mensagem de erro de validação visível;
            #   2) a assinatura do step (progresso + nº de campos vazios) não mudou.
            # Qualquer uma → o step travou; clicar de novo só repetiria até cair
            # em 'Salvar candidatura?' → jobs-tracker. Dump do DOM + bail limpo.
            if True:
                erros = await _detectar_erros_validacao(driver)
                sig_depois = await _obter_progresso(driver)
                travado = bool(erros) or (sig_antes and sig_antes == sig_depois)
                if travado:
                    motivo = (" | ".join(erros[:3])) if erros else f"sem avanço ({sig_depois})"
                    print(f"[LINKEDIN] Step {step}: avanço bloqueado — {motivo}")
                    await notify_browser_step("step_"+str(step), "bloqueado", f"Travou: {motivo[:80]}")
                    try:
                        await _run_in_thread(_dump_form_debug_sync, driver)
                    except Exception:
                        pass
                    break
                continue

            break

        if await _candidatura_enviada(driver):
            b64 = await screenshot_base64()
            await _finalizar_sucesso(driver, motivo="sucesso-fim")
            return {
                "sucesso": True,
                "perguntas_respondidas": perguntas_customizadas,
                "mensagem": "Candidatura enviada com sucesso via LinkedIn Easy Apply!",
                "screenshot": b64[:100] if b64 else "",
            }

        b64 = await screenshot_base64()
        # Captura a estrutura real do formulário antes de descartar (modal ainda aberto)
        try:
            await _run_in_thread(_dump_form_debug_sync, driver)
        except Exception:
            pass
        # Fecha modal Easy Apply ainda aberto (se houver) antes de navegar — descarta candidatura
        try:
            await _escapar_modal_ativo(driver)
        except Exception:
            pass
        await _voltar_para_listagem(driver, motivo="formulario-incompleto")
        return {
            "sucesso": False,
            "motivo_falha": "formulario_incompleto",
            "mensagem": f"Nao consegui completar o formulario. Candidate-se manualmente: {vaga_url}",
            "screenshot": b64[:100] if b64 else "",
        }
    except Exception as e:
        logger.error(f"_processar_formulario_multistep_selenium erro: {e}")
        _registrar_run_log(f"FORM except: {e}")
        try:
            await _run_in_thread(_dump_form_debug_sync, driver)
        except Exception:
            pass
        try:
            await _escapar_modal_ativo(driver)
            await _voltar_para_listagem(driver, motivo="form-except")
        except Exception:
            pass
        return {
            "sucesso": False,
            "motivo_falha": "erro_tecnico",
            "mensagem": f"Erro no formulario. Candidate-se manualmente: {vaga_url}",
        }


def _extrair_cidade_do_curriculo(resumo: str) -> str:
    """Extrai a cidade do texto do currículo/informações pessoais.
    Cobre 'Cidade: Joinville', 'Cidade Joinville', 'Localização: X', 'Endereço ... , Cidade'.
    Determinístico (regex) — não depende de chamada de LLM."""
    if not resumo:
        return ""
    # 1) Rótulos explícitos
    for pat in (r"cidade\s*[:\-]\s*([^\n,;:]+)",
                r"localiza[çc][aã]o\s*[:\-]\s*([^\n,;:]+)",
                r"city\s*[:\-]\s*([^\n,;:]+)"):
        m = re.search(pat, resumo, re.IGNORECASE)
        if m:
            cidade = m.group(1).strip(" .,-:\t")
            # remove sufixos de estado/país que possam ter colado
            cidade = re.split(r"\s+(?:estado|pa[íi]s|bairro|brasil|brazil)\b", cidade, flags=re.IGNORECASE)[0].strip()
            if 1 < len(cidade) <= 40:
                return cidade
    return ""


async def _preencher_cidade_autocomplete(driver, cidade: str) -> None:
    """Preenche campo cidade/localização com suporte ao autocomplete do LinkedIn.

    O campo de cidade usa typeahead — só enviar teclas não basta; é preciso selecionar
    uma sugestão ou pressionar Tab para comprometer o valor, evitando que o dropdown
    aberto interfira nos cliques seguintes (ex.: botão Avançar fora do modal).
    """
    # Campo real do LinkedIn: id '...location-GEO-LOCATION', label 'Location (city)',
    # role='combobox' (typeahead). Os seletores antigos só viam 'city' e eram
    # case-sensitive — não casavam com 'location'/'Location (city)'. Cobrir tudo.
    CITY_SELS = [
        "input[id*='location']",
        "input[id*='GEO-LOCATION']",
        "input[id*='typeahead-entity']",
        "input[id*='city' i]",
        "input[role='combobox'][id*='typeahead']",
        "input[aria-label*='location' i]",
        "input[aria-label*='city' i]",
        "input[aria-label*='cidade' i]",
        "input[aria-label*='locali' i]",
        "input[name*='city' i]",
        "input[name*='location' i]",
        "input[placeholder*='city' i]",
        "input[placeholder*='cidade' i]",
    ]
    SUGGESTION_SELS = [
        "[role='option']",
        "[role='listbox'] li",
        ".basic-typeahead__selectable",
        ".artdeco-dropdown__item",
        "li[data-test-text-selectable-option]",
    ]

    def _type_city():
        import time
        for sel in CITY_SELS:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                try:
                    if not el.is_displayed() or not el.is_enabled():
                        continue
                    current = (el.get_attribute("value") or "").strip()
                    if current:
                        # Trap: texto inválido de uma tentativa anterior conta como
                        # "preenchido" mas a validação rejeita ("Insira uma resposta
                        # válida"). Se o campo está marcado inválido, limpa e re-digita
                        # para comprometer uma sugestão real do autocomplete.
                        invalido = (el.get_attribute("aria-invalid") or "").lower() == "true"
                        if not invalido:
                            print(f"[LINKEDIN] Cidade já preenchida: {current[:30]}")
                            return "already_filled"
                        print(f"[LINKEDIN] Cidade inválida ('{current[:30]}') — re-digitando")
                    el.click()
                    time.sleep(0.2)
                    el.clear()
                    el.send_keys(cidade[:60])
                    print(f"[LINKEDIN] Cidade digitada: {cidade[:40]}")
                    return "typed"
                except Exception:
                    continue
        print("[LINKEDIN] Cidade: campo não encontrado neste step")
        return False

    result = await _run_in_thread(_type_city)
    if result == "already_filled":
        return True
    if result != "typed":
        return False  # campo não encontrado

    await asyncio.sleep(1.5)

    def _click_first_suggestion():
        for sel in SUGGESTION_SELS:
            opts = driver.find_elements(By.CSS_SELECTOR, sel)
            visible = [o for o in opts if o.is_displayed() and (o.text or "").strip()]
            if visible:
                driver.execute_script("arguments[0].click();", visible[0])
                print(f"[LINKEDIN] Cidade autocomplete selecionado: {visible[0].text.strip()[:40]}")
                return True
        return False

    clicked = await _run_in_thread(_click_first_suggestion)
    if not clicked:
        # Sem sugestão — Tab compromete o valor e fecha qualquer dropdown
        def _tab_commit():
            from selenium.webdriver.common.keys import Keys
            for sel in CITY_SELS:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    try:
                        if el.is_displayed():
                            el.send_keys(Keys.TAB)
                            print("[LINKEDIN] Cidade: nenhuma sugestão, comprometido via Tab")
                            return True
                    except Exception:
                        continue
            return False
        await _run_in_thread(_tab_commit)
        await asyncio.sleep(0.3)
    return True


async def _preencher_step_selenium(driver, perfil: dict, curriculo_path: str) -> None:
    """Preenche campos conhecidos no step atual via Selenium.
    Usa find_elements (sem timeout) para não bloquear em campos ausentes."""
    nome_completo = perfil.get("nome", "")
    partes = nome_completo.split()
    primeiro_nome = partes[0] if partes else nome_completo
    ultimo_nome = " ".join(partes[1:]) if len(partes) >= 2 else ""

    # Mapa: lista de seletores CSS → valor (cidade tratada separadamente com autocomplete)
    campos = [
        (["input[name='firstName']", "#first-name", "input[id*='firstName']"], primeiro_nome),
        (["input[name='lastName']", "#last-name", "input[id*='lastName']"], ultimo_nome),
        (["input[name='email']", "#email", "input[type='email'][name*='email']",
          "input[aria-label*='email' i]", "input[aria-label*='e-mail' i]"], perfil.get("email", _get_linkedin_email())),
        (["input[name='phone']", "#phone", "input[type='tel']",
          "input[aria-label*='phone' i]", "input[aria-label*='telefone' i]",
          "input[aria-label*='celular' i]"], str(perfil.get("telefone", perfil.get("phone", "")))),
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

    # Selects obrigatórios da etapa de contato: e-mail e código do país.
    # No Easy Apply esses campos costumam ser <select> (não inputs) e ficam
    # vazios quando a conta não tem padrão definido → "Obrigatório" bloqueia o
    # avanço. Preenche preservando qualquer seleção válida já existente.
    email_perfil = (perfil.get("email", "") or _get_linkedin_email() or "").strip().lower()
    pais_perfil = (perfil.get("pais", perfil.get("país", "")) or "").strip().lower()

    def _preencher_selects_contato():
        from selenium.webdriver.support.ui import Select
        EMAIL_KW = ("email", "e-mail")
        PAIS_KW = ("código do país", "codigo do pais", "country code", "país", "pais", "country")
        for sel_elem in driver.find_elements(By.CSS_SELECTOR, "select"):
            try:
                if not sel_elem.is_displayed() or not sel_elem.is_enabled():
                    continue
                aria = (sel_elem.get_attribute("aria-label") or "").lower()
                lbl = ""
                sid = sel_elem.get_attribute("id") or ""
                if sid:
                    lbls = driver.find_elements(By.CSS_SELECTOR, f"label[for='{sid}']")
                    if lbls:
                        lbl = (lbls[0].text or "").lower()
                texto_ident = f"{aria} {lbl}"
                is_email = any(k in texto_ident for k in EMAIL_KW)
                is_pais = any(k in texto_ident for k in PAIS_KW)
                if not (is_email or is_pais):
                    continue

                sel_obj = Select(sel_elem)
                cur = sel_obj.first_selected_option
                cur_text = (cur.text or "").strip().lower()
                cur_val = (cur.get_attribute("value") or "").strip()
                if cur_val and not _eh_placeholder_opcao(cur_text):
                    continue  # já tem seleção válida — preserva

                opcoes = [
                    (o.get_attribute("value"), (o.text or "").strip())
                    for o in sel_obj.options
                    if not _eh_placeholder_opcao(o.text)
                    and (o.get_attribute("value") or "").strip()
                ]
                if not opcoes:
                    continue

                escolhido = None
                if is_email and email_perfil:
                    for val, txt in opcoes:
                        if email_perfil in txt.lower():
                            escolhido = val
                            break
                if is_pais:
                    alvos = [pais_perfil] if pais_perfil else []
                    alvos += ["brasil", "brazil", "(+55)", "+55", "55"]
                    for alvo in alvos:
                        if not alvo:
                            continue
                        for val, txt in opcoes:
                            if alvo in txt.lower():
                                escolhido = val
                                break
                        if escolhido:
                            break
                if escolhido is None:
                    escolhido = opcoes[0][0]  # fallback: primeira opção válida

                sel_obj.select_by_value(escolhido)
                _tipo = "email" if is_email else "país"
                print(f"[LINKEDIN] Select contato ({_tipo}) preenchido: {escolhido}")
            except Exception:
                continue

    try:
        await _run_in_thread(_preencher_selects_contato)
    except Exception:
        pass

    # Cidade com autocomplete — campo obrigatório no Easy Apply (vazio = "Insira uma
    # resposta válida" e trava tudo). Ordem de origem da cidade:
    #   1) perfil.localizacao  2) extraída do texto do currículo  3) env (último caso)
    cidade_val = perfil.get("localizacao", "").split(",")[0].strip() if perfil.get("localizacao") else ""
    if not cidade_val:
        cidade_val = _extrair_cidade_do_curriculo(perfil.get("resumo_curriculo", ""))
        if cidade_val:
            print(f"[LINKEDIN] Cidade extraída do currículo: {cidade_val}")
    if not cidade_val:
        cidade_val = os.getenv("LINKEDIN_CIDADE", "").strip()
    if cidade_val:
        ok_cidade = await _preencher_cidade_autocomplete(driver, cidade_val)
        _registrar_run_log(f"CIDADE '{cidade_val[:25]}' resultado={ok_cidade}")
    else:
        print("[LINKEDIN] AVISO: cidade não encontrada (perfil/currículo) — campo de localização vai travar o form")
        _registrar_run_log("CIDADE: não encontrada (perfil/currículo/env)")

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
    "", "select an option", "selecione uma opção", "selecionar opção",
    "selecione", "--", "select", "choose", "selecione uma resposta",
    "selecionar uma opção", "escolha uma opção", "nenhum selecionado",
    "select a response", "please select", "escolha", "seleccione",
    "selecionar", "escolher",
}


def _eh_placeholder_opcao(texto: str) -> bool:
    """True se o texto da opção/estado de um select é placeholder (não é resposta).
    Robusto a variações ('Selecionar opção', 'Selecione uma opção', 'Select an
    option', 'Choose...'). O exact-match antigo deixava 'Selecionar opção' passar
    como se já estivesse respondido → pergunta nunca preenchida → form travava."""
    t = (texto or "").strip().lower()
    if not t or t in _SELECT_PLACEHOLDER_TEXTS:
        return True
    return (
        t.startswith("selecion") or t.startswith("seleccion") or t.startswith("escolh")
        or t.startswith("choose") or t in ("-", "...", "—")
        or "select an option" in t or "select a response" in t or "please select" in t
    )

_DECIMAL_LABEL_KEYWORDS = {
    # inglês
    "salary", "wage", "compensation", "pay", "rate", "price", "gpa", "score",
    "expected", "desired", "gross", "ctc",
    # português — com e sem acento, forma adjetiva
    "salário", "salario", "salarial",
    "remuneração", "remuneracao", "remuner",
    "pretens",      # pretensão, pretendido
    "taxa", "valor", "preço", "preco",
    "nota",
    # espanhol/italiano presentes em vagas
    "sueldo", "salario",
}
_INTEGER_LABEL_KEYWORDS = {
    "years", "months", "anos", "meses", "quantos", "quanto", "how many",
    "number of", "quantidade", "experiencia", "experiência", "experience",
}


def _modal_scope(driver):
    """Retorna o elemento do modal Easy Apply (ou o driver, se não achar).
    Restringe buscas de campos AO modal — fora dele existe a barra de busca do
    topo do LinkedIn e outros inputs que poluíam a detecção de perguntas."""
    for sel in ("div.jobs-easy-apply-modal", ".jobs-easy-apply-modal",
                "div[data-test-modal][role='dialog']", "div[role='dialog']",
                ".artdeco-modal"):
        try:
            els = [e for e in driver.find_elements(By.CSS_SELECTOR, sel) if e.is_displayed()]
            if els:
                return els[-1]
        except Exception:
            continue
    return driver


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

    # Escopo no modal Easy Apply — senão a barra de busca do topo do LinkedIn
    # ("Pesquisar cargo, competência ou empresa") é detectada como pergunta e
    # recebe a resposta da IA, disparando uma busca/refresh.
    scope = _modal_scope(driver)

    try:
        inputs = scope.find_elements(By.CSS_SELECTOR,
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
        selects = scope.find_elements(By.CSS_SELECTOR, "select")
        _registrar_run_log(
            f"DETECT scope={'modal' if scope is not driver else 'PAGE'} "
            f"inputs={len(inputs)} selects={len(selects)} "
            f"visiveis_select={sum(1 for s in selects if s.is_displayed())}"
        )
        for sel_elem in selects:
            if not sel_elem.is_displayed():
                continue
            try:
                from selenium.webdriver.support.ui import Select
                sel_obj = Select(sel_elem)
                first_opt = sel_obj.first_selected_option
                selected_text = (first_opt.text or "").strip().lower()
                first_val = (first_opt.get_attribute("value") or "").strip()
                if _eh_placeholder_opcao(selected_text) or first_val == "":
                    label = await _get_label_selenium(driver, sel_elem)
                    if label and not any(p in label.lower() for p in _CAMPOS_PADRAO):
                        options_text = [
                            o.text.strip() for o in sel_obj.options
                            if o.text.strip() and not _eh_placeholder_opcao(o.text)
                        ]
                        if options_text:
                            _add(f"SELECT:{label}:{','.join(options_text[:10])}")
                    elif not label:
                        _registrar_run_log("DETECT: select sem label — não adicionado")
            except Exception as _se:
                _registrar_run_log(f"DETECT select ERRO: {_se}")

        # LinkedIn custom comboboxes (role=combobox / aria-haspopup=listbox)
        combo_sels = "[role='combobox'], button[aria-haspopup='listbox'], button[aria-haspopup='true'][aria-expanded]"
        comboboxes = scope.find_elements(By.CSS_SELECTOR, combo_sels)
        for cb in comboboxes:
            if not cb.is_displayed():
                continue
            try:
                # Verifica se já tem valor selecionado
                current_text = (cb.text or cb.get_attribute("aria-label") or "").strip().lower()
                if current_text and not _eh_placeholder_opcao(current_text):
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
        fieldsets = scope.find_elements(By.CSS_SELECTOR, "fieldset")
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

        # Checkboxes standalone (fora de fieldset — termos, consents, perguntas booleanas)
        checkboxes = scope.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
        for chk in checkboxes:
            try:
                if chk.is_selected():
                    continue  # já marcado
                chk_id = chk.get_attribute("id") or ""
                label = ""
                if chk_id:
                    lbs = driver.find_elements(By.CSS_SELECTOR, f"label[for='{chk_id}']")
                    if lbs:
                        label = (lbs[0].text or "").strip()
                if not label:
                    try:
                        parent = chk.find_element(By.XPATH, "./ancestor::div[1]")
                        label = (parent.text or "").strip().split("\n")[0]
                    except Exception:
                        pass
                if not label or any(p in label.lower() for p in _CAMPOS_PADRAO):
                    continue
                # Não detectar checkboxes de upload de CV (tratados por _selecionar_cv_ingles)
                if any(kw in label.lower() for kw in ("resume", "currículo", ".pdf", ".doc", "upload")):
                    continue
                _add(f"CHECKBOX:{label}")
            except Exception:
                continue
    except Exception:
        pass
    return perguntas[:12]


async def _preencher_checkbox_selenium(driver, label_text: str, resposta: str) -> None:
    """Marca checkbox pelo texto da label. Usa JS click (LinkedIn oculta com CSS)."""
    def _check():
        checkboxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
        for chk in checkboxes:
            try:
                if chk.is_selected():
                    continue
                chk_id = chk.get_attribute("id") or ""
                found_label = ""
                if chk_id:
                    lbs = driver.find_elements(By.CSS_SELECTOR, f"label[for='{chk_id}']")
                    if lbs:
                        found_label = (lbs[0].text or "").strip()
                if not found_label:
                    try:
                        found_label = chk.find_element(
                            By.XPATH, "./ancestor::div[1]"
                        ).text.strip().split("\n")[0]
                    except Exception:
                        pass
                if not found_label:
                    continue
                if label_text.lower()[:30] in found_label.lower() or found_label.lower()[:30] in label_text.lower():
                    # Clica via label element (se existir) ou via JS direto no input
                    if chk_id:
                        lbs = driver.find_elements(By.CSS_SELECTOR, f"label[for='{chk_id}']")
                        if lbs:
                            driver.execute_script("arguments[0].click();", lbs[0])
                            return True
                    driver.execute_script("arguments[0].click();", chk)
                    return True
            except Exception:
                continue
        return False

    try:
        deveria_marcar = resposta.strip().lower() not in ("não", "nao", "no", "n", "false", "0")
        if not deveria_marcar:
            print(f"[LINKEDIN] Checkbox '{label_text[:40]}' → IA respondeu não marcar")
            return
        result = await _run_in_thread(_check)
        if result:
            print(f"[LINKEDIN] Marcou checkbox: {label_text[:40]}")
        else:
            print(f"[LINKEDIN] Checkbox não encontrado: {label_text[:40]}")
    except Exception as e:
        logger.debug("_preencher_checkbox_selenium erro: %s", e)


async def _preencher_resposta_customizada_selenium(driver, pergunta: str, resposta: str) -> None:
    """Preenche campo de pergunta customizada pela label via Selenium.
    Suporta text, number, textarea, select, radio, checkbox, NUMERO, DECIMAL e COMBO."""
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

        if pergunta.startswith("CHECKBOX:"):
            label_text = pergunta[9:].strip()
            await _preencher_checkbox_selenium(driver, label_text, resposta)
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

        labels = _modal_scope(driver).find_elements(By.CSS_SELECTOR, "label")
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
        _registrar_run_log(f"PREENCHER: label não casou p/ '{pergunta[:40]}'")
    except Exception as e:
        logger.debug("linkedin_selenium: erro ao preencher resposta: %s", e)
        _registrar_run_log(f"PREENCHER ERRO '{pergunta[:35]}': {e}")


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

    def _js_click(el):
        driver.execute_script("arguments[0].scrollIntoView({block:'nearest'}); arguments[0].click();", el)

    def _click_option(resposta_lower: str):
        # Opções podem estar fora do modal (portal no body) — busca global
        all_selectors = [
            "[role='option']",
            "[role='listbox'] li",
            ".artdeco-dropdown__item",
            ".basic-typeahead__selectable",
            "li[data-test-text-selectable-option]",
            ".jobs-easy-apply-form-element__listbox li",
        ]
        for sel in all_selectors:
            opts = driver.find_elements(By.CSS_SELECTOR, sel)
            visible = [o for o in opts if o.is_displayed() and (o.text or "").strip()]
            if not visible:
                continue
            # Correspondência exata
            for opt in visible:
                if opt.text.strip().lower() == resposta_lower:
                    _js_click(opt)
                    return True
            # Correspondência parcial
            for opt in visible:
                t = opt.text.strip().lower()
                if resposta_lower in t or t in resposta_lower:
                    _js_click(opt)
                    return True
            # "Sim"/"Não" heurística
            if resposta_lower in ("sim", "yes", "s", "y") and visible:
                _js_click(visible[0])
                return True
            if resposta_lower in ("não", "nao", "no", "n") and len(visible) >= 2:
                _js_click(visible[1])
                return True
            # Fallback: primeira opção
            _js_click(visible[0])
            return True
        return False

    try:
        trigger = await _run_in_thread(_find_trigger)
        if not trigger:
            await _preencher_select_selenium(driver, label_text, resposta)
            return False

        # Abre o dropdown via JS click
        await _run_in_thread(lambda: _js_click(trigger))
        await asyncio.sleep(1.2)  # aguarda renderização das opções

        resultado = await _run_in_thread(lambda: _click_option(resposta.strip().lower()))
        if not resultado:
            # Segunda tentativa: aguarda mais e tenta de novo
            await asyncio.sleep(1.0)
            resultado = await _run_in_thread(lambda: _click_option(resposta.strip().lower()))

        if resultado:
            print(f"[LINKEDIN] Preencheu combobox: {label_text[:40]} = {resposta[:30]}")
        else:
            print(f"[LINKEDIN] Combobox: nenhuma opção para '{label_text[:30]}'")
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
            # Só opções reais (exclui placeholder 'Selecionar opção' etc.)
            options = [
                (o.get_attribute("value"), o.text.strip())
                for o in sel_obj.options
                if o.text.strip() and not _eh_placeholder_opcao(o.text)
            ]
            resposta_lower = resposta.strip().lower()
            escolhido = None
            for val, text in options:
                if resposta_lower == text.lower() or resposta_lower in text.lower() or text.lower() in resposta_lower:
                    escolhido = (val, text); break
            if escolhido is None and options:
                escolhido = options[0]  # fallback: 1ª opção real (não deixa travar)
            if escolhido is None:
                _registrar_run_log(f"SELECT '{label_text[:30]}': SEM opções reais")
                return
            val, text = escolhido
            sel_obj.select_by_value(val)
            # React: o <select> do LinkedIn é controlado — select_by_value pode não
            # disparar o onChange e o React reverte p/ 'Selecionar opção'. Força os
            # eventos e verifica se a seleção ficou.
            try:
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
                    "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                    sel_elem,
                )
            except Exception:
                pass
            ficou = (Select(sel_elem).first_selected_option.text or "").strip()
            print(f"[LINKEDIN] Select: {label_text[:30]} = {text} (ficou: {ficou[:20]})")
            _registrar_run_log(f"SELECT '{label_text[:30]}' = '{text}' (ficou: '{ficou[:20]}')")
            return
        _registrar_run_log(f"SELECT '{label_text[:30]}': nenhum <select> casou a label")
    except Exception as e:
        logger.debug("linkedin_selenium: erro ao preencher select: %s", e)
        _registrar_run_log(f"SELECT ERRO '{label_text[:30]}': {e}")


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
                    _txt = (btn.text or "").strip()
                    _aria = (btn.get_attribute("aria-label") or "").strip()
                    # Prefere o rótulo que tenha sinal de submit/review (p/ classificar
                    # corretamente o passo de envio); senão usa o que tiver conteúdo.
                    _kw = ("enviar", "submit", "revisar", "review")
                    if _aria and any(k in _aria.lower() for k in _kw):
                        text = _aria
                    elif _txt and any(k in _txt.lower() for k in _kw):
                        text = _txt
                    else:
                        text = _txt or _aria
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


async def _clicar_proximo_card_vaga(driver, ja_tentadas: set) -> tuple:
    """
    Clica no próximo card de vaga não processado na lista do painel esquerdo.
    Não navega — apenas clica para carregar o painel direito.
    Retorna (vaga_id, vaga_url) ou ("", "") se não houver mais cards.
    """
    _CARD_SELS = [
        "li.jobs-search-results__list-item",
        ".job-card-container",
        ".scaffold-layout__list-item",
        ".jobs-job-board-list__item",
        ".jobs-search__result-card",
        "li[data-occludable-job-id]",
    ]

    def _find_and_click():
        for sel in _CARD_SELS:
            cards = driver.find_elements(By.CSS_SELECTOR, sel)
            for card in cards:
                if not card.is_displayed():
                    continue
                href = ""
                vaga_id = ""
                # Tenta pegar o job-id do atributo direto
                jid = card.get_attribute("data-occludable-job-id") or card.get_attribute("data-job-id") or ""
                if jid:
                    vaga_id = jid.strip()
                    href = f"https://www.linkedin.com/jobs/view/{vaga_id}/"
                else:
                    # Busca link dentro do card
                    links = card.find_elements(By.XPATH, ".//a[contains(@href, '/jobs/view/')]")
                    if links:
                        href = links[0].get_attribute("href") or ""
                        if "/jobs/view/" in href:
                            vaga_id = href.split("/jobs/view/")[1].split("/")[0].split("?")[0]

                chave = vaga_id or href
                if not chave or chave in ja_tentadas:
                    continue

                # Clica no card para carregar o painel direito
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'smooth'});", card)
                    driver.execute_script("arguments[0].click();", card)
                    return vaga_id, href
                except Exception:
                    # Tenta clicar no link interno
                    if links:
                        try:
                            driver.execute_script("arguments[0].click();", links[0])
                            return vaga_id, href
                        except Exception:
                            pass
                    continue
        return "", ""

    try:
        vaga_id, vaga_url = await _run_in_thread(_find_and_click)
        return vaga_id, vaga_url
    except Exception as e:
        logger.debug("_clicar_proximo_card_vaga erro: %s", e)
        return "", ""


async def _scroll_lista_vagas(driver) -> None:
    """Rola o painel esquerdo da lista de vagas para carregar mais cards."""
    def _scroll():
        for sel in [
            ".jobs-search-results-list",
            ".scaffold-layout__list",
            "[class*='jobs-search-results']",
            ".jobs-job-board-list",
        ]:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els and els[0].is_displayed():
                driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollTop + arguments[0].clientHeight * 0.8;",
                    els[0]
                )
                return
        driver.execute_script("window.scrollBy(0, window.innerHeight * 0.8);")
    try:
        await asyncio.wait_for(_run_in_thread(_scroll), timeout=8)
    except Exception as e:
        logger.debug("_scroll_lista_vagas erro: %s", e)


async def _ir_proxima_pagina_vagas(driver) -> bool:
    """Avança para a próxima página de vagas no LinkedIn. Retorna True se navegou.

    A paginação do LinkedIn é numerada (`<button aria-label='Página 2'><span>2</span></button>`)
    e só renderiza depois de rolar a lista até o fim. A seta 'Avançar' (PT) também existe.
    Estratégia: rola até a paginação aparecer → clica o número atual+1 → fallback seta."""
    def _scroll_ate_paginacao():
        # A paginação fica no rodapé da lista esquerda; rola o container/janela até ela.
        for _ in range(6):
            pag = driver.find_elements(By.CSS_SELECTOR, ".artdeco-pagination, [class*='pagination']")
            if pag and any(p.is_displayed() for p in pag):
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});",
                                          next(p for p in pag if p.is_displayed()))
                except Exception:
                    pass
                return True
            for sel in [".jobs-search-results-list", ".scaffold-layout__list",
                        "[class*='jobs-search-results']"]:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].is_displayed():
                    driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", els[0])
                    break
            else:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        return False

    def _pagina_atual():
        # Retorna o número da página selecionada, ou 0 se não achar.
        for sel in ["li.active[data-test-pagination-page-btn]",
                    "li.selected[data-test-pagination-page-btn]",
                    ".artdeco-pagination__indicator--number.active",
                    ".artdeco-pagination__indicator--number.selected",
                    "button[aria-current='true']", "button[aria-current='page']",
                    "[aria-current='true']", "[aria-current='page']"]:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                txt = (el.get_attribute("data-test-pagination-page-btn") or el.text or "").strip()
                # pode vir "Página 2\n2" — pega o último token numérico
                for tok in reversed(txt.replace("\n", " ").split()):
                    if tok.isdigit():
                        return int(tok)
        return 0

    def _click_numero(n):
        # Tenta clicar diretamente o botão da página n.
        for sel in [f"li[data-test-pagination-page-btn='{n}'] button",
                    f"button[aria-label='Página {n}']",
                    f"button[aria-label='Page {n}']"]:
            for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                if btn.is_displayed() and btn.is_enabled():
                    driver.execute_script("arguments[0].click();", btn)
                    return True
        return False

    def _click_seta_next():
        for sel in [".artdeco-pagination__button--next",
                    "button[aria-label='Avançar']", "button[aria-label='Next']",
                    "button[aria-label='Próximo']", "button[aria-label='Próxima']"]:
            for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                if btn.is_displayed() and btn.is_enabled():
                    driver.execute_script("arguments[0].click();", btn)
                    return True
        return False

    def _navegar():
        _scroll_ate_paginacao()
        atual = _pagina_atual()
        # 1) Página conhecida → clica exatamente a próxima (forward garantido).
        if atual and _click_numero(atual + 1):
            print(f"[LINKEDIN] Paginação: página {atual} → {atual + 1}")
            return True
        # 2) Página desconhecida → a seta 'Avançar'/Next é forward-safe (nunca volta).
        #    Tentada ANTES de qualquer scan numérico cego (que poderia clicar uma
        #    página anterior e fazer o loop girar sem sair do lugar).
        if _click_seta_next():
            print("[LINKEDIN] Paginação: clicou seta Avançar/Next")
            return True
        print("[LINKEDIN] Paginação: nenhum controle de próxima página encontrado")
        return False

    try:
        result = await asyncio.wait_for(_run_in_thread(_navegar), timeout=12)
        if result:
            await asyncio.sleep(3)
        return bool(result)
    except Exception as e:
        logger.debug("_ir_proxima_pagina_vagas erro: %s", e)
        return False


_URL_LISTAGEM = "https://www.linkedin.com/jobs/collections/easy-apply/"

# Última listagem REAL onde o bot esteve (busca com filtros, easy-apply, etc.).
# A recuperação volta para CÁ — preservando filtros e a paginação numerada —
# em vez de sempre cair na collections genérica. Default: collections.
_ULTIMA_LISTAGEM_URL = _URL_LISTAGEM


def _limpar_url_listagem(url: str) -> str:
    """Remove o currentJobId (transitório) mas preserva keywords/filtros/start."""
    try:
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        parts = urlsplit(url)
        q = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() != "currentjobid"]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))
    except Exception:
        return url


def _lembrar_listagem(url: str) -> None:
    """Memoriza a URL de listagem atual para a recuperação voltar nela."""
    global _ULTIMA_LISTAGEM_URL
    if _url_eh_listagem(url) and "jobs/view" not in (url or "").lower():
        _ULTIMA_LISTAGEM_URL = _limpar_url_listagem(url)


def _registrar_run_log(linha: str) -> None:
    """Append-only num arquivo legível (não depende do scrollback do terminal).
    Cada decisão/branch grava uma linha com timestamp + URL. Lido por mim no
    próximo run para diagnosticar sem adivinhar."""
    import os, datetime
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_linkedin_run.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {linha}\n")
    except Exception:
        pass


def _url_eh_listagem(url: str) -> bool:
    """True se a URL é a listagem/feed real de vagas.
    NÃO conta: jobs-tracker (vagas aplicadas) nem post-apply (página "e agora?"
    de recomendações pós-candidatura, com cards diferentes — fazia o bot se
    perder nos cards). Esses disparam volta para o feed real."""
    u = (url or "").lower()
    if "jobs-tracker" in u or "stage=applied" in u or "post-apply" in u:
        return False
    return ("jobs/collections" in u) or ("jobs/search" in u) or ("jobs/view" in u)


async def _voltar_para_listagem(driver, motivo: str = "") -> bool:
    """Garante que o browser saia de jobs-tracker e volte para a listagem.

    LinkedIn redireciona para jobs-tracker?stage=applied após 'Concluído' numa
    candidatura. Um único driver.get nem sempre vence o redirect SPA — então
    navega, verifica a URL final e re-tenta. Retorna True se terminou numa
    listagem válida."""
    # Tenta voltar para a última listagem real (busca/filtros); fallback collections.
    alvos = [_ULTIMA_LISTAGEM_URL, _URL_LISTAGEM]
    for tentativa in range(3):
        alvo = alvos[min(tentativa, len(alvos) - 1)]
        try:
            await navegar(alvo)
        except Exception as e:
            _registrar_run_log(f"VOLTAR erro navegar ({motivo}): {e}")
        await asyncio.sleep(3)
        try:
            cur = await asyncio.wait_for(_run_in_thread(lambda: driver.current_url), timeout=5)
        except Exception:
            cur = ""
        if _url_eh_listagem(cur):
            _registrar_run_log(f"VOLTAR ok t{tentativa} ({motivo}) -> {cur[:80]}")
            return True
        _registrar_run_log(f"VOLTAR falhou t{tentativa} ({motivo}) alvo={alvo[:50]} ainda em {cur[:80]}")
        await asyncio.sleep(1.5)
    return False


async def _finalizar_sucesso(driver, motivo: str = "") -> None:
    """Fecha o modal de sucesso e só re-navega se o LinkedIn redirecionou para
    jobs-tracker. Evita o 'refresh' desnecessário (e o spin de re-cair na mesma
    vaga) quando o modal fecha sem sair da listagem."""
    await _fechar_modal_sucesso(driver)
    await asyncio.sleep(1.0)
    try:
        cur = await asyncio.wait_for(_run_in_thread(lambda: driver.current_url), timeout=5)
    except Exception:
        cur = ""
    if _url_eh_listagem(cur):
        _registrar_run_log(f"SUCESSO sem refresh ({motivo}) -> {cur[:70]}")
        return
    await _voltar_para_listagem(driver, motivo=motivo)


async def _tratar_apply_externo(driver, vaga_id: str, user_id: str) -> bool:
    """Trata candidaturas que NÃO são Easy Apply: o botão 'Candidatar-se' abre
    outra aba ou redireciona para o site da empresa (fora do LinkedIn). Sem isso
    o bot fica perdido fora do site e não vai para a próxima vaga.

    Fecha abas extras, volta ao feed se saiu do LinkedIn, marca a vaga como
    'apply_externo' (não automatizável) e retorna True para pular a vaga."""
    def _info():
        fechou = 0
        try:
            handles = driver.window_handles
            if len(handles) > 1:
                principal = handles[0]
                for h in handles[1:]:
                    try:
                        driver.switch_to.window(h)
                        driver.close()
                        fechou += 1
                    except Exception:
                        pass
                try:
                    driver.switch_to.window(principal)
                except Exception:
                    pass
        except Exception:
            pass
        url = ""
        try:
            url = driver.current_url
        except Exception:
            pass
        return fechou, url

    fechou, url = await _run_in_thread(_info)
    fora = "linkedin.com" not in (url or "").lower()
    if not fechou and not fora:
        return False
    _registrar_run_log(f"APPLY-EXTERNO vaga={vaga_id} abas_fechadas={fechou} fora={fora} url={url[:50]}")
    print(f"[LINKEDIN] {vaga_id}: candidatura externa/nova aba — pulando")
    if vaga_id:
        try:
            from graph.neo4j_client import get_neo4j
            get_neo4j().registrar_candidatura(
                user_id=user_id, vaga_id=vaga_id,
                plataforma="linkedin", status="apply_externo",
            )
        except Exception:
            pass
    if fora:
        await _voltar_para_listagem(driver, motivo="apply-externo")
    return True


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

        # Listagem real exigida no início: jobs-tracker contém "jobs" mas NÃO serve
        # (só mostra vagas já aplicadas → streak de ja_aplicado).
        if not _url_eh_listagem(current_url):
            await _voltar_para_listagem(driver, motivo="inicio")
        else:
            _lembrar_listagem(current_url)  # preserva a busca/filtros do usuário

        await notify_browser_step("selenium_linkedin", "iniciando", f"Página atual: {current_url}")
        await notify_browser_step("selenium_linkedin", "iniciando", f"Resumo curriculo: {'SIM' if (perfil.get('resumo_curriculo') or await _get_resumo_curriculo(user_id)) else 'NAO'}")

        resumo_curriculo = perfil.get("resumo_curriculo", "") or await _get_resumo_curriculo(user_id)
        curriculo_path = perfil.get("curriculo_path", "") or ""

        resultados = {"sucesso": True, "aplicacoes": [], "falhas": 0}
        vagas_tentadas_sessao: set = set()
        vagas_aplicadas = 0        # candidaturas submetidas (ou tentadas)
        sem_cards_count = 0        # cards vazios consecutivos → scroll / próxima página
        _MAX_SEM_CARDS = 4         # após N vezes sem card: tenta próxima página e desiste
        _MAX_ITER = max_vagas * 8  # teto absoluto de iterações (evita loop infinito)
        _iter = 0

        while vagas_aplicadas < max_vagas and sem_cards_count < _MAX_SEM_CARDS and _iter < _MAX_ITER:
            _iter += 1
            await wait_if_paused(None, f"vaga_{vagas_aplicadas+1}")
            await notify_browser_step(
                "selenium_linkedin", "buscando",
                f"Procurando vaga {vagas_aplicadas+1}/{max_vagas}..."
            )

            driver = await get_driver()
            if not driver:
                break

            # 0. Guarda anti-jobs-tracker: se o LinkedIn nos deixou parados na
            # aba de candidaturas (?stage=applied), os cards ali são vagas já
            # aplicadas — clicá-los gera streak de 'ja_aplicado' e nunca avança.
            # Volta para a listagem real ANTES de procurar card.
            try:
                _url_topo = await asyncio.wait_for(_run_in_thread(lambda: driver.current_url), timeout=5)
            except Exception:
                _url_topo = ""
            _registrar_run_log(f"ITER {_iter} url={_url_topo[:90]}")

            # Limpa modal 'Salvar candidatura?/Descartar' que possa ter sobrado de um
            # form abortado — senão fica sobreposto e bloqueia o clique no próximo card.
            try:
                await _fechar_modal_descarte(driver, manter=False)
            except Exception:
                pass

            if _url_eh_listagem(_url_topo):
                _lembrar_listagem(_url_topo)  # recuperação volta para a busca atual
            else:
                _registrar_run_log(f"ITER {_iter} fora da listagem — recuperando")
                await notify_browser_step("selenium_linkedin", "recuperando", "Saindo de jobs-tracker → listagem")
                ok_volta = await _voltar_para_listagem(driver, motivo=f"topo-iter-{_iter}")
                if not ok_volta:
                    sem_cards_count += 1
                    if sem_cards_count >= _MAX_SEM_CARDS:
                        _registrar_run_log(f"ITER {_iter} não conseguiu voltar à listagem — encerrando")
                        break
                    continue

            # 1. Clica no próximo card (timeout 12s — evita freeze)
            try:
                vaga_id, vaga_url = await asyncio.wait_for(
                    _clicar_proximo_card_vaga(driver, vagas_tentadas_sessao),
                    timeout=12
                )
            except asyncio.TimeoutError:
                logger.warning("linkedin_selenium: timeout ao buscar próximo card")
                vaga_id, vaga_url = "", ""
            except Exception as _ce:
                logger.warning("linkedin_selenium: erro ao buscar card: %s", _ce)
                vaga_id, vaga_url = "", ""

            # Se não achou card: rola a lista para carregar mais ou vai para próxima página
            if not vaga_id and not vaga_url:
                sem_cards_count += 1
                await notify_browser_step(
                    "selenium_linkedin", "scroll",
                    f"Sem cards visíveis ({sem_cards_count}/{_MAX_SEM_CARDS}) — carregando mais..."
                )
                await _scroll_lista_vagas(driver)
                await asyncio.sleep(2.5)
                if sem_cards_count >= _MAX_SEM_CARDS - 1:
                    # Tenta avançar para a próxima página (paginação numerada)
                    foi_proxima = await _ir_proxima_pagina_vagas(driver)
                    _registrar_run_log(f"PAGINACAO proxima={foi_proxima} iter={_iter}")
                    if foi_proxima:
                        sem_cards_count = 0
                        await asyncio.sleep(3)
                        print("[LINKEDIN] Avançou para a próxima página de vagas")
                        await notify_browser_step("selenium_linkedin", "pagina", "Próxima página de vagas")
                    else:
                        print("[LINKEDIN] Sem mais vagas para processar")
                continue  # tenta de novo sem contar como vaga processada

            sem_cards_count = 0
            vagas_tentadas_sessao.add(vaga_id or vaga_url)
            await notify_browser_step("selenium_linkedin", "card", f"Card: {vaga_id}")
            await asyncio.sleep(1.5)  # aguarda painel direito carregar

            # 2. Verifica já candidatado via Neo4j
            if vaga_id:
                try:
                    from graph.neo4j_client import get_neo4j
                    if get_neo4j().ja_se_candidatou(user_id, vaga_id):
                        print(f"[LINKEDIN] {vaga_id} já candidatada (Neo4j) — pulando")
                        _registrar_run_log(f"SKIP {vaga_id} já-candidatado (Neo4j)")
                        await notify_browser_step("selenium_linkedin", "pulando", f"{vaga_id} — já candidatou (Neo4j)")
                        continue
                except Exception:
                    pass

            # 3. Badge "Candidatou-se" no painel direito
            try:
                ja_na_pag = await asyncio.wait_for(_verificar_ja_aplicado_na_pagina(driver), timeout=5)
            except asyncio.TimeoutError:
                ja_na_pag = False
            if ja_na_pag:
                print(f"[LINKEDIN] {vaga_id} — badge 'Aplicado' visível — pulando")
                _registrar_run_log(f"SKIP {vaga_id} badge-Aplicado")
                await notify_browser_step("selenium_linkedin", "pulando", f"{vaga_id} — badge Aplicado")
                if vaga_id:
                    try:
                        from graph.neo4j_client import get_neo4j
                        get_neo4j().registrar_candidatura(
                            user_id=user_id, vaga_id=vaga_id,
                            plataforma="linkedin", status="ja_aplicado"
                        )
                    except Exception:
                        pass
                continue

            # 4. Match com currículo — verifica cache Neo4j antes de chamar LLM
            idioma_vaga = "pt"
            descricao_vaga = ""
            if vaga_id and resumo_curriculo:
                # 4a. Cache: se já avaliado negativamente, pula sem LLM
                try:
                    from graph.neo4j_client import get_neo4j
                    match_cache = get_neo4j().get_match_vaga(user_id, vaga_id)
                    if match_cache and not match_cache.get("aplicar", True):
                        motivo_cache = match_cache.get("motivo", "match ruim (Neo4j cache)")
                        print(f"[LINKEDIN] Sem match (Neo4j cache) — pulando {vaga_id}: {motivo_cache}")
                        _registrar_run_log(f"SKIP {vaga_id} sem-match-cache: {motivo_cache[:40]}")
                        await notify_browser_step("selenium_linkedin", "pulando", f"Cache: {motivo_cache}")
                        continue
                except Exception:
                    pass

            if resumo_curriculo:
                try:
                    descricao_vaga = await asyncio.wait_for(
                        _extrair_descricao_vaga(driver), timeout=8
                    )
                except asyncio.TimeoutError:
                    descricao_vaga = ""

                # 4b. Salva vaga no Neo4j com descrição (enriquece grafo)
                if descricao_vaga and vaga_id:
                    try:
                        from graph.neo4j_client import get_neo4j
                        def _titulo_empresa_rapido():
                            titulo_v, empresa_v = "", ""
                            for s in [".jobs-unified-top-card__job-title h1",
                                      ".job-details-jobs-unified-top-card__job-title h1",
                                      "h1[class*='job-title']", "h1"]:
                                els = driver.find_elements(By.CSS_SELECTOR, s)
                                for e in els:
                                    t = (e.text or "").strip()
                                    if t and len(t) < 120:
                                        titulo_v = t; break
                                if titulo_v: break
                            for s in [".jobs-unified-top-card__company-name a",
                                      ".job-details-jobs-unified-top-card__company-name",
                                      "a[data-tracking-control-name*='company']"]:
                                els = driver.find_elements(By.CSS_SELECTOR, s)
                                for e in els:
                                    t = (e.text or "").strip()
                                    if t and len(t) < 100:
                                        empresa_v = t; break
                                if empresa_v: break
                            return titulo_v, empresa_v
                        titulo_v, empresa_v = await _run_in_thread(_titulo_empresa_rapido)
                        get_neo4j().upsert_vaga({
                            "id": vaga_id,
                            "titulo": titulo_v,
                            "empresa": empresa_v,
                            "url": vaga_url,
                            "fonte": "linkedin",
                            "descricao": descricao_vaga,
                            "salario": "",
                            "modalidade": "",
                        })
                    except Exception as _uve:
                        logger.debug("linkedin_selenium: upsert_vaga falhou: %s", _uve)

                if descricao_vaga:
                    await notify_browser_step("selenium_linkedin", "avaliando", f"Verificando match: {vaga_id}")
                    try:
                        avaliacao = await asyncio.wait_for(
                            _avaliar_match_vaga(descricao_vaga, resumo_curriculo), timeout=30
                        )
                    except asyncio.TimeoutError:
                        avaliacao = {"aplicar": True, "idioma": "pt", "motivo": "timeout LLM"}
                    idioma_vaga = avaliacao.get("idioma", "pt")
                    logger.info("linkedin_selenium: vaga=%s match=%s idioma=%s motivo=%s",
                                vaga_id, avaliacao.get("aplicar"), idioma_vaga, avaliacao.get("motivo"))

                    # 4c. Salva avaliação de match no Neo4j para uso futuro (cache + priorização)
                    if vaga_id:
                        try:
                            from graph.neo4j_client import get_neo4j
                            get_neo4j().registrar_match_vaga(
                                user_id=user_id,
                                vaga_id=vaga_id,
                                aplicar=bool(avaliacao.get("aplicar", True)),
                                motivo=avaliacao.get("motivo", ""),
                            )
                        except Exception as _mve:
                            logger.debug("linkedin_selenium: registrar_match_vaga falhou: %s", _mve)

                    if not avaliacao.get("aplicar", True):
                        motivo = avaliacao.get("motivo", "sem match")
                        print(f"[LINKEDIN] Sem match — pulando {vaga_id}: {motivo}")
                        _registrar_run_log(f"SKIP {vaga_id} sem-match-LLM: {motivo[:50]}")
                        await notify_browser_step("selenium_linkedin", "pulando", f"Sem match: {motivo}")
                        continue

            # 5. Clica em Easy Apply (já visível no painel direito)
            await notify_browser_step("selenium_linkedin", "candidatando", f"Abrindo Easy Apply: {vaga_id}")
            clicou = await _clicar_easy_apply_js(driver)
            if not clicou:
                await asyncio.sleep(2)
                clicou = await _clicar_easy_apply_js(driver)
            if not clicou:
                print(f"[LINKEDIN] Easy Apply não encontrado para {vaga_id}")
                await notify_browser_step("selenium_linkedin", "pulando", f"{vaga_id} — Easy Apply não encontrado")
                continue

            await asyncio.sleep(2)
            # Candidatura externa (abre outra aba / sai do LinkedIn)? Limpa e pula.
            try:
                if await _tratar_apply_externo(driver, vaga_id, user_id):
                    continue
            except Exception:
                pass
            modal = await _aguardar_modal_easy_apply(driver)
            if not modal:
                # Verifica se LinkedIn redirecionou para jobs-tracker (já aplicado ou one-click Apply)
                try:
                    _url_pos = await asyncio.wait_for(
                        _run_in_thread(lambda: driver.current_url), timeout=5
                    )
                except Exception:
                    _url_pos = ""

                if "jobs-tracker" in _url_pos.lower() or "stage=applied" in _url_pos.lower():
                    print(f"[LINKEDIN] {vaga_id} → jobs-tracker detectado — registrando como ja_aplicado e voltando")
                    await notify_browser_step("selenium_linkedin", "sucesso", f"{vaga_id} — jobs-tracker (ja_aplicado)")
                    _registrar_run_log(f"BRANCH not-modal jobs-tracker vaga={vaga_id}")
                    if vaga_id:
                        try:
                            from graph.neo4j_client import get_neo4j
                            get_neo4j().registrar_candidatura(
                                user_id=user_id, vaga_id=vaga_id,
                                plataforma="linkedin", status="ja_aplicado",
                            )
                        except Exception as _re:
                            logger.warning("linkedin_selenium: erro ao registrar ja_aplicado: %s", _re)
                    await _voltar_para_listagem(driver, motivo="not-modal-jobs-tracker")
                    continue

                print(f"[LINKEDIN] Modal não apareceu para {vaga_id}")
                await notify_browser_step("selenium_linkedin", "pulando", f"{vaga_id} — modal não apareceu")
                try:
                    await _fechar_modal_descarte(driver, manter=False)
                except Exception:
                    pass
                # Garante retorno à listagem se URL saiu de jobs
                try:
                    _url_sem_modal = await asyncio.wait_for(
                        _run_in_thread(lambda: driver.current_url), timeout=5
                    )
                    if not _url_eh_listagem(_url_sem_modal):
                        await _voltar_para_listagem(driver, motivo="modal-nao-apareceu")
                except Exception:
                    pass
                continue

            # 6. Preenche formulário
            await notify_browser_step("selenium_linkedin", "formulario", f"Preenchendo: {vaga_id}")
            # Timeout protege contra um form/Selenium travado matar o run inteiro
            # ("morre no meio do caminho"). Se estourar, abandona e segue pra próxima.
            try:
                resultado = await asyncio.wait_for(
                    _processar_formulario_multistep_selenium(
                        driver, perfil, curriculo_path, vaga_url, resumo_curriculo, idioma=idioma_vaga
                    ),
                    timeout=150,
                )
            except asyncio.TimeoutError:
                _registrar_run_log(f"FORM TIMEOUT vaga={vaga_id} — abandonando e seguindo")
                print(f"[LINKEDIN] Form travou (timeout) em {vaga_id} — seguindo")
                try:
                    await _escapar_modal_ativo(driver)
                    await _voltar_para_listagem(driver, motivo="form-timeout")
                except Exception:
                    pass
                resultado = {"sucesso": False, "motivo_falha": "timeout"}
            resultados["aplicacoes"].append(resultado)
            if not resultado.get("sucesso"):
                resultados["falhas"] += 1
            vagas_aplicadas += 1

            # 7. Registra no Neo4j
            if vaga_id:
                try:
                    from graph.neo4j_client import get_neo4j
                    _status = "candidatado" if resultado.get("sucesso") else "tentativa_falhou"
                    get_neo4j().registrar_candidatura(
                        user_id=user_id, vaga_id=vaga_id,
                        plataforma="linkedin", status=_status,
                    )
                except Exception as _re:
                    logger.warning("linkedin_selenium: erro ao registrar candidatura: %s", _re)

            # 8. Após formulário: o 'Concluído' do LinkedIn redireciona para
            # jobs-tracker. Volta para a listagem com verificação + retry, senão
            # a próxima iteração fica presa clicando vagas já aplicadas.
            await asyncio.sleep(2)
            try:
                cur = await asyncio.wait_for(
                    _run_in_thread(lambda: driver.current_url), timeout=5
                )
                _registrar_run_log(f"POS-FORM vaga={vaga_id} sucesso={resultado.get('sucesso')} url={cur[:80]}")
                if not _url_eh_listagem(cur):
                    print(f"[LINKEDIN] Step 8: URL fora da listagem ({cur[:70]}) — voltando")
                    await _voltar_para_listagem(driver, motivo="pos-formulario")
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
    # Clica 'Concluído'/'Done' PRIMEIRO (finaliza a candidatura corretamente).
    # O redirect p/ post-apply/jobs-tracker que isso causa é tratado depois pelo
    # _voltar_para_listagem. X/Dismiss só como fallback se não houver Concluído.
    done_labels = [
        "concluído", "concluido", "done", "fechar", "close", "dismiss", "ok",
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


async def _fechar_modal_descarte(driver, manter: bool = True) -> bool:
    """Detecta e fecha modal 'Descartar candidatura' ou 'Salvar candidatura'.

    manter=True  → tenta clicar 'Continuar candidatura' (voltar ao form).
    manter=False → tenta clicar 'Descartar' (abandonar candidatura).
    Fallback em ambos os casos: Escape.
    """
    keep_labels = [
        "continue applying", "continuar candidatura", "continuar editando",
        "keep editing", "manter candidatura", "go back", "voltar",
        "não, continuar", "no, continue",
    ]
    discard_labels = [
        "descartar", "discard", "não salvar", "don't save", "delete", "excluir",
    ]
    discard_markers = ["descartar", "abandon", "discard"]
    save_markers = [
        "salvar esta candidatura", "save this application",
        "save application", "salve para voltar",
    ]

    def _dismiss():
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.common.action_chains import ActionChains

        def _escape():
            try:
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                return True
            except Exception:
                return False

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

                if any(m in text for m in save_markers):
                    # Modal "Salvar esta candidatura?" — ação depende de manter
                    btns = dialog.find_elements(By.TAG_NAME, "button")
                    target_labels = keep_labels if manter else discard_labels
                    for btn in btns:
                        try:
                            t = (btn.text or "").strip().lower()
                            if any(k in t for k in target_labels):
                                if btn.is_displayed() and btn.is_enabled():
                                    btn.click()
                                    print(f"[LINKEDIN] _fechar_modal_descarte: clicou '{btn.text.strip()}' (manter={manter})")
                                    return True
                        except Exception:
                            continue
                    print(f"[LINKEDIN] _fechar_modal_descarte: Escape em 'Salvar candidatura?' (manter={manter})")
                    return _escape()

                if not any(k in text for k in discard_markers):
                    continue

                # Modal de descarte "Tem certeza?" — tenta botão "Continuar" (sempre)
                btns = dialog.find_elements(By.TAG_NAME, "button")
                for btn in btns:
                    try:
                        t = (btn.text or "").strip().lower()
                        if any(k in t for k in keep_labels):
                            if btn.is_displayed() and btn.is_enabled():
                                btn.click()
                                print(f"[LINKEDIN] _fechar_modal_descarte: clicou 'Continuar' em modal de descarte")
                                return True
                    except Exception:
                        continue

                # Nenhum botão "Continuar" encontrado → Escape como fallback
                print("[LINKEDIN] _fechar_modal_descarte: Escape (fallback modal de descarte)")
                return _escape()
        except Exception:
            pass
        return False

    try:
        result = await _run_in_thread(_dismiss)
        if result:
            await asyncio.sleep(0.9)
        return result
    except Exception:
        return False


async def _escapar_modal_ativo(driver) -> None:
    """Fecha modal Easy Apply ativo (se aberto) antes de navegar para outra URL.

    Pressiona Escape para fechar o form. Se aparecer 'Salvar candidatura?', DESCARTA
    (manter=False) pois estamos abandonando esta candidatura de qualquer forma.
    """
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains

    def _has_modal():
        for sel in ["div[role='dialog']", ".artdeco-modal", ".jobs-easy-apply-modal"]:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if any(e.is_displayed() for e in els):
                return True
        return False

    def _escape():
        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        except Exception:
            pass

    try:
        if not await _run_in_thread(_has_modal):
            print("[LINKEDIN] _escapar_modal_ativo: nenhum modal detectado")
            return
        print("[LINKEDIN] _escapar_modal_ativo: modal detectado, enviando Escape")
        await _run_in_thread(_escape)
        await asyncio.sleep(1.0)
        # Escape pode ter exibido "Salvar candidatura?" — descarta (não queremos continuar)
        dispensado = await _fechar_modal_descarte(driver, manter=False)
        if dispensado:
            print("[LINKEDIN] _escapar_modal_ativo: 'Salvar candidatura?' descartado com sucesso")
        await asyncio.sleep(0.5)
    except Exception as e:
        print(f"[LINKEDIN] _escapar_modal_ativo: erro não crítico: {e}")


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


# Nota mínima (0-100) de relevância vaga×currículo para aplicar.
# 40 = "na dúvida, aplica": só pula o que é claramente de outra área (0-39).
# Ajustável via env LINKEDIN_LIMIAR_MATCH sem mexer no código.
_LIMIAR_MATCH = int(os.getenv("LINKEDIN_LIMIAR_MATCH", "40"))


async def _avaliar_match_vaga(descricao: str, curriculo: str) -> dict:
    """
    Avalia se a vaga é compatível com o currículo via LLM barato.
    Rigoroso: exige correlação real de cargo/área (nota >= _LIMIAR_MATCH).
    Fail-open: se não der para avaliar (sem dados, timeout, erro), aplica.
    Retorna: {"aplicar": bool, "idioma": "pt"|"en", "motivo": str}
    """
    if not descricao or not curriculo:
        return {"aplicar": True, "idioma": "pt", "motivo": "sem dados para avaliar"}

    import json as _json
    from ai.openrouter import openrouter

    prompt = f"""You are a strict recruiter deciding whether a candidate should apply to a job, based on how well the job matches their resume.

JOB DESCRIPTION (first 2000 chars):
{descricao[:2000]}

CANDIDATE RESUME (first 1500 chars):
{curriculo[:1500]}

Score the relevance from 0 to 100. The candidate prefers to apply WHENEVER there is a plausible fit ("when in doubt, apply"). Only score LOW for jobs that are clearly a different profession.
- Score below 40 ONLY if the core role is a clearly different profession/field with little overlap (e.g. a software developer resume vs nursing, accounting, pure sales, recruiter, designer, hardware technician).
- If the job is in tech/software and shares ANY meaningful overlap with the resume (language, framework, backend/frontend/fullstack, similar domain), score 40 or above so it applies — even if some requirements are missing or seniority differs.
- Reward overlap; do not penalize heavily for missing a few requirements or a seniority gap.

Scoring guide: 80-100 strong; 60-79 good; 40-59 plausible (still APPLY); 0-39 clearly different profession (skip).

Respond with ONLY valid JSON, no markdown, no explanation:
{{"nota": 0, "idioma": "pt", "motivo": "brief reason"}}
- "nota": integer 0-100 as defined above
- "idioma": language of the job description — "pt" for Portuguese, "en" for English
- "motivo": one sentence explaining the score"""

    try:
        resp = openrouter.converse([{"role": "user", "content": prompt}])
        resp = resp.strip()
        json_match = re.search(r'\{[^{}]+\}', resp, re.DOTALL)
        if json_match:
            data = _json.loads(json_match.group())
            idioma = str(data.get("idioma", "pt"))
            motivo = str(data.get("motivo", ""))
            # Decisão determinística pelo score (mais confiável que o booleano do LLM).
            # Se a nota vier ausente/ilegível, mantém fail-open (aplica) por escolha do usuário.
            try:
                nota = int(float(data.get("nota")))
                aplicar = nota >= _LIMIAR_MATCH
                return {
                    "aplicar": aplicar,
                    "idioma": idioma,
                    "motivo": f"nota {nota}/100 — {motivo}",
                }
            except (TypeError, ValueError):
                return {"aplicar": True, "idioma": idioma, "motivo": f"sem nota — {motivo}"}
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
