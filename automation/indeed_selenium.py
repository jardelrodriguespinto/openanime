"""
Candidatura via Indeed usando Selenium + Chrome visível.

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
from automation.form_filler import responder_pergunta, detectar_idioma_texto
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
# ao LinkedIn: na dúvida (sem dados/timeout/erro), aplica. 30 = "na dúvida aplica",
# só pula quem NÃO tem nada a ver com o perfil. Ajustável no .env.
_LIMIAR_MATCH = int(os.getenv("INDEED_LIMIAR_MATCH", "20"))

# Pausa para intervenção manual ANTES de clicar em "Enviar sua candidatura", para
# o usuário resolver o reCAPTCHA da tela de revisão. "1" = pausa (padrão, pedido do
# usuário); "0" = envia sozinho (autônomo). Vale para candidatura única e em lote.
def _pausar_antes_envio() -> bool:
    return os.getenv("INDEED_PAUSA_ENVIO", "1").strip().lower() not in ("0", "false", "no", "nao", "não", "")

_BASE = "https://br.indeed.com"

# Run-log durável (o Indeed só printava pro terminal → sem como diagnosticar depois).
# Grava marcos do fluxo (login/busca/cards/apply) em automation/_indeed_run.log.
_INDEED_RUN_LOG = os.path.join(os.path.dirname(__file__), "_indeed_run.log")


def _ilog(msg: str) -> None:
    import datetime as _dt
    line = f"{_dt.datetime.now().strftime('%H:%M:%S')} {msg}"
    print(f"[INDEED] {msg}")
    try:
        with open(_INDEED_RUN_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


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
    'button[name="submit-application"]',
    '[data-testid="submit-application-button"]',
    'button[data-testid="submit-application-button"]',
    '[data-testid="submit-application"]',
    'button[data-testid="submit-application"]',
    'button:has-text("Enviar sua candidatura")',
    'button:has-text("Enviar candidatura")',
    'button:has-text("Submit your application")',
    'button:has-text("Submit application")',
    'button:has-text("Enviar")',
    'button:has-text("Submit")',
]
_BTN_REVISAR = [
    'button:has-text("Revisar")',
    'button:has-text("Verificar sua candidatura")',
    'button:has-text("Verificar")',
    'button:has-text("Review")',
]

# Detecção (sem clicar) da tela de revisão/preview: seletores ESTREITOS do botão
# de envio, para não confundir com um genérico "Enviar" de outro step.
_SUBMIT_DETECTAR = [
    'button[name="submit-application"]',
    '[data-testid="submit-application-button"]',
    'button[data-testid="submit-application-button"]',
    '[data-testid="submit-application"]',
    'button[data-testid="submit-application"]',
]

# Post-apply: volta para a lista de resultados após enviar a candidatura.
_BTN_VOLTAR_BUSCA = [
    '#returnToSearchButton',
    'button#returnToSearchButton',
    '#continueButton',
    'button#continueButton',
    'button.ia-PostApply-ContinueFooter-button',
    'button[class*="post-apply"]',
    'button:has-text("Voltar à busca de vagas")',
    'button:has-text("Voltar à busca")',
    'button:has-text("Return to job search")',
    'button:has-text("Return to search")',
]

# Botão que abre o Indeed Apply na página da vaga (abre em nova aba/janela).
_BTN_APPLY = [
    "#indeedApplyButton",
    'button#indeedApplyButton',
    'button[data-testid="indeedApplyButton-test"]',
    'span[data-testid="indeed-apply-widget"] button',
    '.jobsearch-IndeedApplyButton-newDesign',
    'button[aria-label*="Candidatar-se com o Indeed"]',
    'button[aria-label*="Candidatura simplificada"]',
    'button[aria-label*="Easily apply"]',
    'button:has-text("Candidatar-se com o Indeed")',
    'button:has-text("Candidatar-se")',
    'button:has-text("Candidatura simplificada")',
    'button:has-text("Apply now")',
    'button:has-text("Easily apply")',
]

# Botão "Continue" da tela intersticial de passkey/WebAuthn ("Entre mais rápido
# neste dispositivo / Crie uma chave de acesso"). Ela aparece JÁ LOGADO, entre o
# login e a lista de vagas; se não for dispensada, cobre a página e trava tudo —
# era isso que fazia o Indeed "logar e não fazer nada". Clicar Continue só pula a
# etapa (o próprio Indeed diz "This device does not support passkeys").
_BTN_PASSKEY = [
    '#pass-WebAuthn-continue',
    'button#pass-WebAuthn-continue',
    'button[id*="WebAuthn" i]',
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
        # CAPTCHA do envio do SmartApply (resolução manual)
        "hcaptcha", "recaptcha", "confirme que você é humano", "verifique que você é humano",
        "i'm not a robot", "não sou um robô", "prove you are human",
    ):
        if marca in t or marca in h[:4000]:
            return True
    return False


async def _smartapply_bloqueado(driver) -> bool:
    """Detecta CAPTCHA/verificação DENTRO do contexto atual do SmartApply."""
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        ttl = await get_title()
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        return False
    return _pagina_bloqueada(cur, ttl, html)


async def _clicar_voltar_busca(driver) -> bool:
    """Clica 'Voltar à busca de vagas' (#returnToSearchButton) na tela pós-envio,
    para retornar à lista e seguir para o próximo card. Best-effort."""
    try:
        _txt, clicou = await _clicar_botao_smartapply(driver, _BTN_VOLTAR_BUSCA)
        if clicou:
            print("[INDEED] Voltando à busca de vagas (post-apply)")
            await asyncio.sleep(2)
        return clicou
    except Exception:
        return False


async def _aguardar_resolucao_manual(driver, origem: str = "login", auto_ok=None) -> bool:
    """
    Pausa a automação e espera o usuário resolver login/CAPTCHA/Cloudflare no browser.
    Retorna True quando a página sai do estado de bloqueio, False se clicar Parar.
    Mesmo padrão do LinkedIn: qualquer seletor incerto deve cair aqui.

    `auto_ok`: callable async opcional que retorna True quando a condição de sucesso
    já foi atingida SOZINHA (ex.: login concluído → caiu em settings/account). Aí
    RETOMA automático, sem exigir o clique "Retomar Auto" no dashboard — era o que
    fazia a automação "parar em settings/account e não fazer nada" após o login.
    """
    await set_intervention_state("paused", True)
    await set_intervention_state("intervention_type", "manual")
    await notify_browser_step(
        "selenium_indeed", "manual",
        f"⚠️ Ação manual necessária ({origem})! Resolva no navegador (Chrome) "
        f"(login / verificação) e clique ▶️ Continuar no dashboard."
    )
    print(f"[INDEED] Intervenção manual em '{origem}' — aguardando resolução...")

    while True:
        control = await get_intervention_state()
        if control.get("current_action") == "parar":
            print("[INDEED] Usuário clicou Parar durante espera manual")
            return False

        # Passkey ("Acessar mais rápido neste dispositivo") pode surgir DURANTE a
        # espera (ex.: logo após o código de login) e travar tudo. Dispensa aqui, na
        # hora (find+click direto por id, barato) — depois a página avança pra
        # settings/account e a auto-retomada abaixo detecta o login e segue sozinha.
        try:
            _t, _c = await _clicar_botao_smartapply(driver, _BTN_PASSKEY)
            if _c:
                _ilog("passkey dispensada durante espera manual (#pass-WebAuthn-continue)")
                await asyncio.sleep(1.5)
        except Exception:
            pass

        # Auto-retomada: se a condição de sucesso já foi atingida (login concluído →
        # settings/account), continua SOZINHO sem esperar o "Retomar Auto".
        if auto_ok is not None:
            try:
                pronto = await auto_ok()
            except Exception:
                pronto = False
            if pronto:
                print(f"[INDEED] Auto-retomando '{origem}': condição de sucesso detectada")
                await notify_browser_step("selenium_indeed", "retomando",
                                          "Login detectado — retomando automático")
                await set_intervention_state("paused", False)
                await set_intervention_state("intervention_type", None)
                return True

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


async def _passar_passkey(driver, timeout: float = 10.0, grace: float = 3.5) -> bool:
    """Dispensa a tela "Sign in faster on this device" ("Create a passkey…" /
    "Crie uma chave de acesso") clicando em Continue (#pass-WebAuthn-continue). Ela
    aparece JÁ LOGADO, entre o login e a busca; sem dispensar, cobre a página e
    trava o fluxo.

    Clique BLINDADO (o handler anterior não pegava): (1) acha o botão por id e
    clica via JS — NÃO depende de is_displayed(), que reporta False no intersticial;
    (2) procura também DENTRO de iframes; (3) fallback: numa página cujo header é
    "Sign in faster / passkey", clica qualquer botão cujo texto seja Continue/
    Continuar. Poll: espera até `grace`s a tela APARECER (barato quando ausente) e,
    uma vez detectada, insiste até ela sumir (teto `timeout`). Loga o que vê para
    diagnóstico. Idempotente. Retorna True se dispensou algo."""
    import time as _time

    def _tentar():
        # Retorna (achou_tela_passkey, clicou). Roda no doc principal e em iframes.
        def _no_contexto():
            achou = False
            # 1) Botão por id — o caminho confiável. JS click ignora overlay/visibility.
            els = driver.find_elements(
                By.CSS_SELECTOR,
                "#pass-WebAuthn-continue, button[id*='WebAuthn'], button[id*='webauthn']")
            if not els:
                # 2) Fallback por header: página de passkey → clica Continue por texto.
                try:
                    src = (driver.page_source or "").lower()
                except Exception:
                    src = ""
                if any(k in src for k in (
                        "sign in faster", "create a passkey", "passkey",
                        "crie uma chave de acesso", "chave de acesso")):
                    achou = True
                    for b in driver.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
                        try:
                            t = (b.text or "").strip().lower()
                        except Exception:
                            t = ""
                        if t in ("continue", "continuar"):
                            els = [b]
                            break
            if not els:
                return achou, False
            achou = True
            el = els[0]
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            except Exception:
                pass
            # JS click primeiro (funciona mesmo "não exibido"/coberto); nativo de reserva.
            try:
                driver.execute_script("arguments[0].click();", el)
                return achou, True
            except Exception:
                try:
                    el.click()
                    return achou, True
                except Exception:
                    return achou, False

        achou, clicou = _no_contexto()
        if clicou:
            return True, True
        for fr in driver.find_elements(By.CSS_SELECTOR, "iframe"):
            try:
                driver.switch_to.frame(fr)
                a2, c2 = _no_contexto()
            except Exception:
                a2, c2 = False, False
            finally:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
            achou = achou or a2
            if c2:
                return True, True
        return achou, False

    dispensou = False
    detectou = False
    inicio = _time.monotonic()
    while _time.monotonic() - inicio < timeout:
        try:
            achou, clicou = await _run_in_thread(_tentar)
        except Exception:
            achou, clicou = False, False
        if achou and not detectou:
            detectou = True
            _ilog("passkey DETECTADA ('Sign in faster' / #pass-WebAuthn-continue)")
        if clicou:
            dispensou = True
            _ilog("passkey: Continue clicado")
            await asyncio.sleep(1.5)             # aguarda redirect / re-render
            continue                            # re-checa: pode re-renderizar
        if dispensou and not achou:
            _ilog("passkey dispensada — tela saiu")
            return True
        if not achou:
            if _time.monotonic() - inicio > grace:
                return dispensou                # nunca apareceu → no-op barato
            await asyncio.sleep(0.4)
            continue
        await asyncio.sleep(0.5)                 # detectada, botão ainda não clicável
    if detectou and not dispensou:
        _ilog("passkey DETECTADA mas NÃO consegui clicar Continue — pode travar")
    return dispensou


# ── Login (manual-first) ──────────────────────────────────────────────────────

def _esta_logado(url: str, title: str, html: str = "") -> bool:
    """Heurística de login no Indeed. Conservadora: na dúvida, NÃO está logado
    (para cair na intervenção manual, que é segura)."""
    u = (url or "").lower()
    h = (html or "").lower()
    # A tela de passkey/WebAuthn ("Acessar mais rápido neste dispositivo" / "crie uma
    # chave de acesso") só aparece com sessão ativa → trata como logado (será
    # dispensada por _passar_passkey). Vem ANTES do early-return de /auth (a tela mora
    # em secure.indeed.com/auth). Escaneia o HTML INTEIRO (não h[:8000]): essa tela
    # tem um SVG gigante (~15k chars) ANTES do botão/header, então os marcadores caem
    # depois de 8000 — era por isso que a auto-retomada não detectava e travava.
    if any(m in h for m in (
        "pass-webauthn-continue", "crie uma chave de acesso",
        "acessar mais rápido", "sign in faster", "entre mais rápido",
    )):
        return True
    if "login" in u or "/auth" in u or "account/login" in u:
        return False
    # Landings pós-login do Indeed (redireciona pra home/mensagens/settings/passport
    # após o código): ex. ?from=gnav-passport--passport-webapp, /settings/account
    if ("from=gnav" in u or "from=passport" in u or "from=messaging" in u
            or "mypage" in u or "/myjobs" in u or "/settings/" in u):
        return True
    # Indicadores de sessão ativa (menu da conta, sair, etc.)
    for marca in ("gnav-", "logout", "sair da conta", "account-menu", "minhas vagas", "myjobs"):
        if marca in h[:6000]:
            return True
    return False


async def _aceitar_cookies(driver) -> bool:
    """Dispensa o banner de cookies OneTrust do Indeed. Em perfil NOVO ele aparece no
    RODAPÉ — exatamente onde fica o link 'Acessar com um código' na tela de login —
    e intercepta/cobre o clique (era por isso que o OTP 'não clicava'). Clica
    'Aceitar todos os cookies' (`#onetrust-accept-btn-handler`). No-op barato quando
    ausente (com perfil persistente só aparece no 1º run). Retorna True se dispensou."""
    sels = [
        '#onetrust-accept-btn-handler',
        'button#onetrust-accept-btn-handler',
        '#onetrust-reject-all-handler',
        'button:has-text("Aceitar todos os cookies")',
        'button:has-text("Accept all cookies")',
        'button:has-text("Aceitar todos")',
    ]
    try:
        _txt, clicou = await _clicar_botao_smartapply(driver, sels)
    except Exception:
        return False
    if clicou:
        _ilog("cookies: banner OneTrust dispensado")
        await asyncio.sleep(1)
    return clicou


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

    # 0. Dispensa o banner de cookies (cobre o rodapé / o link de código).
    await _aceitar_cookies(driver)

    # 1. E-mail. O campo do Indeed passport tem id DINÂMICO (React useId, ex.
    # `ifl-InputFormField-:passport-ssr-Reqktala:`) → NÃO dá pra ancorar num id fixo
    # (o antigo `#ifl-InputFormField-ihl` nunca casava e ainda gastava 15s no
    # WebDriverWait). Casa pelo que é ESTÁVEL: name="__email" primeiro, depois prefixo
    # do id e type=email. Uma chamada só (digitar_robusto tenta as partes em ordem e
    # para na 1ª presente — a estável casa na hora, sem os 15s à toa).
    if email:
        sel_email = (
            "input[name='__email'], input[id^='ifl-InputFormField'], "
            "input[type='email'], input[autocomplete='email'], "
            "input[id*='email'], input[autocomplete='username'], input[name='email']"
        )
        if await digitar_robusto(sel_email, email):
            print("[INDEED] E-mail preenchido no login")
        else:
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

    # 3. "Acessar com um código".
    # Depois do e-mail Gmail, o Indeed mostra a tela "É bom ver você de novo" que
    # FORÇA o Google SSO ("Continuar com o Google") e põe o link de código como
    # fallback NO RODAPÉ (id ESTÁVEL `auth-page-google-otp-fallback`, um <a>). Esse
    # link renderiza DEPOIS do widget do Google, então o clique único logo após o
    # Continuar não pegava — daí "não clica no acessar com um código". Fix: casa pelo
    # id estável primeiro e faz POLL (~10s) até o link aparecer. NUNCA clica no botão
    # do Google (não está nos seletores).
    _CODIGO = [
        '#auth-page-google-otp-fallback',
        'a#auth-page-google-otp-fallback',
        '[data-tn-element="auth-page-google-password-fallback"]',
        '[data-tn-element="auth-page-otp-fallback"]',
        'a:has-text("Acessar com um código")',
        'button:has-text("Acessar com um código")',
        'a:has-text("Entrar com um código")',
        'button:has-text("Entrar com um código")',
        'a:has-text("Enviar um código")',
        'a:has-text("Sign in with a login code")',
        'a:has-text("login code")',
        '[data-testid*="passwordless"]',
    ]
    await _aceitar_cookies(driver)  # banner pode reaparecer sobre o link nesta tela
    clicou_cod = False
    for _tent in range(12):  # ~10s: o link OTP renderiza depois do widget do Google
        _, clicou_cod = await _clicar_botao_smartapply(driver, _CODIGO)
        if clicou_cod:
            break
        # Bloqueio (Cloudflare/verificação) pode surgir no meio → cai no manual.
        try:
            cur = await _run_in_thread(lambda: driver.current_url)
            ttl = await get_title()
            html = await _run_in_thread(lambda: driver.page_source)
        except Exception:
            cur, ttl, html = "", "", ""
        if _pagina_bloqueada(cur, ttl, html):
            return  # deixa a intervenção manual assumir
        await asyncio.sleep(0.8)
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

    # Dispensa o banner de cookies (perfil novo) — cobre rodapé/botões e atrapalha.
    await _aceitar_cookies(driver)

    # Dispensa a tela de passkey ("Sign in faster on this device" → botão Continue)
    # ANTES de decidir login/bloqueio. CRÍTICO p/ não travar: essa tela SÓ aparece
    # com sessão já autenticada (é enrollment pós-login), então se conseguimos
    # dispensá-la já ESTAMOS logados → retorna True direto e NÃO cai no login por
    # e-mail+código (cuja espera manual é o que travava e "matava" a automação).
    if await _passar_passkey(driver):
        try:
            cur = await _run_in_thread(lambda: driver.current_url)
            ttl = await get_title()
            html = await _run_in_thread(lambda: driver.page_source)
        except Exception:
            pass
        if not _pagina_bloqueada(cur, ttl, html):
            print("[INDEED] Passkey dispensada → sessão logada, seguindo pra busca")
            await notify_browser_step("indeed_login", "sucesso", "Login OK (passkey dispensada)")
            return True

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

    # O código chega no e-mail e é digitado à mão: pausa em intervenção manual — MAS
    # com auto-retomada: assim que o login concluir (cair em settings/account / página
    # logada), a automação SEGUE SOZINHA pra busca, sem exigir clique no dashboard.
    # Era isso que faltava: parava em settings/account e "não fazia nada".
    async def _login_ja_concluido() -> bool:
        try:
            c = await _run_in_thread(lambda: driver.current_url)
            t = await get_title()
            h = await _run_in_thread(lambda: driver.page_source)
        except Exception:
            return False
        # Só considera concluído quando SAIU da tela de login/código (/auth,
        # /account/login) e a página é claramente de sessão ativa.
        return (not _pagina_bloqueada(c, t, h)) and _esta_logado(c, t, h)

    await notify_browser_step(
        "indeed_login", "codigo",
        "📧 Digite o código do seu e-mail no navegador. Assim que o login concluir, "
        "eu sigo sozinho pra busca (não precisa clicar em nada)."
    )
    resolvido = await _aguardar_resolucao_manual(
        driver, "código de acesso do Indeed", auto_ok=_login_ja_concluido)
    if not resolvido:
        return False

    # Logo após o código, o Indeed abre a tela "Sign in faster / passkey" — dispensa
    # aqui (com espera generosa, é o momento em que ela aparece) para não travar.
    await _passar_passkey(driver, grace=8.0)

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
    # Mimetiza uma busca feita a partir da home (é o que o Indeed anexa e o que o
    # usuário vê ao pesquisar): reduz a chance do deep-link ser re-desafiado/redir.
    url += "&from=searchOnHP"
    return url


async def _buscar_via_home(driver, query: str = "") -> bool:
    """Vai pra HOME do Indeed e digita a palavra-chave no campo de busca
    (#text-input-what) como um humano — mais robusto que o deep-link /jobs?q= (que
    o Cloudflare tende a re-desafiar) e cobre o redirect pós-login (passport /
    settings/account / mensagens). Retorna True se caiu numa lista /jobs."""
    from selenium.webdriver.common.keys import Keys
    q = (query or "").strip() or _get_query_padrao()
    try:
        await navegar("https://br.indeed.com/?from=gnav-passport--passport-webapp")
        await asyncio.sleep(3)
    except Exception:
        return False

    # Passkey pode aparecer na home e cobrir o campo de busca → dispensa antes.
    await _passar_passkey(driver)

    # Cloudflare pode aparecer na home → intervenção manual.
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
        ttl = await get_title()
        html = await _run_in_thread(lambda: driver.page_source)
    except Exception:
        cur, ttl, html = "", "", ""
    if _pagina_bloqueada(cur, ttl, html):
        if not await _aguardar_resolucao_manual(driver, "busca na home do Indeed"):
            return False

    await notify_browser_step("indeed_busca", "digitando", f"Digitando '{q}' na busca")
    sel_what = ("#text-input-what, input[name='q'], "
                "input[aria-label*='keywords' i], input[placeholder*='Cargo' i]")
    if not await digitar_robusto(sel_what, q):
        print("[INDEED] Campo de busca (#text-input-what) não encontrado na home")
        return False

    # Localização (opcional): preenche se INDEED_CIDADE estiver definido.
    cidade = _get_cidade().strip()
    if cidade:
        try:
            await digitar_robusto(
                "#text-input-where, input[name='l'], input[aria-label*='where' i]", cidade
            )
        except Exception:
            pass

    await asyncio.sleep(0.6)
    # Submete: Enter no campo; fallback clica o botão de busca.
    def _enter():
        el = driver.find_element(By.CSS_SELECTOR, "#text-input-what")
        el.send_keys(Keys.RETURN)
        return True
    enviado = False
    try:
        enviado = await _run_in_thread(_enter)
    except Exception:
        enviado = False
    if not enviado:
        _, enviado = await _clicar_botao_smartapply(driver, [
            'button[type="submit"]',
            'button:has-text("Buscar")', 'button:has-text("Pesquisar")',
            'button:has-text("Find jobs")', 'button:has-text("Search")',
        ])
    await asyncio.sleep(3)
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
    except Exception:
        cur = ""
    ok = "/jobs" in (cur or "").lower()
    print(f"[INDEED] Busca via home → {'OK' if ok else 'não caiu em /jobs'} ({(cur or '')[:80]})")
    return ok


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
    _ilog(f"login_ok={login_ok} query='{(query or _get_query_padrao())[:30]}'")
    if not login_ok:
        _ilog("PAROU: login/verificação não concluído (Cloudflare/código por e-mail?)")
        return {
            "sucesso": False, "vagas": [],
            "mensagem": "Não foi possível acessar o Indeed (login/verificação). Resolva no browser e tente de novo.",
        }

    # Pós-login o Indeed cai numa LANDING que NÃO é a lista de vagas — tipicamente
    # secure.indeed.com/settings/account (ou home/mensagens). Sequência: dispensa a
    # passkey dessa landing → vai DIRETO pra /jobs?q=... (determinístico, é o que o
    # usuário quer: sair de settings/account e cair na busca) → se o deep-link for
    # redirecionado/re-desafiado, digita a palavra-chave na home (robusto vs
    # Cloudflare). Só pula tudo se já estamos na busca da própria palavra-chave.
    await _passar_passkey(driver)
    try:
        cur = await _run_in_thread(lambda: driver.current_url)
    except Exception:
        cur = ""
    from urllib.parse import quote_plus
    q_atual = (query or "").strip() or _get_query_padrao()
    ja_na_busca = "/jobs" in (cur or "").lower() and f"q={quote_plus(q_atual)}".lower() in (cur or "").lower()
    if not ja_na_busca:
        url_busca = _build_search_url(query)
        _ilog(f"pos_login landing='{(cur or '')[:60]}' → indo direto pra {url_busca[:60]}")
        await notify_browser_step("indeed_pos_login", "navegando", "Abrindo busca de vagas")
        await navegar(url_busca)
        await asyncio.sleep(3)
        await _passar_passkey(driver)
        try:
            cur = await _run_in_thread(lambda: driver.current_url)
        except Exception:
            cur = ""
        if "/jobs" not in (cur or "").lower():
            # Deep-link foi redirecionado (settings/account de novo, home, Cloudflare)
            # → tenta digitar a palavra-chave na home e submeter.
            _ilog(f"deep-link não caiu em /jobs (='{(cur or '')[:50]}') — tentando via home")
            await _buscar_via_home(driver, query)

    # Antes de extrair: dispensa cookies (banner OneTrust cobre o rodapé dos cards em
    # perfil novo) e a passkey (intercepta a navegação para /jobs). Sem isso → 0 vagas.
    await _aceitar_cookies(driver)
    await _passar_passkey(driver)
    await notify_browser_step("indeed_extracao", "iniciando", "Extraindo vagas do Indeed")
    cards = await _extrair_cards_vaga(max_vagas)
    try:
        cur_final = await _run_in_thread(lambda: driver.current_url)
    except Exception:
        cur_final = ""
    _ilog(f"cards_extraidos={len(cards)} url_busca='{(cur_final or '')[:80]}'")
    if not cards:
        _ilog("PAROU/VAZIO: 0 cards na busca (seletor de card mudou? bloqueio? busca vazia?)")
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

                # PREFERE a URL canônica viewjob?jk= — a href do card é um link de
                # RASTREIO /rc/clk?jk=...&bb=...&xkcb=... que, navegado DIRETO, cai no
                # "Security Check" do Indeed (os tokens bb/xkcb são de clique único na
                # lista). Era ISSO que quebrava o "card por card": aplicar() abria o
                # /rc/clk, batia no Security Check e nunca achava o botão de aplicar.
                # (Validado ao vivo: /rc/clk → "Security Check"; /viewjob?jk= → vaga OK.)
                if not vaga_id and url:
                    m = re.search(r"[?&]jk=([0-9A-Za-z]+)", url)
                    if m:
                        vaga_id = m.group(1)
                if vaga_id:
                    url = f"{_BASE}/viewjob?jk={vaga_id}"
                if not url or url in seen:
                    continue
                seen.add(url)

                try:
                    emp_el = card.find_element(By.CSS_SELECTOR, "[data-testid='company-name'], .companyName, span.companyName")
                    empresa = (emp_el.text or "").strip()
                except Exception:
                    empresa = ""

                # Elegibilidade Indeed Apply. O rótulo PT-BR atual no card é
                # "Candidate-se facilmente" (não "Candidatura simplificada") — sem ele
                # TODA vaga vinha easy_apply=False e só o fallback (elegiveis=todas)
                # salvava. Casa todas as variantes conhecidas.
                easy = False
                try:
                    card_html = (card.get_attribute("innerHTML") or "").lower()
                    easy = ("indeedapply" in card_html or "candidatura simplificada" in card_html
                            or "candidate-se facilmente" in card_html or "candidatar-se facilmente" in card_html
                            or "easily apply" in card_html or "eas:apply" in card_html or "ialbl" in card_html)
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
            # Idioma determinístico do texto prevalece sobre o do LLM (que caía em 'pt').
            idioma = detectar_idioma_texto(descricao) or str(data.get("idioma", "pt"))
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
        # Tela pós-envio (post-apply) — o botão de voltar à busca só existe após enviar.
        "voltar à busca de vagas", "returntosearchbutton", "return to job search",
        "post-apply", "sua candidatura foi entregue",
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
        # Clique robusto: nativo e, se falhar (interceptado/stale em SPA React/styled-
        # components — causa do "às vezes não clica em Continuar" no Gupy), cai no JS
        # click, que ignora sobreposição. Retorna True se algum dos dois pegou.
        def _do_click(el):
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            except Exception:
                pass
            try:
                el.click()
                return True
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", el)
                    return True
                except Exception:
                    return False

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
                        if _do_click(el):
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
                        if _do_click(el):
                            return (txt, True)
                except Exception:
                    continue
        return ("", False)

    try:
        return await _run_in_thread(_clicar)
    except Exception as e:
        logger.warning("_clicar_botao_smartapply erro: %s", e)
        return ("", False)


async def _botao_presente(driver, seletores: list) -> bool:
    """True se existe um botão visível casando algum seletor CSS (sem :has-text),
    SEM clicar. Usado para saber se já estamos na tela de revisão/preview."""
    def _check():
        for s in seletores:
            if ":has-text" in s:
                continue
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, s):
                    if el.is_displayed():
                        return True
            except Exception:
                continue
        return False
    try:
        return await _run_in_thread(_check)
    except Exception:
        return False


async def _tentar_recaptcha_checkbox(driver, timeout: float = 8.0) -> str:
    """Tenta resolver o reCAPTCHA v2 do envio clicando SÓ o checkbox 'não sou um
    robô'. Em ambiente confiável (Chrome stealth, navigator.webdriver=false, IP
    residencial, perfil persistente) ele costuma passar sem a grade de imagens — que
    o bot NÃO resolve (aí volta pro humano). NÃO usa solver externo/pydoll (pydoll
    também não resolve v2-imagem). Retorna:
      'sem_captcha' — nenhum reCAPTCHA v2 na tela → pode enviar direto
      'resolvido'   — checkbox marcado/validado → pode enviar
      'desafio'     — subiu a grade de imagens → precisa do humano
      'indefinido'  — clicou mas não confirmou → trata como precisa do humano
    """
    import time as _time

    def _anchor_iframe():
        # iframe do checkbox (api2/anchor ou enterprise/anchor; title='reCAPTCHA').
        for fr in driver.find_elements(By.CSS_SELECTOR, "iframe"):
            try:
                src = (fr.get_attribute("src") or "").lower()
                title = (fr.get_attribute("title") or "").lower()
            except Exception:
                continue
            if ("recaptcha" in src and "anchor" in src) or title == "recaptcha":
                return fr
        return None

    def _checked():
        fr = _anchor_iframe()
        if not fr:
            return False
        try:
            driver.switch_to.frame(fr)
            return bool(driver.find_elements(
                By.CSS_SELECTOR,
                "#recaptcha-anchor[aria-checked='true'], .recaptcha-checkbox-checked"))
        except Exception:
            return False
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

    def _click_checkbox():
        fr = _anchor_iframe()
        if not fr:
            return False
        try:
            driver.switch_to.frame(fr)
            alvo = None
            for sel in ("#recaptcha-anchor", ".recaptcha-checkbox-border",
                        "div.recaptcha-checkbox"):
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els:
                    alvo = els[0]
                    break
            if not alvo:
                return False
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", alvo)
                alvo.click()
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", alvo)
                except Exception:
                    return False
            return True
        except Exception:
            return False
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

    def _desafio_visivel():
        # iframe da grade de imagens (bframe) visível = escalou pro desafio.
        for fr in driver.find_elements(By.CSS_SELECTOR, "iframe"):
            try:
                src = (fr.get_attribute("src") or "").lower()
                if "recaptcha" in src and "bframe" in src:
                    if fr.is_displayed() and (fr.size or {}).get("height", 0) > 100:
                        return True
            except Exception:
                continue
        return False

    # 1) Há reCAPTCHA v2 na tela? Dá uma janela curta pro widget renderizar.
    achou = False
    for _ in range(5):  # ~2s
        try:
            if await _run_in_thread(lambda: _anchor_iframe() is not None):
                achou = True
                break
        except Exception:
            pass
        await asyncio.sleep(0.4)
    if not achou:
        return "sem_captcha"

    # 2) Já validado? (ex.: passou num step anterior.)
    try:
        if await _run_in_thread(_checked):
            return "resolvido"
    except Exception:
        pass

    # 3) Clica o checkbox e observa o desfecho.
    await _run_in_thread(_click_checkbox)
    inicio = _time.monotonic()
    while _time.monotonic() - inicio < timeout:
        await asyncio.sleep(0.6)
        try:
            if await _run_in_thread(_checked):
                _ilog("recaptcha v2: checkbox passou (sem desafio de imagem)")
                return "resolvido"
            if await _run_in_thread(_desafio_visivel):
                _ilog("recaptcha v2: subiu desafio de IMAGEM → humano")
                return "desafio"
        except Exception:
            pass
    _ilog("recaptcha v2: checkbox clicado mas sem confirmação → humano")
    return "indefinido"


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

        # CAPTCHA/verificação pode surgir no meio do SmartApply (comum antes do
        # envio). A resolução é MANUAL: pausa, avisa o usuário no dashboard e só
        # retoma quando resolvido (aí re-avalia o step e segue no botão de envio).
        if await _smartapply_bloqueado(driver):
            await notify_browser_step(
                f"indeed_step_{step}", "manual",
                "🔒 CAPTCHA/verificação — resolva no browser e clique ▶️ Continuar no dashboard"
            )
            if not await _aguardar_resolucao_manual(driver, f"CAPTCHA no SmartApply step {step}"):
                b64 = await screenshot_base64()
                return {"sucesso": False, "motivo_falha": "captcha",
                        "mensagem": f"CAPTCHA não resolvido. Aplique manualmente: {vaga_url}",
                        "screenshot": b64[:100] if b64 else ""}
            continue  # re-avalia o step após o CAPTCHA ser resolvido

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
        # Na tela de revisão/preview (botão de envio presente): antes de pausar pro
        # humano, TENTA resolver o reCAPTCHA v2 clicando só o checkbox (no Chrome
        # stealth costuma passar sem a grade de imagens). Se passar — ou não houver
        # captcha — envia automaticamente; se subir o desafio de IMAGEM (que o bot
        # não resolve), aí sim entrega o controle ao humano.
        if _pausar_antes_envio() and await _botao_presente(driver, _SUBMIT_DETECTAR):
            estado_captcha = await _tentar_recaptcha_checkbox(driver)
            if estado_captcha not in ("resolvido", "sem_captcha"):
                await notify_browser_step(
                    f"indeed_step_{step}", "manual",
                    "🔒 reCAPTCHA pediu desafio de imagem — resolva no browser e clique "
                    "🔄 Retomar Auto no dashboard para eu enviar."
                )
                if not await _aguardar_resolucao_manual(driver, "reCAPTCHA (desafio de imagem) antes do envio"):
                    b64 = await screenshot_base64()
                    return {"sucesso": False, "motivo_falha": "captcha",
                            "mensagem": f"Envio não confirmado. Aplique manualmente: {vaga_url}",
                            "screenshot": b64[:100] if b64 else ""}
                # Usuário pode ter enviado manualmente durante a pausa.
                if await _candidatura_enviada(driver):
                    b64 = await screenshot_base64()
                    await notify_browser_step(f"indeed_step_{step}", "sucesso", "Candidatura enviada!")
                    return {"sucesso": True, "perguntas_respondidas": perguntas_feitas,
                            "mensagem": "Candidatura enviada com sucesso via Indeed!",
                            "screenshot": b64[:100] if b64 else ""}
            else:
                _ilog(f"recaptcha antes do envio: {estado_captcha} → enviando automaticamente")

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
        # Passkey e banner de cookies podem interceptar/cobrir o botão de aplicar.
        await _passar_passkey(driver)
        await _aceitar_cookies(driver)

        cur = await _run_in_thread(lambda: driver.current_url)
        ttl = await get_title()
        html = await _run_in_thread(lambda: driver.page_source)
        if _pagina_bloqueada(cur, ttl, html):
            if not await _aguardar_resolucao_manual(driver, "abrir vaga"):
                return {"sucesso": False, "mensagem": "Verificação não resolvida."}
            html = await _run_in_thread(lambda: driver.page_source)

        # Idioma determinístico + filtro modalidade/região + match com currículo (fail-open).
        idioma_vaga = "pt"
        descricao = await _extrair_descricao_vaga(driver)
        if descricao:
            idioma_vaga = detectar_idioma_texto(descricao)
            # Filtro modalidade + região (presencial/híbrido só candidata se a cidade da
            # vaga cai numa região aceita). Fail-open: sem config, aplica.
            try:
                from automation.localizacao import vaga_aceita
                aceita_loc, motivo_loc = vaga_aceita(
                    descricao, perfil.get("modalidades_aceitas", []),
                    perfil.get("regioes_relocacao", []),
                )
            except Exception:
                aceita_loc, motivo_loc = True, ""
            if not aceita_loc:
                await notify_browser_step("selenium_indeed", "pulada", f"Fora do filtro: {motivo_loc[:50]}")
                return {"sucesso": False, "pulada": True, "motivo_falha": "fora_modalidade_regiao",
                        "mensagem": f"Vaga ignorada (modalidade/região): {motivo_loc}"}
            if resumo_curriculo:
                await notify_browser_step("selenium_indeed", "avaliando", "Verificando match com currículo...")
                aval = await _avaliar_match_vaga(descricao, resumo_curriculo)
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

        # Após enviar, clica "Voltar à busca de vagas" (post-apply) para retornar à
        # lista e seguir ao próximo card. Depois sai do contexto (fecha janela/iframe).
        if resultado.get("sucesso"):
            try:
                await _clicar_voltar_busca(driver)
            except Exception:
                pass
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

async def aplicar_vagas_visiveis_na_pagina(perfil: dict, max_vagas: int = 5, user_id: str = "admin",
                                           query: str = "") -> dict:
    """
    Extrai as vagas elegíveis (Indeed Apply) da busca atual e aplica em cada uma,
    registrando no Neo4j. Loop mínimo — sem a máquina de paginação/anti-parking do
    LinkedIn (que resolvia bugs específicos daquela plataforma).
    `query` = palavra-chave do DASHBOARD (tem prioridade sobre INDEED_QUERY do .env)."""
    set_platform("indeed")
    try:
        from graph.neo4j_client import get_neo4j
        neo4j = get_neo4j()
    except Exception:
        neo4j = None

    resumo_curriculo = perfil.get("resumo_curriculo", "") or await _get_resumo_curriculo(user_id)
    if resumo_curriculo and not perfil.get("resumo_curriculo"):
        perfil = {**perfil, "resumo_curriculo": resumo_curriculo}

    _ilog(f"=== APLICAR início | max_vagas={max_vagas} query='{query or _get_query_padrao()}' teto={_cont.get_teto()} count={_cont.get_count(user_id)} ===")
    extra = await extrair_vagas_da_busca(perfil, max_vagas=max(max_vagas * 3, 15), query=query)
    if not extra.get("sucesso"):
        _ilog(f"PAROU: extrair_vagas falhou — {extra.get('mensagem','')[:80]}")
        return {"sucesso": False, "aplicacoes": [], "mensagem": extra.get("mensagem", "Falha ao extrair vagas")}

    todas = extra.get("vagas", [])
    elegiveis = [v for v in todas if v.get("easy_apply")]
    _ilog(f"vagas_totais={len(todas)} elegiveis_easy_apply={len(elegiveis)}")
    # Fallback: se a heurística de "Candidatura simplificada" (string no innerHTML do
    # card) não marcou NENHUMA vaga, NÃO desiste — era exatamente isso que fazia o
    # Indeed "logar e não fazer nada": o rótulo no card muda de tempos em tempos e
    # zerava `elegiveis`. Cai pra TODAS as vagas e deixa o aplicar() decidir de
    # verdade — ele clica o botão Indeed Apply e devolve 'sem_indeed_apply' pras que
    # não têm candidatura simplificada (tratado como pulada abaixo, sem contar falha).
    if not elegiveis:
        if not todas:
            return {"sucesso": True, "aplicacoes": [],
                    "mensagem": "Nenhuma vaga encontrada na busca do Indeed."}
        print(f"[INDEED] Nenhuma vaga pré-marcada como Candidatura Simplificada — "
              f"tentando todas ({len(todas)}) e deixando o aplicar() checar de verdade")
        elegiveis = todas

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

        _ilog(f"apply '{(vaga.get('titulo') or '')[:35]}' → sucesso={res.get('sucesso')} "
              f"motivo={res.get('motivo_falha','')} {('| '+res.get('mensagem','')[:50]) if not res.get('sucesso') else ''}")

        # 'pulada' (sem match) ou vaga sem Candidatura Simplificada de verdade
        # (gate real do aplicar(), especialmente no fallback acima): segue para a
        # próxima SEM contar como falha — não é erro, só não é aplicável.
        if res.get("pulada") or res.get("motivo_falha") == "sem_indeed_apply":
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
    _ilog(f"=== FIM | aplicadas={aplicadas} falhas={resultados.get('falhas',0)} | {resultados.get('mensagem','')[:60]} ===")
    return resultados
