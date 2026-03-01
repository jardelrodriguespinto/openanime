import logging
import re

from agents.orchestrator import State
from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
from graph.weaviate_client import get_weaviate
from data.jikan import jikan
from data.reddit import reddit
import prompts.recommendation as rec_prompt

logger = logging.getLogger(__name__)

_PATTERNS_REFERENCIA = [
    r'parecid[oa]s?\s+com\s+["\']?([^"\',.!?\n]{3,80})["\']?',
    r'igual\s+a[o]?\s+["\']?([^"\',.!?\n]{3,80})["\']?',
    r'estilo\s+["\']?([^"\',.!?\n]{3,80})["\']?',
    r'baseado\s+em\s+["\']?([^"\',.!?\n]{3,80})["\']?',
    r'no\s+estilo\s+de\s+["\']?([^"\',.!?\n]{3,80})["\']?',
]

_FEEDBACK_POS = re.compile(r"\b(curti|gostei|amei|mais assim|mais desse)\b", re.IGNORECASE)
_FEEDBACK_NEG = re.compile(r"\b(nao curti|não curti|nao gostei|não gostei|odiei|menos assim|evita isso)\b", re.IGNORECASE)


def recommendation_node(state: State) -> dict:
    """Agente de recomendacao personalizada."""
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    user_profile = {}
    neo4j = None
    try:
        neo4j = get_neo4j()
        neo4j.get_or_create_user(user_id)
        user_profile = neo4j.get_user_profile(user_id)
        logger.info(
            "Recomendacao: perfil carregado user=%s assistidos=%d",
            user_id,
            len(user_profile.get("assistidos", [])),
        )
    except Exception as e:
        logger.error("Recomendacao: erro ao carregar perfil Neo4j: %s", e)

    # Feedback explicito rapido (feature 17), mesmo que roteado para recomendacao.
    if neo4j:
        handled = _processar_feedback_rapido(neo4j, user_id, user_message)
        if handled:
            return {
                "response": handled,
                "user_profile": user_profile,
            }
        try:
            user_profile["watchlist_inteligente"] = neo4j.get_watchlist_inteligente(user_id, limit=5)
        except Exception:
            user_profile["watchlist_inteligente"] = []

    semantic_results = []
    try:
        weaviate_client = get_weaviate()
        generos_preferidos = user_profile.get("generos_favoritos", [])
        semantic_results = weaviate_client.busca_semantica(
            user_message,
            limit=10,
            generos_preferidos=generos_preferidos,
        )
        logger.debug(
            "Recomendacao: %d resultados semanticos generos=%s",
            len(semantic_results),
            generos_preferidos[:3],
        )
    except Exception as e:
        logger.warning("Recomendacao: erro Weaviate: %s", e)

    jikan_results = []
    try:
        jikan_results = jikan.buscar_anime(user_message[:80])
        logger.info("Recomendacao: %d resultados Jikan", len(jikan_results))
    except Exception as e:
        logger.warning("Recomendacao: erro Jikan: %s", e)

    reddit_results = []
    try:
        reddit_results = reddit.buscar_discussoes(user_message[:80], limit=4)
        logger.info("Recomendacao: %d posts Reddit", len(reddit_results))
    except Exception as e:
        logger.warning("Recomendacao: erro Reddit: %s", e)

    assistidos = {a["titulo"].lower() for a in user_profile.get("assistidos", []) if a.get("titulo")}
    dropados = {d["titulo"].lower() for d in user_profile.get("dropados", []) if d.get("titulo")}
    recomendados_recentes = {
        t.lower() for t in (user_profile.get("recomendados_recentes", []) or []) if t
    }
    excluir = assistidos | dropados | recomendados_recentes

    filtros_maturidade = user_profile.get("filtros_maturidade", {}) or {}
    permitir_nsfw = bool(filtros_maturidade.get("permitir_nsfw"))

    semantic_filtrado = []
    for item in semantic_results:
        titulo = (item.get("titulo") or "").strip()
        if not titulo or titulo.lower() in excluir:
            continue
        if not permitir_nsfw and _parece_nsfw(item):
            continue
        semantic_filtrado.append(item)

    jikan_filtrado = [r for r in jikan_results if (r.get("titulo") or "").lower() not in excluir]
    if not permitir_nsfw:
        jikan_filtrado = [r for r in jikan_filtrado if not _jikan_nsfw(r)]

    tempo_disponivel = user_profile.get("tempo_disponivel_min")
    if isinstance(tempo_disponivel, int) and tempo_disponivel > 0:
        jikan_filtrado = _ordenar_jikan_por_tempo(jikan_filtrado, tempo_disponivel)

    logger.debug(
        "Recomendacao: %d semanticos apos filtros | %d jikan apos filtros",
        len(semantic_filtrado),
        len(jikan_filtrado),
    )

    messages = rec_prompt.build_messages(
        user_message=user_message,
        history=history,
        user_profile=user_profile,
        semantic_results=semantic_filtrado,
        jikan_results=jikan_filtrado,
        reddit_results=reddit_results,
    )

    logger.info("Agente Recomendacao: gerando para user=%s", user_id)
    try:
        response = openrouter.converse(messages)
    except Exception as e:
        logger.error("Agente Recomendacao: erro OpenRouter: %s", e)
        response = "Nao consegui gerar recomendacoes agora. Tenta em instantes!"

    if neo4j:
        _salvar_preferencias(user_id, user_message, neo4j)
        recomendados = _extrair_titulos_recomendados(response)
        if not recomendados:
            recomendados = _fallback_titles(semantic_filtrado, jikan_filtrado)
        if recomendados:
            try:
                neo4j.registrar_recomendacoes(user_id, recomendados[:6])
            except Exception as e:
                logger.debug("Recomendacao: nao conseguiu registrar recomendados: %s", e)

    return {"response": response, "user_profile": user_profile}


