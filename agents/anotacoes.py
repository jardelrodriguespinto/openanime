"""Agente de anotacoes - mini Obsidian com busca e tags."""
import json
import logging
import re
import unicodedata

from agents.orchestrator import State
from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
import prompts.anotacoes as anotacoes_prompt

logger = logging.getLogger(__name__)


def _normalizar_para_match(texto: str) -> str:
    base = (texto or "").lower().strip()
    base = unicodedata.normalize("NFD", base)
    base = "".join(ch for ch in base if unicodedata.category(ch) != "Mn")
    base = re.sub(r"(.)\1{2,}", r"\1", base)
    base = re.sub(r"[^a-z0-9\s_-]", " ", base)
    return re.sub(r"\s+", " ", base).strip()


def _history_tem_notas(history: list[dict]) -> bool:
    for msg in history[-6:]:
        txt = _normalizar_para_match(msg.get("content", ""))
        if any(term in txt for term in ("nota", "notas", "obsidian", "anot")):
            return True
    return False


def _resolve_ref(ref_text: str) -> dict:
    alvo = (ref_text or "").strip()
    if re.fullmatch(r"[a-f0-9-]{4,36}", alvo.lower()):
        return {"nota_id": alvo.lower(), "titulo": None}
    return {"nota_id": None, "titulo": alvo or None}


def _rule_based_action(user_message: str, history: list[dict]) -> dict | None:
    raw = (user_message or "").strip()
    norm = _normalizar_para_match(raw)
    if not norm:
        return None

    compact = norm.replace(" ", "")

    if compact in {"listarnotas", "minhasnotas"} or any(p in norm for p in ("minhas notas", "lista notas", "o que anotei")):
        return {"action": "listar_notas"}

    m_busca_cmd = re.match(r"^buscarnotas\s+(.+)$", norm)
    if m_busca_cmd:
        return {"action": "buscar_notas", "query": m_busca_cmd.group(1).strip()}

    m_busca = re.match(r"^(?:busca(?:r)?\s+nas?\s+notas?|tenho\s+nota\s+sobre)\s+(.+)$", norm)
    if m_busca:
        return {"action": "buscar_notas", "query": m_busca.group(1).strip()}

    m_ver_cmd = re.match(r"^vernota\s+(.+)$", norm)
    if m_ver_cmd:
        ref = _resolve_ref(m_ver_cmd.group(1))
        return {"action": "ver_nota", **ref}

    m_ver = re.match(r"^(?:mostra\s+nota|abre\s+nota)\s+(.+)$", norm)
    if m_ver:
        ref = _resolve_ref(m_ver.group(1))
        return {"action": "ver_nota", **ref}

    m_edit_cmd = re.match(r"(?is)^\s*(?:editarnota|edita\s+nota)\s+([^:]+):\s*(.+)$", raw)
    if m_edit_cmd:
        ref = _resolve_ref(m_edit_cmd.group(1))
        return {
            "action": "editar_nota",
            **ref,
            "conteudo": m_edit_cmd.group(2).strip(),
        }

    m_del_cmd = re.match(r"^deletarnota\s+(.+)$", norm)
    if m_del_cmd:
        ref = _resolve_ref(m_del_cmd.group(1))
        return {"action": "deletar_nota", **ref}

    m_del = re.match(r"^(?:deleta\s+nota|apaga\s+nota)\s+(.+)$", norm)
    if m_del:
        ref = _resolve_ref(m_del.group(1))
        return {"action": "deletar_nota", **ref}

    m_create = re.match(r"(?is)^\s*(?:anota\s+que|cria\s+nota(?:\s+sobre)?|crianota|salva\s+isso:?)\s+(.+)$", raw)
    if m_create:
        conteudo = m_create.group(1).strip()
        return {"action": "criar_nota", "conteudo": conteudo}

    if norm in {"quais", "quais sao", "mostra", "me mostra"} and _history_tem_notas(history):
        return {"action": "listar_notas"}

    return None


