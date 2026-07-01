"""
Preenchedor de formularios com LLM — responde perguntas customizadas de candidatura.
Nunca inventa qualificacoes que o candidato nao tem.
"""

import logging
import re

from ai.openrouter import openrouter

logger = logging.getLogger(__name__)

SYSTEM = """You are filling out a job application form AS the candidate. Write all responses in FIRST PERSON.

IMPORTANT RULES:
- ALWAYS write in FIRST PERSON ("I", "my", "me") — NEVER refer to "the candidate", "he", "she", or any third-person form
- Respond in the SAME LANGUAGE as the form question (Portuguese question → Portuguese answer; English question → English answer)
- Base your answers primarily on the provided resume text
- Be honest — never claim skills or experiences not mentioned in the resume
- If the resume doesn't mention something relevant, be honest and show willingness to learn
- Professional but natural tone, not robotic
- Maximum 150 words per answer
- Direct and specific answers, not generic

When answering:
1. First check if the resume text contains relevant information
2. If yes, use that specific information to craft your answer IN FIRST PERSON
3. If no relevant info is found, use the profile data as fallback
4. Match the language of your response to the language of the question
5. Keep answers concise and directly address the question asked

WRONG: "The candidate has 3 years of Python experience."
RIGHT: "I have 3 years of Python experience."

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
    # REMUNERAÇÃO: NUNCA deixa a IA "chutar" salário (chegou a gerar R$ 75 trilhões).
    # Usa os valores configurados no perfil (CLT/PJ/dólar). Vale p/ todas as automações.
    _plain = pergunta
    for _pre in ("NUMERO:", "DECIMAL:", "SELECT:", "RADIO:", "CHECKBOX:"):
        if _plain.startswith(_pre):
            _plain = _plain.split(":", 1)[1] if _pre in ("SELECT:", "RADIO:") else _plain[len(_pre):]
            break
    if _eh_pergunta_remuneracao(_plain):
        val = _valor_remuneracao(perfil, _plain)
        if val:
            if pergunta.startswith(("NUMERO:", "DECIMAL:")):
                if pergunta.startswith("DECIMAL:"):
                    m = re.search(r'\d+(?:[.,]\d+)?', val)
                    return m.group().replace(",", ".") if m else re.sub(r'[^\d]', '', val)
                return re.sub(r'[^\d]', '', val) or val  # NUMERO: só dígitos
            return val

    # NUMERO: campo inteiro — retorna apenas dígitos (ex: anos de experiência).
    # Sem info (ex: "anos de experiência com .NET" e o candidato não tem) → "0",
    # nunca vazio: campo obrigatório em branco faz o LinkedIn descartar a candidatura.
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
        return digitos or "0"

    # DECIMAL: campo decimal — retorna número com ponto (ex: 2.5, 10000.0).
    # Sem info → "0" (nunca vazio), mesma razão do NUMERO.
    if pergunta.startswith("DECIMAL:"):
        pergunta_real = pergunta[8:].strip()
        resposta_bruta = _responder_pergunta_raw(
            f"[RESPONDA APENAS COM UM NÚMERO DECIMAL USANDO PONTO, EX: 2.5 ou 10000.0, SEM TEXTO] {pergunta_real}",
            perfil, vaga_titulo, vaga_empresa, resumo_curriculo, idioma
        )
        m = re.search(r'\d+[.,]\d+|\d+', resposta_bruta)
        if m:
            return m.group().replace(',', '.')
        return re.sub(r'[^\d.]', '', resposta_bruta) or "0"

    resposta = _responder_pergunta_raw(pergunta, perfil, vaga_titulo, vaga_empresa, resumo_curriculo, idioma)
    # Garante não-vazio: campo obrigatório em branco descarta a candidatura.
    return resposta.strip() or resposta_segura(pergunta, idioma)


def resposta_segura(pergunta: str, idioma: str = "pt") -> str:
    """Valor de preenchimento que NUNCA é vazio — usado como último recurso quando
    não há informação e o LLM não respondeu, para o campo obrigatório não travar
    nem descartar a candidatura. Numérico → '0'; alfanumérico → 'Nenhum'/'None'."""
    if pergunta.startswith(("NUMERO:", "DECIMAL:")):
        return "0"
    if pergunta.startswith(("SELECT:", "RADIO:", "CHECKBOX:")):
        # Opção/consentimento: 'não' é o default seguro e não-vazio.
        return "não" if idioma == "pt" else "no"
    return "Nenhum" if idioma == "pt" else "None"


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

    # CHECKBOX: responde "sim" para consentimentos/termos, ou pergunta ao LLM
    if pergunta.startswith("CHECKBOX:"):
        pergunta_real = pergunta[9:].strip()
        consent_kw = ("agree", "consent", "aceito", "concordo", "confirmo", "autorizo",
                      "terms", "termos", "privacy", "privacidade", "policy", "política",
                      "acknowledge", "declaro", "confirm")
        if any(kw in pergunta_real.lower() for kw in consent_kw):
            return "sim"
        return _responder_pergunta_raw(
            f"[RESPONDA APENAS: sim OU não] Você marcaria este checkbox? {pergunta_real}",
            perfil, vaga_titulo, vaga_empresa, resumo_curriculo, idioma
        )

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

    contexto = f"""You are applying for the position of {vaga_titulo} at {vaga_empresa}. Answer all questions in FIRST PERSON as if you are the applicant.

{chr(10).join(contexto_parts)}

Form question: {pergunta_real}{opcoes_disponiveis}

IMPORTANT: {idioma_instrucao} Answer IN FIRST PERSON. Base your answer primarily on the resume text. If the resume doesn't mention relevant info, use the structured profile data or politely indicate willingness to learn. Never claim skills not present in the resume.
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
        # LLM pode devolver vazio — cai no fallback por palavra-chave (nunca vazio).
        return resposta.strip() or _resposta_fallback(pergunta, perfil, idioma)
    except Exception as e:
        logger.error("form_filler: erro LLM: %s", e)
        return _resposta_fallback(pergunta, perfil, idioma)


def _eh_pergunta_remuneracao(pergunta: str) -> bool:
    p = (pergunta or "").lower()
    return any(k in p for k in (
        "remunera", "salári", "salari", "pretens", "salary", "compensation",
        "remuneration", "expected pay", "expected salary",
    ))


def _valor_remuneracao(perfil: dict, pergunta: str) -> str:
    """Escolhe o valor de remuneração configurado conforme o TIPO pedido na pergunta
    (PJ / CLT / dólar). Fallback: CLT → pretensao_salarial."""
    p = (pergunta or "").lower()
    if any(k in p for k in ("dólar", "dolar", "usd", "us$", "dollar")):
        v = perfil.get("remuneracao_dolar")
    elif any(k in p for k in ("pj", "pessoa jur", "cnpj", "jurídica", "juridica")):
        v = perfil.get("remuneracao_pj")
    elif "clt" in p:
        v = perfil.get("remuneracao_clt")
    else:
        v = perfil.get("remuneracao_clt") or perfil.get("pretensao_salarial")
    return str(v).strip() if v not in (None, "") else ""


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
