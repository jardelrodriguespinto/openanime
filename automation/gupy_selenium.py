"""
Automação de candidatura no Gupy (portal.gupy.io) via Selenium/Firefox.

Criada nos moldes do GeekHunter (ver automation/geekhunter_selenium.py): login por
e-mail+senha do .env, busca por palavra-chave do dashboard, e aplica card a card —
abrir a vaga leva ao detalhe (subdomínio da empresa, ex.: firedev.gupy.io/job/…),
onde roda o WIZARD de candidatura do Gupy (passos FIXOS, não é o form Chakra genérico
do GeekHunter):

  Candidatar-se (a[data-testid=job-cta-link])
    → Continuar
    → "Alguém indicou você?" radio → Não (radioGroupIsIndicatedNo)
    → "Onde você encontrou essa vaga?" (Opcional) combobox → tenta e segue
    → Responder agora (button[aria-label=Responder agora])
    → perguntas (MUI: clica o LABEL, o input é oculto) [se houver]
    → Salvar e continuar
    → modal → Finalizar candidatura (#dialog-give-up-personalization-step)
  → fecha a aba, volta pra busca, próxima página.

Regras herdadas do resto do projeto:
- Todo seletor incerto degrada para intervenção manual (_aguardar_resolucao_manual),
  NUNCA falha silenciosa.
- Sucesso é CONSERVADOR (sinal forte: clicar o #dialog-give-up-personalization-step
  e/ou frase de confirmação) — evita o bug de falso-sucesso que envenena o dedup.
"""

import asyncio
import logging
import os
import random
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()

from automation.selenium_browser import (
    nova_pagina, navegar, digitar_robusto, screenshot_base64,
    get_driver, get_title, _run_in_thread,
)
from automation.browser import (
    notify_browser_step, get_intervention_state,
)
from automation.run_context import set_platform

# Reuso dos helpers GENÉRICOS do Indeed (agnósticos de plataforma): clique por texto,
# detecção de bloqueio, espera de intervenção manual, resposta de perguntas por IA.
from automation.indeed_selenium import (
    _clicar_botao_smartapply,
    _pagina_bloqueada,
    _smartapply_bloqueado,
    _aguardar_resolucao_manual,
    _get_resumo_curriculo,
)
from automation.form_filler import responder_pergunta
from automation.contador_aplicacoes import GUPY as _cont

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import StaleElementReferenceException

logger = logging.getLogger(__name__)


# ── Config via .env ──────────────────────────────────────────────────────────

_PORTAL = "https://portal.gupy.io"
# Login do Gupy: /candidates/signin (aparece no portal E no subdomínio da empresa, ex.:
# fcamara.gupy.io/candidates/signin — ao clicar Candidatar-se a Gupy pode mandar pra lá).
_LOGIN = f"{_PORTAL}/candidates/signin"


def _get_email() -> str:
    return os.getenv("GUPY_EMAIL", "")


def _get_password() -> str:
    return os.getenv("GUPY_PASSWORD", "")


def _get_query_padrao() -> str:
    return os.getenv("GUPY_QUERY", "desenvolvedor")


def _build_search_url(query: str = "", page: int = 1) -> str:
    """URL da busca do portal Gupy com a palavra-chave (do dashboard). Formato que o
    usuário forneceu: /job-search/term=<q>. Paginação via ?page=N (best-effort — o
    terminador REAL é acabarem os cards; confirmar o mecanismo no dump da busca)."""
    q = (query or "").strip() or _get_query_padrao()
    base = f"{_PORTAL}/job-search/term={quote_plus(q)}"
    return base if page <= 1 else f"{base}?page={page}"


# Botões de avançar do wizard (styled-components: classes sc-* são hasheadas/instáveis
# → SEMPRE por TEXTO/atributo, nunca por classe).
_BTN_CONTINUAR = [
    'button:has-text("Continuar")',
    'button:has-text("Próximo")',
    'button:has-text("Próxima")',
    'button:has-text("Avançar")',
]
_BTN_RESPONDER_AGORA = [
    'button[aria-label="Responder agora"]',
    'button:has-text("Responder agora")',
]
_BTN_SALVAR_CONTINUAR = [
    'button[name="saveAndContinueButton"]',
    'button:has-text("Salvar e continuar")',
    'button:has-text("Salvar")',
]
# Botão que FINALIZA a candidatura no modal "pular personalização". FORTE = ID estável
# + texto específico "Finalizar candidatura" → clicar = ENVIO (conta sucesso). O bare
# "Finalizar" fica no FRACO (só avança; sucesso só se vier frase de confirmação) — evita
# o falso-sucesso que envenena o dedup (lição do GeekHunter).
_BTN_FINALIZAR = [
    '#dialog-give-up-personalization-step',
    'button#dialog-give-up-personalization-step',
    'button:has-text("Finalizar candidatura")',
]
_BTN_FINALIZAR_FRACO = [
    'button:has-text("Finalizar")',
    'button:has-text("Concluir")',
]
# Botão/atalho que abre o formulário de candidatura no detalhe da vaga.
_BTN_CANDIDATAR = [
    'a[data-testid="job-cta-link"]',
    'a[href*="/apply"]',
    'button:has-text("Candidatar-se")',
    'a:has-text("Candidatar-se")',
    'button:has-text("Candidatar")',
]

# Frases ESPECÍFICAS de candidatura enviada (conservador — nada genérico que possa
# aparecer como marketing/estatística e dar falso sucesso, que envenena o dedup).
_FRASES_SUCESSO_GUPY = (
    "candidatura realizada",
    "candidatura foi realizada",
    "inscrição realizada",
    "você se candidatou",
    "sua candidatura foi enviada",
    "recebemos sua candidatura",
    "application submitted",
    "you have applied",
)


# ── Login ────────────────────────────────────────────────────────────────────

def _esta_logado(url: str, html: str = "") -> bool:
    """Heurística de login no Gupy pela página ATUAL (busca do portal).

    NUNCA decidir pela URL da busca: 'job-search' está SEMPRE na URL → o fallback
    antigo retornava True deslogado, o login do portal era PULADO ('Já está logado')
    e todo Candidatar-se batia no signin da empresa. Marcador REAL (confirmado no dump
    do DOM): o menu do usuário diz 'Usuário deslogado'/'Logged out user' quando não há
    sessão — se presente, NÃO está logado, ponto."""
    u = (url or "").lower()
    if "/login" in u or "/signin" in u or "/sign_in" in u or "/auth" in u:
        return False
    h = (html or "").lower()
    # Marcador de DESLOGADO tem prioridade (visto no DOM real da busca, PT e EN).
    for marca in ("usuário deslogado", "usuario deslogado", "logged out user", "logged-out"):
        if marca in h:
            return False
    for marca in ("sair", "logout", "minha conta", "meu perfil", "meus dados",
                  "minhas candidaturas", "sign out"):
        if marca in h:
            return True
    # Sem nenhum marcador: portal sempre renderiza o menu do usuário (deslogado OU
    # logado); se não achamos 'deslogado', considera logado (fail-open, igual projeto).
    return True


