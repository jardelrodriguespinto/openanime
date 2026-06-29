"""
Preenchedor de formularios com LLM — responde perguntas customizadas de candidatura.
Nunca inventa qualificacoes que o candidato nao tem.
"""

import logging

from ai.openrouter import openrouter

logger = logging.getLogger(__name__)

SYSTEM = """You answer job application form questions based on the candidate's resume text.

IMPORTANT RULES:
- Always respond in ENGLISH, regardless of the form language
- Base your answers primarily on the provided resume text
- Be honest — never claim skills or experiences not mentioned in the resume
- If the resume doesn't mention something relevant, be honest and show willingness to learn
- Professional but natural tone, not robotic
- Maximum 150 words per answer
- Direct and specific answers, not generic

When answering:
1. First check if the resume text contains relevant information
2. If yes, use that specific information to craft your answer
3. If no relevant info is found, use the profile data as fallback
4. Always translate the answer to English
5. Keep answers concise and directly address the question asked

Resume text is the PRIMARY source — use it FIRST for every answer."""


def responder_pergunta(pergunta: str, perfil: dict, vaga_titulo: str = "", vaga_empresa: str = "", resumo_curriculo: str = "") -> str:
    """
    Gera resposta para pergunta de formulario de candidatura.
    Baseado primeiro no resumo do curriculo fornecido pelo usuario, depois no perfil.
    Sempre responde em ingles.
    """
    perfil_resumido = _resumir_perfil(perfil)

    contexto_parts = []
    if resumo_curriculo and resumo_curriculo.strip():
        contexto_parts.append(f"RESUME TEXT (PRIMARY SOURCE - use this first):\n{resumo_curriculo.strip()}")
    if perfil_resumido and perfil_resumido != "Perfil nao informado":
        contexto_parts.append(f"STRUCTURED PROFILE DATA (supplementary):\n{perfil_resumido}")

    contexto = f"""Candidate applying for: {vaga_titulo} at {vaga_empresa}

{chr(10).join(contexto_parts)}

Form question: {pergunta}

IMPORTANT: Answer in ENGLISH based primarily on the resume text. If the resume doesn't mention relevant info, use the structured profile data or politely indicate willingness to learn. Never claim skills not present in the resume."""

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": contexto},
    ]

    try:
        resposta = openrouter.converse(messages)
        logger.info("form_filler: pergunta respondida | pergunta=%s...", pergunta[:50])
        return resposta
    except Exception as e:
        logger.error("form_filler: erro LLM: %s", e)
        return _resposta_fallback(pergunta, perfil)


def _resumir_perfil(perfil: dict) -> str:
    linhas = []
    if perfil.get("cargo_atual"):
        linhas.append(f"Cargo atual: {perfil['cargo_atual']}")
    if perfil.get("nivel_senioridade"):
        linhas.append(f"Senioridade: {perfil['nivel_senioridade']}")
    if perfil.get("habilidades"):
        skills = [h.get("nome", "") for h in perfil["habilidades"][:10]]
        linhas.append(f"Habilidades: {', '.join(skills)}")
    if perfil.get("experiencias"):
        exp = perfil["experiencias"][0]
        linhas.append(f"Ultima empresa: {exp.get('empresa', '')} — {exp.get('cargo', '')}")
    if perfil.get("pretensao_salarial"):
        linhas.append(f"Pretensao salarial: {perfil['pretensao_salarial']}")
    if perfil.get("modalidade_preferida"):
        linhas.append(f"Modalidade preferida: {perfil['modalidade_preferida']}")
    if perfil.get("localizacao"):
        linhas.append(f"Localizacao: {perfil['localizacao']}")
    return "\n".join(linhas) or "Perfil nao informado"


def _resposta_fallback(pergunta: str, perfil: dict) -> str:
    """Resposta basica sem LLM baseada em palavras-chave — sempre em ingles."""
    p = pergunta.lower()
    if any(x in p for x in ["pretensao", "salario", "remuneracao", "salary"]):
        return perfil.get("pretensao_salarial") or "Open to discussion based on benefits"
    if any(x in p for x in ["remoto", "modalidade", "trabalh", "remote", "work"]):
        return perfil.get("modalidade_preferida") or "Open to discussion"
    if any(x in p for x in ["disponibilidade", "quando", "inicio", "availability", "start"]):
        return "Immediate availability"
    return "Information available upon request"
