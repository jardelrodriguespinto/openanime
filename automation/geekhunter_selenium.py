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


def _build_search_url(query: str = "", page: int = 1) -> str:
    q = (query or "").strip() or _get_query_padrao()
    return f"{_VAGAS}?page={page}&searchTerm={quote_plus(q)}"


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

# Frases ESPECÍFICAS de candidatura enviada no GeekHunter (só sinais de envio — NADA
# de "voltar"/"post-apply" genérico, que aparece no detalhe ANTES de enviar e dava
# falso sucesso). Inclui o modal "confirme sua candidatura pelo e-mail" (a vaga já
# foi enviada; a confirmação por e-mail é ação do usuário, não do bot → segue).
_FRASES_SUCESSO_GEEK = (
    "confirmar sua candidatura pelo e-mail",
    "confirmar sua candidatura pelo email",
    "para que ela seja enviada",
    "sua candidatura foi enviada",
    "candidatura enviada com sucesso",
    "candidatura foi enviada",
    "recebemos sua candidatura",
    "sua candidatura foi realizada",
)

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

async def _abrir_busca(driver, query: str = "", page: int = 1) -> None:
    """Navega para a busca de vagas com a palavra-chave (do dashboard) na página dada."""
    url = _build_search_url(query, page)
    print(f"[GEEK] Abrindo busca (página {page}) → {url}")
    await notify_browser_step("geekhunter_busca", "navegando", f"Buscando: {query or _get_query_padrao()} (pág. {page})")
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


def _telefone_digitos(tel: str) -> str:
    """Só dígitos do telefone (máscara 'Celular com DDD' rejeita +, espaço, hífen).
    Remove o código de país 55 se presente, deixando DDD+número (10-11 dígitos)."""
    import re as _re
    d = _re.sub(r"\D", "", tel or "")
    if len(d) > 11 and d.startswith("55"):
        d = d[2:]
    return d


def _contexto_input(driver, el) -> str:
    """Texto que identifica UM input: aria-label/placeholder/name + label[for] +
    texto do wrapper MAIS PRÓXIMO (ancestor::div[1]). Usar o wrapper mais próximo é
    o que separa 'CLT?' de 'PJ?' — um ancestral mais alto pegaria os dois juntos."""
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


def _digitar_no_input(el, valor: str) -> None:
    """Foca, limpa e digita — robusto pra inputs com máscara (React/Chakra). Pausa no
    fim (pacing): o GeekHunter é React e se preenchermos rápido demais ele 'se perde'
    (re-render não acompanha). O pequeno sleep deixa o estado assentar entre campos."""
    import time as _t
    try:
        el.click()
    except Exception:
        pass
    try:
        el.clear()
    except Exception:
        pass
    el.send_keys(valor)
    _t.sleep(random.uniform(0.4, 0.9))


async def _preencher_contato_geekhunter(driver, perfil: dict) -> None:
    """Preenche os campos de contato do form do GeekHunter POR RÓTULO (Confirmar
    Email, Nome completo, Celular com DDD, LinkedIn) — os inputs Chakra não têm
    name/type confiáveis, então casamos pelo texto do label. Preenche só se vazio.
    Feito ANTES da detecção de perguntas: campo preenchido não vira 'pergunta'."""
    nome = _get_nome(perfil)
    email = perfil.get("email", "") or _get_email()
    linkedin = _get_linkedin_url(perfil)

    def _fill():
        for el in driver.find_elements(By.CSS_SELECTOR, "input, textarea"):
            try:
                if not el.is_displayed():
                    continue
                tipo = (el.get_attribute("type") or "").lower()
                name = (el.get_attribute("name") or "").lower()
                # Telefone tem tratamento próprio (widget +55) — pula aqui.
                if tipo == "tel" or "phone" in name or "celular" in name:
                    continue
                if (el.get_attribute("value") or "").strip():
                    continue
                ctx = _contexto_input(driver, el)
                val = ""
                if "linkedin" in ctx:
                    val = linkedin
                elif "email" in ctx or "e-mail" in ctx or tipo == "email":
                    val = email
                elif "nome" in ctx or "name" in ctx:
                    val = nome
                if val:
                    _digitar_no_input(el, val)
                    print(f"[GEEK] Contato preenchido ({ctx[:25]}) = {val[:30]}")
            except Exception:
                continue

    try:
        await _run_in_thread(_fill)
        await _preencher_telefone_geekhunter(driver, perfil)
    except Exception as e:
        logger.warning("geekhunter _preencher_contato erro: %s", e)


