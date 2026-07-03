"""
Automação de candidatura no Portal de Talentos da Senior
(https://www.portaldetalentos.senior.com.br) via Selenium/Firefox.

Criada nos moldes do Gupy (ver automation/gupy_selenium.py): login por e-mail+senha
do .env, busca por palavra-chave do dashboard e aplica card a card, rodando o WIZARD
de candidatura da Senior (passos fixos com "Avançar").

Diferenças importantes em relação ao Gupy/GeekHunter:
- O site é uma SPA **Angular Material** (mat-*). As classes utilitárias e os `_ngcontent`
  são auto-gerados/instáveis → NUNCA ancorar neles. Âncoras estáveis: TEXTO do botão
  ("Entrar", "Candidatar-se", "Avançar", "Acompanhar candidatura"), `formcontrolname`,
  `aria-label` e componentes `mat-select`/`mat-checkbox`.
- É **master-detail na MESMA página**: clicar num card da lista atualiza o painel de
  detalhe à direita (NÃO abre nova aba, diferente de Gupy/GeekHunter). Por isso o loop
  não troca de aba; após aplicar (que navega pra tela "Acompanhar candidatura") ele
  re-navega pra busca e segue.
- `mat-select` renderiza as opções num `.cdk-overlay-container` no NÍVEL DO BODY, não
  como filhas do `<mat-select>` → procurar opção dentro do select acha zero. O
  `mat-checkbox` real é um input `cdk-visually-hidden` → clica-se o `label`/inner-container,
  checando a classe `mat-checkbox-checked` pra idempotência.

Fluxo (informado pelo usuário):
  Entrar (login-btn) → e-mail + senha → submeter
    → busca /search/vacancies?jobFunction=<query>
    → clica o card (título p.customized-card-job-function)
    → lê a descrição; se condizente com o currículo (_avaliar_match_vaga) → Candidatar-se
    → Avançar … Avançar (responde perguntas / mat-select "Como você encontrou a vaga?")
    → marca o checkbox "Li e estou ciente com o termo de uso e privacidade"
    → Avançar final → aparece "Acompanhar candidatura" (= ENVIADO) → clica
  → volta pra busca, próxima vaga.

Regras herdadas do resto do projeto:
- Todo seletor incerto degrada para intervenção manual (_aguardar_resolucao_manual),
  NUNCA falha silenciosa.
- Sucesso é CONSERVADOR (sinal FORTE: botão/tela "Acompanhar candidatura" após pelo
  menos um "Avançar" real) — evita o falso-sucesso que envenena o dedup (lição do
  GeekHunter, ver automation/_geekhunter_form_debug.txt).
"""

import asyncio
import logging
import os
import random
import re
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
# detecção de bloqueio, espera de intervenção manual, resumo do currículo, match vaga×CV.
from automation.indeed_selenium import (
    _clicar_botao_smartapply,
    _pagina_bloqueada,
    _smartapply_bloqueado,
    _aguardar_resolucao_manual,
    _get_resumo_curriculo,
    _avaliar_match_vaga,
)
from automation.form_filler import responder_pergunta
from automation.contador_aplicacoes import SENIOR as _cont

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import StaleElementReferenceException

logger = logging.getLogger(__name__)


# ── Config via .env ──────────────────────────────────────────────────────────

_BASE = "https://www.portaldetalentos.senior.com.br"
# A busca aceita a palavra-chave em ?jobFunction=<q> (igual GeekHunter/Gupy).
_LOGIN = f"{_BASE}/login"


def _get_email() -> str:
    return os.getenv("SENIOR_EMAIL", "")


def _get_password() -> str:
    return os.getenv("SENIOR_PASSWORD", "")


def _get_query_padrao() -> str:
    return os.getenv("SENIOR_QUERY", "desenvolvedor")


def _build_search_url(query: str = "") -> str:
    """URL da busca do portal com a palavra-chave (do dashboard).
    Formato informado pelo usuário: /search/vacancies?jobFunction=<q>."""
    q = (query or "").strip() or _get_query_padrao()
    return f"{_BASE}/search/vacancies?jobFunction={quote_plus(q)}"


# Botões do wizard (Angular Material — classes instáveis → por TEXTO/aria-label).
# "Candidatar-se" abre o formulário de candidatura no detalhe.
_BTN_CANDIDATAR = [
    "#applyToVacancy button",
    "button.apply-to-candidature-button",
    'button:has-text("Candidatar-se")',
    'button:has-text("Candidatar")',
]
# "Avançar" = passo a passo do wizard. É `button.primary-button.default`, mas
# "Acompanhar candidatura" também é `.primary-button` → NUNCA casar por `.primary-button`
# sozinho; só por TEXTO "Avançar" (+ variações defensivas).
_BTN_AVANCAR = [
    'button:has-text("Avançar")',
    'button:has-text("Continuar")',
    'button:has-text("Próximo")',
    'button:has-text("Próxima")',
    'button:has-text("Enviar")',
    'button:has-text("Finalizar")',
]
# "Acompanhar candidatura" = tela pós-envio (SINAL FORTE de sucesso). É
# `button.primary-button.buttons`.
_BTN_ACOMPANHAR = [
    'button:has-text("Acompanhar candidatura")',
    'button:has-text("Acompanhar")',
]

# Frases que SÓ existem APÓS o envio (conservador — nada genérico nem "acompanhar",
# que pode ser um item de menu persistente 'Acompanhar candidaturas' e dar falso-sucesso
# que envenena o dedup; ver lição do GeekHunter em _geekhunter_form_debug.txt).
_FRASES_SUCESSO_SENIOR = (
    "candidatura realizada",
    "candidatura foi realizada",
    "candidatura enviada com sucesso",
    "sua candidatura foi enviada",
    "inscrição realizada",
    "recebemos sua candidatura",
    "application submitted",
)


# ── Login ────────────────────────────────────────────────────────────────────

