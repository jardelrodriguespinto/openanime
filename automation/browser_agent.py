"""
Agente de busca de vagas usando fontes reais (Indeed, Gupy, LinkedIn, etc.)
sem depender de browser visível. Usa scraping/API das plataformas.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

BROWSER_USE_AVAILABLE = True


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
            })
        logger.info("browser_agent: %d vagas encontradas para '%s'", len(resultados), query)
        return resultados
    except Exception as e:
        logger.error("buscar_vagas_browser_use erro: %s", e)
        return []


async def buscar_vagas_com_browser_visivel(query: str) -> list[dict]:
    """Alias de compatibilidade."""
    return await buscar_vagas_browser_use(query)


async def aplicar_vaga_browser_use(vaga_url: str, perfil: dict) -> dict:
    """
    Abre a página de candidatura no navegador visível para você preencher.
    """
    from automation.browser import notify_browser_step

    try:
        await notify_browser_step("aplicacao_browser", "aplicando", f"Abrindo: {vaga_url}")

        import webbrowser
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