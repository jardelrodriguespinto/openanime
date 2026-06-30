"""
Preenchedor de formularios com LLM — responde perguntas customizadas de candidatura.
Nunca inventa qualificacoes que o candidato nao tem.
"""

import logging
import re

from ai.openrouter import openrouter

logger = logging.getLogger(__name__)

SYSTEM = """You answer job application form questions based on the candidate's resume text.

IMPORTANT RULES:
- Respond in the SAME LANGUAGE as the form question (if the question is in Portuguese, answer in Portuguese; if in English, answer in English)
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
4. Match the language of your response to the language of the question
5. Keep answers concise and directly address the question asked

Resume text is the PRIMARY source — use it FIRST for every answer."""


def responder_pergunta(
    pergunta: str,
    perfil: dict,
    vaga_titulo: str = "",
    vaga_empresa: str = "",
    resumo_curriculo: str = "",
    idioma: str = "pt",
) -> str:
    """
    Gera resposta para pergunta de formulario de candidatura.
    Baseado primeiro no resumo do curriculo fornecido pelo usuario, depois no perfil.
    Responde no idioma da pergunta (detectado externamente via idioma param).
    Suporta perguntas SELECT:label:opcoes, RADIO:label:opcoes e NUMERO:label.
    """
    # NUMERO: campo inteiro — retorna apenas dígitos (ex: anos de experiência)
    if pergunta.startswith("NUMERO:"):
        pergunta_real = pergunta[7:].strip()
        resposta_bruta = _responder_pergunta_raw(
            f"[RESPONDA APENAS COM UM NÚMERO INTEIRO, SEM TEXTO] {pergunta_real}",
            perfil, vaga_titulo, vaga_empresa, resumo_curriculo, idioma
        )
        m = re.search(r'\b\d+\b', resposta_bruta)
        if m:
            return m.group()
        digitos = re.sub(r'[^\d]', '', resposta_bruta)
        return digitos or "1"

    # DECIMAL: campo decimal — retorna número com ponto (ex: 2.5, 10000.0)
    if pergunta.startswith("DECIMAL:"):
        pergunta_real = pergunta[8:].strip()
        resposta_bruta = _responder_pergunta_raw(
            f"[RESPONDA APENAS COM UM NÚMERO DECIMAL USANDO PONTO, EX: 2.5 ou 10000.0, SEM TEXTO] {pergunta_real}",
            perfil, vaga_titulo, vaga_empresa, resumo_curriculo, idioma
        )
        m = re.search(r'\d+[.,]\d+|\d+', resposta_bruta)
        if m:
            return m.group().replace(',', '.')
        return re.sub(r'[^\d.]', '', resposta_bruta) or "1.0"

    return _responder_pergunta_raw(pergunta, perfil, vaga_titulo, vaga_empresa, resumo_curriculo, idioma)


def _responder_pergunta_raw(
    pergunta: str,
    perfil: dict,
    vaga_titulo: str = "",
    vaga_empresa: str = "",
    resumo_curriculo: str = "",
    idioma: str = "pt",
) -> str:
    """Internamente gera resposta, sem tratar prefixos especiais."""
    perfil_resumido = _resumir_perfil(perfil)

    opcoes_disponiveis = ""
    pergunta_real = pergunta

    if pergunta.startswith("SELECT:") or pergunta.startswith("RADIO:"):
        parts = pergunta.split(":", 2)
        pergunta_real = parts[1] if len(parts) > 1 else pergunta
        opcoes_raw = parts[2] if len(parts) > 2 else ""
        if opcoes_raw:
            opcoes_disponiveis = f"\nAVAILABLE OPTIONS (you MUST choose exactly one of these): {opcoes_raw}\nIMPORTANT: Reply with ONLY the exact text of the chosen option, nothing else."

    idioma_instrucao = (
        "Answer in PORTUGUESE (Brazil)." if idioma == "pt"
        else "Answer in ENGLISH."
    )

    contexto_parts = []
    if resumo_curriculo and resumo_curriculo.strip():
        contexto_parts.append(f"RESUME TEXT (PRIMARY SOURCE - use this first):\n{resumo_curriculo.strip()}")
    if perfil_resumido and perfil_resumido != "Perfil nao informado":
        contexto_parts.append(f"STRUCTURED PROFILE DATA (supplementary):\n{perfil_resumido}")

    contexto = f"""Candidate applying for: {vaga_titulo} at {vaga_empresa}

{chr(10).join(contexto_parts)}

Form question: {pergunta_real}{opcoes_disponiveis}

IMPORTANT: {idioma_instrucao} Base your answer primarily on the resume text. If the resume doesn't mention relevant info, use the structured profile data or politely indicate willingness to learn. Never claim skills not present in the resume.
For numeric questions (years of experience, etc.), reply with ONLY the number."""

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": contexto},
    ]

    try:
        resposta = openrouter.converse(messages)
        logger.info(
            "form_filler: pergunta respondida | pergunta=%s... | resposta=%s...",
            pergunta_real[:50], resposta[:50]
        )
        return resposta.strip()
    except Exception as e:
        logger.error("form_filler: erro LLM: %s", e)
        return _resposta_fallback(pergunta, perfil, idioma)


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


def _resposta_fallback(pergunta: str, perfil: dict, idioma: str = "pt") -> str:
    """Resposta basica sem LLM baseada em palavras-chave."""
    p = pergunta.lower()
    if any(x in p for x in ["pretensao", "salario", "remuneracao", "salary"]):
        valor = perfil.get("pretensao_salarial")
        if valor:
            return str(valor)
        return "A combinar" if idioma == "pt" else "Open to discussion based on benefits"
    if any(x in p for x in ["remoto", "modalidade", "trabalh", "remote", "work"]):
        valor = perfil.get("modalidade_preferida")
        if valor:
            return str(valor)
        return "Flexível" if idioma == "pt" else "Open to discussion"
    if any(x in p for x in ["disponibilidade", "quando", "inicio", "availability", "start"]):
        return "Imediata" if idioma == "pt" else "Immediate availability"
    return "Disponível mediante contato" if idioma == "pt" else "Information available upon request"
