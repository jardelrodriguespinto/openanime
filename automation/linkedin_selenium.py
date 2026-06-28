"""
Candidatura via LinkedIn Easy Apply usando Selenium + Firefox visível.
Substitui Playwright que não funciona no Ubuntu 26.04.
"""

import asyncio
import logging
import os
import re

from automation.selenium_browser import (
    nova_pagina, navegar, wait_for_selector, wait_for_selector_visible,
    click, digitar, digitar_com_delay, screenshot_base64, fechar, get_driver, get_title, _run_in_thread
)
from automation.browser import notify_browser_step, get_intervention_state
from selenium.webdriver.common.by import By

logger = logging.getLogger(__name__)

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")


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


async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "") -> dict:
    """Aplica em vaga LinkedIn Easy Apply via Selenium."""
    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        return {
            "sucesso": False,
            "motivo_falha": "credenciais_ausentes",
            "mensagem": "Configure LINKEDIN_EMAIL e LINKEDIN_PASSWORD no .env.",
        }

    try:
        await notify_browser_step("selenium_linkedin", "iniciando", "Abrindo LinkedIn")
        print("[LINKEDIN] Iniciando aplicacao com Selenium")

        # Verifica se o driver existe e tem sessão válida
        driver = await get_driver()
        if driver and await _driver_session_valida():
            print("[LINKEDIN] Reutilizando browser existente")
            await navegar("https://www.linkedin.com")
            await asyncio.sleep(2)
        else:
            if driver:
                print("[LINKEDIN] Sessão inválida - fechando browser antigo")
                try:
                    await fechar()
                except Exception:
                    pass
            print("[LINKEDIN] Abrindo novo browser")
            await nova_pagina("https://www.linkedin.com")
            await asyncio.sleep(2)
        
        driver = await get_driver()
        current_url = driver.current_url
        print(f"[LINKEDIN] URL: {current_url} | Title: {await get_title()}")

        # Verifica se precisa login
        if "login" in current_url.lower() or "sign in" in (await get_title()).lower():
            await notify_browser_step("selenium_linkedin", "login", "Fazendo login...")
            print("[LINKEDIN] Fazendo login...")
            await digitar("#username", LINKEDIN_EMAIL)
            await digitar("#password", LINKEDIN_PASSWORD)
            await click("button[type='submit'], input[type='submit']")
            await asyncio.sleep(4)
            print(f"[LINKEDIN] Login enviado. URL: {driver.current_url}")

        # Navega para a vaga
        await notify_browser_step("selenium_linkedin", "navegando", f"Abrindo vaga")
        await navegar(vaga_url)
        await asyncio.sleep(3)
        print(f"[LINKEDIN] Vaga carregada: {await get_title()}")

        # Clica em Easy Apply (ingles ou portugues)
        await notify_browser_step("selenium_linkedin", "easy_apply", "Procurando Easy Apply...")
        easy_btn = await wait_for_selector_visible(
            "button.jobs-apply-button, button[data-control-name='apply_show_modal'], "
            "button:has-text('Easy Apply'), button:has-text('Candidatura simplificada')",
            timeout=10
        )

        if not easy_btn:
            print("[LINKEDIN] Botao Easy Apply NAO encontrado")
            await notify_browser_step("selenium_linkedin", "erro", "Easy Apply nao encontrado")
            b64 = await screenshot_base64()
            await navegar("about:blank")  # Limpa aba sem fechar browser
            return {"sucesso": False, "mensagem": "Easy Apply nao encontrado nesta vaga.", "screenshot": b64[:100] if b64 else ""}

        await click("button.jobs-apply-button, button[data-control-name='apply_show_modal']")
        print("[LINKEDIN] Clicou em Easy Apply")
        await asyncio.sleep(3)

        # Verifica se modal abriu
        modal = await wait_for_selector_visible(".jobs-easy-apply-modal", timeout=10)
        if not modal:
            print("[LINKEDIN] Modal Easy Apply nao apareceu")
            await notify_browser_step("selenium_linkedin", "erro", "Modal nao apareceu")
            b64 = await screenshot_base64()
            await navegar("about:blank")  # Limpa aba sem fechar browser
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
            ("input[name='email'], #email, input[type='email']", perfil.get("email", LINKEDIN_EMAIL)),
            ("input[name='phone'], #phone, input[type='tel']", str(perfil.get("telefone", perfil.get("phone", "")))),
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
                await navegar("https://www.linkedin.com")  # Volta para feed, mantém browser aberto
                return {"sucesso": True, "mensagem": "Candidatura enviada no LinkedIn!", "screenshot": b64[:100] if b64 else ""}
            else:
                print("[LINKEDIN] Enviou - aguardando confirmacao")
                await notify_browser_step("selenium_linkedin", "sucesso", "Candidatura enviada!")
                b64 = await screenshot_base64()
                await navegar("https://www.linkedin.com")  # Volta para feed, mantém browser aberto
                return {"sucesso": True, "mensagem": "Candidatura enviada (aguardando confirmacao).", "screenshot": b64[:100] if b64 else ""}
        else:
            print("[LINKEDIN] Botao enviar nao encontrado")
            await notify_browser_step("selenium_linkedin", "erro", "Botao enviar nao encontrado")
            b64 = await screenshot_base64()
            await navegar("https://www.linkedin.com")  # Volta para feed, mantém browser aberto
            return {"sucesso": False, "mensagem": "Botao enviar nao encontrado. Complete manualmente.", "screenshot": b64[:100] if b64 else ""}

    except Exception as e:
        logger.error(f"aplicar_linkedin_selenium erro: {e}")
        await notify_browser_step("selenium_linkedin", "erro", str(e))
        print(f"[LINKEDIN] ERRO: {e}")
        try:
            await navegar("https://www.linkedin.com")  # Volta para feed apesar do erro
        except Exception:
            pass
        return {"sucesso": False, "mensagem": str(e)}


