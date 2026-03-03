"""Agente de ranking pessoal — tops personalizados do histórico do usuário."""
import json
import logging

from agents.orchestrator import State
from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
import prompts.ranking as ranking_prompt

logger = logging.getLogger(__name__)


def ranking_node(state: State) -> dict:
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    neo4j = get_neo4j()
    user_profile = {}
    try:
        user_profile = neo4j.get_user_profile(user_id)
    except Exception as e:
        logger.warning("ranking: erro ao carregar perfil: %s", e)

    messages = ranking_prompt.build_messages(user_message, history, user_profile)

    try:
        raw = openrouter.converse(messages)
        data = _parse_json(raw)
    except Exception as e:
        logger.error("ranking: erro LLM: %s", e)
        return {"response": "Não consegui montar o ranking agora."}

    action = data.get("action", "ranking_geral")
    mensagem = data.get("mensagem", "")
    genero = (data.get("genero") or "").strip() or None
    ano = data.get("ano")
    tipo = (data.get("tipo") or "").strip() or None
    limit = int(data.get("limit") or 10)

    logger.info("ranking: user=%s action=%s", user_id, action)

    try:
        if action in ("ranking_geral", "ranking_por_genero", "ranking_por_ano", "ranking_por_tipo"):
            ano_i = int(ano) if ano else None
            items = neo4j.get_ranking_filtrado(
                user_id, genero=genero, ano=ano_i, tipo=tipo, limit=limit
            )
            mensagem = _formatar_ranking(items, genero=genero, ano=ano_i, tipo=tipo)

        elif action == "top_drops":
            dropados = user_profile.get("dropados", [])
            if not dropados:
                mensagem = "Você não tem drops registrados."
            else:
                linhas = ["<b>Suas obras dropadas:</b>"]
                for d in dropados[:15]:
                    ep = d.get("episodio")
                    ep_txt = f" (ep {ep})" if ep else ""
                    linhas.append(f"- {d.get('titulo', '?')}{ep_txt}")
                mensagem = "\n".join(linhas)

        elif not mensagem:
            mensagem = "Me diz que tipo de ranking você quer! Ex: \"meu top 10\", \"melhores animes de 2023\"."

    except Exception as e:
        logger.error("ranking: erro action=%s: %s", action, e)
        mensagem = "Tive um problema ao gerar o ranking."

    return {"response": mensagem}


def _formatar_ranking(items: list[dict], genero=None, ano=None, tipo=None) -> str:
    if not items:
        filtros = []
        if genero:
            filtros.append(f"gênero \"{genero}\"")
        if ano:
            filtros.append(str(ano))
        if tipo:
            filtros.append(tipo)
        filtro_txt = " de " + ", ".join(filtros) if filtros else ""
        return f"Sem obras{filtro_txt} com nota registrada ainda."

    filtros = []
    if genero:
        filtros.append(genero)
    if ano:
        filtros.append(str(ano))
    if tipo:
        filtros.append(tipo)
    titulo_ranking = "Seu ranking" + (f" — {', '.join(filtros)}" if filtros else "")

    linhas = [f"<b>{titulo_ranking}:</b>"]
    for i, item in enumerate(items, start=1):
        nota = item.get("nota")
        nota_txt = f"{nota}/10" if nota is not None else "sem nota"
        tipo_txt = f" [{item.get('tipo','?')}]" if not tipo else ""
        linhas.append(f"{i}. {item.get('titulo', '?')} — {nota_txt}{tipo_txt}")
    return "\n".join(linhas)


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except Exception:
            pass
    return {"action": "ranking_geral", "mensagem": raw}
