import logging
from agents.orchestrator import State

logger = logging.getLogger(__name__)


def responder_node(state: State) -> dict:
    """
    Nó final — apenas loga e passa a resposta adiante.
    A formatação real é feita no bot/formatter.py.
    """
    response = state.get("response", "")
    user_id = state.get("user_id", "?")
    intent = state.get("intent", "?")

    if not response:
        response = "Hmm, não soube o que responder. Tenta reformular?"

    logger.info(
        "Responder: finalizando | user=%s intent=%s resposta_len=%d",
        user_id,
        intent,
        len(response),
    )
    return {"response": response}