def _esta_logado(url: str, html: str = "") -> bool:
    """Heurística de login pela página ATUAL da busca. Fail-open (o login do .env é
    tratado quando a tela/botão de login aparece). NÃO ancora na CLASSE 'login-btn' do
    HTML — Angular inlina os estilos compilados (.login-btn{...}) num <style>, então a
    string estaria SEMPRE presente e nunca confiaríamos na sessão; a detecção de deslogado
    é por ELEMENTO VISÍVEL em _botao_login_visivel(driver)."""
    u = (url or "").lower()
    if "/login" in u or "/signin" in u or "/auth" in u:
        return False
    h = (html or "").lower()
    for marca in ("sair", "logout", "minha conta", "meu perfil", "minhas candidaturas",
                  "sign out"):
        if marca in h:
            return True
    return True


async def _botao_login_visivel(driver) -> bool:
    """True se há um botão "Entrar" (login-btn) VISÍVEL — marcador de DESLOGADO
    (análogo ao 'usuário deslogado' do Gupy), checado por elemento renderizado, não por
    substring de classe no page_source."""
    def _check():
        for el in driver.find_elements(By.CSS_SELECTOR,
                                       "button.login-btn, button, a"):
            try:
                if not el.is_displayed():
                    continue
                t = (el.text or el.get_attribute("aria-label") or "").strip().lower()
                cls = el.get_attribute("class") or ""
                if "login-btn" in cls or t == "entrar" or t.startswith("entrar,"):
                    return True
            except Exception:
                continue
        return False
    try:
        return await _run_in_thread(_check)
    except Exception:
        return False


async def _pagina_de_login_senior(driver) -> bool:
    """A tela ATUAL é de login? (URL /login|/signin OU campo de e-mail/senha visível)."""
    def _tem_form():
        try:
            campos = driver.find_elements(
                By.CSS_SELECTOR,
                "input[formcontrolname='inputUsernameEmail'], input[name='inputUsernameEmail'], "
                "input[type='password'], input[autocomplete='current-password']")
            return any(e.is_displayed() for e in campos)
        except Exception:
            return False
    try:
        u = (await _run_in_thread(lambda: driver.current_url) or "").lower()
    except Exception:
        u = ""
    if "/login" in u or "/signin" in u or "/auth" in u:
        return True
    try:
        return await _run_in_thread(_tem_form)
    except Exception:
        return False


def _tem_campo(driver, css: str) -> bool:
    try:
        return any(e.is_displayed() for e in driver.find_elements(By.CSS_SELECTOR, css))
    except Exception:
        return False


async def _tratar_login_senior(driver) -> bool:
    """Se a tela ATUAL é de login, preenche e-mail+senha do .env e submete. Trata tanto
    o formato de 1 tela (e-mail+senha juntos) quanto o de 2 telas (e-mail → Avançar →
    senha). Retorna True se detectou/tratou um login (mesmo caindo em manual)."""
    if not await _pagina_de_login_senior(driver):
        return False

    email, senha = _get_email(), _get_password()
    try:
        u = await _run_in_thread(lambda: driver.current_url)
    except Exception:
        u = ""
    print(f"[SENIOR] Tela de login detectada ({(u or '')[:70]})")
    if not (email and senha):
        await notify_browser_step("senior_login", "manual",
                                  "Defina SENIOR_EMAIL/PASSWORD no .env ou logue à mão")
        await _aguardar_resolucao_manual(driver, "login manual Senior")
        return True

    await notify_browser_step("senior_login", "login", "Preenchendo login da Senior")

    _SEL_EMAIL = ("input[formcontrolname='inputUsernameEmail'], input[name='inputUsernameEmail'], "
                  "input[type='email'], input[placeholder*='mail' i]")
    _SEL_SENHA = ("input[formcontrolname='inputPassword'], input[type='password'], "
                  "input[name='inputPassword'], input[autocomplete='current-password']")

    # 1) E-mail.
    preencheu_email = await digitar_robusto(_SEL_EMAIL, email)

    # 2) Senha. Se o campo de senha ainda não está na tela (login em 2 etapas), avança.
    if not await _run_in_thread(lambda: _tem_campo(driver, _SEL_SENHA)):
        await _clicar_botao_smartapply(driver, [
            'button:has-text("Avançar")', 'button:has-text("Continuar")',
            'button:has-text("Próximo")', 'button:has-text("Entrar")',
        ])
        await asyncio.sleep(2)
    preencheu_senha = await digitar_robusto(_SEL_SENHA, senha)

    if not (preencheu_email and preencheu_senha):
        print("[SENIOR] Campos de login não preenchidos — intervenção manual")
        await _aguardar_resolucao_manual(driver, "login manual Senior")
        return True

    await asyncio.sleep(0.6)
    # 3) Submeter: RETURN na senha (mais confiável) e, se seguir na tela, botão "Entrar".
    def _enter():
        try:
            el = driver.find_element(By.CSS_SELECTOR, _SEL_SENHA)
            el.send_keys(Keys.RETURN)
            return True
        except Exception:
            return False
    try:
        await _run_in_thread(_enter)
    except Exception:
        pass
    await asyncio.sleep(2)
    if await _pagina_de_login_senior(driver):
        await _clicar_botao_smartapply(driver, [
            'button:has-text("Entrar")', 'button:has-text("Acessar")',
            'button:has-text("Login")', 'button[type="submit"]',
        ])
    await asyncio.sleep(4)

    # Verificação/CAPTCHA ou ainda no login → manual.
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, html = "", ""
    if _pagina_bloqueada(cur, await get_title(), html) or await _pagina_de_login_senior(driver):
        await notify_browser_step("senior_login", "manual",
                                  "🔒 Verificação/senha — conclua no browser e clique 🔄 Retomar Auto")
        await _aguardar_resolucao_manual(driver, "verificação de login Senior")
    return True