async def _preencher_telefone_geekhunter(driver, perfil: dict) -> None:
    """Telefone do GeekHunter é react-phone-input-2 (input[type=tel][name=phone] que
    já vem com '+55 ' pré-preenchido). NÃO pular por ter valor: se só tem o DDI, vai
    pro fim e digita o número NACIONAL (DDD+número, sem 55) — o widget formata."""
    import re as _re
    nacional = _telefone_digitos(_get_telefone(perfil))  # DDD+número, sem 55
    if not nacional:
        return

    def _fill():
        for el in driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='tel'], input[name='phone'], input[name*='phone' i], input[name*='celular' i]",
        ):
            try:
                if not el.is_displayed():
                    continue
                atual = _re.sub(r"\D", "", el.get_attribute("value") or "")
                if len(atual) >= 10:      # já tem número real
                    return
                el.click()
                if len(atual) <= 3:       # só o DDI (+55) → vai pro fim e digita o nacional
                    try:
                        el.send_keys(Keys.END)
                    except Exception:
                        pass
                    el.send_keys(nacional)
                else:                     # valor parcial estranho → limpa e digita
                    try:
                        el.send_keys(Keys.CONTROL, "a")
                        el.send_keys(Keys.DELETE)
                    except Exception:
                        pass
                    el.send_keys(nacional)
                print(f"[GEEK] Telefone preenchido: ...{nacional[-4:]}")
                return
            except Exception:
                continue

    try:
        await _run_in_thread(_fill)
    except Exception as e:
        logger.warning("geekhunter _preencher_telefone erro: %s", e)


async def _preencher_remuneracao_geekhunter(driver, perfil: dict) -> None:
    """Preenche remuneração (CLT/PJ/dólar) ancorando na LABEL do Chakra
    (`label.chakra-form__label`, que tem o texto distintivo 'CLT?' / 'PJ?') e achando
    o input do MESMO FormControl. Corrige o bug de CLT e PJ pegarem ambos o valor PJ
    (a label sem `for` não linkava, e o rótulo genérico misturava os dois)."""
    from automation.form_filler import _eh_pergunta_remuneracao, _valor_remuneracao
    import re as _re

    def _fill():
        # Rótulo da remuneração pode ser <label> OU <p> (Chakra varia). Ancora nele e
        # acha o input associado — prioridade: o PRÓXIMO input/textarea em ordem de
        # documento (layout 'rótulo → campo'), que separa CLT do PJ com precisão.
        rotulos = driver.find_elements(By.CSS_SELECTOR, "label.chakra-form__label, label, legend, p.chakra-text")
        vistos = set()
        for lab in rotulos:
            try:
                txt = (lab.text or "").strip().lower()
                if not txt or not _eh_pergunta_remuneracao(txt):
                    continue
                if txt in vistos:
                    continue
                vistos.add(txt)
                val = _valor_remuneracao(perfil, txt)  # CLT/PJ/dólar pelo TEXTO deste rótulo
                if not val:
                    print(f"[GEEK] Remuneração SEM valor configurado p/ '{txt[:35]}'")
                    continue
                digs = _re.sub(r"[^\d]", "", val) or val
                inp = None
                for xp in (
                    "./following::input[1]",
                    "./following::textarea[1]",
                    "./ancestor::div[contains(@class,'form-control')][1]//input",
                    "./ancestor::div[1]//input",
                ):
                    try:
                        cand = lab.find_element(By.XPATH, xp)
                        if cand is not None and cand.is_displayed():
                            inp = cand
                            break
                    except Exception:
                        continue
                if inp is None:
                    print(f"[GEEK] Remuneração: input não encontrado p/ '{txt[:35]}'")
                    continue
                if (inp.get_attribute("value") or "").strip():
                    continue
                _digitar_no_input(inp, digs)
                tipo = "PJ" if ("pj" in txt or "jur" in txt) else ("dólar" if any(k in txt for k in ("dólar", "dolar", "usd", "dollar")) else "CLT")
                print(f"[GEEK] Remuneração {tipo} = {digs}")
            except Exception:
                continue

    try:
        await _run_in_thread(_fill)
    except Exception as e:
        logger.warning("geekhunter _preencher_remuneracao erro: %s", e)


