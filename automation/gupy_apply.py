"""
Candidatura via Gupy — multi-step, login social/email, upload curriculo, LLM para perguntas.
"""

import asyncio
import logging
import os
import random

logger = logging.getLogger(__name__)

GUPY_EMAIL = os.getenv("GUPY_EMAIL", os.getenv("LINKEDIN_EMAIL", ""))
GUPY_PASSWORD = os.getenv("GUPY_PASSWORD", "")

_BTN_CANDIDATAR = [
    'button[data-testid="apply-button"]',
    'a[data-testid="apply-button"]',
    'button:has-text("Candidatar-se")',
    'button:has-text("Candidatar")',
    'a:has-text("Candidatar-se")',
    'button:has-text("Quero me candidatar")',
    '[class*="apply-button"]',
]
_BTN_NEXT = [
    'button:has-text("Continuar")',
    'button:has-text("Proximo")',
    'button:has-text("Avancar")',
    'button[type="submit"]:has-text("Continuar")',
    '[data-testid="next-button"]',
    'button.btn-primary',
]
_BTN_SUBMIT = [
    'button:has-text("Enviar candidatura")',
    'button:has-text("Finalizar candidatura")',
    'button:has-text("Enviar")',
    'button:has-text("Concluir")',
    '[data-testid="submit-button"]',
    'button[type="submit"]:has-text("Enviar")',
]


async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "") -> dict:
    """
    Candidatura via Gupy com multi-step, login e LLM para perguntas customizadas.
    """
    from automation.browser import detectar_bloqueio, nova_pagina, clicar_qualquer, esperar_navegacao, screenshot_debug

    page = None
    try:
        page = await nova_pagina(stealth=True)

        await page.goto(vaga_url)
        await esperar_navegacao(page, timeout=15000)

        html = await page.content()
        if detectar_bloqueio(html):
            await page.close()
            return {
                "sucesso": False,
                "motivo_falha": "bloqueio_detectado",
                "mensagem": f"Gupy bloqueou acesso. Candidate-se manualmente: {vaga_url}",
            }

        # Tenta clicar em Candidatar
        clicou = await clicar_qualquer(page, _BTN_CANDIDATAR, timeout=12000)
        if not clicou:
            await screenshot_debug(page, "gupy_sem_botao")
            await page.close()
            return {
                "sucesso": False,
                "motivo_falha": "botao_nao_encontrado",
                "mensagem": f"Nao encontrei botao de candidatura no Gupy. Acesse manualmente: {vaga_url}",
            }

        await esperar_navegacao(page, timeout=10000)
        await asyncio.sleep(random.uniform(1, 2))

        # Verifica se redirecionou para login/cadastro
        url_atual = page.url
        if any(k in url_atual for k in ("login", "signup", "cadastro", "auth")):
            resultado_login = await _fazer_login_gupy(page, url_atual)
            if not resultado_login["sucesso"]:
                await page.close()
                return resultado_login
            # Volta para a vaga
            await page.goto(vaga_url)
            await esperar_navegacao(page)
            await clicar_qualquer(page, _BTN_CANDIDATAR, timeout=8000)
            await esperar_navegacao(page)

        # Processa formulario multi-step
        resultado = await _processar_formulario_gupy(page, perfil, curriculo_path, vaga_url)
        await page.close()
        return resultado

    except Exception as e:
        logger.error("gupy_apply: erro: %s", e)
        if page:
            await screenshot_debug(page, "gupy_erro")
            try:
                await page.close()
            except Exception:
                pass
        return {
            "sucesso": False,
            "motivo_falha": "erro_tecnico",
            "mensagem": f"Erro tecnico no Gupy. Candidate-se manualmente: {vaga_url}",
        }


