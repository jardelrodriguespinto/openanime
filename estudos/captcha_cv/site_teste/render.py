"""Renderização dos CAPTCHAs do site de teste (compartilhada com o solver clássico).

Tudo é gerado LOCALMENTE. Fonte fixa (DejaVuSans) → o solver clássico consegue casar
templates. O tipo 'distorcido' adiciona rotação + ruído + linhas pra mostrar o limite
do template matching (é onde a IA de visão ganha).
"""
import random
import string

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ALFABETO = string.ascii_lowercase + string.digits
OPERADORES = "+-x"
CELL_W, CELL_H, FONT_SIZE = 44, 64, 40


def _fonte():
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, FONT_SIZE)
        except Exception:
            pass
    return ImageFont.load_default(FONT_SIZE)


FONTE = _fonte()


def render_char(ch: str) -> Image.Image:
    img = Image.new("L", (CELL_W, CELL_H), 255)
    d = ImageDraw.Draw(img)
    bb = d.textbbox((0, 0), ch, font=FONTE)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((CELL_W - w) // 2 - bb[0], (CELL_H - h) // 2 - bb[1]), ch, fill=0, font=FONTE)
    return img


def render_sequencia(txt: str, distorcer: bool = False) -> Image.Image:
    img = Image.new("L", (CELL_W * len(txt), CELL_H), 255)
    for i, ch in enumerate(txt):
        c = render_char(ch)
        if distorcer:
            c = c.rotate(random.uniform(-28, 28), expand=False, fillcolor=255)
        img.paste(c, (i * CELL_W, 0))
    if distorcer:
        a = np.array(img).astype(int) + np.random.randint(-45, 45, (img.height, img.width))
        img = Image.fromarray(np.clip(a, 0, 255).astype("uint8"), "L")
        d = ImageDraw.Draw(img)
        for _ in range(3):
            pts = [(random.randint(0, img.width), random.randint(0, img.height)) for _ in range(2)]
            d.line(pts, fill=random.randint(0, 120), width=1)
    return img


def texto_aleatorio(n: int = 5) -> str:
    return "".join(random.choice(ALFABETO) for _ in range(n))


def gerar_math():
    a, b = random.randint(1, 9), random.randint(1, 9)
    op = random.choice(["+", "-", "x"])
    val = a + b if op == "+" else (a - b if op == "-" else a * b)
    return f"{a}{op}{b}", str(val)
