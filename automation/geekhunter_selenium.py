"""
Candidatura automática no GeekHunter (https://www.geekhunter.com) via Selenium.

Nos mesmos moldes do Indeed (automation/indeed_selenium.py), mas adaptado ao
GeekHunter, que é um SPA (Chakra UI/React):

- Login por e-mail+senha do .env (GEEK_HUNTER_EMAIL / GEEK_HUNTER_PASSWORD).
  CAPTCHA/verificação cai em intervenção manual (pausa e avisa no dashboard).
- Busca por palavra-chave (igual Indeed), navegando direto para
  /pt/vagas?page=1&searchTerm=<palavra-chave do dashboard>.
- Os cards NÃO têm href — clicar em "Visualizar vaga" abre uma NOVA ABA com o
  detalhe da vaga. O fluxo é card a card: clica → troca pra aba nova → preenche o
  formulário → envia → fecha a aba → volta pra lista → próximo card.
- O preenchimento reusa os helpers genéricos do Indeed (contato, detecção de
  perguntas, respostas via IA, clique de botão) — são agnósticos de plataforma.
- Todo seletor incerto degrada para pausa manual, nunca falha silenciosa.

Config via .env:
  GEEK_HUNTER_EMAIL, GEEK_HUNTER_PASSWORD  → credenciais
  GEEK_HUNTER_QUERY                        → palavra-chave padrão (fallback do dashboard)
  GEEK_HUNTER_LIMIAR_MATCH                 → nota mínima (0-100) p/ aplicar (fail-open)
  GEEK_HUNTER_TETO_APLICACOES              → teto total (persistente no Redis)
"""

import asyncio
import logging
import os
import random
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()

from automation.selenium_browser import (
    nova_pagina, navegar, digitar_robusto, screenshot_base64, fechar,
    get_driver, get_title, _run_in_thread, _driver_session_valida,
)
from automation.browser import (
    notify_browser_step, get_intervention_state, set_intervention_state,
)
from automation.run_context import set_platform

# Reuso dos preenchedores GENÉRICOS do Indeed (agnósticos de plataforma) + os
# detectores de bloqueio/sucesso e a espera de intervenção manual. Mantém DRY: se
# o formulário do GeekHunter tiver perguntas customizadas, a mesma IA responde.
from automation.indeed_selenium import (
    _preencher_contato_smartapply,
    _tratar_curriculo_smartapply,
    _detectar_perguntas_smartapply,
    _preencher_resposta_smartapply,
    _clicar_botao_smartapply,
    _candidatura_enviada,
    _smartapply_bloqueado,
    _pagina_bloqueada,
    _aguardar_resolucao_manual,
    _avaliar_match_vaga,
    _get_resumo_curriculo,
)
from automation.form_filler import responder_pergunta
from automation.contador_aplicacoes import GEEK_HUNTER as _cont

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException

logger = logging.getLogger(__name__)


# ── Config via .env ──────────────────────────────────────────────────────────

_BASE = "https://www.geekhunter.com"
_SIGN_IN = f"{_BASE}/pt/candidates/sign_in"
_VAGAS = f"{_BASE}/pt/vagas"

# Match reusa o avaliador fail-open do Indeed (_avaliar_match_vaga), que aplica na
# dúvida — mesma política das outras plataformas.


def _get_email() -> str:
    return os.getenv("GEEK_HUNTER_EMAIL", "")


def _get_password() -> str:
    return os.getenv("GEEK_HUNTER_PASSWORD", "")


def _get_query_padrao() -> str:
    return os.getenv("GEEK_HUNTER_QUERY", "desenvolvedor")


def _build_search_url(query: str = "") -> str:
    q = (query or "").strip() or _get_query_padrao()
    return f"{_VAGAS}?page=1&searchTerm={quote_plus(q)}"


