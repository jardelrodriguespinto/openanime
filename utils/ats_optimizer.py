"""
Otimizador de curriculo para ATS — usa LLM para personalizar para vaga especifica.
"""

import json
import logging

from ai.openrouter import openrouter

logger = logging.getLogger(__name__)

SYSTEM_ATS = """Voce e um especialista em curriculos ATS. Personalize o curriculo para a vaga.

Regras ATS obrigatorias:
- Layout de uma coluna (sem colunas paralelas)
- Fontes padrao
- Sem tabelas para layout
- Keywords da vaga injetadas naturalmente
- Bullets no formato: "Verbo de acao + o que fez + resultado/impacto"
- Nunca invente experiencias ou habilidades que o candidato nao tem
- Use keywords da vaga naturalmente, nao em excesso

Retorne APENAS JSON valido:
{
  "objetivo": "2 linhas especificas para esta vaga",
  "habilidades": ["lista", "ordenada", "por", "relevancia"],
  "experiencias": [
    {
      "empresa": "...",
      "cargo": "...",
      "periodo": "MM/AAAA - MM/AAAA",
      "bullets": ["bullet 1", "bullet 2", "bullet 3"]
    }
  ],
  "keywords_usadas": ["keyword1", "keyword2"]
}
"""


def _parse_json_safe(raw: str) -> dict:
    raw = (raw or "").strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except Exception:
            pass
    return {}


def otimizar_para_vaga(perfil: dict, vaga_titulo: str, vaga_empresa: str,
                       vaga_descricao: str, vaga_requisitos: list[str]) -> dict:
    """
    Usa LLM para personalizar o curriculo para a vaga especifica.
    Retorna dict pronto para o template HTML.
    """
    perfil_resumido = {
        "habilidades": [h.get("nome", "") for h in perfil.get("habilidades", [])[:15]],
        "experiencias": perfil.get("experiencias", [])[:5],
        "formacao": perfil.get("formacao", []),
        "nivel_senioridade": perfil.get("nivel_senioridade", ""),
    }

    prompt_user = f"""PERFIL DO CANDIDATO:
{json.dumps(perfil_resumido, ensure_ascii=False, indent=2)}

VAGA ALVO:
Titulo: {vaga_titulo}
Empresa: {vaga_empresa}
Descricao: {vaga_descricao[:2000]}
Requisitos: {', '.join(vaga_requisitos[:20])}

Personalize o curriculo para esta vaga seguindo as regras ATS."""

    messages = [
        {"role": "system", "content": SYSTEM_ATS},
        {"role": "user", "content": prompt_user},
    ]

    try:
        raw = openrouter.converse(messages)
        otimizado = _parse_json_safe(raw)
    except Exception as e:
        logger.error("ats_optimizer: erro LLM: %s", e)
        otimizado = {}

    # Monta contexto completo para o template
    resultado = {
        "nome": perfil.get("nome", ""),
        "email": perfil.get("email", ""),
        "telefone": perfil.get("telefone", ""),
        "linkedin": perfil.get("linkedin", ""),
        "github": perfil.get("github", ""),
        "portfolio": perfil.get("portfolio", ""),
        "localizacao": perfil.get("localizacao", ""),
        "objetivo": otimizado.get("objetivo", ""),
        "habilidades": otimizado.get("habilidades", [h.get("nome", "") for h in perfil.get("habilidades", [])[:12]]),
        "experiencias": _formatar_experiencias(otimizado.get("experiencias", []), perfil),
        "formacao": _formatar_formacao(perfil.get("formacao", [])),
        "idiomas": perfil.get("idiomas", []),
    }

    logger.info("ats_optimizer: curriculo otimizado para vaga=%s keywords=%d",
                vaga_titulo, len(otimizado.get("keywords_usadas", [])))
    return resultado


def _formatar_experiencias(experiencias_otimizadas: list, perfil: dict) -> list:
    """Usa experiencias otimizadas ou fallback para as do perfil."""
    if experiencias_otimizadas:
        return experiencias_otimizadas

    # Fallback: usa experiencias brutas do perfil
    resultado = []
    for exp in perfil.get("experiencias", [])[:4]:
        resultado.append({
            "empresa": exp.get("empresa", ""),
            "cargo": exp.get("cargo", ""),
            "periodo": f"{exp.get('inicio', '')} - {exp.get('fim', 'Atual')}",
            "bullets": [exp.get("descricao", "")] if exp.get("descricao") else [],
        })
    return resultado


def _formatar_formacao(formacao_list: list) -> list:
    resultado = []
    for f in formacao_list[:3]:
        resultado.append({
            "curso": f.get("curso", ""),
            "instituicao": f.get("instituicao", ""),
            "nivel": f.get("nivel", ""),
            "ano": f.get("ano", ""),
        })
    return resultado
