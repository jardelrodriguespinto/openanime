"""Site de teste LOCAL de CAPTCHA (estudo). Serve 3 tipos gerados na hora:
   math (conta), texto (5 chars) e distorcido (5 chars com rotação/ruído).

API:
   GET  /novo/<tipo>     -> {token, tipo, img(dataURL)}
   POST /verificar       -> {token, resposta} => {ok, esperado}
   GET  /                -> página simples pra olhar no navegador

Roda só em 127.0.0.1. É um alvo de estudo SEU — não tem nada de terceiros aqui.
"""
import base64
import io
import secrets

from flask import Flask, jsonify, request

import render

app = Flask(__name__)
DESAFIOS = {}  # token -> {tipo, resposta}


def _png_dataurl(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@app.route("/novo/<tipo>")
def novo(tipo):
    if tipo == "math":
        txt, resp = render.gerar_math()
        img = render.render_sequencia(txt)
    elif tipo == "texto":
        resp = render.texto_aleatorio(5)
        img = render.render_sequencia(resp)
    elif tipo == "distorcido":
        resp = render.texto_aleatorio(5)
        img = render.render_sequencia(resp, distorcer=True)
    else:
        return jsonify(erro="tipo inválido (use math|texto|distorcido)"), 400
    token = secrets.token_hex(8)
    DESAFIOS[token] = {"tipo": tipo, "resposta": resp}
    return jsonify(token=token, tipo=tipo, img=_png_dataurl(img))


@app.route("/verificar", methods=["POST"])
def verificar():
    d = request.get_json(force=True, silent=True) or {}
    item = DESAFIOS.get(d.get("token"))
    if not item:
        return jsonify(ok=False, erro="token desconhecido"), 404
    ok = str(d.get("resposta", "")).strip().lower() == item["resposta"].strip().lower()
    # 'esperado' é exposto de propósito: é um site de ESTUDO local (facilita medir acurácia)
    return jsonify(ok=ok, esperado=item["resposta"])


@app.route("/")
def home():
    linhas = "".join(
        f"<h3>{t}</h3><img id='img_{t}'><br><button onclick=\"nova('{t}')\">gerar</button>"
        for t in ("math", "texto", "distorcido")
    )
    return f"""<!doctype html><meta charset=utf-8><h1>CAPTCHA — site de teste local (estudo)</h1>
{linhas}
<script>
async function nova(t){{const r=await fetch('/novo/'+t);const j=await r.json();
document.getElementById('img_'+t).src=j.img;}}
['math','texto','distorcido'].forEach(nova);
</script>"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
