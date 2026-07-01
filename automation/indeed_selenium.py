"""
Candidatura via Indeed usando Selenium + Firefox visível.

Nos mesmos moldes da automação do LinkedIn (automation/linkedin_selenium.py),
mas adaptada para o Indeed:

- Busca vagas em br.indeed.com/jobs?q=<palavra-chave customizável> e filtra pelo
  match com o currículo (fail-open, igual ao LinkedIn).
- Aplica SOMENTE em vagas "Candidatura simplificada" (Indeed Apply / SmartApply),
  pulando as que redirecionam para o site da empresa — o análogo direto do
  Easy Apply do LinkedIn.
- O SmartApply roda em outro domínio (smartapply.indeed.com): pode abrir em
  iframe, nova janela ou navegação de página inteira. O preenchimento detecta
  esse contexto e opera dentro dele.
- Login e qualquer bloqueio Cloudflare/CAPTCHA caem em INTERVENÇÃO MANUAL: o bot
  pausa, mostra o browser no dashboard e espera o usuário resolver. Todo seletor
  incerto degrada para "pausa e avisa", nunca para falha silenciosa.

Configuração via .env (nada hardcoded):
  INDEED_EMAIL, INDEED_PASSWORD   → credenciais (login é manual-first)
  INDEED_QUERY                    → palavra-chave de busca padrão (customizável no dashboard)
  INDEED_CIDADE                   → localização (l=) opcional
  INDEED_LIMIAR_MATCH             → nota mínima (0-100) de relevância p/ aplicar
  INDEED_TETO_APLICACOES          → teto total de candidaturas (persistente no Redis)
"""

import asyncio
import logging
import os
import re
import random

from dotenv import load_dotenv

load_dotenv()

from automation.selenium_browser import (
    nova_pagina, navegar, wait_for_selector, wait_for_selector_visible,
    click, digitar, digitar_com_delay, digitar_robusto, digitar_no_elemento,
    screenshot_base64, fechar, get_driver, get_title, _run_in_thread,
    _driver_session_valida,
)
from automation.browser import (
    notify_browser_step, get_intervention_state, set_intervention_state, wait_if_paused,
)
from automation.form_filler import responder_pergunta
from automation.contador_aplicacoes import INDEED as _cont
from automation.run_context import set_platform

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    InvalidSessionIdException, WebDriverException, StaleElementReferenceException,
    NoSuchElementException, TimeoutException,
)

logger = logging.getLogger(__name__)


# ── Config via .env ──────────────────────────────────────────────────────────

def _get_indeed_email() -> str:
    return os.getenv("INDEED_EMAIL", "")


def _get_indeed_password() -> str:
    return os.getenv("INDEED_PASSWORD", "")


def _get_query_padrao() -> str:
    return os.getenv("INDEED_QUERY", "desenvolvedor")


def _get_cidade() -> str:
    return os.getenv("INDEED_CIDADE", "")


# Nota mínima (0-100) de relevância vaga×currículo para aplicar. Fail-open igual
# ao LinkedIn: na dúvida (sem dados/timeout/erro), aplica. Ajustável no .env.
_LIMIAR_MATCH = int(os.getenv("INDEED_LIMIAR_MATCH", "40"))

_BASE = "https://br.indeed.com"


# Campos "de contato" já preenchidos automaticamente — não são perguntas customizadas.
_CAMPOS_PADRAO = {
    "telefone", "phone", "email", "e-mail", "nome", "name", "sobrenome", "lastname",
    "primeiro nome", "first name", "cidade", "city", "pais", "country", "país",
    "endereco", "endereço", "address", "cep", "zip", "estado", "state",
    "full name", "nome completo", "número de telefone", "phone number",
}

# Botões que avançam o formulário SmartApply (ordem = prioridade).
_BTN_CONTINUAR = [
    '[data-testid="continue-button"]',
    'button[data-testid="continue-button"]',
    'button:has-text("Continuar")',
    'button:has-text("Continue")',
    'button:has-text("Próxima")',
    'button:has-text("Next")',
    'button:has-text("Salvar e continuar")',
    'button:has-text("Save and continue")',
]
_BTN_ENVIAR = [
    '[data-testid="submit-application"]',
    'button[data-testid="submit-application"]',
    'button:has-text("Enviar candidatura")',
    'button:has-text("Submit your application")',
    'button:has-text("Submit application")',
    'button:has-text("Enviar")',
    'button:has-text("Submit")',
]
_BTN_REVISAR = [
    'button:has-text("Revisar")',
    'button:has-text("Review")',
]

# Botão que abre o Indeed Apply na página da vaga.
_BTN_APPLY = [
    "#indeedApplyButton",
    'button#indeedApplyButton',
    '.jobsearch-IndeedApplyButton-newDesign',
    'button[aria-label*="Candidatura simplificada"]',
    'button[aria-label*="Easily apply"]',
    'button:has-text("Candidatar-se")',
    'button:has-text("Candidatura simplificada")',
    'button:has-text("Apply now")',
    'button:has-text("Easily apply")',
]


# ── Intervenção manual / bloqueio (Cloudflare, CAPTCHA, login) ────────────────

def _pagina_bloqueada(url: str, title: str, html: str = "") -> bool:
    """Detecta desafio Cloudflare / verificação / captcha do Indeed."""
    u = (url or "").lower()
    t = (title or "").lower()
    h = (html or "").lower()
    if "challenge" in u or "cf_chl" in u or "__cf_chl" in u or "captcha" in u:
        return True
    for marca in (
        "just a moment", "verificando se você é humano", "checking if the site connection is secure",
        "verify you are human", "attention required", "cloudflare",
        "unusual activity", "atividade incomum",
    ):
        if marca in t or marca in h[:4000]:
            return True
    return False


async def _aguardar_resolucao_manual(driver, origem: str = "login") -> bool:
    """
    Pausa a automação e espera o usuário resolver login/CAPTCHA/Cloudflare no browser.
    Retorna True quando a página sai do estado de bloqueio, False se clicar Parar.
    Mesmo padrão do LinkedIn: qualquer seletor incerto deve cair aqui.
    """
    await set_intervention_state("paused", True)
    await set_intervention_state("intervention_type", "manual")
    await notify_browser_step(
        "selenium_indeed", "manual",
        f"⚠️ Ação manual necessária ({origem})! Resolva no browser Firefox "
        f"(login / verificação) e clique ▶️ Continuar no dashboard."
    )
    print(f"[INDEED] Intervenção manual em '{origem}' — aguardando resolução...")

    while True:
        control = await get_intervention_state()
        if control.get("current_action") == "parar":
            print("[INDEED] Usuário clicou Parar durante espera manual")
            return False

        # Usuário clicou Continuar no dashboard: verifica se saiu do bloqueio.
        if not control.get("paused") and control.get("intervention_type") != "manual":
            try:
                cur = await _run_in_thread(lambda: driver.current_url)
                ttl = await get_title()
            except Exception:
                cur, ttl = "", ""
            if not _pagina_bloqueada(cur, ttl):
                print(f"[INDEED] Retomando após intervenção manual. URL: {cur}")
                await notify_browser_step("selenium_indeed", "retomando", "Retomando após intervenção manual...")
                await set_intervention_state("paused", False)
                await set_intervention_state("intervention_type", None)
                return True
            # Ainda bloqueado — re-ativa intervenção.
            await set_intervention_state("paused", True)
            await set_intervention_state("intervention_type", "manual")
            await notify_browser_step(
                "selenium_indeed", "manual",
                "⚠️ Ainda há verificação/login pendente. Resolva no browser antes de continuar."
            )

        # Screenshot periódico para o dashboard.
        try:
            img = await screenshot_base64()
            if img:
                from automation.browser import _send_screenshot_via_sio
                asyncio.create_task(_send_screenshot_via_sio(
                    {"image": img, "step": "manual", "action": "aguardando resolução"}
                ))
        except Exception:
            pass

        await asyncio.sleep(2)