async def _pagina_de_login_gupy(driver) -> bool:
    """A tela ATUAL é de login? (URL /signin|/login OU tem os campos username+password
    visíveis). Login do Gupy pode surgir no portal E no subdomínio da empresa."""
    def _tem_form():
        try:
            pw = driver.find_elements(By.CSS_SELECTOR,
                "input#password, input[type='password'], input[autocomplete='current-password']")
            us = driver.find_elements(By.CSS_SELECTOR,
                "input#username, input[name='username'], input[autocomplete='username'], "
                "input[type='email'], input[name='email']")
            return any(e.is_displayed() for e in pw) and any(e.is_displayed() for e in us)
        except Exception:
            return False
    try:
        u = (await _run_in_thread(lambda: driver.current_url) or "").lower()
    except Exception:
        u = ""
    if "/signin" in u or "/login" in u or "/sign_in" in u:
        return True
    try:
        return await _run_in_thread(_tem_form)
    except Exception:
        return False


async def _tratar_login_gupy(driver) -> bool:
    """Se a tela ATUAL é de login do Gupy, preenche username+password do .env e submete.
    O login pode surgir A QUALQUER MOMENTO (ao clicar Candidatar-se numa vaga a Gupy
    redireciona pro signin da EMPRESA, ex.: fcamara.gupy.io/candidates/signin). Campos são
    MUI: input#username[name=username] (type=text, NÃO email!) e input#password. Retorna
    True se detectou e tratou um login (mesmo caindo em manual); False se não era login."""
    if not await _pagina_de_login_gupy(driver):
        return False

    try:
        u = await _run_in_thread(lambda: driver.current_url)
    except Exception:
        u = ""
    email, senha = _get_email(), _get_password()
    print(f"[GUPY] Tela de login detectada ({(u or '')[:70]})")
    if not (email and senha):
        await notify_browser_step("gupy_login", "manual",
                                  "Defina GUPY_EMAIL/PASSWORD no .env ou logue à mão")
        await _aguardar_resolucao_manual(driver, "login manual Gupy")
        return True

    await notify_browser_step("gupy_login", "login", "Preenchendo login do Gupy")

    def _tem_campo_senha():
        try:
            return any(e.is_displayed() for e in driver.find_elements(
                By.CSS_SELECTOR,
                "input#password, input[type='password'], input[autocomplete='current-password']"))
        except Exception:
            return False

    # PORTAL signin (portal.gupy.io/candidates/signin): NÃO tem campos diretos — tem um
    # botão "Entrar" (#btn-link-signin) que abre o form de login em OUTRA ABA. Clica,
    # troca pra aba nova e loga lá. (No subdomínio da empresa os campos já vêm diretos →
    # este bloco é pulado.)
    janela_orig, aba_login = None, None
    if not await _run_in_thread(_tem_campo_senha):
        try:
            janela_orig = await _run_in_thread(lambda: driver.current_window_handle)
            antes = await _run_in_thread(lambda: list(driver.window_handles))
        except Exception:
            antes = []
        _, clic = await _clicar_botao_smartapply(driver, [
            "#btn-link-signin", "button#btn-link-signin", "a#btn-link-signin",
            "button[aria-label='Entrar']", "button[aria-label='Login']",
            "a[href*='signin']", "a[href*='login']",
        ])
        if clic:
            print("[GUPY] Portal signin: cliquei 'Entrar' (#btn-link-signin) — abre login")
            await asyncio.sleep(3.5)
            try:
                depois = await _run_in_thread(lambda: list(driver.window_handles))
                novas = [h for h in depois if h not in antes]
                if novas:
                    aba_login = novas[0]
                    await _run_in_thread(lambda h=aba_login: driver.switch_to.window(h))
                    await asyncio.sleep(2)
                    print("[GUPY] Trocado pra aba de login")
            except Exception:
                pass

    preencheu_user = await digitar_robusto(
        "input#username, input[name='username'], input[autocomplete='username'], "
        "input[type='email'], input[name='email'], input[name*='email' i]",
        email,
    )
    preencheu_senha = await digitar_robusto(
        "input#password, input[type='password'], input[name='password'], "
        "input[autocomplete='current-password']",
        senha,
    )
    if not (preencheu_user and preencheu_senha):
        print("[GUPY] Campos de login não preenchidos — intervenção manual")
        await _aguardar_resolucao_manual(driver, "login manual Gupy")
        return True

    await asyncio.sleep(0.6)
    # ATENÇÃO: a tela de senha tem DOIS botões — "Acessar conta" (login com SENHA) e
    # "Entrar sem senha" (passwordless). NUNCA casar "Entrar"/"Login"/submit genérico:
    # "Entrar" pega o passwordless e o login com senha nunca acontece. Só o específico:
    _, clicou = await _clicar_botao_smartapply(driver, [
        'button:has-text("Acessar conta")',
        'button:has-text("Access account")',
        'button:has-text("Acessar")',
        'button:has-text("Access")',
    ])
    if not clicou:
        # Fallback: Enter no campo de senha submete o form de SENHA (não o passwordless).
        def _enter():
            el = driver.find_element(By.CSS_SELECTOR, "input#password, input[type='password']")
            el.send_keys(Keys.RETURN)
            return True
        try:
            await _run_in_thread(_enter)
        except Exception:
            pass
    await asyncio.sleep(4)

    # Se logamos numa ABA de login separada, fecha e volta pra original (o cookie de
    # sessão vale pra todas as abas), e refresca a busca (o signin não atualiza sozinho).
    if aba_login:
        try:
            handles = await _run_in_thread(lambda: list(driver.window_handles))
            if aba_login in handles and len(handles) > 1:
                try:
                    cur_win = await _run_in_thread(lambda: driver.current_window_handle)
                except Exception:
                    cur_win = None
                if cur_win == aba_login:
                    await _run_in_thread(lambda: driver.close())
            handles = await _run_in_thread(lambda: list(driver.window_handles))
            alvo = janela_orig if (janela_orig and janela_orig in handles) else (handles[0] if handles else None)
            if alvo:
                await _run_in_thread(lambda h=alvo: driver.switch_to.window(h))
                await asyncio.sleep(1)
            await navegar(_build_search_url())
            await asyncio.sleep(2)
        except Exception:
            pass
        return True  # launcher: login é best-effort; a busca segue (deslogada funciona)

    # Fluxo direto (subdomínio da empresa): verificação/CAPTCHA ou ainda no login → manual.
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, html = "", ""
    if _pagina_bloqueada(cur, await get_title(), html) or await _pagina_de_login_gupy(driver):
        await notify_browser_step("gupy_login", "manual",
                                  "🔒 Verificação/senha — conclua no browser e clique 🔄 Retomar Auto")
        await _aguardar_resolucao_manual(driver, "verificação de login Gupy")
    return True