# Botões que enviam a candidatura no detalhe da vaga (ordem = prioridade).
_BTN_CANDIDATAR = [
    'button:has-text("Finalizar Candidatura")',
    'button:has-text("Finalizar candidatura")',
    'button:has-text("Quero me candidatar")',
    'button:has-text("Candidatar-se")',
    'button:has-text("Candidatar")',
    'button:has-text("Enviar candidatura")',
    'button:has-text("Enviar minha candidatura")',
    'button:has-text("Aplicar para a vaga")',
    'button:has-text("Aplicar")',
    'button:has-text("Enviar")',
    'button[type="submit"]',
]

# Fecha o modal de "atenção"/confirmação que aparece após enviar a candidatura.
_BTN_FECHAR_MODAL = [
    '.chakra-modal__close-btn',
    'button[aria-label="Close"]',
    'button[aria-label="Fechar"]',
    'button:has-text("Entendi")',
    'button:has-text("Fechar")',
    'button:has-text("OK")',
    'button:has-text("Ok")',
    'button:has-text("Continuar")',
]

# Botões de avançar dentro de um fluxo multi-step do formulário.
_BTN_CONTINUAR = [
    'button:has-text("Continuar")',
    'button:has-text("Próximo")',
    'button:has-text("Próxima")',
    'button:has-text("Avançar")',
    'button:has-text("Salvar e continuar")',
]


# ── Login ──────────────────────────────────────────────────────────────────

def _esta_logado(url: str, html: str = "") -> bool:
    """Heurística de login. Conservadora: na dúvida NÃO está logado (cai no login)."""
    u = (url or "").lower()
    if "sign_in" in u or "/login" in u or "/candidates/sign_in" in u:
        return False
    h = (html or "").lower()
    for marca in ("sign_out", "sair", "minha conta", "meu perfil", "logout",
                  "candidates/edit", "candidato"):
        if marca in h[:8000]:
            return True
    # Estar em /vagas sem ter sido redirecionado pro sign_in é bom sinal.
    return "/vagas" in u


async def _garantir_login() -> bool:
    """Garante sessão logada no GeekHunter usando e-mail+senha do .env. CAPTCHA/
    verificação → intervenção manual. Retorna True se logado."""
    driver = await get_driver()
    if not driver:
        await nova_pagina(_SIGN_IN, reutilizar=False)
        await asyncio.sleep(2)
        driver = await get_driver()
    if not driver:
        print("[GEEK] ERRO: driver é None")
        return False

    # Se o perfil persistente já tem sessão, /vagas carrega sem bounce pro sign_in.
    try:
        await navegar(_VAGAS)
        await asyncio.sleep(2.5)
        cur = await _run_in_thread(lambda: driver.current_url)
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, html = "", ""
    if _pagina_bloqueada(cur, await get_title(), html):
        if not await _aguardar_resolucao_manual(driver, "acesso ao GeekHunter"):
            return False
        try:
            cur = await _run_in_thread(lambda: driver.current_url)
            html = await _run_in_thread(lambda: driver.page_source)
        except Exception:
            pass
    if _esta_logado(cur, html):
        print("[GEEK] Já está logado")
        return True

    print("[GEEK] Não logado — abrindo login por e-mail/senha")
    await notify_browser_step("geekhunter_login", "login", "Fazendo login no GeekHunter")
    try:
        await navegar(_SIGN_IN)
        await asyncio.sleep(3)
    except Exception:
        pass

    email, senha = _get_email(), _get_password()
    if not email or not senha:
        print("[GEEK] GEEK_HUNTER_EMAIL/PASSWORD ausentes — caindo em login manual")
        await notify_browser_step("geekhunter_login", "manual",
                                  "Defina GEEK_HUNTER_EMAIL/PASSWORD no .env ou logue à mão")
        return await _aguardar_resolucao_manual(driver, "login manual GeekHunter")

    # Preenche e-mail e senha (best-effort; seletores comuns).
    preencheu_email = await digitar_robusto(
        "input[type='email'], input[name='email'], input[name*='email' i], "
        "input#email, input[autocomplete='username'], input[name='candidate[email]']",
        email,
    )
    preencheu_senha = await digitar_robusto(
        "input[type='password'], input[name='password'], input[name*='password' i], "
        "input#password, input[autocomplete='current-password'], input[name='candidate[password]']",
        senha,
    )
    if not (preencheu_email and preencheu_senha):
        print("[GEEK] Campos de login não encontrados — intervenção manual")
        return await _aguardar_resolucao_manual(driver, "login manual GeekHunter")

    await asyncio.sleep(0.6)
    # Submete: botão "Entrar"/"Login" ou Enter no campo de senha.
    _, clicou = await _clicar_botao_smartapply(driver, [
        'button[type="submit"]',
        'button:has-text("Entrar")', 'button:has-text("Login")',
        'button:has-text("Acessar")', 'input[type="submit"]',
    ])
    if not clicou:
        def _enter():
            el = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            el.send_keys(Keys.RETURN)
            return True
        try:
            await _run_in_thread(_enter)
        except Exception:
            pass
    await asyncio.sleep(4)

    # CAPTCHA/verificação pós-submit → manual.
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, html = "", ""
    if _pagina_bloqueada(cur, await get_title(), html) or "sign_in" in (cur or "").lower():
        await notify_browser_step("geekhunter_login", "manual",
                                  "🔒 Verificação/senha — conclua o login no browser e clique ▶️ Continuar")
        if not await _aguardar_resolucao_manual(driver, "verificação de login GeekHunter"):
            return False
        try:
            cur = await _run_in_thread(lambda: driver.current_url)
            html = await _run_in_thread(lambda: driver.page_source)
        except Exception:
            pass

    logado = _esta_logado(cur, html)
    if logado:
        await notify_browser_step("geekhunter_login", "sucesso", "Login concluído")
    return logado


