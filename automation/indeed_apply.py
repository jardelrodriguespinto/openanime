"""
Candidatura via Indeed — scraping de formulários com Playwright.
Indeed tem formulários variados, requer tratamento genérico de campos.
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

APPLY_DAILY_LIMIT = int(os.getenv("APPLY_DAILY_LIMIT", "10"))

_BTN_APPLY = [
    'button[data-tn-element="applyButton"]',
    'button.jobs-apply-button',
    'button:has-text("Apply")',
    'button:has-text("Candidatar")',
    'a:has-text("Apply")',
]
_BTN_SUBMIT = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Submit")',
    'button:has-text("Enviar")',
    'button:has-text("Send")',
]
_BTN_NEXT = [
    'button:has-text("Next")',
    'button:has-text("Continuar")',
    'button:has-text("Próximo")',
]


async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "") -> dict:
    """
    Candidatura automática no Indeed.
    """
    from automation.browser import nova_pagina, clicar_qualquer, esperar_navegacao, screenshot_debug, set_active_page, get_intervention_state, set_intervention_state, notify_browser_step, wait_if_paused

    page = None
    try:
        page = await nova_pagina(stealth=True)
        await set_active_page(page)
        await page.goto(vaga_url)
        await esperar_navegacao(page, timeout=15000)

        # Clica em Apply
        clicou = await clicar_qualquer(page, _BTN_APPLY, timeout=10000)
        if not clicou:
            return {
                "sucesso": False,
                "motivo_falha": "sem_botao_aplicar",
                "mensagem": f"Indeed: não encontrei botão Apply. Acesse manualmente: {vaga_url}",
            }

        await asyncio.sleep(2)

        # Loop multi-step
        for step in range(12):
            # Verifica pausa/intervencao
            control = await get_intervention_state()
            if control.get("paused") or control.get("intervention_type") == "manual":
                logger.info("indeed_apply: pausado pelo usuario no step %d", step)
                await notify_browser_step("step_"+str(step), "pausado", "Intervenção manual necessária")
                return {
                    "sucesso": False,
                    "pausado": True,
                    "mensagem": "Automação pausada pelo usuário — intervenção manual necessária",
                    "acao_necessaria": "intervencao_manual",
                    "step": step,
                }

            if control.get("current_action") == "pular":
                logger.info("indeed_apply: usuario pediu para pular step %d", step)
                await set_intervention_state("current_action", "rodando")
                await notify_browser_step("step_"+str(step), "pulado", "Usuário pediu pular")
                continue

            await asyncio.sleep(1)
            await notify_browser_step("step_"+str(step), "preenchendo", "Preenchendo campos")
            await wait_if_paused(page, "step_"+str(step))

            # Preenche campos comuns
            await _preencher_campos_indeed(page, perfil, curriculo_path)

            # Tenta Submit
            if await clicar_qualquer(page, _BTN_SUBMIT, timeout=3000):
                await asyncio.sleep(2)
                await notify_browser_step("step_"+str(step), "enviando", "Submetendo candidatura")
                html = await page.content()
                if _detectar_sucesso_indeed(html, page.url):
                    logger.info("Indeed: candidatura enviada")
                    await notify_browser_step("step_"+str(step), "sucesso", "Candidatura enviada!")
                    return {
                        "sucesso": True,
                        "mensagem": "Candidatura enviada com sucesso via Indeed!",
                    }

            # Next step
            await notify_browser_step("step_"+str(step), "navegando", "Avançando step")
            if await clicar_qualquer(page, _BTN_NEXT, timeout=3000):
                await esperar_navegacao(page, timeout=8000)
                continue

            break

        html = await page.content()
        if _detectar_sucesso_indeed(html, page.url):
            return {
                "sucesso": True,
                "mensagem": "Candidatura enviada via Indeed!",
            }

        await screenshot_debug(page, "indeed_erro")
        return {
            "sucesso": False,
            "motivo_falha": "formulario_incompleto",
            "mensagem": f"Não consegui completar candidatura no Indeed. Acesse: {vaga_url}",
        }

    except Exception as e:
        logger.error("Indeed apply erro: %s", e)
        if page:
            try:
                await screenshot_debug(page, "indeed_erro_ex")
            except Exception:
                pass
        return {
            "sucesso": False,
            "motivo_falha": "erro_tecnico",
            "mensagem": f"Erro técnico Indeed. Acesse: {vaga_url}",
        }
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def _preencher_campos_indeed(page, perfil: dict, curriculo_path: str):
    """Preenche campos genéricos do Indeed."""
    from automation.browser import digitar_humano

    # Nome completo
    nome = perfil.get("nome", "")
    if nome:
        try:
            el = await page.query_selector('input[name*="name"], input[id*="name"], input[placeholder*="name"]')
            if el and await el.is_visible():
                await el.fill(nome)
        except Exception:
            pass

    # Email
    email = perfil.get("email", "")
    if email:
        try:
            el = await page.query_selector('input[type="email"], input[name*="email"]')
            if el and await el.is_visible():
                await el.fill(email)
        except Exception:
            pass

    # Telefone
    telefone = perfil.get("telefone", "")
    if telefone:
        try:
            el = await page.query_selector('input[type="tel"], input[name*="phone"]')
            if el and await el.is_visible():
                await el.fill(telefone)
        except Exception:
            pass

    # Experiência
    experiencias = perfil.get("experiencias", [])
    if experiencias:
        try:
            el = await page.query_selector('textarea[name*="experience"], textarea[id*="experience"]')
            if el and await el.is_visible():
                texto = experiencias[0].get("descricao", "")[:500] if experiencias else ""
                if texto:
                    await el.fill(texto)
        except Exception:
            pass

    # Upload de currículo
    if curriculo_path:
        try:
            fi = await page.query_selector('input[type="file"]')
            if fi:
                await fi.set_input_files(curriculo_path)
                await asyncio.sleep(1.5)
        except Exception:
            pass


def _detectar_sucesso_indeed(html: str, url: str) -> bool:
    html_lower = (html or "").lower()
    url_lower = (url or "").lower()
    sinais = [
        "application submitted", "candidatura enviada", "your application was sent",
        "thank you for applying", "obrigado pela candidatura",
        "application confirmation", "confirma candidatura",
    ]
    return any(s in html_lower or s in url_lower for s in sinais)