async def _garantir_login() -> bool:
    """Garante sessão logada no Gupy. Detect-ou-manual: navega pra busca; se cair num
    login (portal OU subdomínio da empresa), preenche pelo .env (_tratar_login_gupy);
    CAPTCHA → manual. Retorna True se logado."""
    driver = await get_driver()
    if not driver:
        await nova_pagina(_build_search_url(), reutilizar=False)
        await asyncio.sleep(2)
        driver = await get_driver()
    if not driver:
        print("[GUPY] ERRO: driver é None")
        return False

    # Perfil persistente pode já ter sessão → busca carrega sem bounce pro login.
    try:
        await navegar(_build_search_url())
        await asyncio.sleep(2.5)
        cur = await _run_in_thread(lambda: driver.current_url)
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, html = "", ""
    if _pagina_bloqueada(cur, await get_title(), html):
        if not await _aguardar_resolucao_manual(driver, "acesso ao Gupy"):
            return False
        try:
            cur = await _run_in_thread(lambda: driver.current_url)
            html = await _run_in_thread(lambda: driver.page_source)
        except Exception:
            pass
    if _esta_logado(cur, html):
        print("[GUPY] Já está logado")
        return True

    # Não logado → trata o login que apareceu; se não apareceu, navega pro signin e trata.
    print("[GUPY] Não logado — tratando login")
    if not await _tratar_login_gupy(driver):
        try:
            await navegar(_LOGIN)
            await asyncio.sleep(3)
        except Exception:
            pass
        await _tratar_login_gupy(driver)

    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, html = "", ""
    logado = _esta_logado(cur, html)
    if logado:
        await notify_browser_step("gupy_login", "sucesso", "Login concluído")
    return logado


# ── Busca / cards ─────────────────────────────────────────────────────────────

# Seletores candidatos do link/card de uma vaga no portal (Gupy usa data-testid).
_CARD_SELECTORS = (
    "[data-testid='job-list__listitem'] a",
    "a[data-testid='job-list__listitem']",
    "a[href*='/job/']",
    "a[href*='gupy.io/job/']",
    "[data-testid*='job'] a[href]",
)


def _achar_cards(driver):
    """Elementos clicáveis de card de vaga (dedupe por href). Best-effort — Gupy é SPA."""
    vistos = set()
    cards = []
    for sel in _CARD_SELECTORS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if not el.is_displayed():
                        continue
                    href = el.get_attribute("href") or ""
                    chave = href or el.id
                    if chave in vistos:
                        continue
                    vistos.add(chave)
                    cards.append(el)
                except Exception:
                    continue
        except Exception:
            continue
        if cards:
            break  # primeiro seletor que rende cards vence
    return cards


async def _abrir_busca(driver, query: str = "", page: int = 1) -> None:
    """Navega para a busca do portal Gupy com a palavra-chave (do dashboard) na página."""
    url = _build_search_url(query, page)
    print(f"[GUPY] Abrindo busca (pág. {page}) → {url}")
    await notify_browser_step("gupy_busca", "navegando", f"Buscando: {query or _get_query_padrao()} (pág. {page})")
    await navegar(url)
    await asyncio.sleep(3)
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, html = "", ""
    if _pagina_bloqueada(cur, await get_title(), html):
        await _aguardar_resolucao_manual(driver, "busca no Gupy")
    # Scroll pra carregar mais cards (lazy-load).
    def _scroll():
        import time as _t
        last = driver.execute_script("return document.body.scrollHeight")
        for _ in range(4):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            _t.sleep(1.2)
            new = driver.execute_script("return document.body.scrollHeight")
            if new == last:
                break
            last = new
        driver.execute_script("window.scrollTo(0, 0);")
    try:
        await _run_in_thread(_scroll)
    except Exception:
        pass
    # Instrumentação da PÁGINA DE BUSCA (a parte mais incerta: URL de busca + seletores
    # de card). Sem isto, n_cards==0 retornaria "sem vagas" SEM diagnóstico do porquê.
    try:
        n = await _run_in_thread(lambda: len(_achar_cards(driver)))
        cur2 = await _run_in_thread(lambda: driver.current_url)
        print(f"[GUPY] busca pág.{page}: {n} card(s) | URL: {cur2}")
    except Exception:
        pass
    await _dump_gupy_debug(driver, f"busca-pag{page}")


async def extrair_vagas_da_busca(perfil: dict, max_vagas: int = 100, query: str = "") -> dict:
    """Extrai (título, url) dos cards da busca — usado pelo dashboard pra listar vagas."""
    set_platform("gupy")
    # Best-effort (a busca funciona deslogada — ver aplicar_vagas_visiveis_na_pagina).
    if not await _garantir_login():
        print("[GUPY] Login do portal não confirmado — extraindo mesmo assim")
    driver = await get_driver()
    await _abrir_busca(driver, query)

    def _coletar():
        vagas = []
        for el in _achar_cards(driver)[:max_vagas]:
            try:
                href = el.get_attribute("href") or ""
                titulo = (el.text or "").strip().split("\n")[0]
                vagas.append({"id": href, "titulo": titulo, "empresa": "",
                              "url": href, "fonte": "Gupy"})
            except Exception:
                continue
        return vagas

    try:
        vagas = await _run_in_thread(_coletar)
    except Exception as e:
        logger.warning("gupy extrair vagas erro: %s", e)
        vagas = []
    return {"sucesso": True, "vagas": vagas, "mensagem": f"{len(vagas)} vaga(s) no Gupy."}


# ── Wizard de candidatura ─────────────────────────────────────────────────────

def _tem_elemento(driver, css: str) -> bool:
    try:
        return any(e.is_displayed() for e in driver.find_elements(By.CSS_SELECTOR, css))
    except Exception:
        return False


# Vagas 404/expiradas: o portal.gupy.io LISTA vagas já removidas → clicar/abrir o
# subdomínio da empresa dá "not found" (HTTP 404; a página tem <title>404</title> e o
# corpo diz "não encontrada"). Sem detectar isso, a automação tentava aplicar numa
# página morta e travava/pulava sem motivo claro.
_MARCAS_INDISPONIVEL_GUPY = (
    "não encontrada", "nao encontrada", "não encontrado", "nao encontrado",
    "not found", "página não encontrada", "pagina nao encontrada",
    "não está mais disponível", "nao esta mais disponivel",
    "não está mais recebendo", "nao esta mais recebendo",
    "vaga encerrada", "vaga expirada", "esta vaga foi encerrada",
)


async def _vaga_indisponivel(driver) -> bool:
    """True se a vaga aberta é 404/expirada/encerrada. Detecta pelo TÍTULO (mais confiável
    — vaga viva tem o CARGO no <title>; a morta tem '404'/'não encontrada') e, como reforço,
    pelo começo do corpo."""
    def _check():
        try:
            titulo = (driver.title or "").strip().lower()
        except Exception:
            titulo = ""
        if titulo == "404" or titulo.startswith("404") or any(
                m in titulo for m in _MARCAS_INDISPONIVEL_GUPY):
            return True
        try:
            corpo = (driver.find_element(By.TAG_NAME, "body").text or "").lower()[:600]
        except Exception:
            corpo = ""
        return any(m in corpo for m in _MARCAS_INDISPONIVEL_GUPY)
    try:
        return await _run_in_thread(_check)
    except Exception:
        return False


async def _extrair_descricao_detalhe(driver) -> str:
    """Texto do detalhe da vaga (pro filtro modalidade/região). Igual GeekHunter: body."""
    def _txt():
        try:
            return (driver.find_element(By.TAG_NAME, "body").text or "")[:3000]
        except Exception:
            return ""
    try:
        return await _run_in_thread(_txt)
    except Exception:
        return ""