async def _garantir_login() -> bool:
    """Garante sessão logada. Navega pra busca; se cair num login, preenche pelo .env
    (perfil persistente pode já ter sessão → busca carrega logada). CAPTCHA → manual."""
    driver = await get_driver()
    if not driver:
        await nova_pagina(_build_search_url(), reutilizar=False)
        await asyncio.sleep(2)
        driver = await get_driver()
    if not driver:
        print("[SENIOR] ERRO: driver é None")
        return False

    try:
        await navegar(_build_search_url())
        await asyncio.sleep(2.5)
        cur = await _run_in_thread(lambda: driver.current_url)
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, html = "", ""
    if _pagina_bloqueada(cur, await get_title(), html):
        if not await _aguardar_resolucao_manual(driver, "acesso à Senior"):
            return False
        try:
            cur = await _run_in_thread(lambda: driver.current_url)
            html = await _run_in_thread(lambda: driver.page_source)
        except Exception:
            pass
    if (_esta_logado(cur, html) and not await _pagina_de_login_senior(driver)
            and not await _botao_login_visivel(driver)):
        print("[SENIOR] Já está logado")
        return True

    # Não logado → clica "Entrar" (login-btn) pra revelar o form, depois trata o login.
    print("[SENIOR] Não logado — abrindo login")
    await _clicar_botao_smartapply(driver, [
        "button.login-btn", 'button:has-text("Entrar")', 'a:has-text("Entrar")',
    ])
    await asyncio.sleep(2.5)
    if not await _tratar_login_senior(driver):
        try:
            await navegar(_LOGIN)
            await asyncio.sleep(3)
        except Exception:
            pass
        await _tratar_login_senior(driver)

    try:
        await navegar(_build_search_url())
        await asyncio.sleep(2.5)
        cur = await _run_in_thread(lambda: driver.current_url)
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, html = "", ""
    logado = (_esta_logado(cur, html) and not await _pagina_de_login_senior(driver)
              and not await _botao_login_visivel(driver))
    if logado:
        await notify_browser_step("senior_login", "sucesso", "Login concluído")
    return logado


# ── Busca / cards ─────────────────────────────────────────────────────────────

# Seletores candidatos do card/título de uma vaga na lista da busca. O título é o
# `p.customized-card-job-function` fornecido pelo usuário; o card clicável é ele ou o
# seu ancestral. CHUTES a confirmar no dump — degradam pra 0 cards → "sem vagas".
_CARD_SELECTORS = (
    "p.customized-card-job-function",
    "app-vacancy-card p.customized-card-job-function",
    "[class*='customized-card-job-function']",
)


def _achar_cards(driver):
    """Elementos de card de vaga (títulos clicáveis) na lista da busca. Best-effort."""
    for sel in _CARD_SELECTORS:
        try:
            els = [e for e in driver.find_elements(By.CSS_SELECTOR, sel) if e.is_displayed()]
            if els:
                return els
        except Exception:
            continue
    return []


async def _abrir_busca(driver, query: str = "") -> None:
    """Navega para a busca do portal com a palavra-chave (do dashboard)."""
    url = _build_search_url(query)
    print(f"[SENIOR] Abrindo busca → {url}")
    await notify_browser_step("senior_busca", "navegando", f"Buscando: {query or _get_query_padrao()}")
    await navegar(url)
    await asyncio.sleep(3)
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, html = "", ""
    if _pagina_bloqueada(cur, await get_title(), html):
        await _aguardar_resolucao_manual(driver, "busca na Senior")
    # Login pode ter voltado à tona ao navegar pra busca.
    if await _pagina_de_login_senior(driver):
        await _tratar_login_senior(driver)
        await navegar(url)
        await asyncio.sleep(2.5)
    # Scroll pra carregar mais cards (lazy-load).
    def _scroll():
        import time as _t
        last = driver.execute_script("return document.body.scrollHeight")
        for _ in range(5):
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
    try:
        n = await _run_in_thread(lambda: len(_achar_cards(driver)))
        cur2 = await _run_in_thread(lambda: driver.current_url)
        print(f"[SENIOR] busca: {n} card(s) | URL: {cur2}")
    except Exception:
        pass
    await _dump_senior_debug(driver, "busca")


async def extrair_vagas_da_busca(perfil: dict, max_vagas: int = 100, query: str = "") -> dict:
    """Extrai títulos dos cards da busca — usado pelo dashboard pra listar vagas.
    A SPA não expõe URL por vaga → id/url ficam com o código quando disponível."""
    set_platform("senior")
    if not await _garantir_login():
        print("[SENIOR] Login não confirmado — extraindo mesmo assim")
    driver = await get_driver()
    await _abrir_busca(driver, query)

    def _coletar():
        vagas = []
        for el in _achar_cards(driver)[:max_vagas]:
            try:
                titulo = (el.text or "").strip().split("\n")[0]
                if titulo:
                    vagas.append({"id": titulo, "titulo": titulo, "empresa": "",
                                  "url": "", "fonte": "Senior"})
            except Exception:
                continue
        return vagas

    try:
        vagas = await _run_in_thread(_coletar)
    except Exception as e:
        logger.warning("senior extrair vagas erro: %s", e)
        vagas = []
    return {"sucesso": True, "vagas": vagas, "mensagem": f"{len(vagas)} vaga(s) na Senior."}


# ── Detalhe da vaga ───────────────────────────────────────────────────────────

def _tem_elemento(driver, css: str) -> bool:
    try:
        return any(e.is_displayed() for e in driver.find_elements(By.CSS_SELECTOR, css))
    except Exception:
        return False


async def _extrair_descricao_detalhe(driver) -> str:
    """Texto do detalhe da vaga (pro match vaga×CV e o filtro modalidade/região)."""
    def _txt():
        # Preferir o corpo da descrição; cair pro body inteiro.
        for sel in ("app-vacancy-detail-card-description", ".vacancy-detail-container",
                    ".personal-settings-container"):
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                txt = " ".join((e.text or "") for e in els if e.is_displayed()).strip()
                if len(txt) > 80:
                    return txt[:4000]
            except Exception:
                continue
        try:
            return (driver.find_element(By.TAG_NAME, "body").text or "")[:4000]
        except Exception:
            return ""
    try:
        return await _run_in_thread(_txt)
    except Exception:
        return ""


