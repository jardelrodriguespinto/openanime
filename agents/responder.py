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

    pdf_bytes = state.get("pdf_bytes")
    pdf_filename = state.get("pdf_filename", "")

    logger.info(
        "Responder: finalizando | user=%s intent=%s resposta_len=%d pdf=%s",
        user_id,
        intent,
        len(response),
        bool(pdf_bytes),
    )
    return {
        "response": response,
        "pdf_bytes": pdf_bytes,
        "pdf_filename": pdf_filename,
    }
