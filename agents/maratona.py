import logging
import re

from graph.neo4j_client import get_neo4j

logger = logging.getLogger(__name__)


def maratona_node(state: dict) -> dict:
    """
    Agente de maratona - monta ordem completa de watch para uma franquia.
    Usa franchise_routes (estatico via Neo4j) sem chamar LLM.
    """
    raw_input = state.get("raw_input", "")
    user_id = state.get("user_id", "?")

    titulo = _extrair_titulo(raw_input)
    if not titulo:
        return {"response": "Me diz qual franquia voce quer maratonar! Ex: /maratona Naruto"}

    logger.info("Maratona: user=%s titulo=%s", user_id, titulo)

    try:
        neo4j = get_neo4j()
        rota = neo4j.get_franchise_timeline(titulo)
    except Exception as e:
        logger.warning("Maratona: erro Neo4j user=%s: %s", user_id, e)
        rota = None

    if not rota:
        return {
            "response": (
                f"Nao encontrei rota de maratona para *{titulo}*.\n\n"
                "Pode ser que a franquia ainda nao esteja no meu banco. "
                "Tenta com o nome completo ou me pede uma analise da obra."
            )
        }

    return {"response": _formatar_maratona(titulo, rota)}


def _extrair_titulo(mensagem: str) -> str:
    mensagem = mensagem.strip()

    # /maratona <titulo>
    m = re.match(r"^/maratona\s+(.+)$", mensagem, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # linguagem natural
    patterns = [
        r"(?:quero|vou|preciso|vamos)\s+maratonar\s+(.+)",
        r"maratona\s+(?:de|do|da|dos|das)?\s*(.+)",
        r"ordem\s+(?:de\s+)?(?:assistir|watch|maratona)\s+(?:de\s+)?(.+)",
        r"como\s+assistir\s+(.+?)\s+em\s+ordem",
    ]
    for pattern in patterns:
        m = re.search(pattern, mensagem, re.IGNORECASE)
        if m:
            titulo = m.group(1).strip()
            titulo = re.sub(r"[?!.,]+$", "", titulo).strip()
            titulo = re.sub(r"\s+(por favor|pls|please)$", "", titulo, flags=re.IGNORECASE).strip()
            return titulo

    return ""


def _formatar_maratona(titulo: str, rota: dict) -> str:
    franquia = rota.get("franquia") or titulo
    pos_obra = rota.get("pos_obra") or []
    ponte = rota.get("ponte_animemanga") or []

    linhas = [f"*Guia de maratona: {franquia}*\n"]
    linhas.append(f"1. *{titulo}* — comece aqui")

    if pos_obra:
        linhas.append("\n*Continuacoes em ordem:*")
        for i, item in enumerate(pos_obra, start=2):
            linhas.append(f"{i}. {item}")

    if ponte:
        linhas.append("\n*Notas (anime vs manga/light novel):*")
        for nota in ponte:
            linhas.append(f"  - {nota}")

    linhas.append("\nQuer que eu busque onde assistir cada parte ou detalhes de episodios?")
    return "\n".join(linhas)