def _label_pergunta_gh(driver, el) -> str:
    """Rótulo de uma pergunta do GeekHunter: o texto está num <p> separado (não é
    linkado por 'for', e o 'name' do input é um número tipo '130317'). Pega o <p>
    imediatamente anterior ou o texto do FormControl ancestral. Remove o '*'."""
    try:
        p = el.find_element(By.XPATH, "./preceding::p[normalize-space(.)!=''][1]")
        t = (p.text or "").strip()
        if t:
            return t.lstrip("* ").strip()
    except Exception:
        pass
    try:
        fc = el.find_element(By.XPATH, "./ancestor::div[contains(@class,'form-control')][1]")
        t = (fc.text or "").strip()
        if t:
            return t.split("\n")[0].lstrip("* ").strip()
    except Exception:
        pass
    return ""


async def _responder_perguntas_geekhunter(driver, perfil: dict, resumo_curriculo: str,
                                          idioma: str, vaga_titulo: str) -> list:
    """Responde as perguntas customizadas (textarea/input de texto) do GeekHunter.
    O rótulo vem do <p> da pergunta (não do 'name', que é numérico). Uma de cada vez,
    com pausa (pacing). Ignora contato/salário/consentimento (tratados à parte)."""
    _IGNORAR = ("nome", "email", "e-mail", "linkedin", "celular", "telefone", "ddd",
                "remunera", "salári", "salari", "pretens", "currículo", "curriculo",
                "política de privacidade", "politica de privacidade", "ciente de que")

    def _coletar():
        achados = []
        for el in driver.find_elements(By.CSS_SELECTOR, "textarea, input[type='text'], input:not([type])"):
            try:
                if not el.is_displayed():
                    continue
                if (el.get_attribute("value") or "").strip():
                    continue
                tipo = (el.get_attribute("type") or "").lower()
                name = (el.get_attribute("name") or "").lower()
                if tipo == "tel" or "phone" in name or "celular" in name:
                    continue
                label = _label_pergunta_gh(driver, el)
                if not label or label.lower() in ("", "*"):
                    continue
                if any(k in label.lower() for k in _IGNORAR):
                    continue
                achados.append((el.get_attribute("id") or "", el.get_attribute("name") or "", label))
            except Exception:
                continue
        return achados

    feitas = []
    try:
        perguntas = await _run_in_thread(_coletar)
    except Exception as e:
        logger.warning("geekhunter _coletar perguntas erro: %s", e)
        return feitas

    for eid, ename, label in perguntas:
        try:
            resp = responder_pergunta(label, perfil, vaga_titulo=vaga_titulo, vaga_empresa="",
                                      resumo_curriculo=resumo_curriculo, idioma=idioma)
        except Exception as e:
            logger.warning("responder_pergunta erro: %s", e)
            from automation.form_filler import resposta_segura
            resp = resposta_segura(label, idioma)

        def _fill(eid=eid, ename=ename, resp=resp, label=label):
            el = None
            if eid:
                try:
                    el = driver.find_element(By.ID, eid)
                except Exception:
                    el = None
            if el is None and ename:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, f"[name='{ename}']")
                except Exception:
                    el = None
            if el is None:
                return
            _digitar_no_input(el, str(resp))
            print(f"[GEEK] Pergunta '{label[:35]}' = {str(resp)[:30]}")

        for _tp in range(3):
            try:
                await _run_in_thread(_fill)
                break
            except StaleElementReferenceException:
                await asyncio.sleep(0.6)
        feitas.append(label)
        await asyncio.sleep(random.uniform(0.6, 1.2))  # pacing entre perguntas
    return feitas


