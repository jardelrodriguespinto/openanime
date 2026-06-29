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
    _scroll_and_focus_element
)
from automation.browser import notify_browser_step, get_intervention_state
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
        title_ok = False
        if driver:
            try:
                title_ok = bool(await get_title())
            except Exception:
                pass
        if driver and await _driver_session_valida() and title_ok:
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

        current_url = driver.current_url if driver else ""
        print(f"[LINKEDIN] Logado com sucesso. URL: {current_url}")

        vaga_url_limpa = _limpar_url_vaga(vaga_url)
        current_url = driver.current_url if driver else ""

        if "/jobs/view/" not in current_url:
            await notify_browser_step("selenium_linkedin", "navegando", f"Abrindo vaga")
            await navegar(vaga_url_limpa)
            await asyncio.sleep(5)
            current_url = driver.current_url if driver else ""
            if "checkpoint" in current_url.lower() or "challenge" in current_url.lower():
                print("[LINKEDIN] Bloqueio detectado (checkpoint/challenge)")
                b64 = await screenshot_base64()
                await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
                return {"sucesso": False, "mensagem": "Verificação de segurança detectada. Candidate-se manualmente.", "screenshot": b64[:100] if b64 else ""}
        else:
            print(f"[LINKEDIN] Já está na página da vaga")

        try:
            await _dismissar_cookie_banner(driver)
        except Exception:
            pass

        html_check = None
        try:
            html_check = driver.page_source.lower()
        except Exception:
            pass
        sinais_bloqueio = ["verify you're a human", "unusual activity", "captcha",
            "security check", "robot", "bot detection", "access denied",
            "forbidden", "cloudflare", "just a moment", "checking your browser"]
        if html_check and any(sinal in html_check for sinal in sinais_bloqueio):
            print("[LINKEDIN] Bloqueio detectado (HTML)")
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            return {"sucesso": False, "mensagem": "Verificação de segurança detectada. Candidate-se manualmente.", "screenshot": b64[:100] if b64 else ""}

        print(f"[LINKEDIN] Vaga carregada: {await get_title()}")

        await notify_browser_step("selenium_linkedin", "easy_apply", "Procurando Easy Apply...")
        easy_btn = await wait_for_selector_visible(
            "button.jobs-apply-button, button[data-control-name='apply_show_modal'], "
            "button[aria-label*='Easy Apply'], button[aria-label*='Candidatura simplificada'], "
            ".jobs-apply-button, [data-control-name='apply_show_modal'], "
            "[aria-label*='Easy Apply'], [aria-label*='Candidatura simplificada']",
            timeout=10
        )

        if not easy_btn:
            print("[LINKEDIN] Botao Easy Apply NAO encontrado")
            await notify_browser_step("selenium_linkedin", "erro", "Easy Apply nao encontrado")
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            return {"sucesso": False, "mensagem": "Easy Apply nao encontrado nesta vaga.", "screenshot": b64[:100] if b64 else ""}

        clicou = await click(
            "button.jobs-apply-button, button[data-control-name='apply_show_modal'], "
            "button[aria-label*='Easy Apply'], button[aria-label*='Candidatura simplificada'], "
            ".jobs-apply-button, [data-control-name='apply_show_modal'], "
            "[aria-label*='Easy Apply'], [aria-label*='Candidatura simplificada']"
        )

        if not clicou:
            print("[LINKEDIN] Falhou clique CSS, tentando JavaScript...")
            driver = await get_driver()
            if driver:
                clicou = await _clicar_easy_apply_js(driver)

        if not clicou:
            print("[LINKEDIN] JavaScript tambem falhou, aguardando e tentando novamente...")
            await asyncio.sleep(3)
            easy_btn = await wait_for_selector_visible(
                "button.jobs-apply-button, button[data-control-name='apply_show_modal'], "
                "button[aria-label*='Easy Apply'], button[aria-label*='Candidatura simplificada'], "
                ".jobs-apply-button, [data-control-name='apply_show_modal'], "
                "[aria-label*='Easy Apply'], [aria-label*='Candidatura simplificada']",
                timeout=10
            )
            if easy_btn:
                clicou = await click(
                    "button.jobs-apply-button, button[data-control-name='apply_show_modal'], "
                    "button[aria-label*='Easy Apply'], button[aria-label*='Candidatura simplificada'], "
                    ".jobs-apply-button, [data-control-name='apply_show_modal'], "
                    "[aria-label*='Easy Apply'], [aria-label*='Candidatura simplificada']"
                )

        if not clicou:
            print("[LINKEDIN] Botao Easy Apply NAO encontrado")
            await notify_browser_step("selenium_linkedin", "erro", "Easy Apply nao encontrado")
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            return {"sucesso": False, "mensagem": "Easy Apply nao encontrado nesta vaga.", "screenshot": b64[:100] if b64 else ""}

        print("[LINKEDIN] Clicou em Easy Apply")
        await asyncio.sleep(3)

        modal = await wait_for_selector_visible(".jobs-easy-apply-modal, .jobs-easy-apply__modal", timeout=10)
        if not modal:
            print("[LINKEDIN] Modal Easy Apply nao apareceu")
            await notify_browser_step("selenium_linkedin", "erro", "Modal nao apareceu")
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            return {"sucesso": False, "mensagem": "Modal Easy Apply nao apareceu.", "screenshot": b64[:100] if b64 else ""}

        print("[LINKEDIN] Modal aberto")

        await notify_browser_step("selenium_linkedin", "preenchendo", "Preenchendo formulário multi-step")
        resultado = await _processar_formulario_multistep_selenium(driver, perfil, curriculo_path, vaga_url, resumo_curriculo)
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


