"""
Preenchedor de formularios com LLM — responde perguntas customizadas de candidatura.
Nunca inventa qualificacoes que o candidato nao tem.
"""

import logging

from ai.openrouter import openrouter

logger = logging.getLogger(__name__)

SYSTEM = """Voce responde perguntas de formularios de candidatura com base no perfil do candidato.

Regras:
- Seja honesto — nunca afirme habilidades que o candidato nao tem
- Se nao tiver experiencia com algo, seja honesto e mostre disposicao para aprender
- Tom profissional mas natural, nao robotico
- Maximo 150 palavras por resposta
- Respostas diretas e especificas, nao genericas

Exemplos de respostas boas:
- "Tenho 3 anos com Python, principalmente em automacoes e APIs REST com FastAPI."
- "Nao trabalhei diretamente com Kubernetes, mas tenho experiencia com Docker e estou estudando K8s."
- "Minha pretensao e de R$ 8.000 a R$ 10.000 dependendo dos beneficios."
"""


def responder_pergunta(pergunta: str, perfil: dict, vaga_titulo: str = "", vaga_empresa: str = "") -> str:
    """
    Gera resposta para pergunta de formulario de candidatura.
    Baseado no perfil real do candidato.
    """
    perfil_resumido = _resumir_perfil(perfil)

    contexto = f"""Candidato se candidatando para: {vaga_titulo} na {vaga_empresa}

Perfil do candidato:
{perfil_resumido}

Pergunta do formulario: {pergunta}

Responda de forma honesta e especifica."""

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
        # Fallback com dados diretos do perfil
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
    """Resposta basica sem LLM baseada em palavras-chave."""
    p = pergunta.lower()
    if "pretensao" in p or "salario" in p or "remuneracao" in p:
        return perfil.get("pretensao_salarial") or "A combinar conforme beneficios"
    if "remoto" in p or "modalidade" in p or "trabalh" in p:
        return perfil.get("modalidade_preferida") or "Aberto a discussao"
    if "disponibilidade" in p or "quando" in p or "inicio" in p:
        return "Disponibilidade imediata"
    return "Informacao disponivel mediante contato"