async def _extrair_codigo_vaga(driver) -> str:
    """Código estável da vaga (ex.: 'Cód 6096' → 'senior-6096'), usado como chave de
    dedup — a SPA pode NÃO mudar a URL por vaga (confirmar no dump). '' se não achar."""
    def _cod():
        # Tenta o texto do header ("Publicado em ... Cód 6096").
        for sel in ("app-vacancy-detail-card-header-information", ".vacancy-detail-container",
                    ".header"):
            try:
                for e in driver.find_elements(By.CSS_SELECTOR, sel):
                    if not e.is_displayed():
                        continue
                    m = re.search(r"[Cc][óo]d\.?\s*(\d+)", e.text or "")
                    if m:
                        return m.group(1)
            except Exception:
                continue
        try:
            m = re.search(r"[Cc][óo]d\.?\s*(\d+)", driver.find_element(By.TAG_NAME, "body").text or "")
            return m.group(1) if m else ""
        except Exception:
            return ""
    try:
        cod = await _run_in_thread(_cod)
    except Exception:
        cod = ""
    return f"senior-{cod}" if cod else ""


# ── Widgets Angular Material ───────────────────────────────────────────────────

async def _responder_mat_selects_senior(driver, perfil: dict, resumo_curriculo: str,
                                         idioma: str, vaga_titulo: str) -> None:
    """Preenche os `mat-select` ainda vazios (ex.: 'Como você encontrou a vaga?').

    GOTCHA do Material: ao clicar o trigger, as opções renderizam num
    `.cdk-overlay-container` no NÍVEL DO BODY — não como filhas do `<mat-select>`.
    IA escolhe (SELECT:) pela label; fallback = 1ª opção (fail-open, é opcional)."""
    def _selects_vazios():
        alvos = []
        for sel in driver.find_elements(By.CSS_SELECTOR, "mat-select"):
            try:
                if not sel.is_displayed():
                    continue
                # Vazio = ainda mostra o placeholder (classe mat-select-empty).
                cls = sel.get_attribute("class") or ""
                if "mat-select-empty" not in cls:
                    continue
                alvos.append(sel)
            except Exception:
                continue
        return alvos

    try:
        n = await _run_in_thread(lambda: len(_selects_vazios()))
    except Exception:
        n = 0
    for idx in range(n):
        # Rótulo do select (o <mat-label> associado).
        def _label(i=idx):
            selects = _selects_vazios()
            if i >= len(selects):
                return "", None
            s = selects[i]
            texto = ""
            try:
                sid = s.get_attribute("id") or ""
                if sid:
                    lbl = driver.find_elements(By.CSS_SELECTOR, f"label[for='{sid}']")
                    if lbl:
                        texto = (lbl[0].text or "").strip()
            except Exception:
                pass
            if not texto:
                texto = (s.get_attribute("aria-label") or "").strip()
            return texto, s
        try:
            label, _ = await _run_in_thread(_label)
        except Exception:
            label = ""

        # Abre o overlay e lê as opções (no body, não dentro do select).
        def _abrir(i=idx):
            selects = _selects_vazios()
            if i >= len(selects):
                return []
            s = selects[i]
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", s)
                try:
                    s.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", s)
            except Exception:
                return []
            import time as _t
            _t.sleep(0.8)
            opts = []
            for o in driver.find_elements(
                    By.CSS_SELECTOR,
                    ".cdk-overlay-container mat-option, .cdk-overlay-container [role='option']"):
                try:
                    if o.is_displayed():
                        opts.append((o.text or "").strip())
                except Exception:
                    continue
            return [t for t in opts if t]
        try:
            opcoes = await _run_in_thread(_abrir)
        except Exception:
            opcoes = []
        if not opcoes:
            # Sem opções legíveis → fecha e segue (é opcional; não bloqueia).
            def _fechar():
                try:
                    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                except Exception:
                    pass
            try:
                await _run_in_thread(_fechar)
            except Exception:
                pass
            continue

        pergunta = "SELECT:" + (label or "Selecione uma opção") + ":" + ";".join(opcoes[:15])
        try:
            escolha = responder_pergunta(pergunta, perfil, vaga_titulo=vaga_titulo, vaga_empresa="",
                                         resumo_curriculo=resumo_curriculo, idioma=idioma)
        except Exception as e:
            logger.warning("responder_pergunta (senior mat-select) erro: %s", e)
            escolha = ""
        escolha = (escolha or "").strip().lower()
        alvo = next((o for o in opcoes if o.lower() == escolha), None) \
            or next((o for o in opcoes if escolha and escolha in o.lower()), None) \
            or opcoes[0]

        def _clicar_opcao(txt=alvo):
            for o in driver.find_elements(
                    By.CSS_SELECTOR,
                    ".cdk-overlay-container mat-option, .cdk-overlay-container [role='option']"):
                try:
                    if o.is_displayed() and (o.text or "").strip().lower() == txt.lower():
                        try:
                            o.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", o)
                        return True
                except Exception:
                    continue
            # Fallback: 1ª opção visível.
            for o in driver.find_elements(By.CSS_SELECTOR, ".cdk-overlay-container mat-option"):
                try:
                    if o.is_displayed():
                        driver.execute_script("arguments[0].click();", o)
                        return True
                except Exception:
                    continue
            return False
        try:
            if await _run_in_thread(_clicar_opcao):
                print(f"[SENIOR] mat-select '{(label or '')[:30]}' = {alvo[:25]}")
                await asyncio.sleep(0.4)
        except Exception:
            pass


# Palavras que identificam um checkbox de CONSENTIMENTO/termo (marcar OK). Outros
# checkboxes (PcD, autodeclaração, opt-in de dados) NÃO devem ser marcados no escuro —
# seria uma submissão factualmente errada; degradam pra manual/perguntas.
_CONSENT_KW = ("termo de uso", "termos de uso", "ciente", "li e estou", "li e concordo",
               "privacidade", "política de privacidade", "concordo com", "aceito os termos")


