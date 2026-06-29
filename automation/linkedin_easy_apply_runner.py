#!/usr/bin/env python3
"""
Runner: loga no LinkedIn, abre a página Easy Apply e aplica automaticamente
nas vagas que aparecem, usando Selenium + Firefox (visible).

Uso:
    python3 -m automation.linkedin_easy_apply_runner --max 10
    python3 -m automation.linkedin_easy_apply_runner --max 5 --nome "Joao Silva" --telefone "11999999999"

Variáveis .env utilizadas:
    LINKEDIN_EMAIL, LINKEDIN_PASSWORD
"""

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _build_perfil(args) -> dict:
    perfil = {
        "nome": args.nome or os.getenv("LINKEDIN_NOME", "Joao Silva"),
        "email": args.email or os.getenv("LINKEDIN_EMAIL", ""),
        "telefone": args.telefone or os.getenv("LINKEDIN_TELEFONE", ""),
        "localizacao": args.cidade or os.getenv("LINKEDIN_CIDADE", "Brasil"),
        "cargo_atual": os.getenv("LINKEDIN_CARGO", "Desenvolvedor"),
        "nivel_senioridade": os.getenv("LINKEDIN_SENIORIDADE", "Junior"),
        "habilidades": [],
        "experiencias": [],
        "pretensao_salarial": os.getenv("LINKEDIN_PRETENSAO", ""),
        "modalidade_preferida": os.getenv("LINKEDIN_MODALIDADE", ""),
    }
    return perfil


async def main():
    parser = argparse.ArgumentParser(description="LinkedIn Easy Apply Auto-Runner")
    parser.add_argument("--max", type=int, default=10, help="Número máximo de vagas para aplicar (padrão: 10)")
    parser.add_argument("--nome", type=str, default=None, help="Nome completo")
    parser.add_argument("--email", type=str, default=None, help="Email de contato")
    parser.add_argument("--telefone", type=str, default=None, help="Telefone/WhatsApp")
    parser.add_argument("--cidade", type=str, default=None, help="Cidade/Estado")
    args = parser.parse_args()

    email = os.getenv("LINKEDIN_EMAIL", "")
    password = os.getenv("LINKEDIN_PASSWORD", "")
    if not email or not password:
        print("[RUNNER] ERRO: Configure LINKEDIN_EMAIL e LINKEDIN_PASSWORD no .env")
        sys.exit(1)

    perfil = _build_perfil(args)
    logger.info("Perfil: nome=%s email=%s telefone=%s cidade=%s", perfil["nome"], perfil["email"], perfil["telefone"], perfil["localizacao"])

    from automation.linkedin_selenium import aplicar_vagas_visiveis_na_pagina

    print(f"\n[RUNNER] Iniciando auto-apply LinkedIn Easy Apply (até {args.max} vagas)")
    print("[RUNNER] O browser Firefox vai abrir. NÃO mova o mouse ou use o teclado durante a automação.\n")

    resultado = await aplicar_vagas_visiveis_na_pagina(perfil, max_vagas=args.max)

    apps = resultado.get("aplicacoes", [])
    falhas = resultado.get("falhas", 0)
    total = len(apps)
    sucessos = sum(1 for a in apps if a.get("sucesso"))

    print(f"\n[RUNNER] Concluído: {total} aplicações | {sucessos} sucesso(s) | {falhas} falha(s)")
    for i, app in enumerate(apps, 1):
        status = "✅" if app.get("sucesso") else "❌"
        msg = app.get("mensagem", "")[:80]
        print(f"  {i}. {status} {msg}")

    if falhas > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