async def _responder_radios_gupy(driver, perfil: dict, resumo_curriculo: str,
                                 idioma: str, vaga_titulo: str) -> None:
    """Responde os radio-groups (styled-components) do step de dados. A resposta DEPENDE
    DA PERGUNTA (legend do <fieldset>): 'Alguém indicou você?' → 'Não' (honesto p/
    referral); demais grupos AINDA sem seleção → IA escolhe pela legend. Radio nativo
    dentro de <label> — clica via JS pelo data-testid/value."""
    def _coletar():
        grupos = {}
        for el in driver.find_elements(By.CSS_SELECTOR, "input[type='radio']"):
            try:
                if not el.is_displayed():
                    continue
                name = el.get_attribute("name") or ""
                if not name:
                    continue
                g = grupos.setdefault(name, {"legend": "", "opts": [], "sel": False})
                txt = ""
                try:
                    lbl = el.find_element(By.XPATH, "./ancestor::label[1]")
                    txt = (lbl.text or "").strip()
                except Exception:
                    pass
                g["opts"].append({"texto": txt or (el.get_attribute("value") or ""),
                                  "value": el.get_attribute("value") or "",
                                  "testid": el.get_attribute("data-testid") or ""})
                if el.is_selected():
                    g["sel"] = True
                if not g["legend"]:
                    try:
                        fs = el.find_element(By.XPATH, "./ancestor::fieldset[1]")
                        g["legend"] = (fs.find_element(By.TAG_NAME, "legend").text or "").strip()
                    except Exception:
                        pass
            except Exception:
                continue
        return grupos

    try:
        grupos = await _run_in_thread(_coletar)
    except Exception as e:
        logger.warning("gupy _coletar_radios erro: %s", e)
        return

    for name, g in grupos.items():
        # Referral ('Alguém indicou você?') → sempre 'Não' (honesto; já vem checked, garante).
        if "indicated" in name.lower() or "indicad" in (g["legend"] or "").lower():
            def _marca_nao(nm=name):
                for sel in ("input[data-testid='radioGroupIsIndicatedNo']",
                            f"input[name='{nm}'][value='no']"):
                    for el in driver.find_elements(By.CSS_SELECTOR, sel):
                        try:
                            if not el.is_selected():
                                driver.execute_script("arguments[0].click();", el)
                            return True
                        except Exception:
                            continue
                return False
            try:
                if await _run_in_thread(_marca_nao):
                    print("[GUPY] Radio 'indicou você?' = Não")
                    await asyncio.sleep(0.3)
            except Exception:
                pass
            continue

        # Outros grupos: só responde se NENHUM selecionado (não mexe em default alheio).
        if g["sel"] or not g["opts"]:
            continue
        legend = g["legend"] or name
        opcoes = [o["texto"] for o in g["opts"] if o["texto"]]
        if not opcoes:
            continue
        pergunta = "SELECT:" + legend + ":" + ";".join(opcoes[:12])
        try:
            escolha = responder_pergunta(pergunta, perfil, vaga_titulo=vaga_titulo, vaga_empresa="",
                                         resumo_curriculo=resumo_curriculo, idioma=idioma)
        except Exception as e:
            logger.warning("responder_pergunta (gupy radio) erro: %s", e)
            escolha = ""
        escolha = (escolha or "").strip().lower()
        alvo = next((o for o in g["opts"] if o["texto"].lower() == escolha), None) \
            or next((o for o in g["opts"] if escolha and escolha in o["texto"].lower()), None) \
            or g["opts"][0]

        def _clica(tid=alvo["testid"], val=alvo["value"], nm=name):
            sels = []
            if tid:
                sels.append(f"input[data-testid='{tid}']")
            if val:
                sels.append(f"input[name='{nm}'][value='{val}']")
            for s in sels:
                for el in driver.find_elements(By.CSS_SELECTOR, s):
                    try:
                        driver.execute_script("arguments[0].click();", el)
                        return True
                    except Exception:
                        continue
            return False
        try:
            if await _run_in_thread(_clica):
                print(f"[GUPY] Radio '{legend[:35]}' = {alvo['texto'][:20]}")
                await asyncio.sleep(0.3)
        except Exception:
            pass


async def _preencher_how_did_you_hear(driver) -> None:
    """Combobox 'Onde você encontrou essa vaga? (Opcional)'. Try-then-skip: abre, pega a
    1ª opção do listbox; se não der, segue (é opcional, NUNCA bloqueia)."""
    def _fill():
        campos = driver.find_elements(By.CSS_SELECTOR,
                                      "input[name='howDidYouHearAboutUs'], input[role='combobox']")
        for inp in campos:
            try:
                if not inp.is_displayed() or (inp.get_attribute("value") or "").strip():
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", inp)
                inp.click()
                import time as _t
                _t.sleep(0.8)
                # Opções do listbox (react-aria/MUI).
                for osel in ("li[role='option']", "[role='option']"):
                    opts = [o for o in driver.find_elements(By.CSS_SELECTOR, osel) if o.is_displayed()]
                    if opts:
                        opts[0].click()
                        return True
                # Sem listbox: fecha e segue (campo é opcional).
                inp.send_keys(Keys.ESCAPE)
                return False
            except Exception:
                continue
        return False
    try:
        if await _run_in_thread(_fill):
            print("[GUPY] 'Onde encontrou a vaga' preenchido (1ª opção)")
            await asyncio.sleep(0.4)
    except Exception as e:
        logger.warning("gupy _preencher_how_did_you_hear erro: %s", e)


def _fill_react(driver, el, valor: str) -> None:
    """Preenche um input/textarea React-safe: send_keys e, se não assentar (React
    controlado ignora), força pelo setter NATIVO + dispatch input/change/blur."""
    import time as _t
    try:
        el.click()
    except Exception:
        pass
    try:
        el.clear()
    except Exception:
        pass
    try:
        el.send_keys(valor)
    except Exception:
        pass
    try:
        atual = (el.get_attribute("value") or "").strip()
        if not atual and str(valor).strip():
            driver.execute_script(
                "const el=arguments[0],val=arguments[1];"
                "const p=el.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype;"
                "const s=Object.getOwnPropertyDescriptor(p,'value').set;s.call(el,val);"
                "el.dispatchEvent(new Event('input',{bubbles:true}));"
                "el.dispatchEvent(new Event('change',{bubbles:true}));",
                el, str(valor),
            )
    except Exception:
        pass
    try:
        driver.execute_script("arguments[0].dispatchEvent(new Event('blur',{bubbles:true}));", el)
    except Exception:
        pass
    _t.sleep(0.4)


