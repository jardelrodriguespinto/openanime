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
    click, digitar, digitar_com_delay, screenshot_base64, fechar, get_driver, get_title
)
from automation.browser import notify_browser_step
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

        # Abre LinkedIn
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
            await fechar()
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
            await fechar()
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
                await fechar()
                return {"sucesso": True, "mensagem": "Candidatura enviada no LinkedIn!", "screenshot": b64[:100] if b64 else ""}
            else:
                print("[LINKEDIN] Enviou - aguardando confirmacao")
                await notify_browser_step("selenium_linkedin", "sucesso", "Candidatura enviada!")
                b64 = await screenshot_base64()
                await fechar()
                return {"sucesso": True, "mensagem": "Candidatura enviada (aguardando confirmacao).", "screenshot": b64[:100] if b64 else ""}
        else:
            print("[LINKEDIN] Botao enviar nao encontrado")
            await notify_browser_step("selenium_linkedin", "erro", "Botao enviar nao encontrado")
            b64 = await screenshot_base64()
            await fechar()
            return {"sucesso": False, "mensagem": "Botao enviar nao encontrado. Complete manualmente.", "screenshot": b64[:100] if b64 else ""}

    except Exception as e:
        logger.error(f"aplicar_linkedin_selenium erro: {e}")
        await notify_browser_step("selenium_linkedin", "erro", str(e))
        print(f"[LINKEDIN] ERRO: {e}")
        try:
            await fechar()
        except Exception:
            pass
        return {"sucesso": False, "mensagem": str(e)}