# ── Login (manual-first) ──────────────────────────────────────────────────────

def _esta_logado(url: str, title: str, html: str = "") -> bool:
    """Heurística de login no Indeed. Conservadora: na dúvida, NÃO está logado
    (para cair na intervenção manual, que é segura)."""
    u = (url or "").lower()
    if "login" in u or "auth" in u or "account/login" in u:
        return False
    h = (html or "").lower()
    # Indicadores de sessão ativa (menu da conta, sair, etc.)
    for marca in ("gnav-", "logout", "sair da conta", "account-menu", "minhas vagas", "myjobs"):
        if marca in h[:6000]:
            return True
    return False


async def _login_email_codigo(driver) -> None:
    """
    Executa o começo do login por e-mail + código do Indeed (o resto — digitar o
    código que chega no e-mail — é manual):
      1. digita o e-mail no campo
      2. clica em "Continuar"
      3. clica em "Acessar com um código"
    Cada passo é best-effort: se um seletor não casar, segue e cai na intervenção
    manual (que já espera o usuário concluir no browser).
    """
    email = _get_indeed_email()

    # 1. E-mail.
    if email:
        preenchido = False
        for sel in ("#ifl-InputFormField-ihl", "input[type='email']",
                    "input[name='__email']", "input[id*='email']",
                    "input[autocomplete='username']", "input[name='email']"):
            try:
                if await digitar_robusto(sel, email):
                    print("[INDEED] E-mail preenchido no login")
                    preenchido = True
                    break
            except Exception:
                continue
        if not preenchido:
            print("[INDEED] Campo de e-mail não encontrado — seguindo para manual")
            return

    # 2. Continuar.
    await asyncio.sleep(1)
    _CONTINUAR = [
        'button[type="submit"]',
        'button:has-text("Continuar")',
        'button:has-text("Continue")',
        '[data-testid="continue-button"]',
    ]
    _, clicou = await _clicar_botao_smartapply(driver, _CONTINUAR)
    if not clicou:
        print("[INDEED] Botão Continuar não encontrado — seguindo para manual")
        return
    await asyncio.sleep(3)

    # Bloqueio pode aparecer entre passos.
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        ttl = await get_title()
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, ttl, html = "", "", ""
    if _pagina_bloqueada(cur, ttl, html):
        return  # deixa a intervenção manual assumir

    # 3. Acessar com um código.
    _CODIGO = [
        'button:has-text("Acessar com um código")',
        'button:has-text("Entrar com um código")',
        'button:has-text("Enviar um código")',
        'a:has-text("Acessar com um código")',
        'button:has-text("Sign in with a login code")',
        'button:has-text("login code")',
        '[data-testid*="passwordless"]',
    ]
    _, clicou_cod = await _clicar_botao_smartapply(driver, _CODIGO)
    if clicou_cod:
        print("[INDEED] Clicou 'Acessar com um código' — aguardando código do e-mail")
        await asyncio.sleep(2)
    else:
        print("[INDEED] Opção de código não encontrada — pode já estar na tela de código")


async def _garantir_login() -> bool:
    """
    Garante sessão logada no Indeed. Login-manual-first: o Indeed empurra
    passwordless/Google-SSO e Cloudflare, então NÃO insistimos em automatizar —
    tentamos preencher o e-mail como conveniência e, se não estiver claramente
    logado, pausamos para o usuário concluir no browser.
    Retorna True se logado (após intervenção manual se preciso).
    """
    driver = await get_driver()
    if not driver:
        await nova_pagina(_BASE, reutilizar=False)
        await asyncio.sleep(2)
        driver = await get_driver()
    if not driver:
        print("[INDEED] ERRO: driver é None")
        return False

    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        ttl = await get_title()
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, ttl, html = "", "", ""

    if _pagina_bloqueada(cur, ttl, html):
        print("[INDEED] Bloqueio (Cloudflare/CAPTCHA) na página inicial")
        if not await _aguardar_resolucao_manual(driver, "acesso ao Indeed"):
            return False
        try:
            cur = await _run_in_thread(lambda: driver.current_url)
            ttl = await get_title()
            html = await _run_in_thread(lambda: driver.page_source)
        except Exception:
            pass

    if _esta_logado(cur, ttl, html):
        print("[INDEED] Já está logado")
        return True

    print("[INDEED] Não parece logado — abrindo login (e-mail → código)")
    await notify_browser_step("indeed_login", "login", "Abrindo login do Indeed")
    try:
        await navegar("https://secure.indeed.com/account/login")
        await asyncio.sleep(3)
    except Exception:
        pass

    # Fluxo escolhido: continuar pelo e-mail e acessar com um código.
    await _login_email_codigo(driver)

    # O código chega no e-mail e é digitado à mão: pausa em intervenção manual.
    await notify_browser_step(
        "indeed_login", "codigo",
        "📧 Enviamos/solicitamos um código de acesso. Digite o código do seu e-mail "
        "no browser e clique ▶️ Continuar no dashboard."
    )
    resolvido = await _aguardar_resolucao_manual(driver, "código de acesso do Indeed")
    if not resolvido:
        return False

    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        ttl = await get_title()
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, ttl, html = "", "", ""
    # Após intervenção manual, confia: se saiu de login e não está bloqueado, ok.
    if not _pagina_bloqueada(cur, ttl, html) and "login" not in (cur or "").lower():
        await notify_browser_step("indeed_login", "sucesso", "Login concluído")
        return True
    return _esta_logado(cur, ttl, html)


# ── Busca ─────────────────────────────────────────────────────────────────────

def _build_search_url(query: str = "") -> str:
    """Monta a URL de busca. A palavra-chave (customizável no dashboard) vira q=;
    a filtragem fina por currículo fica com o gate de match (search broad, gate)."""
    from urllib.parse import quote_plus
    q = (query or "").strip() or _get_query_padrao()
    url = f"{_BASE}/jobs?q={quote_plus(q)}"
    cidade = _get_cidade().strip()
    url += f"&l={quote_plus(cidade)}" if cidade else "&l="
    return url


