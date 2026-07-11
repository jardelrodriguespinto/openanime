"""Loop de treino FUNCIONAL. Treina a CNN nos CAPTCHAs locais e reporta acurácia.

Dataset: lê data/train e data/val, rótulo = prefixo do nome do arquivo (<texto>_<n>.png).

Uso:
    python src/train.py --epochs 15 --batch 64
"""
import argparse
from pathlib import Path

import torch
from torch.utils.data import Dataset, DataLoader

import sys
sys.path.append(str(Path(__file__).parent))
from preprocess import pipeline                                  # noqa: E402
from model import CaptchaCNN, texto_para_alvo, decodificar       # noqa: E402

RAIZ = Path(__file__).parent.parent
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


class CaptchaDataset(Dataset):
    def __init__(self, split: str):
        self.arquivos = sorted((RAIZ / "data" / split).glob("*.png"))
        if not self.arquivos:
            raise RuntimeError(f"Sem imagens em data/{split} — rode gerar_dataset.py antes.")

    def __len__(self):
        return len(self.arquivos)

    def __getitem__(self, i):
        arq = self.arquivos[i]
        texto = arq.name.split("_")[0]
        x = torch.from_numpy(pipeline(str(arq))).unsqueeze(0)  # (1, H, W)
        y = texto_para_alvo(texto)                             # (n_chars,)
        return x, y


@torch.no_grad()
def avaliar(model, dl):
    """Acurácia por CARACTERE e por CAPTCHA INTEIRO."""
    model.eval()
    chars_ok = chars_tot = full_ok = full_tot = 0
    for x, y in dl:
        x, y = x.to(DEVICE), y.to(DEVICE)
        pred = model(x).argmax(dim=-1)          # (B, n_chars)
        chars_ok += (pred == y).sum().item()
        chars_tot += y.numel()
        full_ok += (pred == y).all(dim=1).sum().item()
        full_tot += y.size(0)
    return chars_ok / chars_tot, full_ok / full_tot


def treinar(epochs: int = 15, batch: int = 64, lr: float = 1e-3):
    train_ds, val_ds = CaptchaDataset("train"), CaptchaDataset("val")
    n_chars = len(train_ds.arquivos[0].name.split("_")[0])

    train_dl = DataLoader(train_ds, batch_size=batch, shuffle=True)
    val_dl = DataLoader(val_ds, batch_size=batch)

    model = CaptchaCNN(n_chars=n_chars).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = torch.nn.CrossEntropyLoss()
    print(f"device={DEVICE} | n_chars={n_chars} | treino={len(train_ds)} val={len(val_ds)}")

    for ep in range(epochs):
        model.train()
        perda_soma = 0.0
        for x, y in train_dl:
            x, y = x.to(DEVICE), y.to(DEVICE)               # x:(B,1,H,W) y:(B,n_chars)
            logits = model(x)                               # (B, n_chars, n_classes)
            # CrossEntropy espera (N, C) vs (N,): achata as N posições de todos os itens
            loss = criterion(logits.reshape(-1, model.n_classes), y.reshape(-1))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            perda_soma += loss.item() * x.size(0)

        acc_c, acc_f = avaliar(model, val_dl)
        print(f"epoch {ep+1:2d}/{epochs} | loss {perda_soma/len(train_ds):.3f} "
              f"| val char-acc {acc_c:.3f} | val captcha-acc {acc_f:.3f}")

    torch.save({"state_dict": model.state_dict(), "n_chars": n_chars}, RAIZ / "modelo.pt")
    print("modelo salvo em modelo.pt")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    args = ap.parse_args()
    treinar(args.epochs, args.batch, args.lr)
