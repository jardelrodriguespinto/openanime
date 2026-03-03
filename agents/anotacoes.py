"""Agente de anotações — mini Obsidian com busca semântica e tags."""
import json
import logging

from agents.orchestrator import State
from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
import prompts.anotacoes as anotacoes_prompt

logger = logging.getLogger(__name__)


def anotacoes_node(state: State) -> dict:
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    neo4j = get_neo4j()
    total_notas = 0
    try:
        notas_lista = neo4j.listar_notas(user_id, limit=1)
        # Pega contagem real
        total_notas_q = neo4j.listar_notas(user_id, limit=200)
        total_notas = len(total_notas_q)
    except Exception as e:
        logger.warning("anotacoes: erro ao contar notas: %s", e)

    messages = anotacoes_prompt.build_messages(user_message, history, total_notas)

    try:
        raw = openrouter.converse(messages)
        data = _parse_json(raw)
    except Exception as e:
        logger.error("anotacoes: erro LLM: %s", e)
        return {"response": "Não consegui processar. Tenta de novo!"}

    action = data.get("action", "conversa")
    mensagem = data.get("mensagem", "")
    titulo = (data.get("titulo") or "").strip()
    conteudo = (data.get("conteudo") or "").strip()
    tags = [t.strip().lower() for t in (data.get("tags") or []) if t and t.strip()]
    query = (data.get("query") or "").strip()
    tag = (data.get("tag") or "").strip() or None
    nota_id = (data.get("nota_id") or "").strip()

    logger.info("anotacoes: user=%s action=%s", user_id, action)

    try:
        if action == "criar_nota":
            if not titulo and conteudo:
                # Auto-gera título das primeiras palavras
                titulo = " ".join(conteudo.split()[:6]).rstrip(".,!?") or "Nota"
            if not conteudo:
                mensagem = "Me diz o conteúdo da nota!"
            else:
                nid = neo4j.criar_nota(user_id, titulo or "Nota", conteudo, tags)
                tags_txt = " · ".join(f"#{t}" for t in tags) if tags else "sem tags"
                mensagem = f"Nota criada! <b>{titulo or 'Nota'}</b> [{nid[:6]}]\n{tags_txt}"

        elif action == "ver_nota":
            nota = neo4j.get_nota(user_id, nota_id=nota_id or None, titulo=titulo or None)
            if not nota:
                # Tenta buscar por query
                resultados = neo4j.buscar_notas(user_id, titulo or query or user_message)
                nota = resultados[0] if resultados else None
            mensagem = _formatar_nota_completa(nota) if nota else "Nota não encontrada."

        elif action == "buscar_notas":
            q = query or titulo or user_message
            notas = neo4j.buscar_notas(user_id, q)
            mensagem = _formatar_lista_notas(notas, titulo_secao=f"Resultados para \"{q}\"")

        elif action == "listar_notas":
            notas = neo4j.listar_notas(user_id, tag=tag, limit=20)
            titulo_secao = f"Notas com #{tag}" if tag else "Suas notas"
            mensagem = _formatar_lista_notas(notas, titulo_secao=titulo_secao)

        elif action == "editar_nota":
            # Resolve ID curto
            if nota_id and len(nota_id) < 36:
                todas = neo4j.listar_notas(user_id, limit=200)
                nota_id = next((n["id"] for n in todas if n["id"].startswith(nota_id)), nota_id)
            novo_titulo = (data.get("titulo") or "").strip() or None
            novo_conteudo = (data.get("conteudo") or "").strip() or None
            novas_tags = [t.strip().lower() for t in (data.get("tags") or []) if t] or None
            ok = neo4j.editar_nota(user_id, nota_id, titulo=novo_titulo, conteudo=novo_conteudo, tags=novas_tags)
            mensagem = "Nota atualizada!" if ok else "Nota não encontrada."

        elif action == "deletar_nota":
            if nota_id and len(nota_id) < 36:
                todas = neo4j.listar_notas(user_id, limit=200)
                nota_id = next((n["id"] for n in todas if n["id"].startswith(nota_id)), nota_id)
            ok = neo4j.deletar_nota(user_id, nota_id)
            mensagem = "Nota deletada." if ok else "Nota não encontrada."

        elif not mensagem:
            mensagem = "O que quer anotar? Ex: \"anota que preciso estudar LangGraph\" ou \"minhas notas sobre python\"."

    except Exception as e:
        logger.error("anotacoes: erro action=%s: %s", action, e)
        mensagem = "Tive um problema. Tenta de novo?"

    return {"response": mensagem}


def _formatar_nota_completa(nota: dict) -> str:
    titulo = nota.get("titulo", "Nota")
    conteudo = nota.get("conteudo", "")
    tags = nota.get("tags") or []
    nid = (nota.get("id") or "")[:6]
    tags_txt = " ".join(f"#{t}" for t in tags) if tags else ""
    return f"<b>{titulo}</b> [{nid}]\n{tags_txt}\n\n{conteudo}"


def _formatar_lista_notas(notas: list[dict], titulo_secao: str = "Notas") -> str:
    if not notas:
        return "Nenhuma nota encontrada."
    linhas = [f"<b>{titulo_secao}:</b>"]
    for n in notas[:15]:
        nid = (n.get("id") or "")[:6]
        titulo = n.get("titulo", "Nota")
        tags = n.get("tags") or []
        tags_txt = " " + " ".join(f"#{t}" for t in tags[:3]) if tags else ""
        preview = (n.get("preview") or n.get("conteudo") or "")[:60]
        linhas.append(f"- [{nid}] <b>{titulo}</b>{tags_txt}")
        if preview:
            linhas.append(f"  {preview}…")
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
