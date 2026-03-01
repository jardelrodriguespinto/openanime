"""
Candidatura via LinkedIn Easy Apply.
Funciona para vagas com formulario padrao sem captcha.
"""

import logging
import os

logger = logging.getLogger(__name__)

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")


async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "") -> dict:
    """
    Tenta candidatura via LinkedIn Easy Apply.

    Retorna:
    {
        "sucesso": bool,
        "motivo_falha": str | None,
        "perguntas_customizadas": list[str] | None,
        "mensagem": str
    }
    """
    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        return {
            "sucesso": False,
            "motivo_falha": "credenciais_ausentes",
            "perguntas_customizadas": None,
            "mensagem": "Configure LINKEDIN_EMAIL e LINKEDIN_PASSWORD no .env para candidatura automatica.",
        }

    try:
        from automation.browser import detectar_bloqueio, nova_pagina
        page = await nova_pagina()

        # Login
        await page.goto("https://www.linkedin.com/login")
        await page.fill("#username", LINKEDIN_EMAIL)
        await page.fill("#password", LINKEDIN_PASSWORD)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle", timeout=15000)

        html = await page.content()
        if detectar_bloqueio(html) or "checkpoint" in page.url:
            await page.close()
            return {
                "sucesso": False,
                "motivo_falha": "bloqueio_detectado",
                "mensagem": "LinkedIn detectou automacao. Candidate-se manualmente: " + vaga_url,
            }

        # Navega para a vaga
        await page.goto(vaga_url)
        await page.wait_for_load_state("networkidle", timeout=15000)

        # Verifica se existe botao Easy Apply
        easy_apply = await page.query_selector('button.jobs-apply-button, button[aria-label*="Easy Apply"]')
        if not easy_apply:
            await page.close()
            return {
                "sucesso": False,
                "motivo_falha": "sem_easy_apply",
                "mensagem": f"Esta vaga nao tem Easy Apply. Candidate-se manualmente: {vaga_url}",
            }

        await easy_apply.click()
        await page.wait_for_timeout(2000)

        # Detecta perguntas customizadas
        perguntas = await _detectar_perguntas_customizadas(page)
        if perguntas:
            await page.close()
            return {
                "sucesso": False,
                "motivo_falha": "perguntas_customizadas",
                "perguntas_customizadas": perguntas,
                "mensagem": "Esta vaga tem perguntas especificas. Responda abaixo e confirme para continuar.",
            }

        # Preenche campos basicos
        await _preencher_campos_basicos(page, perfil)

        # Upload curriculo se fornecido
        if curriculo_path:
            await _upload_curriculo(page, curriculo_path)

        # Tenta submeter
        submit_btn = await page.query_selector('button[aria-label="Submit application"], button[aria-label*="Enviar"]')
        if submit_btn:
            await submit_btn.click()
            await page.wait_for_timeout(3000)
            html_final = await page.content()
            if "application submitted" in html_final.lower() or "candidatura enviada" in html_final.lower():
                await page.close()
                return {"sucesso": True, "mensagem": "Candidatura enviada com sucesso via LinkedIn!"}

        await page.close()
        return {
            "sucesso": False,
            "motivo_falha": "submit_falhou",
            "mensagem": f"Nao consegui confirmar o envio. Tente manualmente: {vaga_url}",
        }

    except Exception as e:
        logger.error("linkedin_apply: erro: %s", e)
        return {
            "sucesso": False,
            "motivo_falha": "erro_tecnico",
            "mensagem": f"Erro tecnico na automacao. Candidate-se manualmente: {vaga_url}",
        }


async def _detectar_perguntas_customizadas(page) -> list[str]:
    """Detecta perguntas customizadas no formulario."""
    perguntas = []
    try:
        inputs = await page.query_selector_all("input[type='text'], textarea, select")
        for inp in inputs:
            label = await _get_label_for_input(page, inp)
            if label and label not in ["Telefone", "Email", "Nome", "Sobrenome", "Cidade", "Phone", "Email address"]:
                perguntas.append(label)
    except Exception:
        pass
    return perguntas[:5]  # maximo 5 perguntas


async def _get_label_for_input(page, inp) -> str:
    """Tenta obter o label associado a um input."""
    try:
        input_id = await inp.get_attribute("id")
        if input_id:
            label = await page.query_selector(f'label[for="{input_id}"]')
            if label:
                return (await label.inner_text()).strip()
        aria_label = await inp.get_attribute("aria-label")
        if aria_label:
            return aria_label.strip()
    except Exception:
        pass
    return ""


async def _preencher_campos_basicos(page, perfil: dict) -> None:
    """Preenche campos padrao do formulario."""
    try:
        campos_padrao = {
            'input[name*="phone"], input[type="tel"]': perfil.get("telefone", ""),
            'input[name*="city"], input[aria-label*="Cidade"]': perfil.get("localizacao", ""),
        }
        for seletor, valor in campos_padrao.items():
            if not valor:
                continue
            try:
                el = await page.query_selector(seletor)
                if el:
                    await el.fill(valor)
            except Exception:
                pass
    except Exception as e:
        logger.debug("linkedin_apply: erro ao preencher campos basicos: %s", e)


async def _upload_curriculo(page, curriculo_path: str) -> None:
    """Faz upload do curriculo se houver campo de arquivo."""
    try:
        file_input = await page.query_selector('input[type="file"]')
        if file_input:
            await file_input.set_input_files(curriculo_path)
            await page.wait_for_timeout(2000)
    except Exception as e:
        logger.debug("linkedin_apply: erro upload curriculo: %s", e)