def _coletar_perguntas_gupy(driver) -> list:
    """Perguntas do step de perguntas (MUI). Agrupa por <h3> (enunciado). Varre h3 +
    labels(MUI) + campos de texto em ORDEM DE DOCUMENTO (uma query) e atribui ao h3 mais
    recente. Evita XPath com current() (XSLT, não existe no XPath 1.0 → lançava exceção).

    CRÍTICO: as opções são CHECKBOX MUI e há textos repetidos entre perguntas (vários
    'Sim'/'Não') → captura o `name` ÚNICO do input de cada opção (ex.: checkbox-<idQ>-<i>)
    pra clicar ESCOPADO, e PULA perguntas já respondidas (opção marcada / texto preenchido)
    — senão re-clicar alterna (toggle) o checkbox e trava o 'Salvar e continuar'."""
    perguntas = []
    try:
        els = driver.find_elements(
            By.XPATH,
            "//*[self::h3 or (self::label and contains(@class,'MuiFormControlLabel-root')) "
            "or self::textarea or (self::input and (@type='text' or not(@type)))]",
        )
    except Exception:
        els = []

    atual = None  # {pergunta, tipo, opcoes:[{texto,name}], campo:{name,id}, respondida}

    def _flush(q):
        if not q or q.get("respondida"):
            return
        if q["opcoes"] or q.get("campo"):
            perguntas.append(q)

    for el in els:
        try:
            if not el.is_displayed():
                continue
            tag = (el.tag_name or "").lower()
            if tag == "h3":
                _flush(atual)
                texto = (el.text or "").strip().lstrip("0123456789. ").rstrip(" *").strip()
                atual = ({"pergunta": texto, "tipo": None, "opcoes": [], "campo": None,
                          "respondida": False} if texto else None)
            elif atual is not None:
                if tag == "label":
                    t = (el.text or "").strip()
                    if not t:
                        continue
                    atual["tipo"] = "mui"
                    nome, checked = "", False
                    try:
                        inp = el.find_element(By.CSS_SELECTOR, "input")
                        nome = inp.get_attribute("name") or ""
                        checked = inp.is_selected()
                    except Exception:
                        pass
                    if checked:
                        atual["respondida"] = True
                    if not any(o["texto"] == t for o in atual["opcoes"]):
                        atual["opcoes"].append({"texto": t, "name": nome})
                elif atual["tipo"] is None:  # textarea / input de texto livre
                    if (el.get_attribute("value") or "").strip():
                        atual["respondida"] = True
                    else:
                        atual["tipo"] = "texto"
                        atual["campo"] = {"name": el.get_attribute("name") or "",
                                          "id": el.get_attribute("id") or ""}
        except Exception:
            continue
    _flush(atual)
    return [q for q in perguntas if q["tipo"]]


async def _responder_perguntas_gupy(driver, perfil: dict, resumo_curriculo: str,
                                    idioma: str, vaga_titulo: str) -> list:
    """Responde as perguntas AINDA não respondidas (MUI + texto). MUI: clica a opção
    ESCOPADA pelo `name` do input (o input é oculto → clica o label ancestral, com
    fallback JS). Texto: fill React-safe no campo certo (por name/id). IA via SELECT:."""
    feitas = []
    try:
        perguntas = await _run_in_thread(lambda: _coletar_perguntas_gupy(driver))
    except Exception as e:
        logger.warning("gupy _coletar_perguntas erro: %s", e)
        return feitas

    for q in perguntas:
        label = q["pergunta"]
        if q["tipo"] == "mui":
            opcoes_txt = [o["texto"] for o in q["opcoes"]]
            pergunta_fmt = "SELECT:" + label + ":" + ";".join(opcoes_txt[:15])
            try:
                escolha = responder_pergunta(pergunta_fmt, perfil, vaga_titulo=vaga_titulo,
                                             vaga_empresa="", resumo_curriculo=resumo_curriculo,
                                             idioma=idioma)
            except Exception as e:
                logger.warning("responder_pergunta (gupy mui) erro: %s", e)
                escolha = ""
            escolha = (escolha or "").strip()
            alvo = next((o for o in q["opcoes"] if o["texto"].lower() == escolha.lower()), None) \
                or next((o for o in q["opcoes"] if escolha and escolha.lower() in o["texto"].lower()), None) \
                or q["opcoes"][0]

            # Clica ESCOPADO pelo name do input (único por pergunta+opção). Se já marcado,
            # no-op. Input MUI é oculto → clica o <label> ancestral (fallback: JS no input).
            def _clicar_opcao(nome=alvo["name"], txt=alvo["texto"]):
                inputs = []
                if nome:
                    inputs = driver.find_elements(By.CSS_SELECTOR, f"input[name='{nome}']")
                for inp in inputs:
                    try:
                        if inp.is_selected():
                            return True
                        try:
                            lbl = inp.find_element(By.XPATH, "./ancestor::label[1]")
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", lbl)
                            try:
                                lbl.click()
                            except Exception:
                                driver.execute_script("arguments[0].click();", lbl)
                        except Exception:
                            driver.execute_script("arguments[0].click();", inp)
                        return True
                    except Exception:
                        continue
                return False

            clicado = False
            for _tp in range(3):
                try:
                    clicado = await _run_in_thread(_clicar_opcao)
                    if clicado:
                        break
                except StaleElementReferenceException:
                    await asyncio.sleep(0.5)
            # Estado REAL pós-clique: a opção escolhida ficou is_selected()? (detecta o bug
            # de toggle MUI — marca e desmarca — que trava o 'Salvar e continuar'.)
            def _mui_selecionado(nome=alvo["name"]):
                try:
                    for inp in driver.find_elements(By.CSS_SELECTOR, f"input[name='{nome}']"):
                        return inp.is_selected()
                except Exception:
                    pass
                return None
            try:
                post_sel = await _run_in_thread(_mui_selecionado)
            except Exception:
                post_sel = None
            _qlog_gupy(f"label='{label[:30]}' tipo=mui ans='{escolha[:20]}' "
                       f"clicado={clicado} post_selected={post_sel}")
            if clicado:
                print(f"[GUPY] Pergunta '{label[:35]}' = {alvo['texto'][:25]}")
                feitas.append(label)
        else:
            try:
                resp = responder_pergunta(label, perfil, vaga_titulo=vaga_titulo, vaga_empresa="",
                                          resumo_curriculo=resumo_curriculo, idioma=idioma)
            except Exception as e:
                logger.warning("responder_pergunta (gupy texto) erro: %s", e)
                from automation.form_filler import resposta_segura
                resp = resposta_segura(label, idioma)

            campo = q.get("campo") or {}

            def _fill_texto(campo=campo, resp=resp):
                el = None
                if campo.get("id"):
                    try:
                        el = driver.find_element(By.ID, campo["id"])
                    except Exception:
                        el = None
                if el is None and campo.get("name"):
                    try:
                        el = driver.find_element(By.CSS_SELECTOR, f"[name='{campo['name']}']")
                    except Exception:
                        el = None
                if el is None:
                    return False
                _fill_react(driver, el, str(resp))
                return True
            try:
                ok_fill = await _run_in_thread(_fill_texto)
            except Exception:
                ok_fill = False
            # Estado REAL pós-fill: o textarea ficou com valor? (discrimina 'IA devolveu
            # vazio' / 'campo não achado' / 'React não assentou' — o caso do Q5 vazio.)
            def _texto_valor(campo=campo):
                el = None
                if campo.get("id"):
                    try:
                        el = driver.find_element(By.ID, campo["id"])
                    except Exception:
                        el = None
                if el is None and campo.get("name"):
                    try:
                        el = driver.find_element(By.CSS_SELECTOR, f"[name='{campo['name']}']")
                    except Exception:
                        el = None
                try:
                    return len((el.get_attribute("value") or "").strip()) if el else -1
                except Exception:
                    return -1
            try:
                post_len = await _run_in_thread(_texto_valor)
            except Exception:
                post_len = -1
            _qlog_gupy(f"label='{label[:30]}' tipo=texto ans_len={len(str(resp))} "
                       f"achou_campo={ok_fill} post_val_len={post_len}")
            if ok_fill:
                print(f"[GUPY] Pergunta texto '{label[:35]}' respondida")
                feitas.append(label)
        await asyncio.sleep(random.uniform(0.5, 1.0))
    return feitas


