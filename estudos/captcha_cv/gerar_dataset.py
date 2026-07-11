"""Gera um dataset LOCAL de CAPTCHAs de texto para estudo.

Cada imagem é salva como  data/<split>/<texto>_<indice>.png  — o rótulo é o próprio
nome do arquivo (parse em src/train.py). Esta é a ÚNICA fonte de dados do projeto:
nada é baixado de serviços de terceiros.

Uso:
    python gerar_dataset.py --n 2000 --chars 5 --val 0.1
"""
import argparse
import random
import string
from pathlib import Path

from captcha.image import ImageCaptcha

RAIZ = Path(__file__).parent
ALFABETO = string.ascii_lowercase + string.digits  # ajuste como quiser estudar


def texto_aleatorio(n: int) -> str:
    return "".join(random.choice(ALFABETO) for _ in range(n))


def gerar(n: int, chars: int, val_frac: float, largura: int, altura: int) -> None:
    motor = ImageCaptcha(width=largura, height=altura)
    n_val = int(n * val_frac)
    for i in range(n):
        split = "val" if i < n_val else "train"
        destino = RAIZ / "data" / split
        destino.mkdir(parents=True, exist_ok=True)
        txt = texto_aleatorio(chars)
        motor.write(txt, str(destino / f"{txt}_{i}.png"))
    print(f"OK: {n - n_val} treino + {n_val} val em data/  (alfabeto={len(ALFABETO)} chars, comprimento={chars})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000, help="total de imagens")
    ap.add_argument("--chars", type=int, default=5, help="letras por CAPTCHA")
    ap.add_argument("--val", type=float, default=0.1, help="fração de validação")
    ap.add_argument("--largura", type=int, default=160)
    ap.add_argument("--altura", type=int, default=60)
    args = ap.parse_args()
    gerar(args.n, args.chars, args.val, args.largura, args.altura)
