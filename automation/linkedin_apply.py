"""
Candidatura via LinkedIn Easy Apply — multi-step robusto com LLM para perguntas customizadas.
"""

import asyncio
import logging
import os
import random

logger = logging.getLogger(__name__)

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")

# Campos que nao sao "perguntas customizadas" — ja sao preenchidos automaticamente
_CAMPOS_PADRAO = {
    "telefone", "phone", "email", "nome", "name", "sobrenome", "lastname",
    "primeiro nome", "first name", "cidade", "city", "pais", "country",
    "endereco", "address", "cep", "zip", "estado", "state",
}

# Seletores do formulario Easy Apply em ordem de tentativa
_BTN_NEXT = [
    'button[aria-label="Continue to next step"]',
    'button[aria-label="Continuar para a proxima etapa"]',
    'button:has-text("Next")',
    'button:has-text("Continuar")',
    'button:has-text("Proximo")',
    'footer button[data-easy-apply-next-button]',
    'button.artdeco-button--primary',
]
_BTN_SUBMIT = [
    'button[aria-label="Submit application"]',
    'button[aria-label="Enviar candidatura"]',
    'button[aria-label="Review your application"]',
    'button:has-text("Submit application")',
    'button:has-text("Enviar candidatura")',
    'button:has-text("Enviar")',
    'button:has-text("Finalizar")',
]
_BTN_EASY_APPLY = [
    'button.jobs-apply-button[aria-label*="Easy Apply"]',
    'button[aria-label*="Easy Apply"]',
    'button.jobs-apply-button',
    'button:has-text("Easy Apply")',
    'button:has-text("Candidatura simplificada")',
]


async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "") -> dict:
    """
    Candidatura via LinkedIn Easy Apply com multi-step handling.
    """
    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        return {
            "sucesso": False,
            "motivo_falha": "credenciais_ausentes",
            "mensagem": "Configure LINKEDIN_EMAIL e LINKEDIN_PASSWORD no .env para candidatura automatica.",
        }

    from automation.browser import (
        detectar_bloqueio, nova_pagina, nova_pagina_com_sessao,
        clicar_qualquer, esperar_navegacao, screenshot_debug,
        salvar_sessao, sessao_existe, limpar_sessao,
    )

    page = None
    context = None
    try:
        page, context = await nova_pagina_com_sessao("linkedin", stealth=True)

        # --- LOGIN (pula se sessao valida) ---
        if sessao_existe("linkedin"):
            # Verifica se sessao ainda e valida
            await page.goto("https://www.linkedin.com/feed/")
            await esperar_navegacao(page, timeout=10000)
            if "feed" in page.url or "mynetwork" in page.url:
                logger.info("linkedin_apply: sessao existente valida, pulando login")
            else:
                # Sessao expirou — remove e faz login
                limpar_sessao("linkedin")
                resultado_login = await _fazer_login(page, context)
                if not resultado_login["sucesso"]:
                    await page.close()
                    return resultado_login
        else:
            resultado_login = await _fazer_login(page, context)
            if not resultado_login["sucesso"]:
                await page.close()
                return resultado_login

        # --- NAVEGA PARA A VAGA ---
        await page.goto(vaga_url)
        await esperar_navegacao(page, timeout=15000)

        html = await page.content()
        if detectar_bloqueio(html) or "checkpoint" in page.url or "login" in page.url:
            await screenshot_debug(page, "linkedin_bloqueio")
            await page.close()
            return {
                "sucesso": False,
                "motivo_falha": "bloqueio_detectado",
                "mensagem": f"LinkedIn detectou automacao. Candidate-se manualmente: {vaga_url}",
            }

        # --- VERIFICA EASY APPLY ---
        clicou = await clicar_qualquer(page, _BTN_EASY_APPLY, timeout=10000)
        if not clicou:
            # Tenta via URL direto para aplicacao
            if "/jobs/view/" in vaga_url:
                vaga_id = vaga_url.split("/jobs/view/")[1].split("/")[0].split("?")[0]
                await page.goto(f"https://www.linkedin.com/jobs/view/{vaga_id}/")
                await esperar_navegacao(page)
                clicou = await clicar_qualquer(page, _BTN_EASY_APPLY, timeout=8000)

            if not clicou:
                await page.close()
                return {
                    "sucesso": False,
                    "motivo_falha": "sem_easy_apply",
                    "mensagem": f"Esta vaga nao tem Easy Apply. Candidate-se manualmente: {vaga_url}",
                }

        await asyncio.sleep(random.uniform(1.5, 2.5))

        # --- LOOP MULTI-STEP ---
        resultado = await _processar_formulario_multistep(page, perfil, curriculo_path, vaga_url)
        await page.close()
        return resultado

    except Exception as e:
        logger.error("linkedin_apply: erro: %s", e)
        if page:
            await screenshot_debug(page, "linkedin_erro")
            try:
                await page.close()
            except Exception:
                pass
        return {
            "sucesso": False,
            "motivo_falha": "erro_tecnico",
            "mensagem": f"Erro tecnico na automacao LinkedIn. Candidate-se manualmente: {vaga_url}",
        }
    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass


async def _fazer_login(page, context=None) -> dict:
    """Login no LinkedIn. Salva cookies apos sucesso para reutilizar na proxima vez."""
    try:
        await page.goto("https://www.linkedin.com/login")
        await asyncio.sleep(random.uniform(1, 2))

        from automation.browser import digitar_humano, esperar_navegacao, salvar_sessao
        await digitar_humano(page, "#username", LINKEDIN_EMAIL)
        await asyncio.sleep(random.uniform(0.3, 0.8))
        await digitar_humano(page, "#password", LINKEDIN_PASSWORD)
        await asyncio.sleep(random.uniform(0.5, 1.0))
        await page.click('button[type="submit"]')
        await esperar_navegacao(page, timeout=20000)

        if "checkpoint" in page.url or "challenge" in page.url:
            return {
                "sucesso": False,
                "motivo_falha": "verificacao_necessaria",
                "mensagem": "LinkedIn pediu verificacao de seguranca (2FA). Faca login manual uma vez para habilitar automacao.",
            }

        if "feed" in page.url or "mynetwork" in page.url or "linkedin.com/in/" in page.url:
            logger.info("linkedin_apply: login OK")
            # Salva sessao para proximas candidaturas
            if context:
                await salvar_sessao(context, "linkedin")
            return {"sucesso": True}

        html = await page.content()
        if "sign in" in html.lower() or "entrar" in html.lower():
            return {
                "sucesso": False,
                "motivo_falha": "login_falhou",
                "mensagem": "Login no LinkedIn falhou. Verifique LINKEDIN_EMAIL e LINKEDIN_PASSWORD no .env.",
            }

        # Sessao possivelmente valida — salva de qualquer forma
        if context:
            await salvar_sessao(context, "linkedin")
        return {"sucesso": True}

    except Exception as e:
        logger.error("linkedin_apply: erro no login: %s", e)
        return {
            "sucesso": False,
            "motivo_falha": "erro_login",
            "mensagem": "Erro ao tentar login no LinkedIn.",
        }


async def _processar_formulario_multistep(page, perfil: dict, curriculo_path: str, vaga_url: str) -> dict:
    """
    Navega por todas as etapas do formulario Easy Apply.
    Preenche campos automaticamente, responde perguntas com LLM, faz upload de curriculo.
    """
    from automation.browser import clicar_qualquer, esperar_navegacao, screenshot_debug
    from automation.form_filler import responder_pergunta

    perguntas_customizadas = []
    respostas_geradas = {}
    max_steps = 12  # Previne loop infinito

    for step in range(max_steps):
        await asyncio.sleep(random.uniform(0.8, 1.5))

        # Preenche campos visiveis neste step
        await _preencher_step(page, perfil, curriculo_path, respostas_geradas)

        # Detecta perguntas customizadas neste step
        perguntas_step = await _detectar_perguntas_nao_respondidas(page)
        if perguntas_step:
            # Responde cada pergunta com LLM
            for pergunta in perguntas_step:
                if pergunta not in respostas_geradas:
                    resposta = responder_pergunta(
                        pergunta, perfil,
                        vaga_titulo="vaga LinkedIn",
                        vaga_empresa=""
                    )
                    respostas_geradas[pergunta] = resposta
                    await _preencher_resposta_customizada(page, pergunta, resposta)
                    perguntas_customizadas.append(pergunta)
                    await asyncio.sleep(random.uniform(0.5, 1.0))

        # Tenta clicar Submit (ultimo step)
        if await clicar_qualquer(page, _BTN_SUBMIT, timeout=3000):
            await asyncio.sleep(2)
            html = await page.content()
            if _detectar_sucesso(html, page.url):
                logger.info("linkedin_apply: candidatura enviada com sucesso")
                return {
                    "sucesso": True,
                    "perguntas_respondidas": perguntas_customizadas,
                    "mensagem": "Candidatura enviada com sucesso via LinkedIn Easy Apply!",
                }
            # Review step — clica Submit de novo
            if await clicar_qualquer(page, _BTN_SUBMIT, timeout=3000):
                await asyncio.sleep(2)
                html = await page.content()
                if _detectar_sucesso(html, page.url):
                    return {
                        "sucesso": True,
                        "perguntas_respondidas": perguntas_customizadas,
                        "mensagem": "Candidatura enviada com sucesso via LinkedIn Easy Apply!",
                    }

        # Avanca para proximo step
        if await clicar_qualquer(page, _BTN_NEXT, timeout=3000):
            await esperar_navegacao(page, timeout=8000)
            continue

        # Nem Submit nem Next encontrado — chegou ao fim ou travou
        break

    # Verifica se realmente enviou
    html = await page.content()
    if _detectar_sucesso(html, page.url):
        return {
            "sucesso": True,
            "perguntas_respondidas": perguntas_customizadas,
            "mensagem": "Candidatura enviada com sucesso via LinkedIn Easy Apply!",
        }

    await screenshot_debug(page, "linkedin_formulario")
    return {
        "sucesso": False,
        "motivo_falha": "formulario_incompleto",
        "mensagem": f"Nao consegui completar o formulario. Candidate-se manualmente: {vaga_url}",
    }