async def _sucesso_gupy(driver) -> bool:
    """Sinal FORTE de candidatura enviada (conservador — evita falso-sucesso): frase
    específica de confirmação no page_source."""
    def _check():
        try:
            html = (driver.page_source or "").lower()
        except Exception:
            return False
        return any(f in html for f in _FRASES_SUCESSO_GUPY)
    try:
        return await _run_in_thread(_check)
    except Exception:
        return False


# ── Instrumentação (debug do 1º run supervisionado) ───────────────────────────

_GUPY_DEBUG = os.getenv("GUPY_DEBUG", "true").lower() == "true"
_GUPY_DEBUG_PATH = os.path.join(os.path.dirname(__file__), "_gupy_form_debug.txt")


def _qlog_gupy(line: str) -> None:
    """Log por-pergunta do estado REAL pós-preenchimento — discrimina os 3 modos de falha
    ('não tentou' / 'clicou mas não grudou' / 'preencheu ok mas validação ainda bloqueia').
    Grava com tag [QLOG] no dump (grep 'QLOG' pra ler)."""
    print(f"[GUPY][QLOG] {line}")
    if not _GUPY_DEBUG:
        return
    try:
        with open(_GUPY_DEBUG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[QLOG] {line}\n")
    except Exception:
        pass


async def _dump_gupy_debug(driver, tag: str = "") -> None:
    """Grava URL + botões visíveis + campos + HTML (append) pra diagnosticar o wizard no
    1º run. Gate por GUPY_DEBUG. Best-effort, nunca quebra o fluxo."""
    if not _GUPY_DEBUG:
        return

    def _dump():
        try:
            url = driver.current_url
            botoes = []
            for b in driver.find_elements(By.CSS_SELECTOR, "button, a[data-testid], [role='button']"):
                try:
                    if b.is_displayed():
                        t = (b.text or b.get_attribute("aria-label") or "").strip()
                        if t:
                            botoes.append(t[:50])
                except Exception:
                    continue
            campos = []
            for c in driver.find_elements(By.CSS_SELECTOR, "input, textarea, label.MuiFormControlLabel-root, h3"):
                try:
                    if c.is_displayed():
                        desc = (c.get_attribute("name") or c.get_attribute("data-testid")
                                or (c.text or "")[:40])
                        # VALOR do campo — mostra quais estão VAZIOS (o que 'não preenche').
                        val = ""
                        try:
                            tg = (c.tag_name or "").lower()
                            if tg in ("input", "textarea"):
                                val = (c.get_attribute("value") or "").strip()
                                if not val and tg == "input" and c.get_attribute("type") == "checkbox":
                                    val = "checked" if c.is_selected() else ""
                        except Exception:
                            pass
                        campos.append((f"{c.tag_name}:{desc}"[:60] + (f" =[{val[:25]}]" if val else " =[]")))
                except Exception:
                    continue
            html = driver.page_source[:20000]
            with open(_GUPY_DEBUG_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n\n===== {tag} =====\nURL: {url}\n"
                        f"BOTÕES: {botoes}\nCAMPOS: {campos}\n--- HTML(20k) ---\n{html}\n")
        except Exception:
            pass
    try:
        await _run_in_thread(_dump)
    except Exception:
        pass


async def _preencher_e_enviar_formulario(driver, perfil: dict, resumo_curriculo: str,
                                         idioma: str, vaga_titulo: str, vaga_url: str) -> dict:
    """Roda o wizard de candidatura do Gupy no detalhe da vaga. Passos fixos; cada miss
    degrada para manual. Sucesso só com sinal FORTE (Finalizar clicado / frase de
    confirmação) — nunca marca enviado no escuro."""
    # 1) Candidatar-se (abre o formulário de apply).
    _, clicou = await _clicar_botao_smartapply(driver, _BTN_CANDIDATAR)
    if not clicou:
        # Talvez já esteja no form (ex.: aplicar() direto na URL de apply).
        if not _tem_elemento(driver, "input, textarea, [role='radiogroup'], label.MuiFormControlLabel-root"):
            await notify_browser_step("gupy_apply", "manual", "Não achei 'Candidatar-se' — controle manual")
            if not await _aguardar_resolucao_manual(driver, "abrir candidatura Gupy"):
                return {"sucesso": False, "motivo_falha": "sem_candidatar",
                        "mensagem": f"Não abriu a candidatura. Candidate-se à mão: {vaga_url}"}
    await asyncio.sleep(2.5)

    await _dump_gupy_debug(driver, "apos-candidatar")

    perguntas_feitas = []
    # Formulários do Gupy podem ter VÁRIAS páginas de perguntas (cada "Salvar e continuar"
    # abre outra) + login no meio consome steps → margem folgada.
    max_steps = 20
    nao_avancou = 0

    for step in range(max_steps):
        control = await get_intervention_state()
        if control.get("current_action") == "parar":
            return {"sucesso": False, "motivo_falha": "parado", "mensagem": "Interrompido pelo usuário."}

        await asyncio.sleep(random.uniform(0.6, 1.2))
        await notify_browser_step(f"gupy_step_{step}", "preenchendo", "Preenchendo candidatura Gupy")

        # CAPTCHA/verificação → manual.
        if await _smartapply_bloqueado(driver):
            await notify_browser_step(f"gupy_step_{step}", "manual",
                                      "🔒 Verificação — resolva no browser e clique 🔄 Retomar Auto")
            if not await _aguardar_resolucao_manual(driver, f"verificação no Gupy step {step}"):
                return {"sucesso": False, "motivo_falha": "captcha",
                        "mensagem": f"Verificação não resolvida. Candidate-se à mão: {vaga_url}"}
            continue

        # Login pode surgir NO MEIO: ao clicar Candidatar-se a Gupy manda pro signin da
        # empresa. Preenche pelo .env e re-tenta abrir a candidatura (o redirect pós-login
        # costuma voltar pro detalhe da vaga, não pro form).
        if await _tratar_login_gupy(driver):
            await asyncio.sleep(1.5)
            await _clicar_botao_smartapply(driver, _BTN_CANDIDATAR)
            await asyncio.sleep(2)
            continue

        # Preenche o que estiver na tela (idempotente — só mexe no que existe/está vazio).
        try:
            await _responder_radios_gupy(driver, perfil, resumo_curriculo, idioma, vaga_titulo)
            await _preencher_how_did_you_hear(driver)
            novas = await _responder_perguntas_gupy(driver, perfil, resumo_curriculo, idioma, vaga_titulo)
            for p in novas:
                if p not in perguntas_feitas:
                    perguntas_feitas.append(p)
        except StaleElementReferenceException:
            await asyncio.sleep(1.0)
            continue

        # Assinatura antes de clicar (detecta não-avanço).
        try:
            sig_antes = await _run_in_thread(
                lambda: driver.current_url + "|" + str(len(driver.find_elements(By.CSS_SELECTOR, "input,textarea,button,label")))
            )
        except Exception:
            sig_antes = ""

        await _dump_gupy_debug(driver, f"step{step}-antes-clique")

        # Avançar: Finalizar-FORTE (ENVIO, conta sucesso) > Salvar e continuar >
        # Responder agora > Continuar > Finalizar-FRACO (só avança; sucesso só por frase).
        btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_FINALIZAR)
        is_finalizar = clicou
        if not clicou:
            btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_SALVAR_CONTINUAR)
        if not clicou:
            btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_RESPONDER_AGORA)
        if not clicou:
            btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_CONTINUAR)
        if not clicou:
            btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_FINALIZAR_FRACO)

        if not clicou:
            # Sem botão de avançar → PULA a vaga (NÃO congela no manual). O usuário
            # reclamou de "travado" no Gupy E na Senior → o pedido direto é NÃO travar; só
            # CAPTCHA vai pra manual (acima). Antes ficava em _aguardar_resolucao_manual.
            await notify_browser_step(f"gupy_step_{step}", "pulada",
                                      "Sem botão de avançar/finalizar — pulando vaga")
            return {"sucesso": False, "motivo_falha": "formulario_incompleto",
                    "mensagem": f"Formulário não concluído (sem botão). Candidate-se à mão: {vaga_url}"}

        print(f"[GUPY] Step {step}: clicou '{btn_text[:40]}'")
        await asyncio.sleep(3 if is_finalizar else 2)
        await _dump_gupy_debug(driver, f"step{step}-apos-{btn_text[:15]}")

        # Clicar o #dialog-give-up-personalization-step É o envio final. Confirma com
        # frase forte; mesmo sem a frase, esse botão é específico o bastante = enviado.
        if is_finalizar:
            await asyncio.sleep(1.5)
            confirmado = await _sucesso_gupy(driver)
            b64 = await screenshot_base64()
            await notify_browser_step(f"gupy_step_{step}", "sucesso", "Candidatura enviada!")
            return {"sucesso": True, "perguntas_respondidas": perguntas_feitas,
                    "mensagem": "Candidatura enviada com sucesso no Gupy!"
                               + ("" if confirmado else " (sem frase de confirmação explícita)"),
                    "screenshot": b64[:100] if b64 else ""}

        # Confirmação pode aparecer após um "Salvar e continuar" final (vaga sem modal).
        if await _sucesso_gupy(driver):
            b64 = await screenshot_base64()
            await notify_browser_step(f"gupy_step_{step}", "sucesso", "Candidatura enviada!")
            return {"sucesso": True, "perguntas_respondidas": perguntas_feitas,
                    "mensagem": "Candidatura enviada com sucesso no Gupy!",
                    "screenshot": b64[:100] if b64 else ""}

        # Detecta avanço.
        try:
            sig_depois = await _run_in_thread(
                lambda: driver.current_url + "|" + str(len(driver.find_elements(By.CSS_SELECTOR, "input,textarea,button,label")))
            )
        except Exception:
            sig_depois = ""
        if sig_antes and sig_antes != sig_depois:
            nao_avancou = 0
            continue
        nao_avancou += 1
        if nao_avancou >= 3:
            # Não avançou após 3 tentativas (form travado — ex.: pergunta obrigatória que
            # não coube preencher) → PULA a vaga em vez de congelar no manual. Antes ficava
            # em _aguardar_resolucao_manual e PARECIA travado (reclamação do usuário).
            await notify_browser_step(f"gupy_step_{step}", "pulada", "Formulário travou — pulando vaga")
            return {"sucesso": False, "motivo_falha": "formulario_travado",
                    "mensagem": f"Formulário travou (não avançou). Candidate-se à mão: {vaga_url}"}

    return {"sucesso": False, "motivo_falha": "formulario_incompleto",
            "mensagem": f"Não consegui concluir o formulário. Candidate-se à mão: {vaga_url}"}