async def extrair_vagas_da_busca(perfil: dict, max_vagas: int = 20, query: str = "") -> dict:
    """
    Extrai vagas da página de busca do Indeed. Usa a página ativa se já estiver
    no Indeed; senão navega para a busca montada a partir da palavra-chave.
    Marca eligibilidade de Indeed Apply (candidatura simplificada) em cada card.
    """
    set_platform("indeed")
    driver = await get_driver()
    if not driver or not await _driver_session_valida():
        await nova_pagina(_build_search_url(query), reutilizar=False)
        await asyncio.sleep(3)
        driver = await get_driver()
    else:
        cur = await _run_in_thread(lambda: driver.current_url)
        if "indeed.com" not in (cur or "").lower():
            await navegar(_build_search_url(query))
            await asyncio.sleep(3)
        elif query:
            # Palavra-chave nova pedida explicitamente → refaz a busca.
            await navegar(_build_search_url(query))
            await asyncio.sleep(3)

    # Login/bloqueio caem em manual.
    login_ok = await _garantir_login()
    if not login_ok:
        return {
            "sucesso": False, "vagas": [],
            "mensagem": "Não foi possível acessar o Indeed (login/verificação). Resolva no browser e tente de novo.",
        }

    # Garante que estamos numa página de resultados.
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
    except Exception:
        cur = ""
    if "/jobs" not in (cur or "").lower():
        await navegar(_build_search_url(query))
        await asyncio.sleep(3)

    await notify_browser_step("indeed_extracao", "iniciando", "Extraindo vagas do Indeed")
    cards = await _extrair_cards_vaga(max_vagas)
    await notify_browser_step("indeed_extracao", "finalizando", f"Extraídas {len(cards)} vagas")
    return {"sucesso": True, "vagas": cards, "total": len(cards)}


async def _extrair_cards_vaga(max_vagas: int = 20) -> list[dict]:
    """Extrai os cards de vaga visíveis na busca, marcando Indeed Apply."""
    import time as _time

    driver = await get_driver()
    if not driver:
        return []

    def _procurar():
        resultados = []
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "div.job_seen_beacon, [data-jk], td.resultContent, .jobsearch-ResultsList > li"
                ))
            )
        except Exception:
            pass
        _time.sleep(2)

        # Scroll para lazy-load.
        last_h = driver.execute_script("return document.body.scrollHeight")
        for _ in range(4):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            _time.sleep(1.2)
            new_h = driver.execute_script("return document.body.scrollHeight")
            if new_h == last_h:
                break
            last_h = new_h
        driver.execute_script("window.scrollTo(0, 0);")
        _time.sleep(1)

        cards = []
        for sel in ("div.job_seen_beacon", ".jobsearch-ResultsList > li",
                    "td.resultContent", "[data-jk]"):
            try:
                found = driver.find_elements(By.CSS_SELECTOR, sel)
                if found:
                    print(f"[INDEED] {len(found)} cards via: {sel}")
                    cards = found
                    break
            except Exception:
                continue

        seen = set()
        for card in cards[:max_vagas * 2]:
            try:
                titulo = ""
                url = ""
                vaga_id = ""
                empresa = ""

                # id da vaga (data-jk) + link.
                try:
                    jk_el = card.find_element(By.CSS_SELECTOR, "[data-jk]")
                    vaga_id = jk_el.get_attribute("data-jk") or ""
                except Exception:
                    vaga_id = card.get_attribute("data-jk") or ""

                try:
                    link_el = card.find_element(By.CSS_SELECTOR, "h2.jobTitle a, a.jcs-JobTitle, a[id^='job_'], a[data-jk]")
                    url = link_el.get_attribute("href") or ""
                    titulo = (link_el.text or link_el.get_attribute("title") or "").strip()
                    if not vaga_id:
                        vaga_id = link_el.get_attribute("data-jk") or ""
                except Exception:
                    pass

                if not url and vaga_id:
                    url = f"{_BASE}/viewjob?jk={vaga_id}"
                if not url or url in seen:
                    continue
                seen.add(url)

                try:
                    emp_el = card.find_element(By.CSS_SELECTOR, "[data-testid='company-name'], .companyName, span.companyName")
                    empresa = (emp_el.text or "").strip()
                except Exception:
                    empresa = ""

                # Elegibilidade Indeed Apply: label "Candidatura simplificada"/"Easily apply".
                easy = False
                try:
                    card_html = (card.get_attribute("innerHTML") or "").lower()
                    easy = ("indeedapply" in card_html or "candidatura simplificada" in card_html
                            or "easily apply" in card_html or "ialbl" in card_html)
                except Exception:
                    easy = False

                resultados.append({
                    "id": f"indeed-{vaga_id}" if vaga_id else url,
                    "titulo": titulo or f"Vaga {vaga_id}",
                    "empresa": empresa,
                    "url": url,
                    "fonte": "Indeed",
                    "easy_apply": easy,
                    "salario": "", "modalidade": "", "descricao": "", "local": "",
                })
                if len(resultados) >= max_vagas:
                    break
            except Exception:
                continue
        return resultados

    try:
        return await _run_in_thread(_procurar)
    except Exception as e:
        logger.warning("_extrair_cards_vaga erro: %s", e)
        return []


# ── Match com currículo (reutiliza a política fail-open do LinkedIn) ───────────

async def _extrair_descricao_vaga(driver) -> str:
    def _extract():
        seletores = [
            "#jobDescriptionText", ".jobsearch-JobComponent-description",
            "div[id*='jobDescription']", ".jobsearch-jobDescriptionText",
            "[data-testid='jobsearch-JobComponent-description']",
        ]
        for sel in seletores:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].is_displayed():
                    text = (els[0].text or "").strip()
                    if len(text) > 100:
                        return text[:3000]
            except Exception:
                continue
        return ""
    try:
        return await _run_in_thread(_extract)
    except Exception:
        return ""


async def _avaliar_match_vaga(descricao: str, curriculo: str) -> dict:
    """Avalia compatibilidade vaga×currículo via LLM barato. Fail-open (na dúvida,
    aplica) — mesma política do LinkedIn, apenas com env INDEED_LIMIAR_MATCH."""
    if not descricao or not curriculo:
        return {"aplicar": True, "idioma": "pt", "motivo": "sem dados para avaliar"}

    import json as _json
    from ai.openrouter import openrouter

    prompt = f"""You are a strict recruiter deciding whether a candidate should apply to a job, based on how well the job matches their resume.

JOB DESCRIPTION (first 2000 chars):
{descricao[:2000]}

CANDIDATE RESUME (first 1500 chars):
{curriculo[:1500]}

Score the relevance from 0 to 100. The candidate prefers to apply WHENEVER there is a plausible fit ("when in doubt, apply"). Only score LOW for jobs that are clearly a different profession.
- Score below 40 ONLY if the core role is a clearly different profession/field with little overlap.
- If the job is in tech/software and shares ANY meaningful overlap with the resume, score 40 or above so it applies — even if some requirements are missing or seniority differs.
- Reward overlap; do not penalize heavily for missing a few requirements or a seniority gap.

Respond with ONLY valid JSON, no markdown:
{{"nota": 0, "idioma": "pt", "motivo": "brief reason"}}
- "nota": integer 0-100 as defined above
- "idioma": language of the job description — "pt" for Portuguese, "en" for English
- "motivo": one sentence explaining the score"""

    try:
        resp = openrouter.converse([{"role": "user", "content": prompt}]).strip()
        m = re.search(r'\{[^{}]+\}', resp, re.DOTALL)
        if m:
            data = _json.loads(m.group())
            idioma = str(data.get("idioma", "pt"))
            motivo = str(data.get("motivo", ""))
            try:
                nota = int(float(data.get("nota")))
                return {"aplicar": nota >= _LIMIAR_MATCH, "idioma": idioma,
                        "motivo": f"nota {nota}/100 — {motivo}"}
            except (TypeError, ValueError):
                return {"aplicar": True, "idioma": idioma, "motivo": f"sem nota — {motivo}"}
    except Exception as e:
        logger.warning("_avaliar_match_vaga erro LLM: %s", e)
    return {"aplicar": True, "idioma": "pt", "motivo": "fallback"}