async def _marcar_consentimento_senior(driver) -> bool:
    """Marca o `mat-checkbox` de CONSENTIMENTO ('Li e estou ciente com o termo de uso e
    privacidade'). Só marca checkboxes cujo rótulo casa `_CONSENT_KW` — NÃO tica outros
    checkboxes (PcD/autodeclaração/opt-in), que seriam resposta errada. Clica o
    `label.mat-checkbox-layout` (o input é `cdk-visually-hidden`) só se AINDA não marcado
    (classe mat-checkbox-checked no host, idempotente). Roda ANTES do Avançar final (o
    submit fica desabilitado sem o consentimento)."""
    def _marca():
        marcou = False
        for cb in driver.find_elements(By.CSS_SELECTOR, "mat-checkbox"):
            try:
                if not cb.is_displayed():
                    continue
                cls = cb.get_attribute("class") or ""
                if "mat-checkbox-checked" in cls:
                    continue
                rotulo = (cb.text or cb.get_attribute("aria-label") or "").lower()
                if not any(k in rotulo for k in _CONSENT_KW):
                    continue  # não é consentimento → não marca
                alvo = None
                for sub in ("label.mat-checkbox-layout", ".mat-checkbox-inner-container"):
                    els = cb.find_elements(By.CSS_SELECTOR, sub)
                    if els:
                        alvo = els[0]
                        break
                if alvo is None:
                    alvo = cb
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", alvo)
                    alvo.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", alvo)
                marcou = True
            except Exception:
                continue
        return marcou
    try:
        if await _run_in_thread(_marca):
            print("[SENIOR] Consentimento marcado")
            await asyncio.sleep(0.4)
            return True
    except Exception as e:
        logger.warning("senior _marcar_consentimento erro: %s", e)
    return False


async def _responder_perguntas_senior(driver, perfil: dict, resumo_curriculo: str,
                                      idioma: str, vaga_titulo: str) -> list:
    """Responde campos de texto/número livres do wizard ainda vazios (perguntas
    customizadas da vaga). DOM não visto → best-effort + instrumentação; radios/selects
    ficam com handlers dedicados. Não bloqueia: sem campo, no-op."""
    feitas = []

    def _coletar():
        campos = []
        for el in driver.find_elements(
                By.CSS_SELECTOR,
                "textarea, input[type='text']:not([formcontrolname='inputUsernameEmail']), "
                "input[type='number']"):
            try:
                if not el.is_displayed() or not el.is_enabled():
                    continue
                if (el.get_attribute("value") or "").strip():
                    continue  # já preenchido
                # Rótulo: mat-label associado por id, aria-label, ou <label> ancestral.
                label = ""
                eid = el.get_attribute("id") or ""
                if eid:
                    lbls = driver.find_elements(By.CSS_SELECTOR, f"label[for='{eid}']")
                    if lbls:
                        label = (lbls[0].text or "").strip()
                if not label:
                    label = (el.get_attribute("aria-label")
                             or el.get_attribute("placeholder") or "").strip()
                if not label:
                    try:
                        wrap = el.find_element(By.XPATH, "./ancestor::mat-form-field[1]")
                        label = (wrap.text or "").strip()
                    except Exception:
                        pass
                campos.append({"id": eid, "name": el.get_attribute("name") or "",
                               "label": label, "tipo": el.get_attribute("type") or "text"})
            except Exception:
                continue
        return campos

    try:
        campos = await _run_in_thread(_coletar)
    except Exception as e:
        logger.warning("senior _coletar_perguntas erro: %s", e)
        return feitas

    # Rótulos de busca/filtro que NÃO são perguntas do formulário — não preencher (evita
    # corromper a barra de busca se ela ainda estiver no DOM sob o wizard). Remuneração
    # É pergunta legítima (responder_pergunta puxa da config) → NÃO entra aqui.
    _NAO_PERGUNTA = ("cargo", "buscar", "pesquis", "palavra-chave", "jobfunction",
                     "filtrar", "filtro")
    for c in campos:
        label = c["label"] or c["name"] or c["id"]
        if not label:
            continue
        if any(k in label.lower() for k in _NAO_PERGUNTA):
            continue
        pref = "NUMERO:" if c["tipo"] == "number" else ""
        try:
            resp = responder_pergunta(pref + label, perfil, vaga_titulo=vaga_titulo,
                                      vaga_empresa="", resumo_curriculo=resumo_curriculo,
                                      idioma=idioma)
        except Exception as e:
            logger.warning("responder_pergunta (senior texto) erro: %s", e)
            from automation.form_filler import resposta_segura
            resp = resposta_segura(label, idioma)

        def _fill(c=c, resp=resp):
            el = None
            if c.get("id"):
                try:
                    el = driver.find_element(By.ID, c["id"])
                except Exception:
                    el = None
            if el is None and c.get("name"):
                try:
                    el = driver.find_element(By.CSS_SELECTOR, f"[name='{c['name']}']")
                except Exception:
                    el = None
            if el is None:
                return False
            try:
                el.click()
            except Exception:
                pass
            try:
                el.clear()
            except Exception:
                pass
            try:
                el.send_keys(str(resp))
            except Exception:
                return False
            # Angular controla o campo → dispara input/change/blur pra assentar o valor.
            try:
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
                    "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));"
                    "arguments[0].dispatchEvent(new Event('blur',{bubbles:true}));", el)
            except Exception:
                pass
            return True
        try:
            if await _run_in_thread(_fill):
                print(f"[SENIOR] Pergunta '{label[:35]}' respondida")
                feitas.append(label)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(0.4, 0.9))
    return feitas


# Rótulos dos botões que indicam que o WIZARD ainda está em curso (form aberto). Sua
# AUSÊNCIA é o sinal estrutural de que já saímos do formulário (enviou).
_CONTROLES_APPLY = ("candidatar", "avançar", "avancar", "continuar", "próximo",
                    "proximo", "enviar", "finalizar")