# ── Fechar aba e voltar pra busca ─────────────────────────────────────────────

async def _fechar_aba_e_voltar(driver, aba_nova, janela_busca) -> None:
    """Fecha a aba do detalhe e volta pra aba da busca."""
    try:
        if aba_nova:
            try:
                await _run_in_thread(lambda: driver.close())
            except Exception:
                pass
        handles = await _run_in_thread(lambda: driver.window_handles)
        alvo = janela_busca if janela_busca in handles else (handles[0] if handles else None)
        if alvo:
            await _run_in_thread(lambda: driver.switch_to.window(alvo))
            await asyncio.sleep(1)
    except Exception as e:
        logger.warning("gupy _fechar_aba_e_voltar erro: %s", e)


# ── Loop: aplicar card a card ─────────────────────────────────────────────────

async def aplicar_vagas_visiveis_na_pagina(perfil: dict, max_vagas: int = 5, user_id: str = "admin",
                                           query: str = "") -> dict:
    """Login → busca → para cada card: abre a vaga (nova aba OU mesma aba) → roda o
    wizard → fecha/volta → próximo. `query` = palavra-chave do dashboard."""
    set_platform("gupy")
    try:
        from graph.neo4j_client import get_neo4j
        neo4j = get_neo4j()
    except Exception:
        neo4j = None

    resumo_curriculo = perfil.get("resumo_curriculo", "") or await _get_resumo_curriculo(user_id)
    if resumo_curriculo and not perfil.get("resumo_curriculo"):
        perfil = {**perfil, "resumo_curriculo": resumo_curriculo}

    # Login do portal é BEST-EFFORT, não fatal: a BUSCA funciona deslogada (confirmado
    # no dump — cards aparecem) e o login que realmente destrava o apply é o do
    # SUBDOMÍNIO da empresa, tratado no wizard (_tratar_login_gupy). Abortar aqui
    # deixaria o Gupy "logando mas sem aplicar" de novo.
    if not await _garantir_login():
        print("[GUPY] Login do portal não confirmado — seguindo (login da empresa é tratado no apply)")

    driver = await get_driver()
    resultados = {"sucesso": True, "aplicacoes": [], "falhas": 0}
    aplicadas = 0
    teto = _cont.get_teto()

    # Loop de PÁGINAS: aplica em todos os cards da página; ao esgotar, vai pra page=2,
    # page=3… até não haver mais vagas (n_cards==0) ou o usuário parar. Terminador REAL
    # = acabarem os cards; GUPY_MAX_PAGINAS é só trava anti-loop.
    MAX_PAGINAS = int(os.getenv("GUPY_MAX_PAGINAS", "1000"))
    pagina = 1
    parar = False
    while pagina <= MAX_PAGINAS and not parar:
        control = await get_intervention_state()
        if control.get("current_action") == "parar":
            resultados["mensagem"] = "Interrompido pelo usuário."
            break
        if teto > 0 and _cont.teto_atingido(user_id):
            resultados["mensagem"] = f"Teto de candidaturas atingido ({teto})."
            break

        await _abrir_busca(driver, query, pagina)
        try:
            janela_busca = await _run_in_thread(lambda: driver.current_window_handle)
        except Exception:
            janela_busca = None
        try:
            n_cards = await _run_in_thread(lambda: len(_achar_cards(driver)))
        except Exception:
            n_cards = 0
        if n_cards == 0:
            print(f"[GUPY] Página {pagina} sem vagas — encerrando paginação")
            break
        print(f"[GUPY] Página {pagina}: {n_cards} vaga(s)")

        for i in range(n_cards):
            control = await get_intervention_state()
            if control.get("current_action") == "parar":
                resultados["mensagem"] = "Interrompido pelo usuário."
                parar = True
                break
            if teto > 0 and _cont.teto_atingido(user_id):
                resultados["mensagem"] = f"Teto de candidaturas atingido ({teto})."
                parar = True
                break

            # Re-busca os cards a cada iteração (evita stale após fechar a aba).
            def _clicar_card(idx=i):
                cards = _achar_cards(driver)
                if idx >= len(cards):
                    return "", False, ""
                c = cards[idx]
                titulo = (c.text or "").strip().split("\n")[0]
                href = c.get_attribute("href") or ""
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", c)
                handles_antes = len(driver.window_handles)
                c.click()
                return titulo, handles_antes, href

            try:
                titulo, handles_antes, href = await _run_in_thread(_clicar_card)
            except Exception as e:
                logger.warning("gupy clicar card %d erro: %s", i, e)
                continue
            if handles_antes is False:
                break  # acabaram os cards desta página → próxima página

            await notify_browser_step("selenium_gupy", "abrindo", f"Abrindo: {titulo[:40]}")
            await asyncio.sleep(2.5)

            # Tolera nova aba OU mesma aba (subdomínio da empresa abre dos dois jeitos).
            try:
                handles = await _run_in_thread(lambda: driver.window_handles)
            except Exception:
                handles = []
            aba_nova = None
            if handles and isinstance(handles_antes, int) and len(handles) > handles_antes:
                aba_nova = handles[-1]
                await _run_in_thread(lambda h=aba_nova: driver.switch_to.window(h))
                await asyncio.sleep(2)

            # Abrir a vaga (subdomínio da empresa) pode exigir login → trata pelo .env
            # ANTES de capturar a URL (senão o dedup guardaria a URL do signin).
            if await _tratar_login_gupy(driver):
                await asyncio.sleep(1.5)

            vaga_url = ""
            try:
                vaga_url = await _run_in_thread(lambda: driver.current_url)
            except Exception:
                pass

            async def _voltar_busca():
                # Fecha a aba (se abriu) ou re-navega pra MESMA página (mesma aba).
                if aba_nova:
                    await _fechar_aba_e_voltar(driver, aba_nova, janela_busca)
                else:
                    await navegar(_build_search_url(query, pagina))
                    await asyncio.sleep(2)

            # Vaga 404/expirada (o portal lista vagas já removidas → abre 'not found').
            # PULA limpo, sem tentar aplicar nem registrar nada.
            if await _vaga_indisponivel(driver):
                print(f"[GUPY] Vaga indisponível/404, pulando: {titulo[:40]} ({vaga_url[:60]})")
                await notify_browser_step("selenium_gupy", "pulada", "Vaga 404/expirada")
                await _voltar_busca()
                continue

            # Dedup por URL do detalhe.
            if neo4j and vaga_url:
                try:
                    if neo4j.ja_se_candidatou(user_id, vaga_url):
                        print(f"[GUPY] Já aplicada, pulando: {vaga_url}")
                        await _voltar_busca()
                        continue
                except Exception:
                    pass

            # Filtro de MODALIDADE + REGIÃO (igual GeekHunter): presencial/híbrido só
            # candidata se a cidade da vaga estiver numa região aceita; remoto passa
            # livre. Fail-open: sem config/modalidade indefinida, candidata.
            try:
                from automation.localizacao import vaga_aceita
                texto_vaga = f"{titulo} {await _extrair_descricao_detalhe(driver)}"
                aceita, motivo = vaga_aceita(
                    texto_vaga, perfil.get("modalidades_aceitas", []),
                    perfil.get("regioes_relocacao", []),
                )
                if not aceita:
                    print(f"[GUPY] Pulando por modalidade/região: {motivo} — {titulo[:40]}")
                    await notify_browser_step("selenium_gupy", "pulada", f"Fora do filtro: {motivo[:50]}")
                    await _voltar_busca()
                    continue
            except Exception as e:
                logger.warning("gupy filtro modalidade/região erro: %s", e)

            await notify_browser_step("selenium_gupy", "aplicando", f"Candidatando: {titulo[:40]}")
            try:
                res = await _preencher_e_enviar_formulario(
                    driver, perfil, resumo_curriculo, "pt", titulo, vaga_url,
                )
            except Exception as e:
                logger.error("gupy aplicar erro: %s", e)
                res = {"sucesso": False, "mensagem": str(e)}

            status = "candidatado" if res.get("sucesso") else "tentativa_falhou"
            if res.get("sucesso"):
                aplicadas += 1
                _cont.incr_count(user_id)
            else:
                resultados["falhas"] += 1
            resultados["aplicacoes"].append({"vaga": titulo, "status": status})

            if neo4j:
                try:
                    neo4j.registrar_candidatura(user_id=user_id, vaga_id=vaga_url or f"gupy-p{pagina}-{i}",
                                                plataforma="gupy", status=status)
                except Exception:
                    pass

            await _voltar_busca()
            await asyncio.sleep(random.uniform(1.5, 3.0))

        pagina += 1  # esgotou a página atual → subsequente

    resultados.setdefault("mensagem", f"{aplicadas} candidatura(s) enviada(s) no Gupy.")
    return resultados