async def _get_resumo_curriculo(user_id: str) -> str:
    try:
        from graph.neo4j_client import get_neo4j
        return get_neo4j().get_resumo_curriculo(user_id) or ""
    except Exception:
        return ""


# ── SmartApply: contexto cross-domain (iframe / janela / página) ──────────────

def _extrair_apply_id(html: str) -> str:
    """Tenta extrair o indeedApplyableJobId da página da vaga (rota direta)."""
    if not html:
        return ""
    m = re.search(r'indeedApplyableJobId["\':=\s]+([0-9a-fA-F\-]+-[A-Za-z0-9]+)', html)
    return m.group(1) if m else ""


async def _entrar_contexto_smartapply(driver) -> str:
    """
    Após clicar em Aplicar, o SmartApply pode estar em: nova janela ('window'),
    iframe ('iframe') ou navegação de página ('page'). Entra no contexto certo e
    retorna o modo (ou '' se não achou). Todos os finds seguintes ficam escopados.
    """
    await asyncio.sleep(2)

    # 1. Nova janela/aba.
    try:
        handles = await _run_in_thread(lambda: driver.window_handles)
        if handles and len(handles) > 1:
            await _run_in_thread(lambda: driver.switch_to.window(handles[-1]))
            print("[INDEED] SmartApply em nova janela")
            return "window"
    except Exception:
        pass

    # 2. Página inteira (mesma janela navegou para smartapply).
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        if "smartapply.indeed.com" in (cur or "").lower() or "indeedapply" in (cur or "").lower():
            print("[INDEED] SmartApply em página inteira")
            return "page"
    except Exception:
        pass

    # 3. Iframe do Indeed Apply.
    def _achar_iframe():
        for fr in driver.find_elements(By.CSS_SELECTOR, "iframe"):
            try:
                src = (fr.get_attribute("src") or "").lower()
                if "indeedapply" in src or "smartapply" in src:
                    driver.switch_to.frame(fr)
                    return True
            except Exception:
                continue
        return False
    try:
        if await _run_in_thread(_achar_iframe):
            print("[INDEED] SmartApply em iframe")
            return "iframe"
    except Exception:
        pass

    return ""


async def _sair_contexto_smartapply(driver, modo: str, janela_original: str = "") -> None:
    """Volta para a listagem depois de aplicar, fechando janela/saindo do iframe."""
    try:
        if modo == "iframe":
            await _run_in_thread(lambda: driver.switch_to.default_content())
        elif modo == "window":
            try:
                await _run_in_thread(lambda: driver.close())
            except Exception:
                pass
            handles = await _run_in_thread(lambda: driver.window_handles)
            alvo = janela_original if janela_original in handles else (handles[0] if handles else None)
            if alvo:
                await _run_in_thread(lambda: driver.switch_to.window(alvo))
    except Exception as e:
        logger.warning("_sair_contexto_smartapply erro: %s", e)


# ── SmartApply: formulário multi-step ─────────────────────────────────────────

async def _candidatura_enviada(driver) -> bool:
    """Detecta a tela de confirmação de candidatura enviada."""
    frases = (
        "candidatura enviada", "sua candidatura foi enviada", "candidatura foi enviada",
        "application submitted", "your application has been submitted", "application sent",
        "we sent your application", "enviamos sua candidatura", "candidatura recebida",
        "você se candidatou", "you've applied", "you applied",
    )
    def _check():
        try:
            html = (driver.page_source or "").lower()
        except Exception:
            return False
        return any(f in html for f in frases)
    try:
        return await _run_in_thread(_check)
    except Exception:
        return False


async def _get_label_smartapply(driver, inp) -> str:
    """Rótulo associado a um input no SmartApply."""
    def _lbl():
        # aria-label / associated label / placeholder / texto ascendente
        for attr in ("aria-label", "placeholder", "name"):
            v = inp.get_attribute(attr)
            if v and v.strip():
                return v.strip()
        iid = inp.get_attribute("id")
        if iid:
            try:
                lab = driver.find_element(By.CSS_SELECTOR, f"label[for='{iid}']")
                if lab.text.strip():
                    return lab.text.strip()
            except Exception:
                pass
        try:
            anc = inp.find_element(By.XPATH, "./ancestor::*[self::div or self::fieldset][1]")
            t = (anc.text or "").strip()
            return t.split("\n")[0][:120] if t else ""
        except Exception:
            return ""
    try:
        return await _run_in_thread(_lbl)
    except Exception:
        return ""


async def _preencher_contato_smartapply(driver, perfil: dict) -> None:
    """Preenche campos de contato conhecidos (nome/email/telefone) se vazios."""
    nome = perfil.get("nome", "") or ""
    partes = nome.split()
    primeiro = partes[0] if partes else ""
    ultimo = " ".join(partes[1:]) if len(partes) > 1 else ""
    telefone = str(perfil.get("telefone", perfil.get("phone", "")) or "")
    email = perfil.get("email", "") or _get_indeed_email()

    mapa = [
        ("input[name*='firstName'], input[id*='firstName'], input[autocomplete='given-name']", primeiro),
        ("input[name*='lastName'], input[id*='lastName'], input[autocomplete='family-name']", ultimo),
        ("input[name='name'], input[id*='fullName'], input[autocomplete='name']", nome),
        ("input[type='email'], input[name*='email'], input[autocomplete='email']", email),
        ("input[type='tel'], input[name*='phone'], input[id*='phone'], input[autocomplete='tel']", telefone),
    ]
    for sel, val in mapa:
        if not val:
            continue
        try:
            def _preenche_se_vazio(s=sel, v=val):
                for el in driver.find_elements(By.CSS_SELECTOR, s):
                    try:
                        if el.is_displayed() and not (el.get_attribute("value") or "").strip():
                            el.clear()
                            el.send_keys(v)
                            return True
                    except Exception:
                        continue
                return False
            await _run_in_thread(_preenche_se_vazio)
        except Exception:
            continue