# ── Busca / extração de cards ─────────────────────────────────────────────────

async def _abrir_busca(driver, query: str = "") -> None:
    """Navega para a busca de vagas com a palavra-chave (do dashboard)."""
    url = _build_search_url(query)
    print(f"[GEEK] Abrindo busca → {url}")
    await notify_browser_step("geekhunter_busca", "navegando", f"Buscando: {query or _get_query_padrao()}")
    await navegar(url)
    await asyncio.sleep(3)
    # Bloqueio pode aparecer.
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, html = "", ""
    if _pagina_bloqueada(cur, await get_title(), html):
        await _aguardar_resolucao_manual(driver, "busca no GeekHunter")


def _extrair_cards_info(driver, max_vagas: int) -> list:
    """Coleta (título, índice) dos cards visíveis via o botão 'Visualizar vaga'.
    Não há href — a navegação é por clique (abre nova aba). Retorna metadados;
    o clique é feito por índice depois (re-buscando pra evitar stale)."""
    import time as _time
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, "//p[contains(., 'Visualizar vaga')]"))
        )
    except Exception:
        pass
    # Scroll pra carregar mais cards (lazy-load).
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

    gatilhos = driver.find_elements(By.XPATH, "//p[contains(., 'Visualizar vaga')]")
    infos = []
    for idx, g in enumerate(gatilhos[:max_vagas]):
        titulo = ""
        try:
            # sobe até um container do card e pega o 1º parágrafo (título).
            card = g.find_element(By.XPATH, "./ancestor::div[.//p[contains(., 'Visualizar vaga')]][last()]")
            ps = card.find_elements(By.CSS_SELECTOR, "p.chakra-text")
            if ps:
                titulo = (ps[0].text or "").strip()
        except Exception:
            titulo = ""
        infos.append({"idx": idx, "titulo": titulo or f"Vaga {idx+1}"})
    print(f"[GEEK] {len(infos)} card(s) na busca")
    return infos


