"""Pré-processamento de imagem com OpenCV — o primeiro elo do pipeline de estudo.

A ideia: transformar a imagem colorida/ruidosa num formato limpo e normalizado que o
modelo aprende melhor. A segmentação de caracteres fica como TODO — é um ótimo exercício
(projeção vertical, contornos, ou deixar a CNN aprender a sequência inteira com CTC).

Uso:
    python src/preprocess.py data/train/abc12_0.png --show
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

ALTURA_PADRAO = 60
LARGURA_PADRAO = 160


def carregar_cinza(caminho: str) -> np.ndarray:
    img = cv2.imread(caminho, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(caminho)
    return img


def limpar(img: np.ndarray) -> np.ndarray:
    """Binariza + remove ruído. Ponto de partida — refine à vontade no estudo."""
    # threshold adaptativo/Otsu costuma ir bem em CAPTCHA de texto
    _, bin_ = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # abertura morfológica tira pontos soltos
    kernel = np.ones((2, 2), np.uint8)
    return cv2.morphologyEx(bin_, cv2.MORPH_OPEN, kernel)


def normalizar(img: np.ndarray, largura: int = LARGURA_PADRAO, altura: int = ALTURA_PADRAO) -> np.ndarray:
    """Redimensiona e escala p/ [0,1] — pronto pra virar tensor."""
    img = cv2.resize(img, (largura, altura), interpolation=cv2.INTER_AREA)
    return img.astype("float32") / 255.0


def segmentar(img_bin: np.ndarray, min_larg: int = 4) -> list:
    """Separa caracteres por PROJEÇÃO VERTICAL (soma de pixels por coluna).

    Não é usado pela CNN fixed-length (que lê a imagem inteira), mas é um bom
    exercício de estudo e serve pra abordagens char-a-char. Retorna a lista de
    recortes (sub-imagens) ordenados da esquerda pra direita.
    Espera `img_bin` já binarizada (texto branco sobre fundo preto), ex.: limpar().
    """
    col = (img_bin > 0).sum(axis=0)          # nº de pixels de texto por coluna
    recortes, inicio = [], None
    for x, tem in enumerate(col > 0):
        if tem and inicio is None:
            inicio = x
        elif not tem and inicio is not None:
            if x - inicio >= min_larg:
                recortes.append(img_bin[:, inicio:x])
            inicio = None
    if inicio is not None and img_bin.shape[1] - inicio >= min_larg:
        recortes.append(img_bin[:, inicio:])
    return recortes


def pipeline(caminho: str) -> np.ndarray:
    return normalizar(limpar(carregar_cinza(caminho)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("imagem")
    ap.add_argument("--show", action="store_true", help="abre janelas com cada etapa")
    args = ap.parse_args()

    cinza = carregar_cinza(args.imagem)
    limpo = limpar(cinza)
    norm = normalizar(limpo)
    print(f"{Path(args.imagem).name}: shape final {norm.shape}, min={norm.min():.2f} max={norm.max():.2f}")
    if args.show:
        cv2.imshow("1-cinza", cinza)
        cv2.imshow("2-limpo", limpo)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