async def _controles_apply_presentes(driver) -> bool:
    """True se ainda há botão visível de 'Candidatar-se'/'Avançar'/'Enviar'/... (wizard
    em curso). Na dúvida retorna True (não declara sucesso)."""
    def _check():
        for el in driver.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
            try:
                if not el.is_displayed():
                    continue
                t = (el.text or el.get_attribute("aria-label") or "").lower()
                if any(a in t for a in _CONTROLES_APPLY):
                    return True
            except Exception:
                continue
        return False
    try:
        return await _run_in_thread(_check)
    except Exception:
        return True


async def _acompanhar_ou_frase(driver) -> bool:
    """True se há o botão 'Acompanhar candidatura' visível OU uma frase que SÓ existe
    pós-envio no page_source."""
    def _check():
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
                try:
                    if el.is_displayed() and "acompanhar candidatura" in (
                            (el.text or "") + " " + (el.get_attribute("aria-label") or "")).lower():
                        return True
                except Exception:
                    continue
            html = (driver.page_source or "").lower()
            return any(f in html for f in _FRASES_SUCESSO_SENIOR)
        except Exception:
            return False
    try:
        return await _run_in_thread(_check)
    except Exception:
        return False


async def _sucesso_estrutural_senior(driver) -> bool:
    """Sucesso ESTRUTURAL (não depende só de string, que envenenaria o dedup): o
    formulário SAIU (nenhum 'Candidatar-se'/'Avançar'/'Enviar' visível) E há sinal de
    pós-envio ('Acompanhar candidatura' / frase submit-only). Exige ESTABILIDADE em 2
    checagens ~1.5s pra descartar a janela transitória de loading logo após um clique
    (o falso-positivo transitório que mordeu o GeekHunter). Um menu persistente
    'Acompanhar candidaturas' NÃO fura isto: durante o wizard sempre há 'Avançar'
    visível → controles_presentes=True → não declara sucesso."""
    if await _controles_apply_presentes(driver):
        return False
    if not await _acompanhar_ou_frase(driver):
        return False
    await asyncio.sleep(1.5)
    if await _controles_apply_presentes(driver):
        return False
    return await _acompanhar_ou_frase(driver)


# ── Instrumentação (debug do 1º run supervisionado) ───────────────────────────

_SENIOR_DEBUG = os.getenv("SENIOR_DEBUG", "true").lower() == "true"
_SENIOR_DEBUG_PATH = os.path.join(os.path.dirname(__file__), "_senior_form_debug.txt")


async def _dump_senior_debug(driver, tag: str = "") -> None:
    """Grava URL + botões visíveis + campos + HTML (append) pra diagnosticar o wizard no
    1º run. Gate por SENIOR_DEBUG. Best-effort, nunca quebra o fluxo."""
    if not _SENIOR_DEBUG:
        return

    def _dump():
        try:
            url = driver.current_url
            botoes = []
            for b in driver.find_elements(By.CSS_SELECTOR, "button, [role='button'], a"):
                try:
                    if b.is_displayed():
                        t = (b.text or b.get_attribute("aria-label") or "").strip()
                        if t:
                            botoes.append(t[:50])
                except Exception:
                    continue
            campos = []
            for c in driver.find_elements(
                    By.CSS_SELECTOR, "input, textarea, mat-select, mat-checkbox, mat-label, "
                    "p.customized-card-job-function"):
                try:
                    if c.is_displayed():
                        desc = (c.get_attribute("formcontrolname") or c.get_attribute("aria-label")
                                or c.get_attribute("name") or (c.text or "")[:40])
                        campos.append(f"{c.tag_name}:{desc}"[:70])
                except Exception:
                    continue
            html = driver.page_source[:20000]
            with open(_SENIOR_DEBUG_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n\n===== {tag} =====\nURL: {url}\n"
                        f"BOTÕES: {botoes}\nCAMPOS: {campos}\n--- HTML(20k) ---\n{html}\n")
        except Exception:
            pass
    try:
        await _run_in_thread(_dump)
    except Exception:
        pass


# ── Wizard de candidatura ─────────────────────────────────────────────────────

