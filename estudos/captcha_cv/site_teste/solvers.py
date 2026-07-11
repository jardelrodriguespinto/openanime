"""Dois solvers para o site de teste local:
  - resolver_classico: template matching puro (numpy), SEM IA. Vai bem em math/texto
    (fonte fixa) e mal no 'distorcido' — mostra o limite da técnica clássica.
  - resolver_llm: manda a imagem pra uma IA de VISÃO via OpenRouter (multimodal).
    Lê a chave de OPENROUTER_API_KEY (env) ou do .env da raiz do projeto.
"""
import base64
import io
import json
import os
import re
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

import render

# ───────────────────────── clássico (sem IA) ─────────────────────────
_TPL = (24, 24)


def _crop_resize(arr):
    m = arr < 128
    if not m.any():
        return np.zeros(_TPL)
    ys, xs = np.where(m)
    crop = arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return np.asarray(Image.fromarray(crop).resize((_TPL[1], _TPL[0]))).astype(float) / 255.0


def _templates(chars):
    return {c: _crop_resize(np.array(render.render_char(c)).astype("uint8")) for c in chars}


_TPL_TXT = _templates(render.ALFABETO)
_TPL_MATH = _templates("0123456789" + render.OPERADORES)


def _segmentos(arr):
    col = (arr < 128).sum(axis=0)
    faixas, ini = [], None
    for x, v in enumerate(col > 0):
        if v and ini is None:
            ini = x
        elif not v and ini is not None:
            faixas.append((ini, x)); ini = None
    if ini is not None:
        faixas.append((ini, arr.shape[1]))
    return [f for f in faixas if f[1] - f[0] >= 3]


def _ler(arr, templates):
    out = []
    for x0, x1 in _segmentos(arr):
        g = _crop_resize(arr[:, x0:x1])
        best, bd = "", 1e18
        for ch, t in templates.items():
            dd = ((g - t) ** 2).sum()
            if dd < bd:
                bd, best = dd, ch
        out.append(best)
    return "".join(out)


def resolver_classico(img: Image.Image, tipo: str) -> str:
    arr = np.array(img.convert("L")).astype("uint8")
    if tipo == "math":
        s = _ler(arr, _TPL_MATH).replace("x", "*")
        toks = re.findall(r"\d+|[+\-*]", s)
        try:
            return str(int(eval("".join(toks))))  # noqa: S307 — expressão só de dígitos/operadores locais
        except Exception:
            return "0"
    return _ler(arr, _TPL_TXT)


# ───────────────────────── IA de visão (OpenRouter) ─────────────────────────
def _api_key():
    k = os.environ.get("OPENROUTER_API_KEY")
    if k:
        return k
    envp = Path(__file__).resolve().parents[3] / ".env"  # raiz do projeto
    if envp.exists():
        for ln in envp.read_text().splitlines():
            if ln.startswith("OPENROUTER_API_KEY="):
                return ln.split("=", 1)[1].strip()
    return None


MODELO = os.environ.get("CAPTCHA_LLM_MODEL", "openai/gpt-4o-mini")  # trocável por env


def resolver_llm(img: Image.Image, tipo: str) -> str:
    key = _api_key()
    if not key:
        raise RuntimeError("sem OPENROUTER_API_KEY (env ou .env)")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    dataurl = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    if tipo == "math":
        instr = "A imagem mostra uma conta simples (ex.: 7+3, 8-2, 4x3). Responda APENAS o resultado numérico, sem mais nada."
    else:
        instr = "A imagem mostra um código de 5 caracteres (letras minúsculas a-z e dígitos 0-9). Responda APENAS o código, sem espaços nem explicação."
    body = {
        "model": MODELO, "max_tokens": 20, "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": instr},
            {"type": "image_url", "image_url": {"url": dataurl}},
        ]}],
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    txt = (data["choices"][0]["message"]["content"] or "").strip()
    return re.sub(r"\s+", "", txt)
