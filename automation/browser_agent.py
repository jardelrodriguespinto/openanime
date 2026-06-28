"""
Agente de busca de vagas usando browser visível — Playwright puro com intervenção via Socket.IO.
Browser abre em modo visível (headless=false) para você ver e intervir em tempo real.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# Sempre tenta usar Playwright (browser visível)
BROWSER_USE_AVAILABLE = True

async def buscar_vagas_com_browser_visivel(query: str) -> list[dict]:
    """
    Busca vagas com browser visível usando Playwright puro.
    Abre o browser em modo visível para você ver e intervir manualmente.
    No Docker, requer DISPLAY ou xvfb para visibilidade.
    """
    from automation.browser import nova_pagina, set_active_page, notify_browser_step, esperar_navegacao
    
    try:
        await notify_browser_step("busca", "navegando", f"Abrindo browser: {query}")
        
        # Browser visível - não headless (configurado via PLAYWRIGHT_HEADLESS=false)
        page = await nova_pagina(stealth=False)
        await set_active_page(page)
        
        # Abre página de busca do LinkedIn (sem login para ver vagas)
        search_query = query.replace(" ", "+")
        url = f"https://www.linkedin.com/jobs/search/?keywords={search_query}&location=Brasil"
        await page.goto(url)
        await esperar_navegacao(page, timeout=15000)
        
        # Verifica se carregou
        if "linkedin.com" in page.url:
            await notify_browser_step("busca", "navegando", f"Browser aberto - navegue manualmente")
            return [{"url": page.url, "titulo": f"Busca: {query}", "empresa": "", "fonte": "browser_visivel"}]
        
        await notify_browser_step("busca", "erro", "Falha ao carregar página")
        return []
    except Exception as e:
        logger.error("buscar_vagas_com_browser_visivel erro: %s", e)
        try:
            await notify_browser_step("busca", "erro", str(e))
        except Exception:
            pass
        return []


async def buscar_vagas_browser_use(query: str) -> list[dict]:
    """
    Alias para compatibilidade - usa Playwright visível.
    """
    return await buscar_vagas_com_browser_visivel(query)


async def aplicar_vaga_browser_use(vaga_url: str, perfil: dict) -> dict:
    """
    Aplica para vaga usando browser visível (Playwright).
    Abre o browser em modo visível para você ver e intervir manualmente.
    """
    from automation.browser import (
        nova_pagina, set_active_page, notify_browser_step, 
        get_intervention_state, wait_if_paused, digitar_humano,
        clicar_qualquer, esperar_navegacao, screenshot_debug,
        detectar_bloqueio
    )
    
    page = None
    try:
        await notify_browser_step("aplicacao_browser", "aplicando", f"Abrindo: {vaga_url}")
        
        # Browser visível - não headless
        page = await nova_pagina(stealth=False)
        await set_active_page(page)
        
        await page.goto(vaga_url)
        await esperar_navegacao(page, timeout=15000)
        
        html = await page.content()
        if detectar_bloqueio(html):
            await screenshot_debug(page, "bloqueio")
            await notify_browser_step("aplicacao_browser", "erro", "Bloqueio detectado - navegue manualmente")
            return {"sucesso": False, "mensagem": "Bloqueio detectado. Complete manualmente no browser aberto."}
        
        await notify_browser_step("aplicacao_browser", "navegando", "Preencha o formulário manualmente no browser aberto")
        
        # Mantém browser aberto para intervenção manual
        return {"sucesso": True, "mensagem": "Browser aberto - complete a candidatura manualmente"}
    except Exception as e:
        logger.error("aplicar_vaga_browser_visivel erro: %s", e)
        try:
            await notify_browser_step("aplicacao_browser", "falha", str(e))
        except Exception:
            pass
        return {"sucesso": False, "mensagem": str(e) if page else "Erro ao abrir browser - instale playwright"}