import logging
import re

from agents.orchestrator import State
from ai.openrouter import openrouter
from data.jikan import jikan
from data.musicbrainz import musicbrainz
from data.openlibrary import openlibrary
from data.reddit import reddit
from data.tmdb import tmdb
from graph.neo4j_client import get_neo4j
from graph.weaviate_client import get_weaviate
import prompts.recommendation as rec_prompt

_KEYWORDS_ANIME = re.compile(
    r"\b(anime|manga|manhwa|webtoon|donghua|ova)\b",
    re.IGNORECASE,
)

_KEYWORDS_MIDIA = re.compile(
    r"\b(filme|filmes|cinema|movie|serie|series|dorama|k-?drama|netflix|amazon|hbo|disney)\b",
    re.IGNORECASE,
)

_KEYWORDS_MUSICA = re.compile(
    r"\b(musica|musicas|artista|banda|album|single|faixa|track|playlist|spotify|deezer|show|turne)\b",
    re.IGNORECASE,
)

_KEYWORDS_MUSICA_ALBUM = re.compile(
    r"\b(album|single|faixa|track|ep|lp|disco)\b",
    re.IGNORECASE,
)

_KEYWORDS_LIVRO = re.compile(
    r"\b(livro|livros|autor|autora|romance|novel|ebook|literatura|conto|ficcao)\b",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)

_PATTERNS_REFERENCIA = [
    r'parecid[oa]s?\s+com\s+["\']?([^"\',.!?\n]{3,80})["\']?',
    r'igual\s+a[o]?\s+["\']?([^"\',.!?\n]{3,80})["\']?',
    r'estilo\s+["\']?([^"\',.!?\n]{3,80})["\']?',
    r'baseado\s+em\s+["\']?([^"\',.!?\n]{3,80})["\']?',
    r'no\s+estilo\s+de\s+["\']?([^"\',.!?\n]{3,80})["\']?',
]

_FEEDBACK_POS = re.compile(r"\b(curti|gostei|amei|mais assim|mais desse)\b", re.IGNORECASE)
_FEEDBACK_NEG = re.compile(r"\b(nao curti|nao gostei|odiei|menos assim|evita isso)\b", re.IGNORECASE)



