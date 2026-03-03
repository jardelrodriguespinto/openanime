"""Agente de lembretes — cria, lista e cancela lembretes com hora:minuto exato."""
import json
import logging

from agents.orchestrator import State
from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
import prompts.lembrete as lembrete_prompt

logger = logging.getLogger(__name__)


def lembrete_node(state: State) -> dict:
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    neo4j = get_neo4j()
    lembretes = []
    try:
        lembretes = neo4j.listar_lembretes(user_id)
    except Exception as e:
        logger.warning("lembrete: erro ao listar: %s", e)

    messages = lembrete_prompt.build_messages(user_message, history, lembretes)

    try:
        raw = openrouter.converse(messages)
        data = _parse_json(raw)
    except Exception as e:
        logger.error("lembrete: erro LLM: %s", e)
        return {"response": "Não consegui processar o lembrete. Tenta de novo!"}

    action = data.get("action", "conversa")
    mensagem = data.get("mensagem", "")
    texto = (data.get("texto") or "").strip()
    datetime_disparo = (data.get("datetime_disparo") or "").strip()
    recorrente = bool(data.get("recorrente", False))
    lembrete_id = (data.get("lembrete_id") or "").strip()

    logger.info("lembrete: user=%s action=%s", user_id, action)

    try:
        if action == "criar_lembrete" and texto and datetime_disparo:
            lid = neo4j.criar_lembrete(user_id, texto, datetime_disparo, recorrente)
            # Formata para exibição
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(datetime_disparo)
                hora_fmt = dt.strftime("%d/%m/%Y às %H:%M")
            except Exception:
                hora_fmt = datetime_disparo
            rec_txt = " (recorrente, todo dia)" if recorrente else ""
            mensagem = f"Lembrete criado! Vou te avisar em <b>{hora_fmt}</b>{rec_txt}:\n\"{texto}\""

        elif action == "listar_lembretes":
            if not lembretes:
                mensagem = "Você não tem lembretes ativos."
            else:
                linhas = ["<b>Seus lembretes:</b>"]
                for l in lembretes[:10]:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(l["datetime_disparo"])
                        hora_fmt = dt.strftime("%d/%m às %H:%M")
                    except Exception:
                        hora_fmt = l.get("datetime_disparo", "?")
                    rec = " 🔁" if l.get("recorrente") else ""
                    linhas.append(f"- [{l['id'][:6]}] {hora_fmt}{rec}: {l['texto']}")
                mensagem = "\n".join(linhas)

        elif action == "cancelar_lembrete" and lembrete_id:
            # Aceita id curto (6 chars) ou completo
            lid_full = next((l["id"] for l in lembretes if l["id"].startswith(lembrete_id)), lembrete_id)
            ok = neo4j.deletar_lembrete(user_id, lid_full)
            mensagem = "Lembrete cancelado!" if ok else "Não encontrei esse lembrete."

        elif action == "cancelar_todos":
            for l in lembretes:
                neo4j.deletar_lembrete(user_id, l["id"])
            mensagem = f"{len(lembretes)} lembrete(s) cancelado(s)."

        elif not mensagem:
            mensagem = "O que você quer lembrar? Me diz o texto e a hora!"

    except Exception as e:
        logger.error("lembrete: erro ao executar action=%s: %s", action, e)
        mensagem = "Tive um problema ao salvar o lembrete. Tenta de novo?"

    return {"response": mensagem}


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except Exception:
            pass
    return {"action": "conversa", "mensagem": raw}
