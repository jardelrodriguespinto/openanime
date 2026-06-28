"""
Candidatura via Gupy usando Selenium + Firefox.
"""

import asyncio
import logging
import os

from automation.browser import notify_browser_step
from automation.selenium_browser import (
    nova_pagina, navegar, wait_for_selector, wait_for_selector_visible,
    click, digitar, digitar_com_delay, screenshot_base64, fechar, get_driver, get_title
)

logger = logging.getLogger(__name__)
GUPY_EMAIL = os.getenv("GUPY_EMAIL", "")
GUPY_PASSWORD = os.getenv("GUPY_PASSWORD", "")


async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "") -> dict:
    """Aplica em vaga Gupy via Selenium."""
    try:
        await notify_browser_step("selenium_gupy", "iniciando", "Abrindo Gupy")
        print(f"[GUPY] Aplicando em: {vaga_url}")

        await nova_pagina(vaga_url)
        await asyncio.sleep(3)
        driver = await get_driver()
        print(f"[GUPY] Carregado: {await get_title()[:80]} | URL: {driver.current_url[:100]}")

        # Tenta encontrar botao de candidatura
        aplicar_btn = await wait_for_selector_visible(
            "button[data-testid='apply-button'], .jobs-apply-button",
            timeout=10
        )
        if aplicar_btn:
            await click("button[data-testid='apply-button'], .jobs-apply-button")
            await asyncio.sleep(3)
            print("[GUPY] Clicou em Candidatar")

            # Preenche campos basicos se aparecerem
            campos = 0
            nome = perfil.get("nome", "")
            partes = nome.split()
            primeiro = partes[0] if partes else ""
            ultimo = " ".join(partes[1:]) if len(partes) > 1 else ""

            if await digitar_com_delay("input[name='firstName']", primeiro, delay_min=20, delay_max=50):
                campos += 1
            if await digitar_com_delay("input[name='lastName']", ultimo, delay_min=20, delay_max=50):
                campos += 1
            if await digitar_com_delay("input[name='email'], input[type='email']", perfil.get("email", GUPY_EMAIL), delay_min=20, delay_max=50):
                campos += 1
            telefone = perfil.get("telefone", perfil.get("phone", ""))
            if telefone and await digitar_com_delay("input[name='phone'], input[type='tel']", str(telefone), delay_min=20, delay_max=50):
                campos += 1

            print(f"[GUPY] Campos preenchidos: {campos}")

            # Tenta enviar
            await notify_browser_step("selenium_gupy", "enviando", "Enviando candidatura...")

            submit = await wait_for_selector_visible(
                "button[type='submit'], button.submit-button, .submit-button",
                timeout=10
            )
            if submit:
                await click("button[type='submit'], button.submit-button, .submit-button")
                await asyncio.sleep(3)
                print("[GUPY] Candidatura enviada!")
                await notify_browser_step("selenium_gupy", "sucesso", "Candidatura enviada!")
                b64 = await screenshot_base64()
                await fechar()
                return {"sucesso": True, "mensagem": "Candidatura enviada no Gupy!", "screenshot": b64[:100] if b64 else ""}
            else:
                print("[GUPY] Botao enviar nao encontrado")
                await notify_browser_step("selenium_gupy", "erro", "Botao enviar nao encontrado")
                b64 = await screenshot_base64()
                await fechar()
                return {"sucesso": False, "mensagem": "Botao enviar nao encontrado. Complete manualmente.", "screenshot": b64[:100] if b64 else ""}
        else:
            print("[GUPY] Botao candidatar nao encontrado")
            await notify_browser_step("selenium_gupy", "erro", "Botao candidatar nao encontrado")
            b64 = await screenshot_base64()
            await fechar()
            return {"sucesso": False, "mensagem": "Botao candidatar nao encontrado. Vaga pode exigir aplicacao externa.", "screenshot": b64[:100] if b64 else ""}

    except Exception as e:
        logger.error(f"aplicar_gupy_selenium erro: {e}")
        await notify_browser_step("selenium_gupy", "erro", str(e))
        print(f"[GUPY] ERRO: {e}")
        try:
            await fechar()
        except Exception:
            pass
        return {"sucesso": False, "mensagem": str(e)}
