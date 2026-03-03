"""Agente de estudos — flashcards com revisão espaçada e resumo de textos."""
import json
import logging

from agents.orchestrator import State
from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
import prompts.estudos as estudos_prompt

logger = logging.getLogger(__name__)


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

    messages = estudos_prompt.build_messages(user_message, history, progresso)

    try:
        raw = openrouter.converse(messages)
        data = _parse_json(raw)
    except Exception as e:
        logger.error("estudos: erro LLM: %s", e)
        return {"response": "Não consegui processar. Tenta de novo!"}

    action = data.get("action", "conversa")
    mensagem = data.get("mensagem", "")
    frente = (data.get("frente") or "").strip()
    verso = (data.get("verso") or "").strip()
    topico = (data.get("topico") or "geral").strip()
    flashcard_id = (data.get("flashcard_id") or "").strip()
    acertou = data.get("acertou")
    limite = int(data.get("limite") or 10)
    texto = (data.get("texto") or "").strip()
    flashcards_multi = data.get("flashcards") or []

    logger.info("estudos: user=%s action=%s", user_id, action)

    try:
        if action == "criar_flashcard" and frente and verso:
            neo4j.criar_flashcard(user_id, frente, verso, topico)
            mensagem = f"Flashcard criado!\n<b>Q:</b> {frente}\n<b>A:</b> {verso} · tópico: {topico}"

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
                mensagem = "Nenhum flashcard para revisar agora. Volte amanhã ou crie novos!"
            else:
                linhas = [f"<b>Revisão — {len(cards)} flashcard(s):</b>\n"]
                for i, c in enumerate(cards, 1):
                    linhas.append(f"{i}. <b>Q:</b> {c['frente']}")
                    linhas.append(f"   <b>A:</b> <tg-spoiler>{c['verso']}</tg-spoiler>")
                    linhas.append(f"   ID: <code>{c['id'][:8]}</code> · nível {c.get('nivel',0)}")
                linhas.append("\nResponda \"acertei [id]\" ou \"errei [id]\" para registrar.")
                mensagem = "\n".join(linhas)

        elif action == "marcar_revisao" and flashcard_id and acertou is not None:
            # Aceita id curto
            cards_all = neo4j.listar_flashcards(user_id)
            fc_full = next((c["id"] for c in cards_all if c["id"].startswith(flashcard_id)), flashcard_id)
            neo4j.atualizar_flashcard_revisao(fc_full, bool(acertou))
            resultado = "✅ Acerto registrado!" if acertou else "❌ Erro registrado — vou mostrar esse card mais cedo."
            mensagem = resultado

        elif action == "listar_flashcards":
            topico_f = (data.get("topico") or "").strip() or None
            cards = neo4j.listar_flashcards(user_id, topico=topico_f)
            mensagem = _formatar_lista_cards(cards, topico_f)

        elif action == "progresso_estudos":
            total = progresso.get("total", 0)
            dominados = progresso.get("dominados", 0)
            topicos = progresso.get("topicos", []) or []
            pct = int(dominados / total * 100) if total else 0
            linhas = [f"<b>Seus estudos:</b>", f"- Total de flashcards: {total}",
                      f"- Dominados (nível ≥4): {dominados} ({pct}%)"]
            if topicos:
                linhas.append(f"- Tópicos: {', '.join(str(t) for t in topicos[:8])}")
            mensagem = "\n".join(linhas)

        elif action == "resumir_texto" and texto:
            # Usa LLM para resumir e sugerir flashcards
            resumo_msgs = [
                {"role": "system", "content": (
                    "Resuma o texto em bullets concisos e sugira 3 flashcards no formato:\n"
                    "Q: pergunta\nA: resposta\n\nSeja direto e útil."
                )},
                {"role": "user", "content": texto[:4000]},
            ]
            mensagem = openrouter.converse(resumo_msgs)

        elif not mensagem:
            mensagem = "O que quer estudar? Posso criar flashcards, conduzir revisão ou resumir um texto!"

    except Exception as e:
        logger.error("estudos: erro action=%s: %s", action, e)
        mensagem = "Tive um problema. Tenta de novo?"

    return {"response": mensagem}


def _formatar_lista_cards(cards: list[dict], topico: str | None = None) -> str:
    if not cards:
        filtro = f" de \"{topico}\"" if topico else ""
        return f"Nenhum flashcard{filtro} ainda."
    linhas = [f"<b>Flashcards{' — ' + topico if topico else ''}:</b>"]
    for c in cards[:20]:
        nivel = c.get("nivel", 0)
        linhas.append(f"- [{c['id'][:6]}] N{nivel} · {c['frente'][:60]}")
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