async def extrair_vagas_da_busca(perfil: dict, max_vagas: int = 20, query: str = "") -> dict:
    """Faz login, abre a busca e lista os cards (título). O apply é card a card em
    aplicar_vagas_visiveis_na_pagina (clique abre nova aba)."""
    set_platform("geekhunter")
    driver = await get_driver()
    if not driver or not await _driver_session_valida():
        await nova_pagina(_SIGN_IN, reutilizar=False)
        await asyncio.sleep(2)

    if not await _garantir_login():
        return {"sucesso": False, "vagas": [],
                "mensagem": "Não foi possível logar no GeekHunter (login/verificação)."}

    driver = await get_driver()
    await _abrir_busca(driver, query)
    try:
        infos = await _run_in_thread(lambda: _extrair_cards_info(driver, max_vagas))
    except Exception as e:
        logger.warning("geekhunter extrair cards erro: %s", e)
        infos = []

    vagas = [{
        "id": f"geekhunter-{query or _get_query_padrao()}-{i['idx']}",
        "titulo": i["titulo"], "empresa": "", "url": "", "fonte": "GeekHunter",
        "easy_apply": True, "salario": "", "modalidade": "", "descricao": "", "local": "",
        "card_idx": i["idx"],
    } for i in infos]
    return {"sucesso": True, "vagas": vagas, "total": len(vagas)}


# ── Formulário de candidatura (na aba nova do detalhe da vaga) ─────────────────

async def _extrair_descricao_detalhe(driver) -> str:
    def _txt():
        try:
            return (driver.find_element(By.TAG_NAME, "body").text or "")[:3000]
        except Exception:
            return ""
    try:
        return await _run_in_thread(_txt)
    except Exception:
        return ""


def _get_linkedin_url(perfil: dict) -> str:
    return (perfil.get("linkedin_url") or perfil.get("linkedin")
            or os.getenv("LINKEDIN_URL", "") or "").strip()


def _get_nome(perfil: dict) -> str:
    """Nome completo com fallback por env (nome/telefone não têm fallback pelo
    perfil Neo4j — se vazios, o form do GeekHunter trava em 'campo obrigatório')."""
    return (perfil.get("nome") or os.getenv("CANDIDATO_NOME")
            or os.getenv("LINKEDIN_NOME") or "").strip()


def _get_telefone(perfil: dict) -> str:
    return str(perfil.get("telefone") or perfil.get("phone")
               or os.getenv("CANDIDATO_TELEFONE") or os.getenv("LINKEDIN_TELEFONE") or "").strip()


async def _preencher_contato_geekhunter(driver, perfil: dict) -> None:
    """Preenche os campos de contato do form do GeekHunter POR RÓTULO (Confirmar
    Email, Nome completo, Celular com DDD, LinkedIn) — os inputs Chakra não têm
    name/type confiáveis, então casamos pelo texto do label. Preenche só se vazio.
    Feito ANTES da detecção de perguntas: campo preenchido não vira 'pergunta'."""
    nome = _get_nome(perfil)
    telefone = _get_telefone(perfil)
    email = perfil.get("email", "") or _get_email()
    linkedin = _get_linkedin_url(perfil)

    def _contexto_de(el):
        """Junta aria-label/placeholder/name + label[for] + texto do bloco ancestral
        (rótulo do Chakra costuma ser um <p> irmão dentro do wrapper, não linkado por
        'for'). Match por 'contains' no texto inteiro é mais tolerante."""
        partes = []
        for attr in ("aria-label", "placeholder", "name"):
            v = el.get_attribute(attr)
            if v and v.strip():
                partes.append(v.strip())
        iid = el.get_attribute("id")
        if iid:
            try:
                lab = driver.find_element(By.CSS_SELECTOR, f"label[for='{iid}']")
                if (lab.text or "").strip():
                    partes.append(lab.text.strip())
            except Exception:
                pass
        try:
            anc = el.find_element(By.XPATH, "./ancestor::div[1]")
            if (anc.text or "").strip():
                partes.append(anc.text.strip())
        except Exception:
            pass
        return " ".join(partes).lower()

    def _fill():
        for el in driver.find_elements(By.CSS_SELECTOR, "input, textarea"):
            try:
                if not el.is_displayed():
                    continue
                if (el.get_attribute("value") or "").strip():
                    continue
                ctx = _contexto_de(el)
                tipo = (el.get_attribute("type") or "").lower()
                val = ""
                if "linkedin" in ctx:
                    val = linkedin
                elif "email" in ctx or "e-mail" in ctx or tipo == "email":
                    val = email
                elif tipo == "tel" or any(k in ctx for k in ("celular", "telefone", "ddd", "phone", "whatsapp")):
                    val = telefone
                elif "nome" in ctx or "name" in ctx:
                    val = nome
                if val:
                    try:
                        el.clear()
                    except Exception:
                        pass
                    el.send_keys(val)
                    print(f"[GEEK] Contato preenchido ({ctx[:25]}) = {val[:30]}")
            except Exception:
                continue

    try:
        await _run_in_thread(_fill)
    except Exception as e:
        logger.warning("geekhunter _preencher_contato erro: %s", e)


