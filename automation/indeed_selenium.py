"""
Candidatura via Indeed usando Selenium + Firefox.
"""

import asyncio
import logging
import os

from automation.selenium_browser import (
    nova_pagina, navegar, wait_for_selector, wait_for_selector_visible,
    click, digitar, digitar_com_delay, screenshot_base64, fechar, get_driver, get_title
)
from automation.browser import notify_browser_step

logger = logging.getLogger(__name__)


async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "") -> dict:
    """Aplica em vaga Indeed via Selenium."""
    try:
        await notify_browser_step("selenium_indeed", "iniciando", "Abrindo Indeed")
        print(f"[INDEED] Aplicando em: {vaga_url}")

        await nova_pagina(vaga_url)
        await asyncio.sleep(3)
        driver = await get_driver()
        print(f"[INDEED] Carregado: {await get_title()[:80]} | URL: {driver.current_url[:100]}")

        # Indeed redireciona para continuar aplicacao
        if "apply" in driver.current_url.lower() or "candidatura" in (await get_title()).lower():
            print("[INDEED] Ja esta na pagina de aplicacao")

            campos = 0
            nome = perfil.get("nome", "")
            partes = nome.split()
            primeiro = partes[0] if partes else ""
            ultimo = " ".join(partes[1:]) if len(partes) > 1 else ""

            if await digitar_com_delay("input[name='firstName'], input[id*='firstName']", primeiro, delay_min=20, delay_max=50):
                campos += 1
            if await digitar_com_delay("input[name='lastName'], input[id*='lastName']", ultimo, delay_min=20, delay_max=50):
                campos += 1
            if await digitar_com_delay("input[name='email'], input[type='email']", perfil.get("email", ""), delay_min=20, delay_max=50):
                campos += 1
            telefone = perfil.get("telefone", perfil.get("phone", ""))
            if telefone and await digitar_com_delay("input[name='phone'], input[type='tel']", str(telefone), delay_min=20, delay_max=50):
                campos += 1

            print(f"[INDEED] Campos preenchidos: {campos}")

            await notify_browser_step("selenium_indeed", "enviando", "Enviando candidatura...")

            submit = await wait_for_selector_visible(
                "button[type='submit'], button.apply-button, .apply-button",
                timeout=10
            )
            if submit:
                await click("button[type='submit'], button.apply-button, .apply-button")
                await asyncio.sleep(3)
                print("[INDEED] Candidatura enviada!")
                await notify_browser_step("selenium_indeed", "sucesso", "Candidatura enviada!")
                b64 = await screenshot_base64()
                await fechar()
                return {"sucesso": True, "mensagem": "Candidatura enviada no Indeed!", "screenshot": b64[:100] if b64 else ""}
            else:
                print("[INDEED] Botao enviar nao encontrado")
                await notify_browser_step("selenium_indeed", "erro", "Botao enviar nao encontrado")
                b64 = await screenshot_base64()
                await fechar()
                return {"sucesso": False, "mensagem": "Botao enviar nao encontrado. Complete manualmente.", "screenshot": b64[:100] if b64 else ""}
        else:
            # Pagina de detalhes da vaga - procura botao aplicar
            aplicar_btn = await wait_for_selector_visible(
                "button.apply-button, .jobs-apply-button, a[href*='apply'], button[data-testid*='apply']",
                timeout=10
            )
            if aplicar_btn:
                await click("button.apply-button, .jobs-apply-button, a[href*='apply'], button[data-testid*='apply']")
                await asyncio.sleep(3)
                print("[INDEED] Clicou em Aplicar")
                await notify_browser_step("selenium_indeed", "clicou_aplicar", "Clicou em Aplicar")
                b64 = await screenshot_base64()
                await fechar()
                return {"sucesso": True, "mensagem": "Redirecionado para aplicacao externa.", "screenshot": b64[:100] if b64 else ""}
            else:
                print("[INDEED] Botao apply nao encontrado na pagina de detalhes")
                await notify_browser_step("selenium_indeed", "erro", "Botao apply nao encontrado")
                b64 = await screenshot_base64()
                await fechar()
                return {"sucesso": False, "mensagem": "Pagina de detalhes sem botao de aplicar visivel.", "screenshot": b64[:100] if b64 else ""}
    except Exception as e:
        logger.error(f"aplicar_indeed_selenium erro: {e}")
        await notify_browser_step("selenium_indeed", "erro", str(e))
        print(f"[INDEED] ERRO: {e}")
        try:
            await fechar()
        except Exception:
            pass
        return {"sucesso": False, "mensagem": str(e)}
