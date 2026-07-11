"""Roda o solver contra o site de teste local e mede a acurácia (quantos "quebrou").

Uso (com o app.py rodando em 127.0.0.1:5000):
    python rodar_teste.py --solver classico --n 10
    python rodar_teste.py --solver llm --n 4        # usa OpenRouter (gasta crédito)
"""
import argparse
import base64
import io
import json
import urllib.request

from PIL import Image

import solvers

BASE = "http://127.0.0.1:5000"


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read())


def _post(path, obj):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _img(dataurl):
    return Image.open(io.BytesIO(base64.b64decode(dataurl.split(",", 1)[1])))


def rodar(solver, tipos, n):
    fn = solvers.resolver_llm if solver == "llm" else solvers.resolver_classico
    for tipo in tipos:
        ok, exemplos = 0, []
        for _ in range(n):
            d = _get(f"/novo/{tipo}")
            try:
                resp = fn(_img(d["img"]), tipo)
            except Exception as e:
                resp = f"<erro:{e}>"
            v = _post("/verificar", {"token": d["token"], "resposta": resp})
            ok += 1 if v.get("ok") else 0
            if len(exemplos) < 6:
                exemplos.append(f"{resp}={v.get('esperado')}{'✓' if v.get('ok') else '✗'}")
        print(f"[{solver}] {tipo:11s} {ok}/{n}  |  {'  '.join(exemplos)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--solver", choices=["classico", "llm"], default="classico")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--tipos", default="math,texto,distorcido")
    a = ap.parse_args()
    rodar(a.solver, a.tipos.split(","), a.n)