async def _processar_formulario_multistep_selenium(driver, perfil: dict, curriculo_path: str, vaga_url: str, resumo_curriculo: str) -> dict:
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
            control = await get_intervention_state()
            if control.get("paused") or control.get("intervention_type") == "manual":
                logger.info("linkedin_selenium: pausado pelo usuario no step %d", step)
                await notify_browser_step("step_"+str(step), "pausado", "Intervenção manual necessária")
                return {
                    "sucesso": False,
                    "pausado": True,
                    "mensagem": "Automação pausada pelo usuário — intervenção manual necessária",
                    "acao_necessaria": "intervencao_manual",
                    "step": step,
                }

            if control.get("current_action") == "pular":
                logger.info("linkedin_selenium: usuario pediu para pular step %d", step)
                await set_intervention_state("current_action", "rodando")
                await notify_browser_step("step_"+str(step), "pulado", "Usuário pediu pular")
                continue

            await asyncio.sleep(random.uniform(0.8, 1.5))
            await notify_browser_step("step_"+str(step), "preenchendo", "Preenchendo campos do formulário")
            await wait_if_paused(None, "step_"+str(step))

            await _preencher_step_selenium(driver, perfil, curriculo_path)

            perguntas_step = await _detectar_perguntas_nao_respondidas_selenium(driver)
            if perguntas_step:
                await notify_browser_step("step_"+str(step), "respondendo", f"{len(perguntas_step)} pergunta(s) customizada(s)")
                for pergunta in perguntas_step:
                    if pergunta not in respostas_geradas:
                        await wait_if_paused(None, "step_"+str(step))
                        resposta = responder_pergunta(
                            pergunta, perfil,
                            vaga_titulo=vaga_titulo,
                            vaga_empresa="",
                            resumo_curriculo=resumo_curriculo
                        )
                        respostas_geradas[pergunta] = resposta
                        await notify_browser_step("step_"+str(step), "respondendo", f"Pergunta: {pergunta[:40]}...")
                        await _preencher_resposta_customizada_selenium(driver, pergunta, resposta)
                        perguntas_customizadas.append(pergunta)
                        await asyncio.sleep(random.uniform(0.5, 1.0))

            if await _clicar_submit_selenium(driver, timeout=3000):
                await asyncio.sleep(2)
                await notify_browser_step("step_"+str(step), "enviando", "Submetendo candidatura")
                html = driver.page_source
                if _detectar_sucesso(html, driver.current_url):
                    logger.info("linkedin_selenium: candidatura enviada com sucesso")
                    await notify_browser_step("step_"+str(step), "sucesso", "Candidatura enviada!")
                    b64 = await screenshot_base64()
                    await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
                    await asyncio.sleep(2)
                    return {
                        "sucesso": True,
                        "perguntas_respondidas": perguntas_customizadas,
                        "mensagem": "Candidatura enviada com sucesso via LinkedIn Easy Apply!",
                        "screenshot": b64[:100] if b64 else "",
                    }

                if await _clicar_submit_selenium(driver, timeout=3000):
                    await asyncio.sleep(2)
                    html = driver.page_source
                    if _detectar_sucesso(html, driver.current_url):
                        await notify_browser_step("step_"+str(step), "sucesso", "Candidatura enviada!")
                        b64 = await screenshot_base64()
                        await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
                        await asyncio.sleep(2)
                        return {
                            "sucesso": True,
                            "perguntas_respondidas": perguntas_customizadas,
                            "mensagem": "Candidatura enviada com sucesso via LinkedIn Easy Apply!",
                            "screenshot": b64[:100] if b64 else "",
                        }

            await notify_browser_step("step_"+str(step), "navegando", "Avançando para próximo step")
            if await _clicar_next_selenium(driver, timeout=3000):
                await asyncio.sleep(2)
                continue

            break

        html = driver.page_source
        if _detectar_sucesso(html, driver.current_url):
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
            await asyncio.sleep(2)
            return {
                "sucesso": True,
                "perguntas_respondidas": perguntas_customizadas,
                "mensagem": "Candidatura enviada com sucesso via LinkedIn Easy Apply!",
                "screenshot": b64[:100] if b64 else "",
            }

        b64 = await screenshot_base64()
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
    """Preenche campos conhecidos no step atual via Selenium."""
    nome_completo = perfil.get("nome", "")
    partes = nome_completo.split()
    primeiro_nome = partes[0] if partes else nome_completo
    ultimo_nome = " ".join(partes[1:]) if len(partes) >= 2 else ""

    campos_nomes = [
        ("input[name='firstName'], #first-name, input[id*='firstName']", primeiro_nome),
        ("input[name='lastName'], #last-name, input[id*='lastName']", ultimo_nome),
        ("input[name='email'], #email, input[type='email'], input[placeholder*='Email']", perfil.get("email", _get_linkedin_email())),
        ("input[name='phone'], #phone, input[type='tel'], input[placeholder*='Phone'], input[aria-label*='Phone Number']", str(perfil.get("telefone", perfil.get("phone", "")))),
        ("input[aria-label*='City'], input[aria-label*='Cidade'], input[name*='city']", perfil.get("localizacao", "").split(",")[0].strip() if perfil.get("localizacao") else ""),
    ]
    for seletor, texto in campos_nomes:
        if not texto:
            continue
        if await digitar_com_delay(seletor, str(texto), delay_min=20, delay_max=60):
            try:
                print(f"[LINKEDIN] Preencheu: {seletor[:50]} = {texto[:30]}")
            except Exception:
                pass

    if curriculo_path:
        try:
            file_input = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
            for fi in file_input:
                if fi.is_displayed():
                    fi.send_keys(curriculo_path)
                    await asyncio.sleep(1.5)
                    break
        except Exception:
            pass

    try:
        selects = driver.find_elements(By.CSS_SELECTOR, "select")
        for sel_elem in selects:
            if not sel_elem.is_displayed():
                continue
            options = sel_elem.find_elements(By.TAG_NAME, "option")
            for opt in options:
                text = (opt.text or "").strip().lower()
                if text in ("yes", "sim", "authorized", "autorizado"):
                    val = opt.get_attribute("value")
                    if val:
                        from selenium.webdriver.support.ui import Select
                        Select(sel_elem).select_by_value(val)
                        break
    except Exception:
        pass


