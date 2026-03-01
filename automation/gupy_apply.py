"""
Candidatura via Gupy — plataforma de vagas brasileira com formulario simples.
"""

import logging

logger = logging.getLogger(__name__)


async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "") -> dict:
    """
    Tenta candidatura via Gupy.
    """
    try:
        from automation.browser import detectar_bloqueio, nova_pagina
        page = await nova_pagina()

        await page.goto(vaga_url)
        await page.wait_for_load_state("networkidle", timeout=15000)

        html = await page.content()
        if detectar_bloqueio(html):
            await page.close()
            return {
                "sucesso": False,
                "motivo_falha": "bloqueio_detectado",
                "mensagem": f"Gupy bloqueou automacao. Candidate-se manualmente: {vaga_url}",
            }

        # Busca botao de candidatura
        btn = await page.query_selector('button[data-testid="apply-button"], a[data-testid="apply-button"], button:has-text("Candidatar"), a:has-text("Candidatar")')
        if not btn:
            await page.close()
            return {
                "sucesso": False,
                "motivo_falha": "botao_nao_encontrado",
                "mensagem": f"Nao encontrei botao de candidatura. Acesse manualmente: {vaga_url}",
            }

        await btn.click()
        await page.wait_for_timeout(3000)

        # Verifica se precisa de login/cadastro
        if "login" in page.url or "signup" in page.url or "cadastro" in page.url:
            await page.close()
            return {
                "sucesso": False,
                "motivo_falha": "login_necessario",
                "mensagem": f"Gupy exige login para candidatura. Acesse manualmente: {vaga_url}",
            }

        # Detecta perguntas customizadas
        perguntas = await _detectar_perguntas(page)
        if perguntas:
            await page.close()
            return {
                "sucesso": False,
                "motivo_falha": "perguntas_customizadas",
                "perguntas_customizadas": perguntas,
                "mensagem": "Esta vaga tem perguntas especificas. Responda abaixo para continuar.",
            }

        # Tenta submeter o formulário
        submit_selectors = [
            'button[type="submit"]',
            'button:has-text("Enviar candidatura")',
            'button:has-text("Enviar")',
            'button:has-text("Concluir")',
            'button:has-text("Finalizar")',
            'button:has-text("Candidatar-se")',
        ]
        for sel in submit_selectors:
            try:
                submit_btn = await page.query_selector(sel)
                if submit_btn:
                    await submit_btn.click()
                    await page.wait_for_timeout(2000)
                    await page.close()
                    return {
                        "sucesso": True,
                        "mensagem": "Candidatura enviada com sucesso via Gupy!",
                    }
            except Exception:
                continue

        # Nenhum botão de submit encontrado — guia para candidatura manual
        await page.close()
        return {
            "sucesso": False,
            "motivo_falha": "formulario_desconhecido",
            "mensagem": f"Nao consegui completar a candidatura automaticamente. Acesse manualmente: {vaga_url}",
        }

    except Exception as e:
        logger.error("gupy_apply: erro: %s", e)
        return {
            "sucesso": False,
            "motivo_falha": "erro_tecnico",
            "mensagem": f"Erro tecnico. Candidate-se manualmente: {vaga_url}",
        }


async def _detectar_perguntas(page) -> list[str]:
    """Detecta perguntas do formulario Gupy."""
    perguntas = []
    try:
        labels = await page.query_selector_all("label")
        for label in labels[:10]:
            texto = (await label.inner_text()).strip()
            if texto and len(texto) > 5 and "?" in texto:
                perguntas.append(texto)
    except Exception:
        pass
    return perguntas[:5]