def _salvar_preferencias(user_id: str, message: str, neo4j) -> None:
    """Extrai titulo de referencia da mensagem e salva como quer_ver no Neo4j."""
    try:
        for pattern in _PATTERNS_REFERENCIA:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                titulo = match.group(1).strip().rstrip("!?.,;")
                if 3 < len(titulo) < 120:
                    neo4j.registrar_quer_ver(user_id, titulo)
                    logger.info("Preferencia salva: user=%s titulo=%s", user_id, titulo)
                    break
    except Exception as e:
        logger.debug("Preferencia: erro ao salvar: %s", e)


def _parece_nsfw(item: dict) -> bool:
    text = " ".join(
        [
            str(item.get("titulo", "")),
            str(item.get("synopsis", "")),
            " ".join(item.get("generos", []) or []),
            " ".join(item.get("temas", []) or []),
        ]
    ).lower()
    return any(tag in text for tag in ["hentai", "ecchi", "adult", "nsfw", "smut"])


def _jikan_nsfw(item: dict) -> bool:
    text = " ".join(
        [
            str(item.get("titulo", "")),
            str(item.get("synopsis", "")),
            " ".join(item.get("generos", []) or []),
            " ".join(item.get("temas", []) or []),
        ]
    ).lower()
    return any(tag in text for tag in ["hentai", "ecchi", "adult", "nsfw", "smut"])


def _ordenar_jikan_por_tempo(items: list[dict], tempo_min: int) -> list[dict]:
    def score(item: dict) -> tuple[float, float]:
        eps = item.get("episodios")
        nota = float(item.get("nota_mal") or 0.0)
        if not isinstance(eps, int) or eps <= 0:
            penalty = 5.0
        elif tempo_min <= 25:
            penalty = abs(eps - 12)
        elif tempo_min <= 45:
            penalty = abs(eps - 24)
        else:
            penalty = abs(eps - 36)
        return (penalty, -nota)

    return sorted(items, key=score)


def _extrair_titulos_recomendados(response: str) -> list[str]:
    if not response:
        return []
    seen = set()
    out = []

    for m in re.finditer(r"\*\*?([^*\n]{2,100})\*\*?", response):
        t = m.group(1).strip(" -:()")
        key = t.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(t)

    for line in response.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("1.", "2.", "3.", "-", "*")):
            clean = re.sub(r"^[\d\-\*\.\)\s]+", "", line)
            cand = clean.split(" - ")[0].split(" — ")[0].strip()
            if 2 < len(cand) < 90:
                key = cand.lower()
                if key not in seen:
                    seen.add(key)
                    out.append(cand)

    return out[:10]


def _fallback_titles(semantic_filtrado: list[dict], jikan_filtrado: list[dict]) -> list[str]:
    out = []
    for item in semantic_filtrado[:4]:
        t = (item.get("titulo") or "").strip()
        if t:
            out.append(t)
    for item in jikan_filtrado[:4]:
        t = (item.get("titulo") or "").strip()
        if t and t not in out:
            out.append(t)
    return out[:8]


def _processar_feedback_rapido(neo4j, user_id: str, msg: str) -> str | None:
    text = (msg or "").strip()
    if not text:
        return None

    is_pos = bool(_FEEDBACK_POS.search(text))
    is_neg = bool(_FEEDBACK_NEG.search(text))
    if not (is_pos or is_neg):
        return None

    # Tenta extrair titulo apos "de", "deu", "sobre", ou entre aspas.
    quoted = re.findall(r'["\']([^"\']{2,120})["\']', text)
    titulo = quoted[0].strip() if quoted else ""

    if not titulo:
        m = re.search(r"(?:de|sobre|em|do|da)\s+([^\n\.,!?]{2,100})", text, re.IGNORECASE)
        if m:
            titulo = m.group(1).strip()

    if not titulo or len(titulo) < 2:
        return None

    try:
        neo4j.registrar_feedback_recomendacao(
            user_id=user_id,
            titulo=titulo,
            curti=bool(is_pos and not is_neg),
            comentario=text[:220],
        )
        if is_pos and not is_neg:
            return f"Perfeito, marquei que voce curtiu *{titulo}*. Vou puxar mais nesse estilo."
        return f"Fechado, marquei que voce nao curtiu *{titulo}*. Vou reduzir esse tipo nas proximas."
    except Exception as e:
        logger.debug("Feedback rapido: falha ao salvar: %s", e)
        return None