async def _fazer_login_gupy(page, url_login: str) -> dict:
    """Login no Gupy via email/senha."""
    from automation.browser import digitar_humano, esperar_navegacao

    if not GUPY_EMAIL:
        return {
            "sucesso": False,
            "motivo_falha": "credenciais_ausentes",
            "mensagem": "Configure GUPY_EMAIL e GUPY_PASSWORD no .env para candidatura automatica via Gupy.",
        }

    try:
        seletores_email = [
            'input[type="email"]',
            'input[name="email"]',
            'input[placeholder*="e-mail"]',
            'input[placeholder*="Email"]',
        ]
        seletores_senha = [
            'input[type="password"]',
            'input[name="password"]',
            'input[name="senha"]',
        ]
        seletores_submit = [
            'button[type="submit"]',
            'button:has-text("Entrar")',
            'button:has-text("Login")',
            'button:has-text("Acessar")',
        ]

        for sel in seletores_email:
            el = await page.query_selector(sel)
            if el:
                await el.fill(GUPY_EMAIL)
                break

        await asyncio.sleep(random.uniform(0.3, 0.7))

        for sel in seletores_senha:
            el = await page.query_selector(sel)
            if el:
                await el.fill(GUPY_PASSWORD or "")
                break

        from automation.browser import clicar_qualquer
        await clicar_qualquer(page, seletores_submit)
        await esperar_navegacao(page, timeout=15000)

        url = page.url
        if any(k in url for k in ("login", "auth", "cadastro")):
            return {
                "sucesso": False,
                "motivo_falha": "login_gupy_falhou",
                "mensagem": "Login no Gupy falhou. Verifique GUPY_EMAIL e GUPY_PASSWORD no .env ou candidate-se manualmente.",
            }

        logger.info("gupy_apply: login OK")
        return {"sucesso": True}

    except Exception as e:
        logger.error("gupy_apply: erro no login: %s", e)
        return {
            "sucesso": False,
            "motivo_falha": "erro_login",
            "mensagem": "Erro ao tentar login no Gupy.",
        }


async def _processar_formulario_gupy(page, perfil: dict, curriculo_path: str, vaga_url: str) -> dict:
    """Processa formulario Gupy multi-step com preenchimento automatico."""
    from automation.browser import clicar_qualquer, esperar_navegacao, screenshot_debug
    from automation.form_filler import responder_pergunta

    perguntas_respondidas = []
    max_steps = 10

    for step in range(max_steps):
        await asyncio.sleep(random.uniform(0.8, 1.5))

        # Preenche campos conhecidos
        await _preencher_dados_pessoais(page, perfil)

        # Upload curriculo se tiver campo
        if curriculo_path:
            await _tentar_upload(page, curriculo_path)

        # Responde perguntas customizadas com LLM
        perguntas = await _detectar_perguntas_gupy(page)
        for pergunta in perguntas:
            if pergunta not in perguntas_respondidas:
                resposta = responder_pergunta(pergunta, perfil)
                await _preencher_resposta(page, pergunta, resposta)
                perguntas_respondidas.append(pergunta)
                await asyncio.sleep(random.uniform(0.3, 0.8))

        # Tenta submit (ultimo step)
        if await clicar_qualquer(page, _BTN_SUBMIT, timeout=3000):
            await asyncio.sleep(2)
            html = await page.content()
            if _detectar_sucesso_gupy(html, page.url):
                return {
                    "sucesso": True,
                    "perguntas_respondidas": perguntas_respondidas,
                    "mensagem": "Candidatura enviada com sucesso via Gupy!",
                }

        # Avanca step
        avancou = await clicar_qualquer(page, _BTN_NEXT, timeout=4000)
        if not avancou:
            break
        await esperar_navegacao(page, timeout=8000)

    # Verificacao final
    html = await page.content()
    if _detectar_sucesso_gupy(html, page.url):
        return {
            "sucesso": True,
            "perguntas_respondidas": perguntas_respondidas,
            "mensagem": "Candidatura enviada com sucesso via Gupy!",
        }

    await screenshot_debug(page, "gupy_formulario")
    return {
        "sucesso": False,
        "motivo_falha": "formulario_incompleto",
        "mensagem": f"Nao consegui completar o formulario Gupy. Candidate-se manualmente: {vaga_url}",
    }


