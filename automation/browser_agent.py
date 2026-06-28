"""
Agente de busca de vagas usando browser visível — abre navegador real para você ver/intervir.
Usa requests + Chromium aberto manualmente (mais confiável que Playwright no Ubuntu 26.04).
"""

import asyncio
import logging
import subprocess
import webbrowser
import time

logger = logging.getLogger(__name__)

BROWSER_USE_AVAILABLE = True


async def buscar_vagas_com_browser_visivel(query: str) -> list[dict]:
    """
    Busca vagas abrindo o navegador visível no seu desktop.
    Abre aba do navegador para você buscar manualmente.
    """
    from automation.browser import notify_browser_step
    
    try:
        await notify_browser_step("busca", "navegando", f"Abrindo browser: {query}")
        
        # Abre aba do navegador no desktop
        search_query = query.replace(" ", "+")
        url = f"https://www.linkedin.com/jobs/search/?keywords={search_query}&location=Brasil"
        
        # Abre no navegador padrão (não headless)
        webbrowser.open(url)
        
        await notify_browser_step("busca", "navegando", f"Browser aberto - navegue manualmente por vagas de {query}")
        
        # Retorna URL para indicar que browser foi aberto
        return [{"url": url, "titulo": f"Busca: {query}", "empresa": "", "fonte": "browser_manual"}]
    except Exception as e:
        logger.error("buscar_vagas_com_browser_visivel erro: %s", e)
        try:
            await notify_browser_step("busca", "erro", str(e))
        except Exception:
            pass
        return []


async def buscar_vagas_browser_use(query: str) -> list[dict]:
    """
    Alias para compatibilidade - abre browser visível.
    """
    return await buscar_vagas_com_browser_visivel(query)


async def aplicar_vaga_browser_use(vaga_url: str, perfil: dict) -> dict:
    """
    Abre a página de candidatura no navegador visível para você preencher.
    """
    from automation.browser import notify_browser_step
    
    try:
        await notify_browser_step("aplicacao_browser", "aplicando", f"Abrindo: {vaga_url}")
        
        # Abre no navegador padrão (visível)
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