# ── Aplicação em uma vaga única (por URL) — paridade com Indeed/GeekHunter ─────

async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "", user_id: str = "admin") -> dict:
    """Aplica numa vaga específica do Gupy pela URL do detalhe."""
    set_platform("gupy")
    resumo_curriculo = perfil.get("resumo_curriculo", "") or await _get_resumo_curriculo(user_id)
    if resumo_curriculo and not perfil.get("resumo_curriculo"):
        perfil = {**perfil, "resumo_curriculo": resumo_curriculo}

    # Best-effort: o login que destrava o apply é o do subdomínio da empresa (wizard).
    if not await _garantir_login():
        print("[GUPY] Login do portal não confirmado — seguindo (login da empresa é tratado no apply)")
    driver = await get_driver()
    await navegar(vaga_url)
    await asyncio.sleep(3)
    # Vaga 404/expirada (URL removida) → não tenta aplicar numa página morta.
    if await _vaga_indisponivel(driver):
        print(f"[GUPY] Vaga indisponível/404: {vaga_url[:70]}")
        return {"sucesso": False, "motivo_falha": "vaga_indisponivel",
                "mensagem": f"Vaga não encontrada / expirada (404): {vaga_url}"}
    return await _preencher_e_enviar_formulario(driver, perfil, resumo_curriculo, "pt", "", vaga_url)
