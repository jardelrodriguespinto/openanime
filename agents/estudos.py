"""Agente de estudos - flashcards com revisao espacada e resumo de textos."""
import json
import logging
import re
import unicodedata

from agents.orchestrator import State
from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
import prompts.estudos as estudos_prompt

logger = logging.getLogger(__name__)


def _normalizar_para_match(texto: str) -> str:
    base = (texto or "").lower().strip()
    base = unicodedata.normalize("NFD", base)
    base = "".join(ch for ch in base if unicodedata.category(ch) != "Mn")
    base = re.sub(r"(.)\1{2,}", r"\1", base)
    base = re.sub(r"[^a-z0-9\s_-]", " ", base)
    return re.sub(r"\s+", " ", base).strip()


def _history_tem_flashcards(history: list[dict]) -> bool:
    for msg in history[-6:]:
        txt = _normalizar_para_match(msg.get("content", ""))
        if any(term in txt for term in ("flashcard", "flashcards", "revisao", "estudos")):
            return True
    return False


def _to_int(value, default: int = 10, min_value: int = 1, max_value: int = 50) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def _parse_listar_flashcards(norm: str) -> dict | None:
    m = re.match(r"^listar\s*flashcards(?:\s+(?:de|sobre)\s+(.+))?$", norm)
    if m:
        return {"action": "listar_flashcards", "topico": (m.group(1) or "").strip() or None}

    m2 = re.match(r"^listarflashcards(?:\s+(.+))?$", norm)
    if m2:
        return {"action": "listar_flashcards", "topico": (m2.group(1) or "").strip() or None}

    return None


def _rule_based_action(user_message: str, history: list[dict]) -> dict | None:
    norm = _normalizar_para_match(user_message)
    if not norm:
        return None

    compact = norm.replace(" ", "")

    # acertei [id] / errei [id]
    m_revisao = re.search(r"\b(acertei|errei)\s*\[?([a-z0-9-]{4,36})\]?\b", norm)
    if m_revisao:
        return {
            "action": "marcar_revisao",
            "flashcard_id": m_revisao.group(2),
            "acertou": m_revisao.group(1) == "acertei",
        }

    parsed_listar = _parse_listar_flashcards(norm)
    if parsed_listar:
        return parsed_listar

    if any(k in compact for k in ("flashcard", "flashcards")) and any(t in norm for t in ("listar", "lista", "mostra", "quais")):
        return {"action": "listar_flashcards", "topico": None}

    if norm in {"quais", "quais sao", "quais sao os", "mostra", "me mostra"} and _history_tem_flashcards(history):
        return {"action": "listar_flashcards", "topico": None}

    if norm in {"revisar", "revisao", "bora comecar", "vamos comecar", "comecar", "iniciar"}:
        if _history_tem_flashcards(history) or any(t in compact for t in ("revisar", "revisao")):
            return {"action": "revisar", "limite": 10}

    if ("revisar" in norm or "revisao" in norm) and ("flashcard" in norm or _history_tem_flashcards(history)):
        return {"action": "revisar", "limite": 10}

    if "progresso" in norm and ("estudo" in norm or "flashcard" in norm):
        return {"action": "progresso_estudos"}

    return None