async def _fechar_modal_atencao(driver) -> None:
    """Fecha o modal de atenção/confirmação exibido após enviar a candidatura."""
    try:
        _txt, clicou = await _clicar_botao_smartapply(driver, _BTN_FECHAR_MODAL)
        if clicou:
            print("[GEEK] Modal de atenção fechado")
            await asyncio.sleep(1.5)
    except Exception:
        pass


async def _preencher_e_enviar_formulario(driver, perfil: dict, resumo_curriculo: str,
                                         idioma: str, vaga_titulo: str, vaga_url: str,
                                         curriculo_path: str = "") -> dict:
    """Percorre o formulário de candidatura do GeekHunter: preenche contato +
    perguntas (IA) e clica Candidatar/Continuar. CAPTCHA/trava → manual."""
    respostas = {}
    perguntas_feitas = []
    max_steps = 10
    nao_avancou = 0

    for step in range(max_steps):
        control = await get_intervention_state()
        if control.get("current_action") == "parar":
            return {"sucesso": False, "motivo_falha": "parado", "mensagem": "Interrompido pelo usuário."}

        await asyncio.sleep(random.uniform(0.6, 1.2))
        await notify_browser_step(f"geek_step_{step}", "preenchendo", "Preenchendo candidatura GeekHunter")

        if await _candidatura_enviada(driver):
            b64 = await screenshot_base64()
            await notify_browser_step(f"geek_step_{step}", "sucesso", "Candidatura enviada!")
            return {"sucesso": True, "perguntas_respondidas": perguntas_feitas,
                    "mensagem": "Candidatura enviada com sucesso no GeekHunter!",
                    "screenshot": b64[:100] if b64 else ""}

        # CAPTCHA/verificação → manual.
        if await _smartapply_bloqueado(driver):
            await notify_browser_step(f"geek_step_{step}", "manual",
                                      "🔒 CAPTCHA/verificação — resolva no browser e clique ▶️ Continuar")
            if not await _aguardar_resolucao_manual(driver, f"CAPTCHA no GeekHunter step {step}"):
                b64 = await screenshot_base64()
                return {"sucesso": False, "motivo_falha": "captcha",
                        "mensagem": f"CAPTCHA não resolvido. Candidate-se à mão: {vaga_url}",
                        "screenshot": b64[:100] if b64 else ""}
            continue

        # Contato (por rótulo: Confirmar Email, Nome completo, Celular, LinkedIn) +
        # upload do currículo (se houver input de arquivo) + perguntas customizadas.
        for _tent in range(3):
            try:
                await _preencher_contato_geekhunter(driver, perfil)
                await _preencher_contato_smartapply(driver, perfil)  # supl.: campos com name/type
                if curriculo_path:
                    await _tratar_curriculo_smartapply(driver, curriculo_path)
                perguntas = await _detectar_perguntas_smartapply(driver)
                break
            except StaleElementReferenceException:
                await asyncio.sleep(0.8)
                perguntas = []
        else:
            perguntas = []

        if perguntas:
            await notify_browser_step(f"geek_step_{step}", "respondendo", f"{len(perguntas)} pergunta(s)")
            for p in perguntas:
                if p not in respostas:
                    try:
                        respostas[p] = responder_pergunta(
                            p, perfil, vaga_titulo=vaga_titulo, vaga_empresa="",
                            resumo_curriculo=resumo_curriculo, idioma=idioma,
                        )
                    except Exception as e:
                        logger.warning("responder_pergunta erro: %s", e)
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
            sig_antes = await _run_in_thread(
                lambda: driver.current_url + "|" + str(len(driver.find_elements(By.CSS_SELECTOR, "input,textarea,select,button")))
            )
        except Exception:
            sig_antes = ""

        # Candidatar (envio) > Continuar (avança).
        btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_CANDIDATAR)
        is_submit = clicou
        if not clicou:
            btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_CONTINUAR)

        if not clicou:
            await notify_browser_step(f"geek_step_{step}", "manual",
                                      "Não encontrei botão de candidatar/continuar — controle manual")
            if not await _aguardar_resolucao_manual(driver, f"formulário GeekHunter step {step}"):
                b64 = await screenshot_base64()
                return {"sucesso": False, "motivo_falha": "formulario_incompleto",
                        "mensagem": f"Formulário não concluído. Candidate-se à mão: {vaga_url}",
                        "screenshot": b64[:100] if b64 else ""}
            continue

        print(f"[GEEK] Step {step}: clicou '{btn_text[:40]}'")
        await asyncio.sleep(3 if is_submit else 2)

        if await _candidatura_enviada(driver):
            b64 = await screenshot_base64()
            return {"sucesso": True, "perguntas_respondidas": perguntas_feitas,
                    "mensagem": "Candidatura enviada com sucesso no GeekHunter!",
                    "screenshot": b64[:100] if b64 else ""}

        try:
            sig_depois = await _run_in_thread(
                lambda: driver.current_url + "|" + str(len(driver.find_elements(By.CSS_SELECTOR, "input,textarea,select,button")))
            )
        except Exception:
            sig_depois = ""
        if sig_antes and sig_antes != sig_depois:
            nao_avancou = 0
            continue
        nao_avancou += 1
        if nao_avancou >= 3:
            await notify_browser_step(f"geek_step_{step}", "manual", "Formulário travado — controle manual")
            if not await _aguardar_resolucao_manual(driver, f"formulário travado GeekHunter step {step}"):
                b64 = await screenshot_base64()
                return {"sucesso": False, "motivo_falha": "formulario_travado",
                        "mensagem": f"Formulário travou. Candidate-se à mão: {vaga_url}",
                        "screenshot": b64[:100] if b64 else ""}
            nao_avancou = 0

    b64 = await screenshot_base64()
    if await _candidatura_enviada(driver):
        return {"sucesso": True, "perguntas_respondidas": perguntas_feitas,
                "mensagem": "Candidatura enviada com sucesso no GeekHunter!",
                "screenshot": b64[:100] if b64 else ""}
    return {"sucesso": False, "motivo_falha": "formulario_incompleto",
            "mensagem": f"Não consegui concluir o formulário. Candidate-se à mão: {vaga_url}",
            "screenshot": b64[:100] if b64 else ""}