def recommendation_node(state: State) -> dict:
    """Agente de recomendacao personalizada multi-dominio."""
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    target_domains = _infer_target_domains(user_message)

    user_profile = {}
    neo4j = None
    try:
        neo4j = get_neo4j()
        neo4j.get_or_create_user(user_id)
        user_profile = neo4j.get_user_profile(user_id)
        logger.info(
            "Recomendacao: perfil carregado user=%s assistidos=%d dominios=%s",
            user_id,
            len(user_profile.get("assistidos", [])),
            ",".join(sorted(target_domains)),
        )
    except Exception as exc:
        logger.error("Recomendacao: erro ao carregar perfil Neo4j: %s", exc)

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
    except Exception as exc:
        logger.warning("Recomendacao: erro Weaviate: %s", exc)

    catalog_results = []

    if "anime" in target_domains:
        try:
            anime_results = jikan.buscar_anime(user_message[:80])
            catalog_results.extend(anime_results)
            logger.info("Recomendacao: %d resultados Jikan", len(anime_results))
        except Exception as exc:
            logger.warning("Recomendacao: erro Jikan: %s", exc)

    if "midia" in target_domains:
        try:
            tmdb_results = tmdb.buscar_midia(user_message[:80])
            catalog_results.extend(tmdb_results)
            logger.info("Recomendacao: %d resultados TMDB", len(tmdb_results))
        except Exception as exc:
            logger.warning("Recomendacao: erro TMDB: %s", exc)

    if "musica" in target_domains:
        musica_results = _collect_music_catalog(user_message)
        catalog_results.extend(musica_results)
        logger.info("Recomendacao: %d resultados MusicBrainz", len(musica_results))

    if "livro" in target_domains:
        livro_results = _collect_book_catalog(user_message)
        catalog_results.extend(livro_results)
        logger.info("Recomendacao: %d resultados OpenLibrary", len(livro_results))

    catalog_results = _dedupe_catalog(catalog_results)

    reddit_results = []
    try:
        reddit_results = reddit.buscar_discussoes(user_message[:80], limit=4)
        logger.info("Recomendacao: %d posts Reddit", len(reddit_results))
    except Exception as exc:
        logger.warning("Recomendacao: erro Reddit: %s", exc)

    assistidos = {
        (a.get("titulo") or "").strip().lower()
        for a in user_profile.get("assistidos", [])
        if a.get("titulo")
    }
    dropados = {
        (d.get("titulo") or "").strip().lower()
        for d in user_profile.get("dropados", [])
        if d.get("titulo")
    }
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

    catalog_filtrado = []
    for item in catalog_results:
        titulo = (item.get("titulo") or "").strip()
        if not titulo or titulo.lower() in excluir:
            continue
        if not permitir_nsfw and _parece_nsfw(item):
            continue
        catalog_filtrado.append(item)

    tempo_disponivel = user_profile.get("tempo_disponivel_min")
    if isinstance(tempo_disponivel, int) and tempo_disponivel > 0:
        catalog_filtrado = _ordenar_catalogo_por_tempo(catalog_filtrado, tempo_disponivel)

    logger.debug(
        "Recomendacao: %d semanticos apos filtros | %d catalogo apos filtros",
        len(semantic_filtrado),
        len(catalog_filtrado),
    )

    messages = rec_prompt.build_messages(
        user_message=user_message,
        history=history,
        user_profile=user_profile,
        semantic_results=semantic_filtrado,
        catalog_results=catalog_filtrado,
        reddit_results=reddit_results,
        target_domains=sorted(target_domains),
    )

    logger.info("Agente Recomendacao: gerando para user=%s", user_id)
    try:
        response = openrouter.converse(messages)
    except Exception as exc:
        logger.error("Agente Recomendacao: erro OpenRouter: %s", exc)
        response = "Nao consegui gerar recomendacoes agora. Tenta em instantes!"

    if neo4j:
        _salvar_preferencias(user_id, user_message, neo4j)
        recomendados = _extrair_titulos_recomendados(response)
        if not recomendados:
            recomendados = _fallback_titles(semantic_filtrado, catalog_filtrado)
        if recomendados:
            try:
                neo4j.registrar_recomendacoes(user_id, recomendados[:6])
                logger.info("Recomendacao: %d titulos registrados user=%s: %s", len(recomendados[:6]), user_id, recomendados[:6])
            except Exception as exc:
                logger.warning("Recomendacao: FALHA ao registrar recomendados user=%s: %s", user_id, exc)
        else:
            logger.warning("Recomendacao: nenhum titulo extraido da resposta para salvar user=%s", user_id)

    return {"response": response, "user_profile": user_profile}



def _infer_target_domains(message: str) -> set[str]:
    text = (message or "").strip()
    domains = set()

    if _KEYWORDS_ANIME.search(text):
        domains.add("anime")
    if _KEYWORDS_MIDIA.search(text):
        domains.add("midia")
    if _KEYWORDS_MUSICA.search(text):
        domains.add("musica")
    if _KEYWORDS_LIVRO.search(text):
        domains.add("livro")

    if not domains:
        return {"anime", "midia"}

    return domains



def _collect_music_catalog(query: str) -> list[dict]:
    q = (query or "").strip()[:80]
    if not q:
        return []

    results = []

    try:
        if _KEYWORDS_MUSICA_ALBUM.search(q):
            results.extend(musicbrainz.buscar_album(q))
    except Exception as exc:
        logger.debug("Recomendacao: erro MusicBrainz album: %s", exc)

    try:
        artists = musicbrainz.buscar_artista(q)
        results.extend(artists)

        if artists and not any((r.get("subtipo") == "album") for r in results):
            seed = (artists[0].get("titulo") or "").strip()[:80]
            if seed:
                results.extend(musicbrainz.buscar_album(seed))
    except Exception as exc:
        logger.debug("Recomendacao: erro MusicBrainz artista: %s", exc)

    return _dedupe_catalog(results)[:10]



def _collect_book_catalog(query: str) -> list[dict]:
    q = (query or "").strip()[:80]
    if not q:
        return []

    try:
        books = openlibrary.buscar_livro(q)
        if books:
            return _dedupe_catalog(books)[:10]
    except Exception as exc:
        logger.debug("Recomendacao: erro OpenLibrary livro: %s", exc)

    try:
        autores = openlibrary.buscar_autor(q)
        if autores:
            nome = (autores[0].get("nome") or "").strip()
            if nome:
                books = openlibrary.get_livros_recentes_autor(nome)
                return _dedupe_catalog(books)[:10]
    except Exception as exc:
        logger.debug("Recomendacao: erro OpenLibrary autor: %s", exc)

    return []