def anotacoes_node(state: State) -> dict:
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    neo4j = get_neo4j()
    total_notas = 0
    try:
        total_notas_q = neo4j.listar_notas(user_id, limit=200)
        total_notas = len(total_notas_q)
    except Exception as e:
        logger.warning("anotacoes: erro ao contar notas: %s", e)

    data = _rule_based_action(user_message, history)
    if not data:
        messages = anotacoes_prompt.build_messages(user_message, history, total_notas)
        try:
            raw = openrouter.converse(messages)
            data = _parse_json(raw)
        except Exception as e:
            logger.error("anotacoes: erro LLM: %s", e)
            return {"response": "Nao consegui processar. Tenta de novo!"}

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
                titulo = " ".join(conteudo.split()[:6]).rstrip(".,!?") or "Nota"
            if not conteudo:
                mensagem = "Me diz o conteudo da nota!"
            else:
                nid = neo4j.criar_nota(user_id, titulo or "Nota", conteudo, tags)
                tags_txt = " - ".join(f"#{t}" for t in tags) if tags else "sem tags"
                mensagem = f"Nota criada! <b>{titulo or 'Nota'}</b> [{nid[:6]}]\n{tags_txt}"

        elif action == "ver_nota":
            if nota_id and len(nota_id) < 36:
                todas = neo4j.listar_notas(user_id, limit=200)
                nota_id = next((n["id"] for n in todas if n["id"].startswith(nota_id)), nota_id)

            nota = neo4j.get_nota(user_id, nota_id=nota_id or None, titulo=titulo or None)
            if not nota:
                resultados = neo4j.buscar_notas(user_id, titulo or query or user_message)
                nota = resultados[0] if resultados else None
            mensagem = _formatar_nota_completa(nota) if nota else "Nota nao encontrada."

        elif action == "buscar_notas":
            q = query or titulo or user_message
            notas = neo4j.buscar_notas(user_id, q)
            mensagem = _formatar_lista_notas(notas, titulo_secao=f'Resultados para "{q}"')

        elif action == "listar_notas":
            notas = neo4j.listar_notas(user_id, tag=tag, limit=20)
            titulo_secao = f"Notas com #{tag}" if tag else "Suas notas"
            mensagem = _formatar_lista_notas(notas, titulo_secao=titulo_secao)

        elif action == "editar_nota":
            if nota_id and len(nota_id) < 36:
                todas = neo4j.listar_notas(user_id, limit=200)
                nota_id = next((n["id"] for n in todas if n["id"].startswith(nota_id)), nota_id)

            if not nota_id and titulo:
                nota_ref = neo4j.get_nota(user_id, titulo=titulo)
                nota_id = (nota_ref or {}).get("id", "")

            if not nota_id:
                mensagem = "Me diga o ID ou titulo da nota para editar."
            else:
                novo_titulo = (data.get("titulo") or "").strip() or None
                novo_conteudo = (data.get("conteudo") or "").strip() or None
                novas_tags = [t.strip().lower() for t in (data.get("tags") or []) if t] or None
                ok = neo4j.editar_nota(user_id, nota_id, titulo=novo_titulo, conteudo=novo_conteudo, tags=novas_tags)
                mensagem = "Nota atualizada!" if ok else "Nota nao encontrada."

        elif action == "deletar_nota":
            if nota_id and len(nota_id) < 36:
                todas = neo4j.listar_notas(user_id, limit=200)
                nota_id = next((n["id"] for n in todas if n["id"].startswith(nota_id)), nota_id)

            if not nota_id and titulo:
                nota_ref = neo4j.get_nota(user_id, titulo=titulo)
                nota_id = (nota_ref or {}).get("id", "")

            if not nota_id:
                mensagem = "Me diga o ID ou titulo da nota para deletar."
            else:
                ok = neo4j.deletar_nota(user_id, nota_id)
                mensagem = "Nota deletada." if ok else "Nota nao encontrada."

        elif not mensagem:
            mensagem = "O que quer anotar? Ex: 'anota que preciso estudar LangGraph' ou 'minhas notas sobre python'."

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
        preview = (n.get("preview") or n.get("conteudo") or "")[:90]
        linhas.append(f"- [{nid}] <b>{titulo}</b>{tags_txt}")
        if preview:
            linhas.append(f"  {preview}...")
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