async def _preencher_e_enviar_formulario(driver, perfil: dict, resumo_curriculo: str,
                                         idioma: str, vaga_titulo: str, vaga_url: str) -> dict:
    """Roda o wizard de candidatura da Senior no detalhe da vaga. Passos fixos com
    'Avançar'; cada miss degrada para manual. Sucesso só com sinal FORTE (tela
    'Acompanhar candidatura' APÓS pelo menos um 'Avançar' real) — nunca marca no escuro."""
    # 1) Candidatar-se (abre o formulário de candidatura).
    _, clicou = await _clicar_botao_smartapply(driver, _BTN_CANDIDATAR)
    if not clicou:
        await notify_browser_step("senior_apply", "manual", "Não achei 'Candidatar-se' — controle manual")
        if not await _aguardar_resolucao_manual(driver, "abrir candidatura Senior"):
            return {"sucesso": False, "motivo_falha": "sem_candidatar",
                    "mensagem": f"Não abriu a candidatura. Candidate-se à mão: {vaga_titulo}"}
    await asyncio.sleep(2.5)
    await _dump_senior_debug(driver, "apos-candidatar")

    perguntas_feitas = []
    max_steps = 15
    nao_avancou = 0
    avancou_alguma_vez = False  # só aceita 'Acompanhar' como sucesso após um Avançar real

    for step in range(max_steps):
        control = await get_intervention_state()
        if control.get("current_action") == "parar":
            return {"sucesso": False, "motivo_falha": "parado", "mensagem": "Interrompido pelo usuário."}

        await asyncio.sleep(random.uniform(0.6, 1.2))
        await notify_browser_step(f"senior_step_{step}", "preenchendo", "Preenchendo candidatura Senior")

        # Sucesso ESTRUTURAL: só APÓS um Avançar real E o form ter saído (nenhum
        # 'Candidatar-se'/'Avançar' visível) + sinal pós-envio, estável em 2 checagens.
        # Isto evita o falso-sucesso que envenena o dedup (lição do GeekHunter).
        if avancou_alguma_vez and await _sucesso_estrutural_senior(driver):
            await _clicar_botao_smartapply(driver, _BTN_ACOMPANHAR)  # navega pro tracking
            b64 = await screenshot_base64()
            await notify_browser_step(f"senior_step_{step}", "sucesso", "Candidatura enviada!")
            return {"sucesso": True, "perguntas_respondidas": perguntas_feitas,
                    "mensagem": "Candidatura enviada com sucesso na Senior!",
                    "screenshot": b64[:100] if b64 else ""}

        # CAPTCHA/verificação → manual.
        if await _smartapply_bloqueado(driver):
            await notify_browser_step(f"senior_step_{step}", "manual",
                                      "🔒 Verificação — resolva no browser e clique 🔄 Retomar Auto")
            if not await _aguardar_resolucao_manual(driver, f"verificação Senior step {step}"):
                return {"sucesso": False, "motivo_falha": "captcha",
                        "mensagem": f"Verificação não resolvida. Candidate-se à mão: {vaga_titulo}"}
            continue

        # Login pode surgir no meio (sessão expirada).
        if await _tratar_login_senior(driver):
            await asyncio.sleep(1.5)
            await _clicar_botao_smartapply(driver, _BTN_CANDIDATAR)
            await asyncio.sleep(2)
            continue

        # Preenche o que estiver na tela (idempotente — só mexe no que existe/está vazio).
        try:
            novas = await _responder_perguntas_senior(driver, perfil, resumo_curriculo, idioma, vaga_titulo)
            for p in novas:
                if p not in perguntas_feitas:
                    perguntas_feitas.append(p)
            await _responder_mat_selects_senior(driver, perfil, resumo_curriculo, idioma, vaga_titulo)
            # Consentimento ANTES de avançar (o submit fica desabilitado sem ele).
            await _marcar_consentimento_senior(driver)
        except StaleElementReferenceException:
            await asyncio.sleep(1.0)
            continue

        # Assinatura antes de clicar (detecta não-avanço).
        try:
            sig_antes = await _run_in_thread(
                lambda: driver.current_url + "|" + str(len(driver.find_elements(
                    By.CSS_SELECTOR, "input,textarea,button,mat-select,mat-checkbox"))))
        except Exception:
            sig_antes = ""

        await _dump_senior_debug(driver, f"step{step}-antes-clique")

        # Avançar (passo do wizard; o último Avançar É o envio).
        btn_text, clicou = await _clicar_botao_smartapply(driver, _BTN_AVANCAR)
        if not clicou:
            await notify_browser_step(f"senior_step_{step}", "manual",
                                      "Não achei 'Avançar'/'Finalizar' — controle manual")
            if not await _aguardar_resolucao_manual(driver, f"formulário Senior step {step}"):
                return {"sucesso": False, "motivo_falha": "formulario_incompleto",
                        "mensagem": f"Formulário não concluído. Candidate-se à mão: {vaga_titulo}"}
            continue

        avancou_alguma_vez = True
        print(f"[SENIOR] Step {step}: clicou '{btn_text[:40]}'")
        await asyncio.sleep(2.5)
        await _dump_senior_debug(driver, f"step{step}-apos-{btn_text[:15]}")

        # Detecta avanço.
        try:
            sig_depois = await _run_in_thread(
                lambda: driver.current_url + "|" + str(len(driver.find_elements(
                    By.CSS_SELECTOR, "input,textarea,button,mat-select,mat-checkbox"))))
        except Exception:
            sig_depois = ""
        if sig_antes and sig_antes != sig_depois:
            nao_avancou = 0
            continue
        nao_avancou += 1
        if nao_avancou >= 3:
            await notify_browser_step(f"senior_step_{step}", "manual", "Formulário travou — controle manual")
            if not await _aguardar_resolucao_manual(driver, f"formulário travado Senior step {step}"):
                return {"sucesso": False, "motivo_falha": "formulario_travado",
                        "mensagem": f"Formulário travou. Candidate-se à mão: {vaga_titulo}"}
            nao_avancou = 0

    return {"sucesso": False, "motivo_falha": "formulario_incompleto",
            "mensagem": f"Não consegui concluir o formulário. Candidate-se à mão: {vaga_titulo}"}


# ── Loop: aplicar card a card (master-detail, mesma página) ────────────────────

