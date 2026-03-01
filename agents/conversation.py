import logging
from agents.orchestrator import State
from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
from graph.weaviate_client import get_weaviate
import prompts.conversation as conv_prompt

logger = logging.getLogger(__name__)


def conversation_node(state: State) -> dict:
    """Agente de conversa geral sobre anime e mangá."""
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    # Carrega perfil do usuário do Neo4j para personalizar a conversa
    user_profile = {}
    try:
        neo4j = get_neo4j()
        neo4j.get_or_create_user(user_id)
        user_profile = neo4j.get_user_profile(user_id)
        logger.debug(
            "Conversa: perfil carregado user=%s assistidos=%d",
            user_id, len(user_profile.get("assistidos", []))
        )
    except Exception as e:
        logger.warning("Conversa: erro ao carregar perfil Neo4j: %s", e)

    # Busca contexto semântico no Weaviate (GraphRAG)
    context = ""
    try:
        weaviate = get_weaviate()
        results = weaviate.busca_semantica(user_message, limit=4)
        if results:
            snippets = []
            for r in results:
                titulo = r.get("titulo", "")
                synopsis = r.get("synopsis", "")[:200]
                if titulo and synopsis:
                    snippets.append(f"{titulo}: {synopsis}")
            context = "\n".join(snippets)
            logger.debug("Conversa: %d resultados semânticos para user=%s", len(results), user_id)
    except Exception as e:
        logger.warning("Conversa: erro ao buscar contexto Weaviate: %s", e)

    messages = conv_prompt.build_messages(user_message, history, context, user_profile)

    logger.info("Agente Conversa: gerando resposta para user=%s", user_id)
    try:
        response = openrouter.converse(messages)
    except Exception as e:
        logger.error("Agente Conversa: erro OpenRouter: %s", e)
        response = "Puts, tive um problema técnico. Pode repetir?"

    return {"response": response, "context": context, "user_profile": user_profile}
