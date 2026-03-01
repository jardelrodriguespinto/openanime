"""
Agente de perfil profissional — gerencia habilidades, experiencias e preferencias de emprego.
"""

import json
import logging

from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
import prompts.profile_pro as pp_prompt

logger = logging.getLogger(__name__)

_KEYWORDS_MOSTRAR = [
    "mostra", "ver", "mostrar", "exibir", "qual", "meu perfil",
    "minhas habilidades", "minha experiencia",
]
_KEYWORDS_EDITAR = [
    "atualiza", "muda", "altera", "adiciona", "remove", "coloca",
    "minha pretensao", "quero trabalhar", "sou senior", "sou junior",
    # Referências a contexto anterior (CV enviado, análise anterior)
    "guarde", "salva", "salve", "preencha", "preenche", "este sou eu",
    "sou eu", "minhas infos", "meus dados", "pega o que mandei",
    "pega o curriculo", "usa o curriculo", "com base no que mandei",
]


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


def profile_pro_node(state: dict) -> dict:
    """No LangGraph do agente de perfil profissional."""
    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "").lower()

    neo4j = get_neo4j()
    neo4j.get_or_create_user(user_id)

    mostrar = any(kw in mensagem for kw in _KEYWORDS_MOSTRAR)
    editar = any(kw in mensagem for kw in _KEYWORDS_EDITAR)

    if mostrar and not editar:
        return _mostrar_perfil(user_id, state.get("raw_input", ""))

    # Tenta extrair dados da mensagem + histórico (captura CVs analisados anteriormente)
    history = state.get("messages", [])
    messages = pp_prompt.build_extracao_messages(state.get("raw_input", ""), history)
    try:
        raw = openrouter.orchestrate(messages)
        dados = _parse_json_safe(raw)
    except Exception as e:
        logger.debug("profile_pro: erro extracao: %s", e)
        dados = {}

    _campos_relevantes = (
        dados.get("habilidades") or dados.get("pretensao_salarial") or
        dados.get("modalidade_preferida") or dados.get("nome") or
        dados.get("experiencias") or dados.get("formacao") or dados.get("cargo_atual")
    )
    if dados and _campos_relevantes:
        _aplicar_atualizacoes(user_id, dados)
        return _mostrar_perfil(user_id, "me mostra o perfil atualizado", confirmacao=True)

    return _mostrar_perfil(user_id, state.get("raw_input", ""))


def _mostrar_perfil(user_id: str, mensagem: str, confirmacao: bool = False) -> dict:
    """Busca perfil e gera resposta descritiva."""
    try:
        neo4j = get_neo4j()
        perfil = neo4j.get_perfil_profissional(user_id)
        score = neo4j.get_score_completude_perfil(user_id)

        perfil["score_completude"] = score

        messages = pp_prompt.build_perfil_messages(perfil, mensagem)
        response = openrouter.converse(messages)

        if confirmacao:
            response = "Perfil atualizado!\n\n" + response

    except Exception as e:
        logger.error("profile_pro: erro ao mostrar perfil: %s", e)
        response = "Nao consegui carregar seu perfil agora. Tenta de novo!"

    return {"response": response}


def _aplicar_atualizacoes(user_id: str, dados: dict) -> None:
    """Aplica atualizacoes extraidas da mensagem no Neo4j."""
    try:
        neo4j = get_neo4j()
        # salvar_perfil_profissional já lida com campos básicos, habilidades,
        # experiencias, formacao e idiomas de uma vez
        neo4j.salvar_perfil_profissional(user_id, dados)

        for cargo in dados.get("cargos_desejados", []):
            if cargo:
                neo4j.adicionar_cargo_desejado(user_id, cargo)

        logger.info("profile_pro: atualizacoes aplicadas user=%s", user_id)
    except Exception as e:
        logger.warning("profile_pro: erro ao aplicar atualizacoes: %s", e)