async def _tratar_curriculo_smartapply(driver, curriculo_path: str) -> None:
    """Trata o step de currículo do SmartApply: prefere usar um currículo já salvo
    na conta; se houver upload e tivermos o PDF ATS gerado, envia o arquivo."""
    def _tratar():
        # 1. Opção "usar currículo existente" (radio/card já selecionado é o ideal).
        for sel in ("input[type='radio'][value*='indeed']",
                    "input[type='radio'][id*='resume']",
                    "[data-testid*='resume'] input[type='radio']"):
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    if el.is_displayed() and not el.is_selected():
                        driver.execute_script("arguments[0].click();", el)
                        return "existente"
                    if el.is_displayed() and el.is_selected():
                        return "existente"
            except Exception:
                continue

        # 2. Upload do PDF ATS gerado para esta vaga.
        if curriculo_path and os.path.exists(curriculo_path):
            for el in driver.find_elements(By.CSS_SELECTOR, "input[type='file']"):
                try:
                    # inputs de arquivo costumam ficar ocultos — não exige is_displayed.
                    el.send_keys(curriculo_path)
                    return "upload"
                except Exception:
                    continue
        return ""

    try:
        modo = await _run_in_thread(_tratar)
        if modo:
            print(f"[INDEED] Currículo tratado ({modo})")
            await asyncio.sleep(2)
    except Exception as e:
        logger.warning("_tratar_curriculo_smartapply erro: %s", e)


async def _detectar_perguntas_smartapply(driver) -> list:
    """
    Detecta perguntas customizadas não respondidas no step atual: textareas/inputs
    de texto vazios e grupos radio/select sem seleção, ignorando campos de contato.
    Prefixa NUMERO:/DECIMAL: para o form_filler quando o input é numérico.
    """
    def _detectar():
        perguntas = []
        vistos = set()

        # Text inputs e textareas vazios.
        for el in driver.find_elements(By.CSS_SELECTOR,
                "textarea, input[type='text'], input[type='number'], input:not([type])"):
            try:
                if not el.is_displayed():
                    continue
                if (el.get_attribute("value") or "").strip():
                    continue
                label = (el.get_attribute("aria-label") or el.get_attribute("placeholder")
                         or el.get_attribute("name") or "")
                iid = el.get_attribute("id")
                if not label and iid:
                    try:
                        label = driver.find_element(By.CSS_SELECTOR, f"label[for='{iid}']").text
                    except Exception:
                        label = ""
                label = (label or "").strip()
                if not label or label.lower() in _CAMPOS_PADRAO:
                    continue
                key = label.lower()
                if key in vistos:
                    continue
                vistos.add(key)
                tipo = (el.get_attribute("type") or "").lower()
                if tipo == "number":
                    perguntas.append("NUMERO:" + label)
                else:
                    perguntas.append(label)
            except Exception:
                continue

        # Selects sem escolha real.
        for el in driver.find_elements(By.CSS_SELECTOR, "select"):
            try:
                if not el.is_displayed():
                    continue
                val = (el.get_attribute("value") or "").strip().lower()
                if val and val not in ("", "0", "select", "selecione", "choose"):
                    continue
                label = (el.get_attribute("aria-label") or el.get_attribute("name") or "").strip()
                iid = el.get_attribute("id")
                if not label and iid:
                    try:
                        label = driver.find_element(By.CSS_SELECTOR, f"label[for='{iid}']").text.strip()
                    except Exception:
                        label = ""
                if not label or label.lower() in _CAMPOS_PADRAO or label.lower() in vistos:
                    continue
                vistos.add(label.lower())
                opts = [o.text.strip() for o in el.find_elements(By.TAG_NAME, "option") if o.text.strip()]
                perguntas.append("SELECT:" + label + ":" + ";".join(opts[:12]))
            except Exception:
                continue

        # Grupos de radio sem seleção.
        grupos = {}
        for el in driver.find_elements(By.CSS_SELECTOR, "input[type='radio']"):
            try:
                if not el.is_displayed():
                    continue
                gname = el.get_attribute("name") or ""
                grupos.setdefault(gname, []).append(el)
            except Exception:
                continue
        for gname, els in grupos.items():
            try:
                if any(e.is_selected() for e in els):
                    continue
                # Label do grupo: procura texto do fieldset/legend ascendente.
                label = ""
                try:
                    anc = els[0].find_element(By.XPATH, "./ancestor::fieldset[1]")
                    label = (anc.text or "").split("\n")[0].strip()
                except Exception:
                    label = gname
                if not label or label.lower() in vistos:
                    continue
                vistos.add(label.lower())
                opts = []
                for e in els:
                    lv = e.get_attribute("value") or ""
                    eid = e.get_attribute("id")
                    if eid:
                        try:
                            lv = driver.find_element(By.CSS_SELECTOR, f"label[for='{eid}']").text.strip() or lv
                        except Exception:
                            pass
                    if lv:
                        opts.append(lv)
                perguntas.append("RADIO:" + label + ":" + ";".join(opts[:12]))
            except Exception:
                continue

        # Checkboxes não marcados (consentimento/termos — quase sempre obrigatórios).
        for el in driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
            try:
                if not el.is_displayed() or el.is_selected():
                    continue
                label = (el.get_attribute("aria-label") or el.get_attribute("name") or "")
                eid = el.get_attribute("id")
                if not label and eid:
                    try:
                        label = driver.find_element(By.CSS_SELECTOR, f"label[for='{eid}']").text.strip()
                    except Exception:
                        label = ""
                if not label:
                    try:
                        anc = el.find_element(By.XPATH, "./ancestor::label[1]")
                        label = (anc.text or "").strip()
                    except Exception:
                        label = ""
                label = (label or "consentimento").strip()
                if label.lower() in vistos:
                    continue
                vistos.add(label.lower())
                perguntas.append("CHECKBOX:" + label)
            except Exception:
                continue

        return perguntas

    try:
        return await _run_in_thread(_detectar)
    except Exception as e:
        logger.warning("_detectar_perguntas_smartapply erro: %s", e)
        return []


