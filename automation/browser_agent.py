"""
Agente de busca de vagas usando fontes reais (Indeed, Gupy, LinkedIn, etc.)
sem depender de browser visível. Usa scraping/API das plataformas.
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BROWSER_USE_AVAILABLE = True


async def _get_driver_title() -> str:
    """Retorna o titulo da pagina atual do driver Selenium."""
    from automation.selenium_browser import get_driver, get_title
    try:
        return await get_title()
    except Exception:
        return ""


async def buscar_vagas_browser_use(query: str) -> list[dict]:
    """
    Busca vagas em multiplas plataformas (Indeed, Gupy, etc.)
    e abre browser visivel para o usuario acompanhar.
    """
    import webbrowser
    import time

    search_query = query.replace(" ", "+")
    linkedin_url = f"https://www.linkedin.com/jobs/search/?keywords={search_query}&location=Brasil"
    print(f"[BROWSER_AGENT] Abrindo browser: {linkedin_url}")
    webbrowser.open(linkedin_url)
    time.sleep(1)
    print("[BROWSER_AGENT] Browser aberto")

    try:
        from data.jobs import buscar_vagas

        def _buscar():
            return buscar_vagas(query, localizacao="Brasil", limite=20)

        vagas = await asyncio.to_thread(_buscar)
        resultados = []
        for v in vagas:
            resultados.append({
                "id": v.id,
                "titulo": v.titulo,
                "empresa": v.empresa,
                "url": v.url,
                "fonte": v.fonte,
                "localizacao": v.localizacao,
                "modalidade": v.modalidade,
                "salario": v.salario,
                "descricao": v.descricao or "",
                "requisitos": v.requisitos or [],
                "easy_apply": getattr(v, "easy_apply", False),
            })
        logger.info("browser_agent: %d vagas encontradas para '%s'", len(resultados), query)
        return resultados
    except Exception as e:
        logger.error("buscar_vagas_browser_use erro: %s", e)
        return []


async def buscar_vagas_com_browser_visivel(query: str) -> list[dict]:
    """Alias de compatibilidade."""
    return await buscar_vagas_browser_use(query)


async def _aplicar_linkedin_selenium(vaga_url: str, perfil: dict) -> dict:
    """Aplica no LinkedIn Easy Apply usando Selenium + Firefox - reutiliza browser se existir."""
    from automation.browser import notify_browser_step
    from automation.selenium_browser import (
        nova_pagina, navegar, wait_for_selector, wait_for_selector_visible,
        click, digitar, digitar_com_delay, screenshot_base64, fechar, get_driver, _run_in_thread,
        clicar_entrar_com_email
    )
    from urllib.parse import urlparse, parse_qs

    try:
        await notify_browser_step("selenium_linkedin", "iniciando", f"Aplicando com Selenium em: {vaga_url}")
        print(f"[SELENIUM] Aplicando em: {vaga_url}")

        email = os.getenv("LINKEDIN_EMAIL", "")
        password = os.getenv("LINKEDIN_PASSWORD", "")
        if not email or not password:
            await fechar()
            return {"sucesso": False, "mensagem": "Configure LINKEDIN_EMAIL e LINKEDIN_PASSWORD no .env para candidatura automatica."}

        driver = await get_driver()
        if driver and await _verifica_sessao_valida():
            print("[SELENIUM] Reutilizando browser existente")
            await navegar("https://www.linkedin.com")
            await asyncio.sleep(3)
            current_url = await _run_in_thread(lambda: driver.current_url)
            title = await get_title()
        else:
            if driver:
                print("[SELENIUM] Sessão inválida - fechando browser antigo")
                try:
                    await fechar()
                except Exception:
                    pass
            print("[SELENIUM] Abrindo novo browser")
            await nova_pagina("https://www.linkedin.com")
            await asyncio.sleep(3)
            driver = await get_driver()
            current_url = await _run_in_thread(lambda: driver.current_url) if driver else ""
            title = await get_title() if driver else ""

        print(f"[SELENIUM] LinkedIn carregado: {current_url}")

        if "login" in current_url.lower() or "sign in" in title.lower():
            await notify_browser_step("selenium_linkedin", "login", "Fazendo login...")
            login_ok = await _fazer_login_selenium(email, password)
            print(f"[SELENIUM] Login enviado: {login_ok}")
        else:
            print("[SELENIUM] Aparentemente já logado")

        driver = await get_driver()
        if driver:
            await navegar(vaga_url)
            await asyncio.sleep(3)
            print(f"[SELENIUM] Vaga aberta: {await get_title()}")

        # Navega para a vaga (na mesma janela, nao cria nova)
        await notify_browser_step("selenium_linkedin", "navegando", f"Abrindo vaga: {vaga_url}")
        await navegar(vaga_url)
        await asyncio.sleep(3)
        print(f"[SELENIUM] Vaga aberta: {await _get_driver_title()}")

        # Tenta encontrar e clicar em Easy Apply
        await notify_browser_step("selenium_linkedin", "easy_apply", "Procurando Easy Apply...")
        easy_apply_selectors = [
            "button.jobs-apply-button",
            "button[data-control-name='apply_show_modal']",
            "button.jobs-s-apply button",
            ".jobs-apply-button--top-card",
        ]
        clicked = False
        for sel in easy_apply_selectors:
            if await click(sel, timeout=5):
                clicked = True
                print(f"[SELENIUM] Clicou em Easy Apply: {sel}")
                break

        if not clicked:
            await notify_browser_step("selenium_linkedin", "erro", "Botao Easy Apply nao encontrado")
            print("[SELENIUM] Easy Apply nao encontrado")
            await navegar("https://www.linkedin.com")  # Mantém browser aberto
            return {"sucesso": False, "mensagem": "Easy Apply nao encontrado. Vaga pode exigir aplicacao externa."}

        await asyncio.sleep(2)

        # Verifica se abriu o modal
        modal = await wait_for_selector_visible(".jobs-easy-apply-modal", timeout=10)
        if not modal:
            await notify_browser_step("selenium_linkedin", "erro", "Modal Easy Apply nao apareceu")
            print("[SELENIUM] Modal nao apareceu")
            await navegar("https://www.linkedin.com")  # Mantém browser aberto
            return {"sucesso": False, "mensagem": "Modal Easy Apply nao apareceu"}

        print("[SELENIUM] Modal Easy Apply aberto")

        # Preenche campos básicos no modal
        filled = 0
        campos_preenchidos = 0

        # Nome completo
        if await digitar_com_delay("input[name='firstName'], #firstName, input[id*='firstName']", perfil.get("nome", ""), delay_min=20, delay_max=50):
            campos_preenchidos += 1
        if await digitar_com_delay("input[name='lastName'], #lastName, input[id*='lastName']", perfil.get("sobrenome", perfil.get("nome", "").split()[-1] if perfil.get("nome") else ""), delay_min=20, delay_max=50):
            campos_preenchidos += 1

        # Email
        if await digitar_com_delay("input[name='email'], #email, input[type='email']", perfil.get("email", os.getenv("LINKEDIN_EMAIL", "")), delay_min=20, delay_max=50):
            campos_preenchidos += 1

        # Telefone
        telefone = perfil.get("telefone", perfil.get("phone", ""))
        if telefone:
            if await digitar_com_delay("input[name='phone'], #phone, input[type='tel']", str(telefone), delay_min=20, delay_max=50):
                campos_preenchidos += 1

        # Cidade
        if perfil.get("cidade"):
            if await digitar_com_delay("input[name='city'], #city, input[id*='city']", perfil.get("cidade", ""), delay_min=20, delay_max=50):
                campos_preenchidos += 1

        print(f"[SELENIUM] Campos preenchidos: {campos_preenchidos}")

        # Tenta encontrar e clicar em "Enviar" ou "Submit"
        await notify_browser_step("selenium_linkedin", "enviando", "Enviando candidatura...")
        submit_selectors = [
            "button[aria-label='Enviar candidatura']",
            "button[aria-label='Submit application']",
            "button.jobs-apply-button",
            "button[data-control-name='submit_apply']",
            "button[type='submit']",
        ]
        submitted = False
        for sel in submit_selectors:
            if await click(sel, timeout=5):
                submitted = True
                print(f"[SELENIUM] Clicou em Enviar: {sel}")
                break

        if submitted:
            await asyncio.sleep(2)
            await notify_browser_step("selenium_linkedin", "sucesso", "Candidatura enviada!")
            print("[SELENIUM] Candidatura enviada!")
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com")  # Volta para feed, mantém browser aberto
            return {"sucesso": True, "mensagem": "Candidatura enviada com Selenium!", "screenshot": b64[:100] if b64 else ""}
        else:
            await notify_browser_step("selenium_linkedin", "erro", "Botao enviar nao encontrado")
            print("[SELENIUM] Botao enviar nao encontrado")
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com")  # Volta para feed, mantém browser aberto
            return {"sucesso": False, "mensagem": "Botao enviar nao encontrado. Complete manualmente.", "screenshot": b64[:100] if b64 else ""}

    except Exception as e:
        logger.error(f"aplicar_linkedin_selenium erro: {e}")
        await notify_browser_step("selenium_linkedin", "erro", str(e))
        print(f"[SELENIUM] ERRO: {e}")
        try:
            await navegar("https://www.linkedin.com")  # Mantém browser aberto apesar do erro
        except Exception:
            pass
        return {"sucesso": False, "mensagem": str(e)}


async def _fazer_login_selenium(email: str, password: str) -> bool:
    """Faz login no LinkedIn usando Selenium com seletores robustos e espera por campos clicáveis."""
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver = await get_driver()
        if not driver:
            return False

        await navegar("https://www.linkedin.com/login")
        await asyncio.sleep(3)

        for tentativa in range(3):
            try:
                el_username = await _run_in_thread(
                    lambda: WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR,
                            "#username, input[name='session_key'], input[type='email'], input[autocomplete='username']"
                        ))
                    )
                )
                el_username = await _run_in_thread(
                    lambda: WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR,
                            "#username, input[name='session_key'], input[type='email'], input[autocomplete='username']"
                        ))
                    )
                )
                print(f"[SELENIUM] Campo username clicável (tentativa {tentativa+1})")
            except Exception:
                try:
                    el_username = await _run_in_thread(
                        lambda: driver.find_element(By.CSS_SELECTOR,
                            "#username, input[name='session_key'], input[type='email'], input[autocomplete='username']"
                        )
                    )
                    print(f"[SELENIUM] Campo username presente (tentativa {tentativa+1})")
                except Exception:
                    el_username = None

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
                    print("[SELENIUM] Detectado botão Google — procurando alternativa email/senha")
                    await clicar_entrar_com_email(driver)
                    await asyncio.sleep(2)

                if tentativa < 2:
                    await navegar("https://www.linkedin.com/login")
                    await asyncio.sleep(2)
                    continue
                print("[SELENIUM] Campo username não ficou disponível")
                return False
            return False

        email_ok = await digitar("#username", email)
        if not email_ok:
            email_ok = await digitar("input[name='session_key']", email)
        if not email_ok:
            email_ok = await digitar("input[type='email']", email)
        print(f"[SELENIUM] Email digitado: {email_ok}")

        await asyncio.sleep(0.5)

        pass_ok = await digitar("#password", password)
        if not pass_ok:
            pass_ok = await digitar("input[name='session_password']", password)
        if not pass_ok:
            pass_ok = await digitar("input[type='password']", password)
        print(f"[SELENIUM] Senha digitada: {pass_ok}")

        await asyncio.sleep(0.3)

        click_ok = await click("button[type='submit']")
        if not click_ok:
            click_ok = await click("button.sign-in-form__submit-button")
        if not click_ok:
            click_ok = await click("//button[contains(text(), 'Entrar')]")
        if not click_ok:
            click_ok = await click("//button[contains(., 'Sign in')]")
        print(f"[SELENIUM] Submit clicado: {click_ok}")
        await asyncio.sleep(5)

        for _ in range(30):
            await asyncio.sleep(1)
            try:
                cur = await _run_in_thread(lambda: driver.current_url)
                ttl = await get_title()
                if "feed" in cur or "mynetwork" in cur or "/in/" in cur:
                    print("[SELENIUM] Login OK")
                    return True
                if "checkpoint" in cur or "challenge" in cur:
                    print("[SELENIUM] Bloqueio de segurança")
                    return False
            except Exception:
                continue

        print("[SELENIUM] Login não confirmado em 30s")
        return False
    except Exception as e:
        print(f"[SELENIUM] Erro no login: {e}")
        return False


async def _verifica_sessao_valida() -> bool:
    """Verifica se o driver Selenium tem uma sessão ativa válida."""
    try:
        from automation.selenium_browser import get_driver
        driver = await get_driver()
        if driver is None:
            return False
        await _run_in_thread(lambda: driver.current_url)
        return True
    except Exception:
        return False


async def fechar_browser_linkedin():
    """Fecha o browser Selenium após terminar todas as candidaturas do lote."""
    from automation.selenium_browser import fechar as selenium_fechar, get_driver
    driver = await get_driver()
    if driver:
        try:
            await selenium_fechar()
            print("[SELENIUM] Browser fechado após lote")
        except Exception as ex:
            print(f"[SELENIUM] Erro ao fechar browser: {ex}")


async def aplicar_vaga_browser_use(vaga_url: str, perfil: dict) -> dict:
    """
    Aplica automaticamente se for LinkedIn Easy Apply (Selenium),
    senao abre o browser para preenchimento manual.
    """
    from automation.browser import notify_browser_step
    try:
        await notify_browser_step("aplicacao_browser", "aplicando", f"Analisando: {vaga_url}")

        if "linkedin.com" in vaga_url.lower():
            print("[BROWSER_AGENT] LinkedIn detectado - usando Selenium auto-apply")
            from automation.linkedin_selenium import aplicar as linkedin_aplicar
            resultado = await linkedin_aplicar(vaga_url, perfil)
            if resultado.get("sucesso"):
                return resultado

        import webbrowser
        import subprocess
        import shutil

        firefox_bin = shutil.which("firefox") or "/snap/firefox/8568/usr/lib/firefox/firefox"
        print(f"[BROWSER_AGENT] Abrindo browser manual: {firefox_bin} -> {vaga_url}")
        try:
            subprocess.Popen(
                [firefox_bin, "--new-window", vaga_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')}
            )
            print("[BROWSER_AGENT] Browser manual aberto")
        except Exception as e:
            print(f"[BROWSER_AGENT] Falha subprocess: {e}, tentando webbrowser")
            webbrowser.open(vaga_url)

        await notify_browser_step("aplicacao_browser", "navegando", "Browser aberto - preencha a candidatura manualmente")
        await notify_browser_step("aplicacao_browser", "aviso", "Complete a candidatura no navegador aberto")

        return {"sucesso": True, "mensagem": "Browser aberto - complete a candidatura manualmente"}
    except Exception as e:
        logger.error("aplicar_vaga_browser_visivel erro: %s", e)
        try:
            await notify_browser_step("aplicacao_browser", "falha", str(e))
        except Exception:
            pass
        return {"sucesso": False, "mensagem": str(e)}


async def aplicar_vagas_visiveis_linkedin(perfil: dict, max_vagas: int = 5) -> dict:
    """
    Aplica em vagas visíveis na página de busca do LinkedIn (Easy Apply).
    Útil após usar 'iniciar busca' no dashboard.
    """
    try:
        await notify_browser_step("selenium_linkedin", "iniciando", "Buscando vagas Easy Apply visíveis")
        from automation.linkedin_selenium import aplicar_vagas_visiveis_na_pagina
        resultado = await aplicar_vagas_visiveis_na_pagina(perfil, max_vagas)
        return resultado
    except Exception as e:
        logger.error("aplicar_vagas_visiveis_linkedin erro: %s", e)
        return {"sucesso": False, "mensagem": str(e)}