def estudos_node(state: State) -> dict:
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    neo4j = get_neo4j()
    progresso = {}
    try:
        progresso = neo4j.get_progresso_estudos(user_id)
    except Exception as e:
        logger.warning("estudos: erro ao buscar progresso: %s", e)

    data = _rule_based_action(user_message, history)
    if not data:
        messages = estudos_prompt.build_messages(user_message, history, progresso)
        try:
            raw = openrouter.converse(messages)
            data = _parse_json(raw)
        except Exception as e:
            logger.error("estudos: erro LLM: %s", e)
            return {"response": "Nao consegui processar. Tenta de novo!"}

    action = data.get("action", "conversa")
    mensagem = data.get("mensagem", "")
    frente = (data.get("frente") or "").strip()
    verso = (data.get("verso") or "").strip()
    topico = (data.get("topico") or "geral").strip()
    flashcard_id = (data.get("flashcard_id") or "").strip()
    acertou = data.get("acertou")
    limite = _to_int(data.get("limite"), default=10)
    texto = (data.get("texto") or "").strip()
    flashcards_multi = data.get("flashcards") or []

    logger.info("estudos: user=%s action=%s", user_id, action)

    try:
        if action == "criar_flashcard" and frente and verso:
            neo4j.criar_flashcard(user_id, frente, verso, topico)
            mensagem = f"Flashcard criado!\n<b>Q:</b> {frente}\n<b>A:</b> {verso} - topico: {topico}"

        elif action == "criar_multiplos" and flashcards_multi:
            criados = 0
            for fc in flashcards_multi[:20]:
                f = (fc.get("frente") or "").strip()
                v = (fc.get("verso") or "").strip()
                t = (fc.get("topico") or topico or "geral").strip()
                if f and v:
                    neo4j.criar_flashcard(user_id, f, v, t)
                    criados += 1
            mensagem = f"{criados} flashcard(s) criado(s)!"

        elif action == "revisar":
            cards = neo4j.get_flashcards_para_revisar(user_id, limit=limite)
            if not cards:
                mensagem = "Nenhum flashcard para revisar agora. Volte amanha ou crie novos!"
            else:
                linhas = [f"<b>Revisao - {len(cards)} flashcard(s):</b>\n"]
                for i, c in enumerate(cards, 1):
                    linhas.append(f"{i}. <b>Q:</b> {c['frente']}")
                    linhas.append(f"   <b>A:</b> <tg-spoiler>{c['verso']}</tg-spoiler>")
                    linhas.append(f"   ID: <code>{c['id'][:8]}</code> - nivel {c.get('nivel', 0)}")
                linhas.append("\nResponda \"acertei [id]\" ou \"errei [id]\" para registrar.")
                mensagem = "\n".join(linhas)

        elif action == "marcar_revisao" and flashcard_id and acertou is not None:
            cards_all = neo4j.listar_flashcards(user_id)
            fc_full = next((c["id"] for c in cards_all if c["id"].startswith(flashcard_id)), flashcard_id)
            neo4j.atualizar_flashcard_revisao(fc_full, bool(acertou))
            mensagem = "Acerto registrado!" if acertou else "Erro registrado - vou mostrar esse card mais cedo."

        elif action == "listar_flashcards":
            topico_f = (data.get("topico") or "").strip() or None
            cards = neo4j.listar_flashcards(user_id, topico=topico_f)
            mensagem = _formatar_lista_cards(cards, topico_f)

        elif action == "progresso_estudos":
            total = progresso.get("total", 0)
            dominados = progresso.get("dominados", 0)
            topicos = progresso.get("topicos", []) or []
            pct = int(dominados / total * 100) if total else 0
            linhas = [
                "<b>Seus estudos:</b>",
                f"- Total de flashcards: {total}",
                f"- Dominados (nivel >=4): {dominados} ({pct}%)",
            ]
            if topicos:
                linhas.append(f"- Topicos: {', '.join(str(t) for t in topicos[:8])}")
            mensagem = "\n".join(linhas)

        elif action == "resumir_texto" and texto:
            resumo_msgs = [
                {
                    "role": "system",
                    "content": (
                        "Resuma o texto em bullets concisos e sugira 3 flashcards no formato:\n"
                        "Q: pergunta\nA: resposta\n\nSeja direto e util."
                    ),
                },
                {"role": "user", "content": texto[:4000]},
            ]
            mensagem = openrouter.converse(resumo_msgs)

        elif not mensagem:
            mensagem = "O que quer estudar? Posso criar flashcards, conduzir revisao ou resumir um texto!"

    except Exception as e:
        logger.error("estudos: erro action=%s: %s", action, e)
        mensagem = "Tive um problema. Tenta de novo?"

    return {"response": mensagem}


def _formatar_lista_cards(cards: list[dict], topico: str | None = None) -> str:
    if not cards:
        filtro = f' de "{topico}"' if topico else ""
        return f"Nenhum flashcard{filtro} ainda."

    titulo = f"<b>Flashcards - {topico}:</b>" if topico else "<b>Flashcards:</b>"
    linhas = [titulo]
    for c in cards[:20]:
        nivel = c.get("nivel", 0)
        frente = (c.get("frente") or "").strip()
        linhas.append(f"- [{c['id'][:6]}] N{nivel}  {frente}")
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
    return {"action": "conversa", "mensagem": raw}