async def _preencher_step(page, perfil: dict, curriculo_path: str, respostas_ja_dadas: dict) -> None:
    """Preenche campos conhecidos no step atual."""
    from automation.browser import digitar_humano

    mapa_campos = {
        # Telefone
        'input[name*="phone"], input[type="tel"], input[aria-label*="Phone"], input[aria-label*="Telefone"]':
            perfil.get("telefone", ""),
        # Cidade
        'input[aria-label*="City"], input[aria-label*="Cidade"], input[name*="city"]':
            perfil.get("localizacao", "").split(",")[0].strip() if perfil.get("localizacao") else "",
    }

    for seletor, valor in mapa_campos.items():
        if not valor:
            continue
        try:
            els = await page.query_selector_all(seletor)
            for el in els:
                if await el.is_visible():
                    current = await el.input_value() if hasattr(el, 'input_value') else ""
                    if not current:
                        await el.fill(valor)
                        await asyncio.sleep(0.2)
        except Exception:
            pass

    # Upload curriculo
    if curriculo_path:
        try:
            file_input = await page.query_selector('input[type="file"]')
            if file_input:
                await file_input.set_input_files(curriculo_path)
                await asyncio.sleep(1.5)
        except Exception:
            pass

    # Selects de "yes/no" — sempre Yes para disponibilidade/autorizacao
    try:
        selects = await page.query_selector_all("select")
        for sel in selects:
            if await sel.is_visible():
                options = await sel.query_selector_all("option")
                # Pega option "Yes" ou "Sim" se existir
                for opt in options:
                    text = (await opt.inner_text()).strip().lower()
                    if text in ("yes", "sim", "authorized", "autorizado"):
                        val = await opt.get_attribute("value")
                        if val:
                            await sel.select_option(value=val)
                            break
    except Exception:
        pass


async def _detectar_perguntas_nao_respondidas(page) -> list:
    """Detecta campos de pergunta ainda vazios no step atual."""
    perguntas = []
    try:
        # Inputs de texto vazios com label
        inputs = await page.query_selector_all(
            "input[type='text']:not([value]), input[type='text'][value=''], textarea"
        )
        for inp in inputs:
            if not await inp.is_visible():
                continue
            label = await _get_label(page, inp)
            if not label:
                continue
            label_lower = label.lower()
            # Ignora campos padrao
            if any(p in label_lower for p in _CAMPOS_PADRAO):
                continue
            # So inclui se parece uma pergunta real
            current_val = ""
            try:
                current_val = await inp.input_value()
            except Exception:
                pass
            if not current_val:
                perguntas.append(label)
    except Exception:
        pass
    return perguntas[:6]


async def _preencher_resposta_customizada(page, pergunta: str, resposta: str) -> None:
    """Preenche campo de pergunta customizada pela label."""
    from automation.browser import digitar_humano
    try:
        # Busca input pelo label
        labels = await page.query_selector_all("label")
        for label in labels:
            if pergunta.lower()[:30] in (await label.inner_text()).lower():
                label_for = await label.get_attribute("for")
                if label_for:
                    el = await page.query_selector(f'#{label_for}')
                    if el and await el.is_visible():
                        tag = await el.evaluate("el => el.tagName.toLowerCase()")
                        if tag == "textarea":
                            await el.fill(resposta)
                        else:
                            await el.fill(resposta[:200])
                        return
    except Exception as e:
        logger.debug("linkedin_apply: erro ao preencher resposta: %s", e)


async def _get_label(page, inp) -> str:
    """Retorna label associada a um input."""
    try:
        input_id = await inp.get_attribute("id")
        if input_id:
            label = await page.query_selector(f'label[for="{input_id}"]')
            if label:
                return (await label.inner_text()).strip()
        aria = await inp.get_attribute("aria-label")
        if aria:
            return aria.strip()
        placeholder = await inp.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()
    except Exception:
        pass
    return ""


def _detectar_sucesso(html: str, url: str) -> bool:
    """Detecta se a candidatura foi enviada com sucesso."""
    html_lower = (html or "").lower()
    url_lower = (url or "").lower()
    sinais = [
        "application submitted", "candidatura enviada", "your application was sent",
        "applied", "candidatura realizada", "obrigado pela candidatura",
        "thank you for applying",
    ]
    return any(s in html_lower or s in url_lower for s in sinais)