async def _preencher_dados_pessoais(page, perfil: dict) -> None:
    """Preenche dados pessoais basicos nos campos visiveis."""
    mapa = {
        'input[name="name"], input[name="nome"], input[placeholder*="nome"]': perfil.get("nome", ""),
        'input[type="email"], input[name="email"]': perfil.get("email", ""),
        'input[type="tel"], input[name="phone"], input[name="telefone"]': perfil.get("telefone", ""),
        'input[name="city"], input[name="cidade"]': (perfil.get("localizacao") or "").split(",")[0].strip(),
        'input[name="linkedin"]': perfil.get("linkedin", ""),
    }
    for seletor, valor in mapa.items():
        if not valor:
            continue
        try:
            els = await page.query_selector_all(seletor)
            for el in els:
                if await el.is_visible():
                    current = await el.input_value()
                    if not current:
                        await el.fill(valor)
                        await asyncio.sleep(0.15)
        except Exception:
            pass


async def _tentar_upload(page, curriculo_path: str) -> None:
    """Tenta fazer upload do curriculo."""
    try:
        seletores = [
            'input[type="file"][accept*="pdf"]',
            'input[type="file"][name*="resume"]',
            'input[type="file"][name*="curriculo"]',
            'input[type="file"]',
        ]
        for sel in seletores:
            el = await page.query_selector(sel)
            if el:
                await el.set_input_files(curriculo_path)
                await asyncio.sleep(2)
                logger.info("gupy_apply: curriculo enviado via upload")
                return
    except Exception as e:
        logger.debug("gupy_apply: upload falhou: %s", e)


async def _detectar_perguntas_gupy(page) -> list:
    """Detecta perguntas abertas nao preenchidas no formulario Gupy."""
    perguntas = []
    try:
        # Textareas vazias
        textareas = await page.query_selector_all("textarea")
        for ta in textareas:
            if not await ta.is_visible():
                continue
            valor = await ta.input_value()
            if valor:
                continue
            label = await _get_label_gupy(page, ta)
            if label and len(label) > 10:
                perguntas.append(label)

        # Inputs de texto com perguntas explicitas (contem "?" ou sao longos)
        inputs = await page.query_selector_all("input[type='text']")
        ignorar = {"nome", "name", "email", "telefone", "phone", "cidade", "city", "linkedin"}
        for inp in inputs:
            if not await inp.is_visible():
                continue
            valor = await inp.input_value()
            if valor:
                continue
            label = await _get_label_gupy(page, inp)
            if not label or label.lower() in ignorar:
                continue
            if "?" in label or len(label) > 30:
                perguntas.append(label)
    except Exception:
        pass
    return perguntas[:5]


async def _preencher_resposta(page, pergunta: str, resposta: str) -> None:
    """Preenche resposta para pergunta encontrada pela label."""
    try:
        labels = await page.query_selector_all("label")
        for label in labels:
            texto_label = (await label.inner_text()).strip()
            if pergunta[:40].lower() in texto_label.lower():
                label_for = await label.get_attribute("for")
                if label_for:
                    el = await page.query_selector(f'#{label_for}')
                    if el and await el.is_visible():
                        tag = await el.evaluate("el => el.tagName.toLowerCase()")
                        limite = 2000 if tag == "textarea" else 300
                        await el.fill(resposta[:limite])
                        return
    except Exception as e:
        logger.debug("gupy_apply: erro ao preencher resposta: %s", e)


async def _get_label_gupy(page, el) -> str:
    """Retorna label de um elemento."""
    try:
        el_id = await el.get_attribute("id")
        if el_id:
            label = await page.query_selector(f'label[for="{el_id}"]')
            if label:
                return (await label.inner_text()).strip()
        aria = await el.get_attribute("aria-label")
        if aria:
            return aria.strip()
        placeholder = await el.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()
    except Exception:
        pass
    return ""


def _detectar_sucesso_gupy(html: str, url: str) -> bool:
    """Detecta confirmacao de candidatura enviada."""
    html_lower = (html or "").lower()
    url_lower = (url or "").lower()
    sinais = [
        "candidatura enviada", "candidatura realizada", "obrigado",
        "inscricao confirmada", "application submitted", "sucesso",
        "voce foi inscrito", "cadastro realizado",
    ]
    return any(s in html_lower or s in url_lower for s in sinais)
