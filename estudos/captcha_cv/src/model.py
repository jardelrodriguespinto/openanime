"""Modelo funcional (PyTorch) — CNN fixed-length para ler N caracteres de uma vez.

Abordagem: a imagem tem N caracteres de um alfabeto de tamanho V; a rede produz N
distribuições de V classes (N "cabeças"). Simples, treina rápido e é ótimo pra estudar.
Evolução natural depois: CNN + BiLSTM + CTC para comprimento variável.
"""
import string

import torch
import torch.nn as nn

ALFABETO = string.ascii_lowercase + string.digits
V = len(ALFABETO)                 # nº de classes por posição
IDX = {c: i for i, c in enumerate(ALFABETO)}

ALTURA_PADRAO = 60
LARGURA_PADRAO = 160


def texto_para_alvo(txt: str) -> torch.Tensor:
    """'abc12' -> tensor de índices [.,.,.,.,.]"""
    return torch.tensor([IDX[c] for c in txt], dtype=torch.long)


class CaptchaCNN(nn.Module):
    def __init__(self, n_chars: int, n_classes: int = V,
                 altura: int = ALTURA_PADRAO, largura: int = LARGURA_PADRAO):
        super().__init__()
        self.n_chars = n_chars
        self.n_classes = n_classes

        # extrator convolucional — 3 blocos conv→relu→pool
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(), nn.MaxPool2d(2),
        )
        # dimensão achatada calculada DINAMICAMENTE (robusto a mudanças de tamanho/arquitetura)
        with torch.no_grad():
            flat = self.features(torch.zeros(1, 1, altura, largura)).flatten(1).shape[1]
        self.classifier = nn.Sequential(
            nn.Linear(flat, 256), nn.ReLU(), nn.Dropout(0.3),
            nn.Linear(256, n_chars * n_classes),
        )

    def forward(self, x):  # x: (B, 1, H, W)
        z = self.features(x)
        z = torch.flatten(z, 1)
        logits = self.classifier(z)                          # (B, n_chars*n_classes)
        return logits.view(-1, self.n_chars, self.n_classes)  # (B, n_chars, n_classes)


def decodificar(logits: torch.Tensor) -> list:
    """(B, n_chars, n_classes) -> lista de strings previstas."""
    idxs = logits.argmax(dim=-1)  # (B, n_chars)
    return ["".join(ALFABETO[i] for i in linha) for linha in idxs.tolist()]