async def _marcar_consentimentos_geekhunter(driver) -> None:
    """Marca os checkboxes de consentimento (LGPD) do GeekHunter. São Chakra
    (span.chakra-checkbox__control com input escondido) — clica o control só se ainda
    não estiver marcado (atributo data-checked ausente)."""
    def _marcar():
        for ctrl in driver.find_elements(By.CSS_SELECTOR, "span.chakra-checkbox__control"):
            try:
                if ctrl.get_attribute("data-checked") is not None:
                    continue  # já marcado
                driver.execute_script("arguments[0].click();", ctrl)
                print("[GEEK] Consentimento marcado")
            except Exception:
                continue
    try:
        await _run_in_thread(_marcar)
        await asyncio.sleep(random.uniform(0.4, 0.8))
    except Exception as e:
        logger.warning("geekhunter _marcar_consentimentos erro: %s", e)


async def _geek_confirmacao_sucesso(driver) -> bool:
    """Detecta a confirmação de candidatura ENVIADA no GeekHunter por frases
    específicas (inclui 'confirme sua candidatura pelo e-mail'). Estrito de propósito
    — não usa 'voltar'/'post-apply', que davam falso sucesso antes de enviar."""
    def _check():
        try:
            html = (driver.page_source or "").lower()
        except Exception:
            return False
        return any(f in html for f in _FRASES_SUCESSO_GEEK)
    try:
        return await _run_in_thread(_check)
    except Exception:
        return False


async def _fechar_modal_atencao(driver) -> None:
    """Fecha o modal de atenção/confirmação exibido após enviar a candidatura."""
    try:
        _txt, clicou = await _clicar_botao_smartapply(driver, _BTN_FECHAR_MODAL)
        if clicou:
            print("[GEEK] Modal de atenção fechado")
            await asyncio.sleep(1.5)
    except Exception:
        pass


# ── Instrumentação (fim do chute) ─────────────────────────────────────────────
# O apply do GeekHunter falha por depender de rótulos/frases que não conhecemos do
# DOM real. Antes de mais um "fix" às cegas, este dump captura a VERDADE em UM run:
# URL, inventário de TODOS os botões visíveis (o rótulo do botão final — 'Finalizar'?
# 'Enviar'? — e se é <a>/<button>/type) e dos campos, mais um trecho do HTML. Espelha
# o _linkedin_form_debug.txt. Gate por env; ligado por padrão pra já capturar agora.

def _dump_geek_debug_sync(driver, tag: str = "") -> str:
    import os
    try:
        try:
            url = driver.current_url
        except Exception:
            url = ""
        botoes = []
        for b in driver.find_elements(By.CSS_SELECTOR, "button, [role='button'], a"):
            try:
                if not b.is_displayed():
                    continue
                txt = (b.text or "").strip().replace("\n", " ")
                aria = (b.get_attribute("aria-label") or "").strip()
                tipo = (b.get_attribute("type") or "").strip()
                if not (txt or aria):
                    continue
                botoes.append(f"<{b.tag_name}> type='{tipo}' text='{txt[:70]}' aria='{aria[:40]}'")
            except Exception:
                continue
        campos = []
        for c in driver.find_elements(By.CSS_SELECTOR, "input, textarea, select"):
            try:
                if not c.is_displayed():
                    continue
                campos.append(
                    f"<{c.tag_name}> type='{c.get_attribute('type') or ''}' "
                    f"name='{c.get_attribute('name') or ''}' "
                    f"placeholder='{c.get_attribute('placeholder') or ''}' "
                    f"value='{(c.get_attribute('value') or '')[:30]}'"
                )
            except Exception:
                continue
        try:
            html = driver.page_source
        except Exception:
            html = ""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_geekhunter_form_debug.txt")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n\n========== DUMP [{tag}] url={url} ==========\n")
            f.write("--- BOTÕES VISÍVEIS ---\n" + "\n".join(botoes) + "\n")
            f.write("--- CAMPOS ---\n" + "\n".join(campos) + "\n")
            f.write("--- HTML (8000 chars) ---\n" + (html or "")[:8000])
        print(f"[GEEK] DOM salvo em: {path} (tag={tag})")
        return path
    except Exception as e:
        print(f"[GEEK] _dump_geek_debug falhou: {e}")
        return ""


