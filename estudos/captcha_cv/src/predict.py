"""Inferência FUNCIONAL numa imagem local.

Uso:
    python src/predict.py data/val/abc12_3.png
"""
import argparse
from pathlib import Path

import torch

import sys
sys.path.append(str(Path(__file__).parent))
from preprocess import pipeline               # noqa: E402
from model import CaptchaCNN, decodificar     # noqa: E402

RAIZ = Path(__file__).parent.parent
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


def prever(caminho: str) -> str:
    pesos = RAIZ / "modelo.pt"
    if not pesos.exists():
        raise RuntimeError("Treine primeiro (src/train.py) — modelo.pt não encontrado.")
    ckpt = torch.load(pesos, map_location=DEVICE)
    model = CaptchaCNN(n_chars=ckpt["n_chars"]).to(DEVICE)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    x = torch.from_numpy(pipeline(caminho)).unsqueeze(0).unsqueeze(0).to(DEVICE)  # (1,1,H,W)
    with torch.no_grad():
        logits = model(x)
    return decodificar(logits)[0]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("imagem")
    args = ap.parse_args()
    rotulo_real = Path(args.imagem).name.split("_")[0]  # <texto>_<n>.png
    pred = prever(args.imagem)
    print(f"previsto: {pred}   (rótulo real: {rotulo_real}   {'✓' if pred == rotulo_real else '✗'})")