async def _detectar_perguntas_nao_respondidas_selenium(driver) -> list:
    """Detecta campos de pergunta ainda vazios no step atual via Selenium."""
    perguntas = []
    try:
        inputs = driver.find_elements(By.CSS_SELECTOR,
            "input[type='text']:not([value]), input[type='text'][value=''], textarea"
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
            if not current_val:
                perguntas.append(label)
    except Exception:
        pass
    return perguntas[:6]


async def _preencher_resposta_customizada_selenium(driver, pergunta: str, resposta: str) -> None:
    """Preenche campo de pergunta customizada pela label via Selenium."""
    try:
        labels = driver.find_elements(By.CSS_SELECTOR, "label")
        for label in labels:
            try:
                label_text = (label.text or "").lower()
            except Exception:
                continue
            if pergunta.lower()[:30] in label_text:
                label_for = label.get_attribute("for")
                if label_for:
                    el = driver.find_elements(By.ID, label_for)
                    if el and el[0].is_displayed():
                        tag = el[0].tag_name.lower()
                        if tag == "textarea":
                            el[0].clear()
                            el[0].send_keys(resposta[:500])
                        else:
                            el[0].clear()
                            el[0].send_keys(resposta[:200])
                        return
    except Exception as e:
        logger.debug("linkedin_selenium: erro ao preencher resposta: %s", e)


async def _clicar_submit_selenium(driver, timeout: int = 5) -> bool:
    """Clica no botao Submit/Enviar."""
    seletores = [
        "button[aria-label='Submit application']",
        "button[aria-label='Enviar candidatura']",
        "button[data-control-name='submit_apply']",
        "button[type='submit']",
    ]
    return await click_qualquer_selenium(driver, seletores, timeout)


async def _clicar_next_selenium(driver, timeout: int = 5) -> bool:
    """Clica no botao Next/Continuar."""
    seletores = [
        "button[aria-label='Continue to next step']",
        "button[aria-label='Continuar para a proxima etapa']",
        "button[data-easy-apply-next-button]",
        "footer button.artdeco-button--primary",
    ]
    return await click_qualquer_selenium(driver, seletores, timeout)


async def click_qualquer_selenium(driver, seletores: list, timeout: int = 5) -> bool:
    """Tenta clicar em qualquer um dos seletores fornecidos."""
    for sel in seletores:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el.is_displayed() and el.is_enabled():
                el.click()
                return True
        except Exception:
            continue
    # Fallback com JavaScript
    for sel in seletores:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el.is_displayed():
                driver.execute_script("arguments[0].click();", el)
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

            vaga_id = _extrair_vaga_id(vaga_url)
            if vaga_id:
                try:
                    from graph.neo4j_client import get_neo4j
                    neo4j = get_neo4j()
                    ja_aplicou = neo4j.ja_se_candidatou(user_id, vaga_id)
                    if ja_aplicou:
                        print(f"[LINKEDIN] Ja candidatou para {vaga_id} — pulando")
                        await notify_browser_step("selenium_linkedin", f"vaga_{i+1}", "Ja aplicou — pulando")
                        await asyncio.sleep(1)
                        continue
                except Exception:
                    pass

            await notify_browser_step("selenium_linkedin", f"vaga_{i+1}", f"Aplicando em: {vaga_url[:60]}...")

            resultado = await aplicar(vaga_url, perfil, user_id=user_id)
            resultados["aplicacoes"].append(resultado)
            if not resultado.get("sucesso"):
                resultados["falhas"] += 1

            driver = await get_driver()
            if driver:
                cur = driver.current_url
                if "jobs" not in cur.lower():
                    await navegar("https://www.linkedin.com/jobs/collections/easy-apply/")
                    await asyncio.sleep(2)

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


_JS_CLICK_EASY_APPLY = """
(() => {
    const textos = ['Candidatura simplificada', 'Easy Apply'];
    const seletor = 'button, a, [role="button"], span[role="button"], div[role="button"]';
    const elementos = document.querySelectorAll(seletor);
    for (const el of elementos) {
        for (const texto of textos) {
            const aria = (el.getAttribute('aria-label') || '').toLowerCase();
            const inner = (el.textContent || el.innerText || '').toLowerCase();
            if (aria.includes(texto.toLowerCase()) || inner.includes(texto.toLowerCase())) {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    el.click();
                    return 'clicou:' + texto;
                }
            }
        }
    }
    return null;
})()
"""


async def _clicar_easy_apply_js(driver) -> bool:
    try:
        resultado = await _run_in_thread(driver.execute_script, _JS_CLICK_EASY_APPLY)
        if resultado and resultado.startswith('clicou:'):
            print(f"[LINKEDIN] Clicou via JS em: {resultado.split(':')[1]}")
            return True
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
                buttons = driver.find_elements(By.XPATH, "//*[contains(@class,'jobs-apply-button') and contains(@aria-label,'Easy Apply')]")
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
                        apply_btn = card.find_element(By.CSS_SELECTOR, "button.jobs-apply-button, button[data-control-name='apply_show_modal'], .jobs-apply-button, [data-control-name='apply_show_modal']")
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
                        "button[aria-label*='Easy Apply'], button[aria-label*='Candidatura simplificada'], "
                        ".jobs-apply-button, [data-control-name='apply_show_modal'], "
                        "[aria-label*='Easy Apply'], [aria-label*='Candidatura simplificada']"
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