async def aplicar_vagas_visiveis_na_pagina(perfil: dict, max_vagas: int = 5) -> dict:
    """
    Aplica em vagas visíveis na página de busca do LinkedIn.
    Procura por botões Easy Apply na página e aplica em até max_vagas vagas.
    """
    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        return {
            "sucesso": False,
            "motivo_falha": "credenciais_ausentes",
            "mensagem": "Configure LINKEDIN_EMAIL e LINKEDIN_PASSWORD no .env.",
        }

    try:
        await notify_browser_step("selenium_linkedin", "iniciando", "Buscando vagas visíveis na página")
        driver = await get_driver()
        
        if not driver:
            await nova_pagina("https://www.linkedin.com")
            await asyncio.sleep(2)
            driver = await get_driver()
        
        if not await _driver_session_valida():
            print("[LINKEDIN] Sessão inválida - reabrindo browser")
            await fechar()
            await nova_pagina("https://www.linkedin.com/jobs/search/?keywords=desenvolvedor&location=Brasil")
            await asyncio.sleep(3)
            driver = await get_driver()

        current_url = driver.current_url
        if "jobs/search" not in current_url:
            await navegar("https://www.linkedin.com/jobs/search/?keywords=desenvolvedor&location=Brasil")
            await asyncio.sleep(3)

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
            # Estratégia 1: Procura por botão Easy Apply e obtém o link da vaga associada
            try:
                buttons = driver.find_elements(By.XPATH, "//button[contains(@class,'jobs-apply-button') and contains(@aria-label,'Easy Apply')]")
                for btn in buttons[:3]:
                    try:
                        # Navega do botão até o link da vaga
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
                job_cards = driver.find_elements(By.CSS_SELECTOR, ".job-card-container, .jobs-search__result-card, .base-card")
                for card in job_cards[:10]:
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
    Tenta primeiro usar a página ativa do Playwright (se existir),
    senão usa o browser Selenium. Navega para busca se necessário.
    """
    # Tenta usar página ativa do Playwright primeiro (browser já aberto pelo usuário)
    try:
        from automation.browser import get_active_page
        page = await get_active_page()
        if page and "linkedin.com" in (page.url or ""):
            print("[LINKEDIN] Usando página Playwright ativa")
            resultado = await _extrair_vagas_playwright(page, max_vagas)
            if resultado.get("vagas"):
                return resultado
    except Exception as e:
        print(f"[LINKEDIN] Falha ao usar Playwright ativo: {e}")

    driver = await get_driver()

    if not driver:
        await nova_pagina("https://www.linkedin.com/jobs/search/?keywords=desenvolvedor&location=Brasil")
        await asyncio.sleep(3)
        driver = await get_driver()

    if not await _driver_session_valida():
        print("[LINKEDIN] Sessão inválida na extração - reabrindo browser")
        await fechar()
        await nova_pagina("https://www.linkedin.com/jobs/search/?keywords=desenvolvedor&location=Brasil")
        await asyncio.sleep(3)
        driver = await get_driver()

    current_url = driver.current_url
    if "jobs/search" not in current_url:
        await navegar("https://www.linkedin.com/jobs/search/?keywords=desenvolvedor&location=Brasil")
        await asyncio.sleep(3)

    await notify_browser_step("linkedin_extracao", "iniciando", "Extraindo vagas da página aberta")

    cards = await _extrair_cards_vaga(max_vagas)

    await notify_browser_step("linkedin_extracao", "finalizando", f"Extraídas {len(cards)} vagas")
    return {"sucesso": True, "vagas": cards, "total": len(cards)}


async def _extrair_vagas_playwright(page, max_vagas: int = 20) -> dict:
    """Extrai vagas usando uma página Playwright já ativa."""
    try:
        cards = await page.query_selector_all(".job-card-container, .jobs-search__result-card, .base-card")
        if not cards:
            return {"sucesso": False, "vagas": [], "mensagem": "Nenhum card de vaga encontrado"}

        resultados = []
        for card in cards[:max_vagas]:
            try:
                titulo = ""
                titulo_el = await card.query_selector(".job-card-list__title, .base-search-card__title, h3, a[data-control-name='job_card_title']")
                if titulo_el:
                    titulo = (await titulo_el.inner_text()).strip()
                if not titulo:
                    link_el = await card.query_selector("a[href*='/jobs/view/']")
                    if link_el:
                        titulo = (await link_el.inner_text()).strip()

                empresa = ""
                emp_el = await card.query_selector(".job-card-container__primary-description, .base-search-card__subtitle, .job-card-container__company-name")
                if emp_el:
                    empresa = (await emp_el.inner_text()).strip()

                url = ""
                link_el = await card.query_selector("a[href*='/jobs/view/']")
                if link_el:
                    url = await link_el.get_attribute("href") or ""

                modalidade = ""
                mod_el = await card.query_selector(".job-card-container__footer, .job-search-card__benefits")
                if mod_el:
                    modalidade = (await mod_el.inner_text()).strip()

                local = ""
                loc_el = await card.query_selector(".job-card-container__metadata, .job-search-card__location, .base-search-card__metadata")
                if loc_el:
                    local = (await loc_el.inner_text()).strip()

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
        cards = driver.find_elements(By.CSS_SELECTOR, ".job-card-container, .jobs-search__result-card, .base-card")
        if not cards:
            return resultados

        for card in cards[:max_vagas]:
            try:
                titulo = ""
                try:
                    el = card.find_element(By.CSS_SELECTOR, ".job-card-list__title, .base-search-card__title, h3, a[data-control-name='job_card_title']")
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
                    el = card.find_element(By.CSS_SELECTOR, ".job-card-container__primary-description, .base-search-card__subtitle, .job-card-container__company-name")
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
                    el = card.find_element(By.CSS_SELECTOR, ".job-card-container__metadata, .job-search-card__location, .base-search-card__metadata")
                    local = el.text.strip()
                except Exception:
                    pass

                modalidade = ""
                try:
                    el = card.find_element(By.CSS_SELECTOR, ".job-card-container__footer, .job-search-card__benefits")
                    modalidade = el.text.strip()
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
                    })
            except Exception:
                continue
        return resultados

    return await _run_in_thread(_procurar)