def _dedupe_catalog(items: list[dict]) -> list[dict]:
    seen = set()
    out = []

    for item in items:
        titulo = (item.get("titulo") or "").strip()
        if not titulo:
            continue

        tipo = (item.get("tipo") or "").strip().lower()
        subtipo = (item.get("subtipo") or "").strip().lower()
        key = f"{tipo}|{subtipo}|{titulo.lower()}"

        if key in seen:
            continue

        seen.add(key)
        out.append(item)

    return out



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
    except Exception as exc:
        logger.debug("Preferencia: erro ao salvar: %s", exc)



def _parece_nsfw(item: dict) -> bool:
    generos = item.get("generos") or []
    temas = item.get("temas") or []

    if isinstance(generos, str):
        generos = [generos]
    if isinstance(temas, str):
        temas = [temas]

    text = " ".join(
        [
            str(item.get("titulo", "")),
            str(item.get("synopsis", "")),
            " ".join(generos),
            " ".join(temas),
        ]
    ).lower()

    return any(tag in text for tag in ["hentai", "ecchi", "adult", "nsfw", "smut"])



def _ordenar_catalogo_por_tempo(items: list[dict], tempo_min: int) -> list[dict]:
    if not items:
        return []

    audiovisual = [it for it in items if _is_audiovisual(it)]
    outros = [it for it in items if not _is_audiovisual(it)]
    audiovisual_ordenado = sorted(audiovisual, key=lambda it: _score_audiovisual_tempo(it, tempo_min))

    return audiovisual_ordenado + outros



def _is_audiovisual(item: dict) -> bool:
    tipo = (item.get("tipo") or "").strip().lower()
    return tipo in {"anime", "filme", "serie", "dorama"}



def _score_audiovisual_tempo(item: dict, tempo_min: int) -> tuple[float, float]:
    tipo = (item.get("tipo") or "").strip().lower()
    nota = float(item.get("nota_mal") or item.get("nota") or 0.0)

    if tipo == "filme":
        dur = item.get("duracao_min")
        if isinstance(dur, int) and dur > 0:
            penalty = abs(dur - tempo_min)
        else:
            penalty = 40.0
        return (penalty, -nota)

    eps = item.get("episodios")
    if not isinstance(eps, int) or eps <= 0:
        penalty = 12.0
    elif tempo_min <= 25:
        penalty = abs(eps - 12)
    elif tempo_min <= 45:
        penalty = abs(eps - 24)
    else:
        penalty = abs(eps - 36)

    return (penalty, -nota)



def _extrair_titulos_recomendados(response: str) -> list[str]:
    if not response:
        return []

    seen = set()
    out = []

    for match in re.finditer(r"\*\*?([^*\n]{2,100})\*\*?", response):
        title = match.group(1).strip(" -:()")
        key = title.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(title)

    for line in response.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("1.", "2.", "3.", "-", "*")):
            clean = re.sub(r"^[\d\-\*\.\)\s]+", "", line)
            cand = clean.split(" - ")[0].strip()
            if 2 < len(cand) < 90:
                key = cand.lower()
                if key not in seen:
                    seen.add(key)
                    out.append(cand)

    return out[:10]



def _fallback_titles(semantic_filtrado: list[dict], catalog_filtrado: list[dict]) -> list[str]:
    out = []
    for item in semantic_filtrado[:4]:
        title = (item.get("titulo") or "").strip()
        if title:
            out.append(title)

    for item in catalog_filtrado[:6]:
        title = (item.get("titulo") or "").strip()
        if title and title not in out:
            out.append(title)

    return out[:8]



def _processar_feedback_rapido(neo4j, user_id: str, msg: str) -> str | None:
    text = (msg or "").strip()
    if not text:
        return None

    is_pos = bool(_FEEDBACK_POS.search(text))
    is_neg = bool(_FEEDBACK_NEG.search(text))
    if not (is_pos or is_neg):
        return None

    quoted = re.findall(r'["\']([^"\']{2,120})["\']', text)
    titulo = quoted[0].strip() if quoted else ""

    if not titulo:
        match = re.search(r"(?:de|sobre|em|do|da)\s+([^\n\.,!?]{2,100})", text, re.IGNORECASE)
        if match:
            titulo = match.group(1).strip()

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
    except Exception as exc:
        logger.debug("Feedback rapido: falha ao salvar: %s", exc)
        return None