async def _dump_geek_debug(driver, tag: str = "") -> None:
    """Wrapper async do dump — no-op se GEEK_HUNTER_DEBUG=false. Nunca quebra o fluxo."""
    if os.getenv("GEEK_HUNTER_DEBUG", "true").lower() != "true":
        return
    try:
        await _run_in_thread(lambda: _dump_geek_debug_sync(driver, tag))
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

        # Confirmação de ENVIO (estrita): inclui o modal "confirme sua candidatura pelo
        # e-mail". Pode surgir após um clique anterior mesmo sem 'Finalizar' explícito
        # (ex.: vagas sem perguntas) → fecha o modal pelo X e conclui como enviada.
        if await _geek_confirmacao_sucesso(driver):
            await _fechar_modal_atencao(driver)
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

        # Preenchimento SEQUENCIAL e pausado (o GeekHunter é React e 'se perde' se
        # preenchermos tudo de uma vez): contato → remuneração → currículo →
        # perguntas → consentimento. Cada campo já pausa dentro de _digitar_no_input.
        try:
            await _preencher_contato_geekhunter(driver, perfil)      # nome/email/linkedin + telefone
            await asyncio.sleep(random.uniform(0.6, 1.1))
            await _preencher_remuneracao_geekhunter(driver, perfil)  # CLT/PJ/dólar (por label)
            await asyncio.sleep(random.uniform(0.6, 1.1))
            if curriculo_path:
                await _tratar_curriculo_smartapply(driver, curriculo_path)
                await asyncio.sleep(random.uniform(0.5, 1.0))
            novas = await _responder_perguntas_geekhunter(
                driver, perfil, resumo_curriculo, idioma, vaga_titulo
            )
            for p in novas:
                if p not in perguntas_feitas:
                    perguntas_feitas.append(p)
            await asyncio.sleep(random.uniform(0.5, 1.0))
            await _marcar_consentimentos_geekhunter(driver)          # checkbox LGPD (Chakra)
            await asyncio.sleep(random.uniform(0.5, 1.0))
        except StaleElementReferenceException:
            await asyncio.sleep(1.0)
            continue

        # Assinatura antes de clicar (detecta não-avanço).
        try:
            sig_antes = await _run_in_thread(
                lambda: driver.current_url + "|" + str(len(driver.find_elements(By.CSS_SELECTOR, "input,textarea,select,button")))
            )
        except Exception:
            sig_antes = ""

        # Instrumentação: captura o DOM REAL logo antes de decidir o clique — é aqui
        # que se vê o rótulo do botão final e se o form abriu inline/modal.
        await _dump_geek_debug(driver, f"step{step}-antes-clique")

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

        # Instrumentação: captura o DOM logo após o clique — mostra a tela de
        # confirmação real (as frases de sucesso) ou o próximo step do form.
        await _dump_geek_debug(driver, f"step{step}-apos-clique-{btn_text[:20]}")

        # "Finalizar candidatura" = ENVIO FINAL → candidatura enviada. (Só 'finalizar'
        # pra não confundir com botões intermediários como 'Continuar'/'Candidatar-se'
        # e pular o modal das perguntas.) Fecha o modal de confirmação pelo X e conclui,
        # pra o caller fechar a aba, marcar a vaga como aplicada e ir pra próxima.
        if is_submit and "finalizar" in btn_text.lower():
            await asyncio.sleep(1.5)
            await _fechar_modal_atencao(driver)
            b64 = await screenshot_base64()
            await notify_browser_step(f"geek_step_{step}", "sucesso", "Candidatura enviada!")
            return {"sucesso": True, "perguntas_respondidas": perguntas_feitas,
                    "mensagem": "Candidatura enviada com sucesso no GeekHunter!",
                    "screenshot": b64[:100] if b64 else ""}

        # Confirmação de envio pode aparecer logo após o clique (ex.: modal "confirme
        # pelo e-mail" em vagas sem perguntas) → fecha o X e conclui como enviada.
        if await _geek_confirmacao_sucesso(driver):
            await _fechar_modal_atencao(driver)
            b64 = await screenshot_base64()
            await notify_browser_step(f"geek_step_{step}", "sucesso", "Candidatura enviada!")
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

    resultados = {"sucesso": True, "aplicacoes": [], "falhas": 0}
    aplicadas = 0
    teto = _cont.get_teto()
    pagina = 1
    MAX_PAGINAS = int(os.getenv("GEEK_HUNTER_MAX_PAGINAS", "20"))

    # Loop de PÁGINAS: aplica em todos os cards da página; ao esgotar, vai pra
    # page=2, page=3... até bater max_vagas/teto ou não haver mais vagas.
    while aplicadas < max_vagas and pagina <= MAX_PAGINAS:
        if teto > 0 and _cont.teto_atingido(user_id):
            resultados["mensagem"] = f"Teto de candidaturas atingido ({teto})."
            break
        control = await get_intervention_state()
        if control.get("current_action") == "parar":
            resultados["mensagem"] = "Interrompido pelo usuário."
            break

        await _abrir_busca(driver, query, pagina)
        try:
            janela_busca = await _run_in_thread(lambda: driver.current_window_handle)
        except Exception:
            janela_busca = None
        try:
            n_cards = await _run_in_thread(
                lambda: len(driver.find_elements(By.XPATH, "//p[contains(., 'Visualizar vaga')]"))
            )
        except Exception:
            n_cards = 0
        if n_cards == 0:
            print(f"[GEEK] Página {pagina} sem vagas — encerrando paginação")
            break
        print(f"[GEEK] Página {pagina}: {n_cards} vaga(s)")

        for i in range(n_cards):
            if aplicadas >= max_vagas:
                break
            if teto > 0 and _cont.teto_atingido(user_id):
                resultados["mensagem"] = f"Teto de candidaturas atingido ({teto})."
                break
            control = await get_intervention_state()
            if control.get("current_action") == "parar":
                resultados["mensagem"] = "Interrompido pelo usuário."
                break

            # Re-busca os gatilhos a cada iteração (evita stale após fechar a aba).
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
                break  # acabaram os cards desta página → próxima página

            await notify_browser_step("selenium_geekhunter", "abrindo", f"Abrindo: {titulo[:40]}")
            await asyncio.sleep(2.5)

            # Troca pra aba nova (detalhe) — detecta pela contagem de abas.
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
                aba_nova = None

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

            # SEM gate de match: o GeekHunter já é curado pela palavra-chave da busca —
            # toda vaga listada tem correlação e DEVE ser aplicada (pedido do usuário).
            idioma_vaga = "pt"

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
                    neo4j.registrar_candidatura(user_id=user_id, vaga_id=vaga_url or f"geekhunter-p{pagina}-{i}",
                                                plataforma="geekhunter", status=status)
                except Exception:
                    pass

            await _fechar_aba_e_voltar(driver, aba_nova, janela_busca)
            await asyncio.sleep(random.uniform(1.5, 3.0))

        pagina += 1  # esgotou a página atual → vai para a subsequente

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