# ── Loop: aplicar card a card (clique abre nova aba) ──────────────────────────

async def aplicar_vagas_visiveis_na_pagina(perfil: dict, max_vagas: int = 5, user_id: str = "admin",
                                           query: str = "") -> dict:
    """Login → busca → para cada card: clica ('Visualizar vaga' abre NOVA ABA) →
    preenche e envia na aba nova → fecha → próximo. `query` = palavra-chave do
    dashboard (prioridade sobre GEEK_HUNTER_QUERY do .env)."""
    set_platform("geekhunter")
    try:
        from graph.neo4j_client import get_neo4j
        neo4j = get_neo4j()
    except Exception:
        neo4j = None

    resumo_curriculo = perfil.get("resumo_curriculo", "") or await _get_resumo_curriculo(user_id)
    if resumo_curriculo and not perfil.get("resumo_curriculo"):
        perfil = {**perfil, "resumo_curriculo": resumo_curriculo}

    if not await _garantir_login():
        return {"sucesso": False, "aplicacoes": [],
                "mensagem": "Não foi possível logar no GeekHunter."}

    driver = await get_driver()
    await _abrir_busca(driver, query)

    try:
        janela_busca = await _run_in_thread(lambda: driver.current_window_handle)
    except Exception:
        janela_busca = None

    resultados = {"sucesso": True, "aplicacoes": [], "falhas": 0}
    aplicadas = 0
    teto = _cont.get_teto()

    for i in range(max_vagas):
        if aplicadas >= max_vagas:
            break
        if teto > 0 and _cont.teto_atingido(user_id):
            resultados["mensagem"] = f"Teto de candidaturas atingido ({teto})."
            break
        control = await get_intervention_state()
        if control.get("current_action") == "parar":
            resultados["mensagem"] = "Interrompido pelo usuário."
            break

        # Re-busca os gatilhos a cada iteração (a lista da busca fica intacta pois o
        # clique abre NOVA ABA, mas re-buscar evita stale após fechar a aba).
        def _clicar_card(idx=i):
            gatilhos = driver.find_elements(By.XPATH, "//p[contains(., 'Visualizar vaga')]")
            if idx >= len(gatilhos):
                return "", False
            g = gatilhos[idx]
            titulo = ""
            try:
                card = g.find_element(By.XPATH, "./ancestor::div[.//p[contains(., 'Visualizar vaga')]][last()]")
                ps = card.find_elements(By.CSS_SELECTOR, "p.chakra-text")
                if ps:
                    titulo = (ps[0].text or "").strip()
            except Exception:
                pass
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", g)
            handles_antes = len(driver.window_handles)
            g.click()
            return titulo, handles_antes

        try:
            titulo, handles_antes = await _run_in_thread(_clicar_card)
        except Exception as e:
            logger.warning("geekhunter clicar card %d erro: %s", i, e)
            continue
        if handles_antes is False:
            break  # acabaram os cards

        await notify_browser_step("selenium_geekhunter", "abrindo", f"Abrindo: {titulo[:40]}")
        await asyncio.sleep(2.5)

        # Troca pra aba nova (detalhe da vaga) — detecta pela contagem de abas.
        try:
            handles = await _run_in_thread(lambda: driver.window_handles)
        except Exception:
            handles = []
        aba_nova = None
        if handles and isinstance(handles_antes, int) and len(handles) > handles_antes:
            aba_nova = handles[-1]
        if aba_nova and aba_nova != janela_busca:
            await _run_in_thread(lambda h=aba_nova: driver.switch_to.window(h))
            await asyncio.sleep(2)
        else:
            aba_nova = None  # não abriu nova aba (navegou na mesma) — segue no que tiver

        vaga_url = ""
        try:
            vaga_url = await _run_in_thread(lambda: driver.current_url)
        except Exception:
            pass

        # Já aplicada? (dedup por URL do detalhe)
        if neo4j and vaga_url:
            try:
                if neo4j.ja_se_candidatou(user_id, vaga_url):
                    print(f"[GEEK] Já aplicada, pulando: {vaga_url}")
                    await _fechar_aba_e_voltar(driver, aba_nova, janela_busca)
                    continue
            except Exception:
                pass

        # Match com currículo (fail-open, igual Indeed).
        idioma_vaga = "pt"
        if resumo_curriculo:
            descricao = await _extrair_descricao_detalhe(driver)
            if descricao:
                aval = await _avaliar_match_vaga(descricao, resumo_curriculo)
                idioma_vaga = aval.get("idioma", "pt")
                if not aval.get("aplicar", True):
                    print(f"[GEEK] Sem match, pulando: {aval.get('motivo','')}")
                    await notify_browser_step("selenium_geekhunter", "pulada", f"Sem match: {aval.get('motivo','')}")
                    await _fechar_aba_e_voltar(driver, aba_nova, janela_busca)
                    continue

        await notify_browser_step("selenium_geekhunter", "aplicando", f"Candidatando: {titulo[:40]}")
        try:
            res = await _preencher_e_enviar_formulario(
                driver, perfil, resumo_curriculo, idioma_vaga, titulo, vaga_url,
                curriculo_path=perfil.get("curriculo_path", ""),
            )
        except Exception as e:
            logger.error("geekhunter aplicar erro: %s", e)
            res = {"sucesso": False, "mensagem": str(e)}

        if res.get("sucesso"):
            await _fechar_modal_atencao(driver)

        status = "candidatado" if res.get("sucesso") else "tentativa_falhou"
        if res.get("sucesso"):
            aplicadas += 1
            _cont.incr_count(user_id)
        else:
            resultados["falhas"] += 1
        resultados["aplicacoes"].append({"vaga": titulo, "status": status})

        if neo4j:
            try:
                neo4j.registrar_candidatura(user_id=user_id, vaga_id=vaga_url or f"geekhunter-{i}",
                                            plataforma="geekhunter", status=status)
            except Exception:
                pass

        await _fechar_aba_e_voltar(driver, aba_nova, janela_busca)
        await asyncio.sleep(random.uniform(1.5, 3.0))

    resultados.setdefault("mensagem", f"{aplicadas} candidatura(s) enviada(s) no GeekHunter.")
    return resultados