async def _preencher_resposta_smartapply(driver, pergunta: str, resposta: str) -> None:
    """Preenche a resposta no campo certo do SmartApply (texto/select/radio)."""
    if pergunta.startswith("SELECT:"):
        corpo = pergunta[len("SELECT:"):]
        label = corpo.split(":", 1)[0]
        await _selecionar_opcao(driver, label, resposta, tag="select")
        return
    if pergunta.startswith("RADIO:"):
        corpo = pergunta[len("RADIO:"):]
        label = corpo.split(":", 1)[0]
        await _selecionar_opcao(driver, label, resposta, tag="radio")
        return
    if pergunta.startswith("CHECKBOX:"):
        label = pergunta[len("CHECKBOX:"):].strip()
        marcar = str(resposta or "").strip().lower() in ("sim", "yes", "true", "1", "concordo", "aceito")
        if not marcar:
            return
        def _marca():
            for el in driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
                try:
                    if not el.is_displayed() or el.is_selected():
                        continue
                    l = (el.get_attribute("aria-label") or el.get_attribute("name") or "")
                    eid = el.get_attribute("id")
                    if not l and eid:
                        try:
                            l = driver.find_element(By.CSS_SELECTOR, f"label[for='{eid}']").text.strip()
                        except Exception:
                            l = ""
                    if not l:
                        try:
                            l = el.find_element(By.XPATH, "./ancestor::label[1]").text.strip()
                        except Exception:
                            l = ""
                    if (l or "consentimento").strip().lower() == label.lower():
                        driver.execute_script("arguments[0].click();", el)
                        return True
                except Exception:
                    continue
            return False
        try:
            await _run_in_thread(_marca)
        except Exception as e:
            logger.warning("checkbox click erro: %s", e)
        return

    label = pergunta.split(":", 1)[1] if pergunta.startswith(("NUMERO:", "DECIMAL:")) else pergunta

    def _preenche():
        alvo = None
        for el in driver.find_elements(By.CSS_SELECTOR, "textarea, input[type='text'], input[type='number'], input:not([type])"):
            try:
                if not el.is_displayed():
                    continue
                l = (el.get_attribute("aria-label") or el.get_attribute("placeholder")
                     or el.get_attribute("name") or "")
                iid = el.get_attribute("id")
                if not l and iid:
                    try:
                        l = driver.find_element(By.CSS_SELECTOR, f"label[for='{iid}']").text
                    except Exception:
                        l = ""
                if (l or "").strip().lower() == label.strip().lower() and not (el.get_attribute("value") or "").strip():
                    alvo = el
                    break
            except Exception:
                continue
        if alvo is not None:
            try:
                alvo.clear()
            except Exception:
                pass
            alvo.send_keys(resposta)
            return True
        return False
    try:
        await _run_in_thread(_preenche)
    except Exception as e:
        logger.warning("_preencher_resposta_smartapply erro: %s", e)


async def _selecionar_opcao(driver, label: str, resposta: str, tag: str) -> None:
    """Seleciona opção em select/radio pelo texto mais próximo da resposta."""
    resp_low = (resposta or "").strip().lower()

    def _sel_select():
        from selenium.webdriver.support.ui import Select
        for el in driver.find_elements(By.CSS_SELECTOR, "select"):
            try:
                l = (el.get_attribute("aria-label") or el.get_attribute("name") or "").strip().lower()
                if l != label.strip().lower():
                    continue
                sel = Select(el)
                for o in sel.options:
                    if resp_low and resp_low in (o.text or "").lower():
                        sel.select_by_visible_text(o.text)
                        return True
                # fallback: primeira opção "sim"/válida não-placeholder
                for o in sel.options:
                    t = (o.text or "").strip().lower()
                    if t and t not in ("selecione", "select", "choose"):
                        sel.select_by_visible_text(o.text)
                        return True
            except Exception:
                continue
        return False

    def _sel_radio():
        for el in driver.find_elements(By.CSS_SELECTOR, "input[type='radio']"):
            try:
                if not el.is_displayed():
                    continue
                lv = el.get_attribute("value") or ""
                eid = el.get_attribute("id")
                if eid:
                    try:
                        lv = driver.find_element(By.CSS_SELECTOR, f"label[for='{eid}']").text.strip() or lv
                    except Exception:
                        pass
                if resp_low and resp_low in (lv or "").lower():
                    driver.execute_script("arguments[0].click();", el)
                    return True
            except Exception:
                continue
        return False

    try:
        if tag == "select":
            await _run_in_thread(_sel_select)
        else:
            await _run_in_thread(_sel_radio)
    except Exception as e:
        logger.warning("_selecionar_opcao erro: %s", e)


async def _clicar_botao_smartapply(driver, seletores: list) -> tuple:
    """Clica no primeiro botão visível dentre os seletores (suporta :has-text).
    Retorna (texto_do_botao, clicou)."""
    def _clicar():
        # Busca genérica por botões visíveis e casa por texto/atributo.
        alvos_texto = []
        for s in seletores:
            m = re.search(r':has-text\(["\'](.+?)["\']\)', s)
            if m:
                alvos_texto.append(m.group(1).lower())

        # 1. Seletores CSS diretos (sem :has-text).
        for s in seletores:
            if ":has-text" in s:
                continue
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, s):
                    if el.is_displayed() and el.is_enabled():
                        txt = (el.text or el.get_attribute("aria-label") or "").strip()
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        el.click()
                        return (txt or s, True)
            except Exception:
                continue

        # 2. Por texto.
        if alvos_texto:
            for el in driver.find_elements(By.CSS_SELECTOR, "button, [role='button'], a"):
                try:
                    if not (el.is_displayed() and el.is_enabled()):
                        continue
                    txt = (el.text or el.get_attribute("aria-label") or "").strip().lower()
                    if any(a in txt for a in alvos_texto):
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        el.click()
                        return (txt, True)
                except Exception:
                    continue
        return ("", False)

    try:
        return await _run_in_thread(_clicar)
    except Exception as e:
        logger.warning("_clicar_botao_smartapply erro: %s", e)
        return ("", False)


