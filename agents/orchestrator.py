import asyncio
import logging
import operator
import re
import unicodedata
from typing import TypedDict, Annotated

from langgraph.graph import StateGraph, END

from ai.openrouter import openrouter
import prompts.orchestrator as orch_prompt

logger = logging.getLogger(__name__)

VALID_INTENTS = {
    "conversa", "recomendacao", "analise", "busca", "perfil", "maratona",
    "noticias", "documento", "perfil_pro", "vaga", "curriculo_ats", "candidatura",
    "lembrete", "financas", "ranking", "treino", "estudos", "anotacoes",
}


def _normalizar_para_match(texto: str) -> str:
    base = (texto or "").lower().strip()
    base = unicodedata.normalize("NFD", base)
    base = "".join(ch for ch in base if unicodedata.category(ch) != "Mn")
    base = re.sub(r"(.)\1{2,}", r"\1", base)
    base = re.sub(r"[^a-z0-9\s/_-]", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base


def _history_tem_termo(history: list, termos: set[str]) -> bool:
    for msg in history[-6:]:
        texto = _normalizar_para_match(msg.get("content", ""))
        if any(t in texto for t in termos):
            return True
    return False


def _heuristica_intent(user_message: str, history: list) -> str | None:
    norm = _normalizar_para_match(user_message)
    if not norm:
        return None

    compacto = norm.replace(" ", "")

    termos_notas = {
        "anota", "anotacao", "anotacoes", "obsidian",
        "crianota", "listarnotas", "buscarnotas", "vernota", "editarnota", "deletarnota",
    }
    termos_estudos = {"flashcard", "flashcards", "revisar", "revisao", "leetcode", "two pointer", "listarflashcards", "acertei", "errei"}

    if any(t in compacto for t in ("crianota", "listarnotas", "buscarnotas", "vernota", "editarnota", "deletarnota")):
        return "anotacoes"
    if any(t in compacto for t in ("listarflashcards", "criarflashcard", "flashcard", "flashcards")):
        return "estudos"

    if any(t in norm for t in termos_notas):
        return "anotacoes"
    if any(p in norm for p in ("minhas notas", "lista notas", "busca notas", "nota sobre", "salva isso")):
        return "anotacoes"
    if any(t in norm for t in termos_estudos):
        return "estudos"

    if norm in {"quais sao", "quais", "mostra", "me mostra", "bora comecar", "comecar", "vamos comecar"}:
        if _history_tem_termo(history, {"flashcard", "flashcards", "revisao", "estudos"}):
            return "estudos"
        if _history_tem_termo(history, {"nota", "notas", "obsidian"}):
            return "anotacoes"

    return None


class State(TypedDict):
    messages: Annotated[list, operator.add]   # histórico da conversa
    user_id: str                               # telegram user id
    intent: str                                # intenção classificada
    user_profile: dict                         # perfil carregado do Neo4j
    context: str                               # contexto do GraphRAG/Weaviate
    response: str                              # resposta final ao usuário
    raw_input: str                             # última mensagem do usuário
    pdf_path: str                              # caminho do PDF recebido (Fase 2)
    pdf_doc_id: str                            # id do documento armazenado
    pdf_bytes: object                          # bytes do PDF gerado para envio
    pdf_filename: str                          # nome do arquivo PDF gerado
    candidatura_pendente: object               # dados de candidatura aguardando confirmacao


def orchestrator_node(state: State) -> dict:
    """Classifica a intenção da mensagem do usuário."""
    # Intenção já definida externamente (ex: override de PDF) — não reclassifica
    if state.get("intent"):
        logger.info("Orquestrador: intent já definido como '%s', pulando classificador", state["intent"])
        return {"intent": state["intent"]}

    user_message = state["raw_input"]
    history = state.get("messages", [])

    intent_heuristica = _heuristica_intent(user_message, history)
    if intent_heuristica:
        logger.info(
            "Orquestrador: heuristica acionada user=%s intent=%s",
            state.get("user_id"),
            intent_heuristica,
        )
        return {"intent": intent_heuristica}

    messages = orch_prompt.build_messages(user_message, history)
    logger.info("Orquestrador: classificando intenção para user=%s", state.get("user_id"))

    try:
        raw = openrouter.orchestrate(messages).strip().lower()
        intent = raw.split()[0] if raw else "conversa"
        if intent not in VALID_INTENTS:
            logger.warning("Orquestrador: intenção inválida '%s', usando 'conversa'", intent)
            intent = "conversa"
    except Exception as e:
        logger.error("Orquestrador: erro na classificação: %s", e)
        intent = "conversa"

    logger.info("Orquestrador: user=%s intent=%s", state.get("user_id"), intent)
    return {"intent": intent}


def route_intent(state: State) -> str:
    """Decide qual agente ativar com base na intenção."""
    intent = state.get("intent", "conversa")
    logger.debug("Router: encaminhando para '%s'", intent)
    return intent


def build_graph() -> StateGraph:
    """Constrói e compila o grafo LangGraph."""
    from agents.conversation import conversation_node
    from agents.recommendation import recommendation_node
    from agents.analysis import analysis_node
    from agents.search import search_node
    from agents.profile import profile_node
    from agents.maratona import maratona_node
    from agents.responder import responder_node
    from agents.news import news_node
    from agents.documents import documents_node
    from agents.profile_pro import profile_pro_node
    from agents.jobs import jobs_node
    from agents.apply import apply_node
    from agents.lembrete import lembrete_node
    from agents.financas import financas_node
    from agents.ranking import ranking_node
    from agents.treino import treino_node
    from agents.estudos import estudos_node
    from agents.anotacoes import anotacoes_node

    graph = StateGraph(State)

    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("conversa", conversation_node)
    graph.add_node("recomendacao", recommendation_node)
    graph.add_node("analise", analysis_node)
    graph.add_node("busca", search_node)
    graph.add_node("perfil", profile_node)
    graph.add_node("maratona", maratona_node)
    graph.add_node("noticias", news_node)
    graph.add_node("documento", documents_node)
    graph.add_node("perfil_pro", profile_pro_node)
    graph.add_node("vaga", jobs_node)
    graph.add_node("curriculo_ats", jobs_node)
    graph.add_node("candidatura", apply_node)
    graph.add_node("lembrete", lembrete_node)
    graph.add_node("financas", financas_node)
    graph.add_node("ranking", ranking_node)
    graph.add_node("treino", treino_node)
    graph.add_node("estudos", estudos_node)
    graph.add_node("anotacoes", anotacoes_node)
    graph.add_node("responder", responder_node)

    graph.set_entry_point("orchestrator")
    graph.add_conditional_edges(
        "orchestrator",
        route_intent,
        {
            "conversa":     "conversa",
            "recomendacao": "recomendacao",
            "analise":      "analise",
            "busca":        "busca",
            "perfil":       "perfil",
            "maratona":     "maratona",
            "noticias":     "noticias",
            "documento":    "documento",
            "perfil_pro":   "perfil_pro",
            "vaga":         "vaga",
            "curriculo_ats": "curriculo_ats",
            "candidatura":  "candidatura",
            "lembrete":     "lembrete",
            "financas":     "financas",
            "ranking":      "ranking",
            "treino":       "treino",
            "estudos":      "estudos",
            "anotacoes":    "anotacoes",
        },
    )

    all_nodes = [
        "conversa", "recomendacao", "analise", "busca", "perfil", "maratona",
        "noticias", "documento", "perfil_pro", "vaga", "curriculo_ats", "candidatura",
        "lembrete", "financas", "ranking", "treino", "estudos", "anotacoes",
    ]
    for node in all_nodes:
        graph.add_edge(node, "responder")

    graph.add_edge("responder", END)

    compiled = graph.compile()
    logger.info(
        "Grafo LangGraph compilado | agentes: %s", ", ".join(all_nodes)
    )
    return compiled


# Singleton do grafo compilado
_graph = None

# Mantém referência das tasks em background para evitar GC
_background_tasks: set = set()


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def processar_mensagem(
    user_id: str,
    user_message: str,
    history: list,
    pdf_path: str = "",
) -> dict:
    """
    Ponto de entrada principal: processa mensagem e retorna dict com resposta e extras.
    Retorna: {"response": str, "pdf_bytes": bytes|None, "pdf_filename": str, "candidatura_pendente": dict|None}
    """
    graph = get_graph()

    # Se pdf_path fornecido, forca intent para "documento"
    intent_override = "documento" if pdf_path else ""

    state: State = {
        "messages": history,
        "user_id": user_id,
        "intent": intent_override,
        "user_profile": {},
        "context": "",
        "response": "",
        "raw_input": user_message,
        "pdf_path": pdf_path,
        "pdf_doc_id": "",
        "pdf_bytes": None,
        "pdf_filename": "",
        "candidatura_pendente": None,
    }

    logger.info("Processando mensagem: user=%s len=%d pdf=%s", user_id, len(user_message), bool(pdf_path))

    try:
        result = await graph.ainvoke(state)
        response = result.get("response", "Não consegui processar sua mensagem.")
        logger.info(
            "Resposta gerada: user=%s intent=%s len=%d",
            user_id, result.get("intent"), len(response)
        )

        # Extrai e salva dados de perfil em background
        intent = result.get("intent", "")
        if intent not in ("perfil", "perfil_pro", "documento"):
            from agents.extrator import extrair_e_salvar
            task = asyncio.create_task(extrair_e_salvar(user_id, user_message))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        return {
            "response": response,
            "pdf_bytes": result.get("pdf_bytes"),
            "pdf_filename": result.get("pdf_filename", "documento.pdf"),
            "candidatura_pendente": result.get("candidatura_pendente"),
        }
    except Exception as e:
        logger.error("Erro no grafo: user=%s error=%s", user_id, e, exc_info=True)
        return {"response": "Tive um problema interno. Tenta de novo?", "pdf_bytes": None, "pdf_filename": "", "candidatura_pendente": None}
