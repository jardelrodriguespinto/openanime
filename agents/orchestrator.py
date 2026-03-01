import asyncio
import logging
import operator
from typing import TypedDict, Annotated

from langgraph.graph import StateGraph, END

from ai.openrouter import openrouter
import prompts.orchestrator as orch_prompt

logger = logging.getLogger(__name__)

VALID_INTENTS = {"conversa", "recomendacao", "analise", "busca", "perfil"}


class State(TypedDict):
    messages: Annotated[list, operator.add]   # histórico da conversa
    user_id: str                               # telegram user id
    intent: str                                # intenção classificada
    user_profile: dict                         # perfil carregado do Neo4j
    context: str                               # contexto do GraphRAG/Weaviate
    response: str                              # resposta final ao usuário
    raw_input: str                             # última mensagem do usuário


def orchestrator_node(state: State) -> dict:
    """Classifica a intenção da mensagem do usuário."""
    user_message = state["raw_input"]
    history = state.get("messages", [])

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
    from agents.responder import responder_node

    graph = StateGraph(State)

    # Adiciona nós — 5 sub-agentes especializados + orquestrador + responder
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("conversa", conversation_node)       # lore, personagens, história
    graph.add_node("recomendacao", recommendation_node) # sugestões personalizadas
    graph.add_node("analise", analysis_node)            # review profundo de uma obra
    graph.add_node("busca", search_node)                # notícias, sites, lançamentos
    graph.add_node("perfil", profile_node)              # histórico e watchlist
    graph.add_node("responder", responder_node)

    # Fluxo: orchestrator → sub-agente → responder → END
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
        },
    )

    for node in ["conversa", "recomendacao", "analise", "busca", "perfil"]:
        graph.add_edge(node, "responder")

    graph.add_edge("responder", END)

    compiled = graph.compile()
    logger.info("Grafo LangGraph compilado | agentes: conversa, recomendacao, analise, busca, perfil")
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


async def processar_mensagem(user_id: str, user_message: str, history: list) -> str:
    """Ponto de entrada principal: processa mensagem e retorna resposta."""
    graph = get_graph()

    state: State = {
        "messages": history,
        "user_id": user_id,
        "intent": "",
        "user_profile": {},
        "context": "",
        "response": "",
        "raw_input": user_message,
    }

    logger.info("Processando mensagem: user=%s len=%d", user_id, len(user_message))

    try:
        result = await graph.ainvoke(state)
        response = result.get("response", "Não consegui processar sua mensagem.")
        logger.info(
            "Resposta gerada: user=%s intent=%s len=%d",
            user_id, result.get("intent"), len(response)
        )

        # Extrai e salva dados de perfil em background (não bloqueia a resposta)
        if result.get("intent") not in ("perfil",):
            from agents.extrator import extrair_e_salvar
            task = asyncio.create_task(extrair_e_salvar(user_id, user_message))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        return response
    except Exception as e:
        logger.error("Erro no grafo: user=%s error=%s", user_id, e, exc_info=True)
        return "Tive um problema interno. Tenta de novo?"
