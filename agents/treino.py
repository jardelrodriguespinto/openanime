"""Agente de treino — registra séries/reps/carga, mostra progressão e PRs."""
import json
import logging
import datetime

from agents.orchestrator import State
from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
import prompts.treino as treino_prompt

logger = logging.getLogger(__name__)


def treino_node(state: State) -> dict:
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    messages = treino_prompt.build_messages(user_message, history)

    try:
        raw = openrouter.converse(messages)
        data = _parse_json(raw)
    except Exception as e:
        logger.error("treino: erro LLM: %s", e)
        return {"response": "Não consegui processar o treino. Tenta de novo!"}

    action = data.get("action", "conversa")
    mensagem = data.get("mensagem", "")
    exercicio = (data.get("exercicio") or "").strip()
    series = _to_int(data.get("series"))
    reps = _to_int(data.get("reps"))
    peso_kg = _to_float(data.get("peso_kg"))
    data_str = (data.get("data") or datetime.date.today().isoformat()).strip()
    observacao = (data.get("observacao") or "").strip()

    logger.info("treino: user=%s action=%s exercicio=%s", user_id, action, exercicio)

    neo4j = get_neo4j()

    try:
        if action == "registrar_treino" and exercicio:
            neo4j.registrar_treino(user_id, exercicio, series, reps, peso_kg, data_str, observacao)
            detalhes = []
            if series and reps:
                detalhes.append(f"{series}x{reps}")
            if peso_kg:
                detalhes.append(f"{peso_kg}kg")
            det_txt = " — " + ", ".join(detalhes) if detalhes else ""
            mensagem = f"Treino registrado! <b>{exercicio}{det_txt}</b>"

            # Verificar PR
            pr = neo4j.get_pr_pessoal(user_id, exercicio)
            if pr and peso_kg and pr["peso_kg"] <= peso_kg and len(neo4j.get_treinos(user_id, exercicio)) > 1:
                mensagem += f"\n🏆 <b>Novo PR! {peso_kg}kg no {exercicio}!</b>"

        elif action == "ver_progressao" and exercicio:
            treinos = neo4j.get_progressao_treino(user_id, exercicio)
            mensagem = _formatar_progressao(exercicio, treinos)

        elif action == "pr_pessoal" and exercicio:
            pr = neo4j.get_pr_pessoal(user_id, exercicio)
            if pr:
                mensagem = (
                    f"🏆 <b>PR no {exercicio}:</b> {pr.get('peso_kg','?')}kg "
                    f"({pr.get('series','?')}x{pr.get('reps','?')}) em {pr.get('data','?')}"
                )
            else:
                mensagem = f"Nenhum registro de {exercicio} com peso ainda."

        elif action == "listar_treinos":
            treinos = neo4j.get_treinos(user_id, exercicio=exercicio or None, limit=15)
            mensagem = _formatar_lista_treinos(treinos)

        elif not mensagem:
            mensagem = "Me diz o que treinou! Ex: \"fiz supino 3x12 com 60kg\"."

    except Exception as e:
        logger.error("treino: erro action=%s: %s", action, e)
        mensagem = "Tive um problema ao salvar o treino."

    return {"response": mensagem}


def _formatar_progressao(exercicio: str, treinos: list[dict]) -> str:
    if not treinos:
        return f"Sem histórico de {exercicio} ainda."
    linhas = [f"<b>Progressão — {exercicio}:</b>"]
    for t in treinos[-10:]:
        peso = t.get("peso_kg")
        peso_txt = f"{peso}kg" if peso else "sem peso"
        s = t.get("series")
        r = t.get("reps")
        vol_txt = f"{s}x{r}" if s and r else ""
        sep = " — " if vol_txt else ""
        linhas.append(f"- {t.get('data','?')}: {peso_txt}{sep}{vol_txt}")
    return "\n".join(linhas)


def _formatar_lista_treinos(treinos: list[dict]) -> str:
    if not treinos:
        return "Sem treinos registrados."
    linhas = ["<b>Treinos recentes:</b>"]
    for t in treinos:
        peso = t.get("peso_kg")
        peso_txt = f" {peso}kg" if peso else ""
        s = t.get("series")
        r = t.get("reps")
        vol_txt = f" {s}x{r}" if s and r else ""
        linhas.append(f"- {t.get('data','?')}: {t.get('exercicio','?')}{vol_txt}{peso_txt}")
    return "\n".join(linhas)


def _to_int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except Exception:
        return None


def _to_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


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