async def _fechar_aba_e_voltar(driver, aba_nova, janela_busca) -> None:
    """Fecha a aba do detalhe da vaga e volta pra aba da busca."""
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
        logger.warning("geekhunter _fechar_aba_e_voltar erro: %s", e)


# ── Aplicação em uma vaga única (por URL) — paridade com Indeed/LinkedIn ───────

async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "", user_id: str = "admin") -> dict:
    """Aplica numa vaga específica do GeekHunter pela URL do detalhe."""
    set_platform("geekhunter")
    resumo_curriculo = perfil.get("resumo_curriculo", "") or await _get_resumo_curriculo(user_id)
    if resumo_curriculo and not perfil.get("resumo_curriculo"):
        perfil = {**perfil, "resumo_curriculo": resumo_curriculo}

    if not await _garantir_login():
        return {"sucesso": False, "motivo_falha": "login_falhou",
                "mensagem": "Não foi possível logar no GeekHunter."}
    driver = await get_driver()
    await navegar(vaga_url)
    await asyncio.sleep(3)

    idioma_vaga = "pt"
    if resumo_curriculo:
        descricao = await _extrair_descricao_detalhe(driver)
        if descricao:
            aval = await _avaliar_match_vaga(descricao, resumo_curriculo)
            idioma_vaga = aval.get("idioma", "pt")
            if not aval.get("aplicar", True):
                return {"sucesso": False, "pulada": True, "motivo_falha": "sem_match",
                        "mensagem": f"Vaga ignorada (sem match): {aval.get('motivo','')}"}

    res = await _preencher_e_enviar_formulario(
        driver, perfil, resumo_curriculo, idioma_vaga, "", vaga_url,
        curriculo_path=curriculo_path or perfil.get("curriculo_path", ""),
    )
    if res.get("sucesso"):
        await _fechar_modal_atencao(driver)
    return res
