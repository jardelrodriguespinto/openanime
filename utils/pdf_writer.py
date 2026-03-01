"""
Geracao de PDFs a partir de templates HTML via weasyprint.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _render_template(template_name: str, contexto: dict) -> str:
    """Renderiza template HTML simples com substituicao de variaveis (Jinja2 leve)."""
    try:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(template_name)
        return tmpl.render(**contexto)
    except ImportError:
        # Fallback sem jinja2 — substituicao manual basica
        template_path = TEMPLATES_DIR / template_name
        if not template_path.exists():
            raise FileNotFoundError(f"Template nao encontrado: {template_name}")
        html = template_path.read_text(encoding="utf-8")
        for key, val in contexto.items():
            if isinstance(val, str):
                html = html.replace("{{ " + key + " }}", val)
        return html


def gerar_pdf(template: str, conteudo: dict, nome_arquivo: str = "documento") -> bytes:
    """
    Gera PDF a partir de template HTML + dados.
    Retorna bytes do PDF prontos para enviar pelo Telegram.
    template: "report" | "resume_ats"
    """
    try:
        import weasyprint
    except ImportError:
        raise RuntimeError("weasyprint nao instalado. Execute: pip install weasyprint")

    template_file = f"{template}.html"
    html_str = _render_template(template_file, conteudo)

    # Caminho base para CSS relativo
    base_url = str(TEMPLATES_DIR) + "/"
    pdf_bytes = weasyprint.HTML(string=html_str, base_url=base_url).write_pdf()

    logger.info("pdf_writer: PDF gerado | template=%s | tamanho=%d bytes", template, len(pdf_bytes))
    return pdf_bytes


def gerar_pdf_report(titulo: str, tipo: str, paginas: int,
                     resumo: str, pontos_chave: list[str],
                     entidades: list[str], conteudo_extra: str = "") -> bytes:
    """Atalho para gerar relatorio de analise de PDF."""
    from datetime import date
    conteudo = {
        "titulo": titulo,
        "tipo": tipo,
        "paginas": paginas,
        "resumo": resumo,
        "pontos_chave": pontos_chave,
        "entidades": entidades,
        "conteudo_extra": conteudo_extra,
        "data_geracao": date.today().strftime("%d/%m/%Y"),
    }
    return gerar_pdf("report", conteudo)


def gerar_pdf_curriculo(dados: dict) -> bytes:
    """Atalho para gerar curriculo ATS."""
    return gerar_pdf("resume_ats", dados)
