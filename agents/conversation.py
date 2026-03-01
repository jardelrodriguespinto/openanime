import logging
import re

from agents.orchestrator import State
from ai.openrouter import openrouter
from data.jikan import jikan
from data.musicbrainz import musicbrainz
from data.openlibrary import openlibrary
from data.tmdb import tmdb
from data.wikipedia import wikipedia
from graph.neo4j_client import get_neo4j
from graph.weaviate_client import get_weaviate
import prompts.conversation as conv_prompt

logger = logging.getLogger(__name__)

_KEYWORDS_REFERENCE = re.compile(
    r"\b(quem\s+e|quem\s+eh|personagem|historia|lore|origem|explica|enredo|biografia)\b",
    re.IGNORECASE,
)

_KEYWORDS_ANIME_LIKE = re.compile(
    r"\b(anime|manga|manhwa|webtoon|donghua|arc|temporada|episodio|personagem)\b",
    re.IGNORECASE,
)

_KEYWORDS_FILM_SERIES = re.compile(
    r"\b(filme|filmes|serie|series|dorama|doramas|temporada|episodio|cinema)\b",
    re.IGNORECASE,
)

_KEYWORDS_MUSIC = re.compile(
    r"\b(musica|musicas|album|artista|banda|single|clipe|lyric|show)\b",
    re.IGNORECASE,
)

_KEYWORDS_BOOK = re.compile(
    r"\b(livro|livros|autor|romance|novel|ebook|literatura)\b",
    re.IGNORECASE,
)


def _build_weaviate_context(user_message: str, user_id: str) -> str:
    context = ""
    try:
        weaviate = get_weaviate()
        results = weaviate.busca_semantica(user_message, limit=4)
        if results:
            snippets = []
            for item in results:
                titulo = item.get("titulo", "")
                synopsis = (item.get("synopsis", "") or "")[:240]
                tipo = item.get("tipo", "")
                if titulo and synopsis:
                    prefix = f"[{tipo}] " if tipo else ""
                    snippets.append(f"{prefix}{titulo}: {synopsis}")
            context = "\n".join(snippets)
            logger.debug("Conversa: %d resultados semanticos para user=%s", len(results), user_id)
    except Exception as exc:
        logger.warning("Conversa: erro ao buscar contexto Weaviate: %s", exc)
    return context


def _build_reference_context(user_message: str, user_id: str) -> str:
    snippets = []
    need_reference = _KEYWORDS_REFERENCE.search(user_message or "") is not None

    if need_reference:
        try:
            wiki_items = wikipedia.search_summaries(query=user_message, limit=3)
            for item in wiki_items[:3]:
                title = item.get("title", "")
                extract = (item.get("extract", "") or "")[:260]
                url = item.get("url", "")
                if title and extract:
                    snippets.append(f"Wikipedia {title}: {extract} ({url})")
            if wiki_items:
                logger.debug("Conversa: wikipedia context=%d user=%s", len(wiki_items), user_id)
        except Exception as exc:
            logger.warning("Conversa: erro ao enriquecer via Wikipedia: %s", exc)

    if _KEYWORDS_ANIME_LIKE.search(user_message or ""):
        try:
            animes = jikan.buscar_anime(user_message)
            if animes:
                best = animes[0]
                titulo = best.get("titulo", "")
                synopsis = (best.get("synopsis", "") or "")[:260]
                if titulo and synopsis:
                    snippets.append(f"Jikan {titulo}: {synopsis}")

                anime_id = best.get("id")
                if anime_id:
                    chars = jikan.get_anime_characters(int(anime_id), limit=6)
                    nomes = [c.get("nome", "") for c in chars if c.get("nome")]
                    if nomes:
                        snippets.append(f"Personagens principais: {', '.join(nomes[:6])}")
        except Exception as exc:
            logger.debug("Conversa: erro ao enriquecer via Jikan: %s", exc)

    if _KEYWORDS_FILM_SERIES.search(user_message or ""):
        try:
            midias = tmdb.buscar_midia(user_message)
            for item in midias[:2]:
                titulo = item.get("titulo", "")
                tipo = item.get("tipo", "")
                synopsis = (item.get("synopsis", "") or "")[:220]
                ano = item.get("ano")
                if titulo and synopsis:
                    snippets.append(f"TMDB [{tipo}] {titulo} ({ano}): {synopsis}")
        except Exception as exc:
            logger.debug("Conversa: erro ao enriquecer via TMDB: %s", exc)

    if _KEYWORDS_MUSIC.search(user_message or ""):
        try:
            artists = musicbrainz.buscar_artista(user_message)
            for artist in artists[:2]:
                titulo = artist.get("titulo", "")
                pais = artist.get("pais", "")
                subtipo = artist.get("subtipo", "")
                if titulo:
                    snippets.append(f"MusicBrainz [{subtipo}] {titulo} | pais: {pais}")
        except Exception as exc:
            logger.debug("Conversa: erro ao enriquecer via MusicBrainz: %s", exc)

    if _KEYWORDS_BOOK.search(user_message or ""):
        try:
            books = openlibrary.buscar_livro(user_message)
            for book in books[:2]:
                titulo = book.get("titulo", "")
                autor = book.get("autor", "")
                ano = book.get("ano")
                if titulo:
                    snippets.append(f"OpenLibrary {titulo} | {autor} | ano: {ano}")
        except Exception as exc:
            logger.debug("Conversa: erro ao enriquecer via OpenLibrary: %s", exc)

    return "\n".join(snippets)


def conversation_node(state: State) -> dict:
    """Agente de conversa geral sobre obras e cultura pop."""
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    user_profile = {}
    try:
        neo4j = get_neo4j()
        neo4j.get_or_create_user(user_id)
        user_profile = neo4j.get_user_profile(user_id)
        logger.debug(
            "Conversa: perfil carregado user=%s assistidos=%d",
            user_id,
            len(user_profile.get("assistidos", [])),
        )
    except Exception as exc:
        logger.warning("Conversa: erro ao carregar perfil Neo4j: %s", exc)

    semantic_context = _build_weaviate_context(user_message, user_id)
    reference_context = _build_reference_context(user_message, user_id)

    context_parts = [p for p in [semantic_context, reference_context] if p]
    context = "\n".join(context_parts)

    messages = conv_prompt.build_messages(user_message, history, context, user_profile)

    logger.info("Agente Conversa: gerando resposta para user=%s", user_id)
    try:
        response = openrouter.converse(messages)
    except Exception as exc:
        logger.error("Agente Conversa: erro OpenRouter: %s", exc)
        response = "Puts, tive um problema tecnico. Pode repetir?"

    return {"response": response, "context": context, "user_profile": user_profile}