async def aplicar_vagas_visiveis_na_pagina(perfil: dict, max_vagas: int = 5, user_id: str = "admin",
                                           query: str = "") -> dict:
    """Login → busca → para cada card: clica (atualiza o detalhe na MESMA página) → lê a
    descrição → dedup (código) → match vaga×CV → modalidade/região → wizard → volta pra
    busca → próximo. `query` = palavra-chave do dashboard.

    A SPA é master-detail (sem nova aba). Dedup pelo CÓDIGO da vaga ('Cód NNNN') — a URL
    pode não mudar por vaga. `processados` (in-memory) + Neo4j evitam re-aplicar. CAVEAT
    a confirmar no 1º run: se aplicar REMOVE o card da lista, o índice desloca — por isso
    re-navegamos e pulamos os códigos já processados a cada iteração."""
    set_platform("senior")
    try:
        from graph.neo4j_client import get_neo4j
        neo4j = get_neo4j()
    except Exception:
        neo4j = None

    resumo_curriculo = perfil.get("resumo_curriculo", "") or await _get_resumo_curriculo(user_id)
    if resumo_curriculo and not perfil.get("resumo_curriculo"):
        perfil = {**perfil, "resumo_curriculo": resumo_curriculo}

    if not await _garantir_login():
        print("[SENIOR] Login não confirmado — seguindo (login é retratado no wizard)")

    driver = await get_driver()
    resultados = {"sucesso": True, "aplicacoes": [], "falhas": 0}
    aplicadas = 0
    teto = _cont.get_teto()
    processados = set()  # códigos de vaga já processados nesta execução

    await _abrir_busca(driver, query)
    try:
        n_cards = await _run_in_thread(lambda: len(_achar_cards(driver)))
    except Exception:
        n_cards = 0
    if n_cards == 0:
        resultados["mensagem"] = "Nenhuma vaga encontrada na busca da Senior."
        return resultados
    print(f"[SENIOR] {n_cards} vaga(s) na busca")

    # Trava anti-loop: no máximo tantas iterações quanto cards (com folga p/ re-scan).
    MAX_ITER = max(n_cards, 1) * 3
    idx = 0
    for _iter in range(MAX_ITER):
        control = await get_intervention_state()
        if control.get("current_action") == "parar":
            resultados["mensagem"] = "Interrompido pelo usuário."
            break
        if teto > 0 and _cont.teto_atingido(user_id):
            resultados["mensagem"] = f"Teto de candidaturas atingido ({teto})."
            break

        # Re-busca os cards a cada iteração (a lista pode ter mudado após aplicar).
        def _clicar_card(i=idx):
            cards = _achar_cards(driver)
            if i >= len(cards):
                return None, False
            c = cards[i]
            titulo = (c.text or "").strip().split("\n")[0]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", c)
            try:
                c.click()
            except Exception:
                driver.execute_script("arguments[0].click();", c)
            return titulo, True
        try:
            titulo, ok = await _run_in_thread(_clicar_card)
        except Exception as e:
            logger.warning("senior clicar card %d erro: %s", idx, e)
            idx += 1
            continue
        if not ok:
            print("[SENIOR] Acabaram os cards da lista")
            break

        await notify_browser_step("selenium_senior", "abrindo", f"Abrindo: {(titulo or '')[:40]}")
        await asyncio.sleep(2.5)

        codigo = await _extrair_codigo_vaga(driver)
        vaga_key = codigo or f"senior-idx{idx}-{(titulo or '')[:30]}"

        # Dedup in-memory por código (evita reprocessar o mesmo card no re-scan).
        if codigo and codigo in processados:
            idx += 1
            continue

        # Dedup persistente (Neo4j).
        if neo4j and codigo:
            try:
                if neo4j.ja_se_candidatou(user_id, vaga_key):
                    print(f"[SENIOR] Já aplicada, pulando: {vaga_key}")
                    processados.add(codigo)
                    idx += 1
                    continue
            except Exception:
                pass

        descricao = await _extrair_descricao_detalhe(driver)

        # Match vaga×currículo (o usuário pediu: "se for condizente com o currículo").
        # Fail-open (na dúvida, aplica) — mesma política do projeto.
        idioma = "pt"
        try:
            aval = await _avaliar_match_vaga(descricao, resumo_curriculo)
            idioma = aval.get("idioma", "pt")
            if not aval.get("aplicar", True):
                print(f"[SENIOR] Pulando por match: {aval.get('motivo','')} — {(titulo or '')[:40]}")
                await notify_browser_step("selenium_senior", "pulada",
                                          f"Fora do perfil: {aval.get('motivo','')[:50]}")
                if codigo:
                    processados.add(codigo)
                idx += 1
                continue
        except Exception as e:
            logger.warning("senior match erro: %s", e)

        # Filtro de MODALIDADE + REGIÃO (fail-open, igual Gupy/GeekHunter).
        try:
            from automation.localizacao import vaga_aceita
            aceita, motivo = vaga_aceita(
                f"{titulo} {descricao}", perfil.get("modalidades_aceitas", []),
                perfil.get("regioes_relocacao", []))
            if not aceita:
                print(f"[SENIOR] Pulando por modalidade/região: {motivo} — {(titulo or '')[:40]}")
                await notify_browser_step("selenium_senior", "pulada", f"Fora do filtro: {motivo[:50]}")
                if codigo:
                    processados.add(codigo)
                idx += 1
                continue
        except Exception as e:
            logger.warning("senior filtro modalidade/região erro: %s", e)

        await notify_browser_step("selenium_senior", "aplicando", f"Candidatando: {(titulo or '')[:40]}")
        try:
            res = await _preencher_e_enviar_formulario(
                driver, perfil, resumo_curriculo, idioma, titulo or "", vaga_key)
        except Exception as e:
            logger.error("senior aplicar erro: %s", e)
            res = {"sucesso": False, "mensagem": str(e)}

        status = "candidatado" if res.get("sucesso") else "tentativa_falhou"
        if res.get("sucesso"):
            aplicadas += 1
            _cont.incr_count(user_id)
        else:
            resultados["falhas"] += 1
        resultados["aplicacoes"].append({"vaga": titulo, "status": status})
        if codigo:
            processados.add(codigo)

        if neo4j:
            try:
                neo4j.registrar_candidatura(user_id=user_id, vaga_id=vaga_key,
                                            plataforma="senior", status=status)
            except Exception:
                pass

        # Volta pra busca (aplicar navega pra tela 'Acompanhar candidatura').
        await _abrir_busca(driver, query)
        await asyncio.sleep(random.uniform(1.5, 3.0))
        idx += 1

    resultados.setdefault("mensagem", f"{aplicadas} candidatura(s) enviada(s) na Senior.")
    return resultados


# ── Aplicação em uma vaga única — paridade com Indeed/GeekHunter/Gupy ──────────

async def aplicar(vaga_url: str, perfil: dict, curriculo_path: str = "", user_id: str = "admin") -> dict:
    """Aplica numa vaga específica da Senior pela URL do detalhe (best-effort — a SPA
    normalmente não tem URL por vaga; use aplicar_vagas_visiveis_na_pagina)."""
    set_platform("senior")
    resumo_curriculo = perfil.get("resumo_curriculo", "") or await _get_resumo_curriculo(user_id)
    if resumo_curriculo and not perfil.get("resumo_curriculo"):
        perfil = {**perfil, "resumo_curriculo": resumo_curriculo}

    if not await _garantir_login():
        print("[SENIOR] Login não confirmado — seguindo")
    driver = await get_driver()
    await navegar(vaga_url or _build_search_url())
    await asyncio.sleep(3)
    return await _preencher_e_enviar_formulario(driver, perfil, resumo_curriculo, "pt", "", vaga_url)