async def _processar_formulario_smartapply(driver, perfil: dict, curriculo_path: str,
                                           vaga_url: str, resumo_curriculo: str,
                                           idioma: str = "pt", vaga_titulo: str = "") -> dict:
    """Percorre o formulário SmartApply multi-step. Preenche contato + perguntas,
    avança/revisa/envia. Trava real → cai em intervenção manual (nunca silencioso)."""
    if resumo_curriculo and not perfil.get("resumo_curriculo"):
        perfil = {**perfil, "resumo_curriculo": resumo_curriculo}

    perguntas_feitas = []
    respostas = {}
    max_steps = 12
    nao_avancou = 0

    for step in range(max_steps):
        # Respeita pausa/pular do dashboard.
        control = await get_intervention_state()
        if control.get("paused") or control.get("intervention_type") == "manual":
            await wait_if_paused(None, f"smartapply_step_{step}")
        control = await get_intervention_state()
        if control.get("current_action") == "parar":
            return {"sucesso": False, "motivo_falha": "parado", "mensagem": "Interrompido pelo usuário."}

        await asyncio.sleep(random.uniform(0.6, 1.2))
        await notify_browser_step(f"indeed_step_{step}", "preenchendo", "Preenchendo SmartApply")

        # Sucesso pode aparecer a qualquer momento.
        if await _candidatura_enviada(driver):
            b64 = await screenshot_base64()
            await notify_browser_step(f"indeed_step_{step}", "sucesso", "Candidatura enviada!")
            return {"sucesso": True, "perguntas_respondidas": perguntas_feitas,
                    "mensagem": "Candidatura enviada com sucesso via Indeed!",
                    "screenshot": b64[:100] if b64 else ""}

        # Preenche contato + currículo + perguntas (resiliente a re-render).
        for _tent in range(3):
            try:
                await _preencher_contato_smartapply(driver, perfil)
                await _tratar_curriculo_smartapply(driver, curriculo_path)
                perguntas = await _detectar_perguntas_smartapply(driver)
                break
            except StaleElementReferenceException:
                await asyncio.sleep(0.8)
                perguntas = []
        else:
            perguntas = []

        if perguntas:
            await notify_browser_step(f"indeed_step_{step}", "respondendo", f"{len(perguntas)} pergunta(s)")
            for p in perguntas:
                if p not in respostas:
                    try:
                        respostas[p] = responder_pergunta(
                            p, perfil, vaga_titulo=vaga_titulo, vaga_empresa="",
                            resumo_curriculo=resumo_curriculo, idioma=idioma,
                        )
                    except Exception as e:
                        logger.warning("responder_pergunta erro: %s", e)
                        # Nunca deixa vazio: campo obrigatório em branco trava/descarta.
                        from automation.form_filler import resposta_segura
                        respostas[p] = resposta_segura(p, idioma)
                for _tp in range(3):
                    try:
                        await _preencher_resposta_smartapply(driver, p, respostas[p])
                        break
                    except StaleElementReferenceException:
                        await asyncio.sleep(0.6)
                if p not in perguntas_feitas:
                    perguntas_feitas.append(p)
                await asyncio.sleep(random.uniform(0.5, 1.0))

        # Assinatura antes de clicar (detecta não-avanço).
        try:
            sig_antes = await _run_in_thread(lambda: driver.current_url + "|" + str(len(driver.find_elements(By.CSS_SELECTOR, "input,textarea,select,button"))))
        except Exception:
            sig_antes = ""

        # Enviar > Revisar > Continuar.
        btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_ENVIAR)
        is_submit = clicou
        if not clicou:
            btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_REVISAR)
        if not clicou:
            btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_CONTINUAR)

        if not clicou:
            # Nenhum botão de avanço → talvez upload de currículo ou campo estranho.
            # Degrada para intervenção manual em vez de falhar silenciosamente.
            await notify_browser_step(f"indeed_step_{step}", "manual",
                                      "Não encontrei botão de avançar — assumindo controle manual")
            if not await _aguardar_resolucao_manual(driver, f"SmartApply step {step}"):
                b64 = await screenshot_base64()
                return {"sucesso": False, "motivo_falha": "formulario_incompleto",
                        "mensagem": f"Formulário não concluído. Aplique manualmente: {vaga_url}",
                        "screenshot": b64[:100] if b64 else ""}
            continue

        print(f"[INDEED] Step {step}: clicou '{btn_text[:40]}'")
        await asyncio.sleep(3 if is_submit else 2)

        if await _candidatura_enviada(driver):
            b64 = await screenshot_base64()
            await notify_browser_step(f"indeed_step_{step}", "sucesso", "Candidatura enviada!")
            return {"sucesso": True, "perguntas_respondidas": perguntas_feitas,
                    "mensagem": "Candidatura enviada com sucesso via Indeed!",
                    "screenshot": b64[:100] if b64 else ""}

        if is_submit:
            await asyncio.sleep(2.5)
            if await _candidatura_enviada(driver):
                b64 = await screenshot_base64()
                return {"sucesso": True, "perguntas_respondidas": perguntas_feitas,
                        "mensagem": "Candidatura enviada com sucesso via Indeed!",
                        "screenshot": b64[:100] if b64 else ""}

        # Detecta avanço.
        try:
            sig_depois = await _run_in_thread(lambda: driver.current_url + "|" + str(len(driver.find_elements(By.CSS_SELECTOR, "input,textarea,select,button"))))
        except Exception:
            sig_depois = ""
        if sig_antes and sig_antes != sig_depois:
            nao_avancou = 0
            continue
        nao_avancou += 1
        if nao_avancou >= 3:
            # Travou de verdade → manual, não silencioso.
            await notify_browser_step(f"indeed_step_{step}", "manual", "Formulário travado — controle manual")
            if not await _aguardar_resolucao_manual(driver, f"SmartApply travado step {step}"):
                b64 = await screenshot_base64()
                return {"sucesso": False, "motivo_falha": "formulario_travado",
                        "mensagem": f"Formulário travou. Aplique manualmente: {vaga_url}",
                        "screenshot": b64[:100] if b64 else ""}
            nao_avancou = 0

    b64 = await screenshot_base64()
    if await _candidatura_enviada(driver):
        return {"sucesso": True, "perguntas_respondidas": perguntas_feitas,
                "mensagem": "Candidatura enviada com sucesso via Indeed!",
                "screenshot": b64[:100] if b64 else ""}
    return {"sucesso": False, "motivo_falha": "formulario_incompleto",
            "mensagem": f"Não consegui concluir o formulário. Aplique manualmente: {vaga_url}",
            "screenshot": b64[:100] if b64 else ""}


# ── Aplicação em uma vaga ─────────────────────────────────────────────────────

async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "", user_id: str = "admin") -> dict:
    """
    Aplica numa vaga do Indeed via SmartApply (Indeed Apply). Avalia match com o
    currículo, clica em Aplicar, entra no contexto do SmartApply (iframe/janela/
    página) e preenche o formulário multi-step. Login/bloqueio → manual.
    """
    set_platform("indeed")
    resumo_curriculo = perfil.get("resumo_curriculo", "") or ""
    if not resumo_curriculo:
        resumo_curriculo = await _get_resumo_curriculo(user_id)

    try:
        await notify_browser_step("selenium_indeed", "iniciando", "Abrindo Indeed")
        print(f"[INDEED] Aplicando em: {vaga_url}")

        driver = await get_driver()
        if not driver or not await _driver_session_valida():
            if driver:
                try:
                    await fechar()
                except Exception:
                    pass
            await nova_pagina(_BASE, reutilizar=False)
            await asyncio.sleep(2)

        if not await _garantir_login():
            return {"sucesso": False, "motivo_falha": "login_falhou",
                    "mensagem": "Não foi possível acessar o Indeed (login/verificação)."}

        driver = await get_driver()
        janela_original = await _run_in_thread(lambda: driver.current_window_handle)

        await notify_browser_step("selenium_indeed", "navegando", "Abrindo vaga")
        await navegar(vaga_url)
        await asyncio.sleep(3)

        cur = await _run_in_thread(lambda: driver.current_url)
        ttl = await get_title()
        html = await _run_in_thread(lambda: driver.page_source)
        if _pagina_bloqueada(cur, ttl, html):
            if not await _aguardar_resolucao_manual(driver, "abrir vaga"):
                return {"sucesso": False, "mensagem": "Verificação não resolvida."}
            html = await _run_in_thread(lambda: driver.page_source)

        # Match com currículo (fail-open).
        idioma_vaga = "pt"
        if resumo_curriculo:
            descricao = await _extrair_descricao_vaga(driver)
            if descricao:
                await notify_browser_step("selenium_indeed", "avaliando", "Verificando match com currículo...")
                aval = await _avaliar_match_vaga(descricao, resumo_curriculo)
                idioma_vaga = aval.get("idioma", "pt")
                logger.info("indeed_selenium: match=%s idioma=%s motivo=%s",
                            aval.get("aplicar"), idioma_vaga, aval.get("motivo"))
                if not aval.get("aplicar", True):
                    motivo = aval.get("motivo", "sem match")
                    await notify_browser_step("selenium_indeed", "pulada", f"Sem match: {motivo}")
                    return {"sucesso": False, "pulada": True, "motivo_falha": "sem_match",
                            "mensagem": f"Vaga ignorada (sem match): {motivo}"}

        # Clica no botão Indeed Apply (pode estar em iframe do próprio botão).
        await notify_browser_step("selenium_indeed", "apply", "Procurando Candidatura Simplificada...")
        btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_APPLY)
        if not clicou:
            # Botão pode estar dentro de um iframe do Indeed Apply.
            def _entrar_iframe_botao():
                for fr in driver.find_elements(By.CSS_SELECTOR, "iframe"):
                    try:
                        src = (fr.get_attribute("src") or "").lower()
                        if "indeedapply" in src:
                            driver.switch_to.frame(fr)
                            return True
                    except Exception:
                        continue
                return False
            try:
                if await _run_in_thread(_entrar_iframe_botao):
                    btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_APPLY)
                    await _run_in_thread(lambda: driver.switch_to.default_content())
            except Exception:
                pass

        if not clicou:
            await notify_browser_step("selenium_indeed", "sem_apply", "Sem Candidatura Simplificada")
            b64 = await screenshot_base64()
            return {"sucesso": False, "motivo_falha": "sem_indeed_apply",
                    "mensagem": f"Vaga sem Candidatura Simplificada (Indeed Apply). Aplique no site: {vaga_url}",
                    "screenshot": b64[:100] if b64 else ""}

        print(f"[INDEED] Clicou aplicar: '{btn_text[:40]}'")

        # Entra no contexto do SmartApply (cross-domain).
        modo = await _entrar_contexto_smartapply(driver)
        if not modo:
            # Não achou o SmartApply → talvez ainda carregando ou redirecionamento
            # externo. Dá uma chance manual antes de desistir.
            await asyncio.sleep(3)
            modo = await _entrar_contexto_smartapply(driver)
        if not modo:
            await notify_browser_step("selenium_indeed", "manual", "SmartApply não abriu — controle manual")
            if not await _aguardar_resolucao_manual(driver, "abertura do SmartApply"):
                b64 = await screenshot_base64()
                return {"sucesso": False, "motivo_falha": "smartapply_nao_abriu",
                        "mensagem": f"SmartApply não abriu. Aplique manualmente: {vaga_url}",
                        "screenshot": b64[:100] if b64 else ""}
            modo = await _entrar_contexto_smartapply(driver) or "page"

        await notify_browser_step("selenium_indeed", "preenchendo", "Preenchendo SmartApply")
        vaga_titulo = ttl or ""
        resultado = await _processar_formulario_smartapply(
            driver, perfil, curriculo_path, vaga_url, resumo_curriculo,
            idioma=idioma_vaga, vaga_titulo=vaga_titulo,
        )

        # Sai do contexto e volta para a listagem.
        try:
            await _sair_contexto_smartapply(driver, modo, janela_original)
        except Exception:
            pass
        return resultado

    except Exception as e:
        logger.error(f"aplicar_indeed_selenium erro: {e}")
        await notify_browser_step("selenium_indeed", "erro", str(e))
        print(f"[INDEED] ERRO: {e}")
        try:
            drv = await get_driver()
            if drv:
                await _run_in_thread(lambda: drv.switch_to.default_content())
        except Exception:
            pass
        return {"sucesso": False, "mensagem": str(e)}


# ── Loop: aplicar nas vagas visíveis (mínimo, sem a máquina anti-parking) ──────

async def aplicar_vagas_visiveis_na_pagina(perfil: dict, max_vagas: int = 5, user_id: str = "admin") -> dict:
    """
    Extrai as vagas elegíveis (Indeed Apply) da busca atual e aplica em cada uma,
    registrando no Neo4j. Loop mínimo — sem a máquina de paginação/anti-parking do
    LinkedIn (que resolvia bugs específicos daquela plataforma)."""
    set_platform("indeed")
    try:
        from graph.neo4j_client import get_neo4j
        neo4j = get_neo4j()
    except Exception:
        neo4j = None

    resumo_curriculo = perfil.get("resumo_curriculo", "") or await _get_resumo_curriculo(user_id)
    if resumo_curriculo and not perfil.get("resumo_curriculo"):
        perfil = {**perfil, "resumo_curriculo": resumo_curriculo}

    extra = await extrair_vagas_da_busca(perfil, max_vagas=max(max_vagas * 3, 15))
    if not extra.get("sucesso"):
        return {"sucesso": False, "aplicacoes": [], "mensagem": extra.get("mensagem", "Falha ao extrair vagas")}

    elegiveis = [v for v in extra.get("vagas", []) if v.get("easy_apply")]
    if not elegiveis:
        return {"sucesso": True, "aplicacoes": [],
                "mensagem": "Nenhuma vaga com Candidatura Simplificada na página."}

    resultados = {"sucesso": True, "aplicacoes": [], "falhas": 0}
    aplicadas = 0
    teto = _cont.get_teto()

    for vaga in elegiveis:
        if aplicadas >= max_vagas:
            break
        if teto > 0 and _cont.teto_atingido(user_id):
            resultados["mensagem"] = f"Teto de candidaturas atingido ({teto})."
            break

        control = await get_intervention_state()
        if control.get("current_action") == "parar":
            resultados["mensagem"] = "Interrompido pelo usuário."
            break

        # Pula vagas já aplicadas.
        if neo4j:
            try:
                if neo4j.ja_se_candidatou(user_id, vaga.get("id", "")):
                    continue
            except Exception:
                pass

        await notify_browser_step("selenium_indeed", "aplicando",
                                  f"Aplicando em {vaga.get('empresa', '?')}")
        try:
            res = await aplicar(vaga.get("url", ""), perfil, curriculo_path=perfil.get("curriculo_path", ""), user_id=user_id)
        except Exception as e:
            logger.error("aplicar_vagas_visiveis: erro em %s: %s", vaga.get("url"), e)
            res = {"sucesso": False, "mensagem": str(e)}

        if res.get("pulada"):
            continue

        status = "candidatado" if res.get("sucesso") else "tentativa_falhou"
        if res.get("sucesso"):
            aplicadas += 1
            _cont.incr_count(user_id)
        else:
            resultados["falhas"] += 1
        resultados["aplicacoes"].append({"vaga": vaga.get("titulo", ""), "status": status})

        if neo4j:
            try:
                neo4j.registrar_candidatura(user_id=user_id, vaga_id=vaga.get("id", vaga.get("url", "")),
                                            plataforma="indeed", status=status)
            except Exception:
                pass
        await asyncio.sleep(random.uniform(1.5, 3.0))

    resultados.setdefault("mensagem", f"{aplicadas} candidatura(s) enviada(s) no Indeed.")
    return resultados
