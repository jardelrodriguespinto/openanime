"""
Extrator de perfil - roda em background apos toda mensagem.
Usa modelo barato para detectar dados e salva no Neo4j.
Tambem tenta canonicalizar titulo no catalogo para reduzir lixo no grafo.
"""

import json
import logging
import re
from difflib import SequenceMatcher

from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
from graph.weaviate_client import get_weaviate

logger = logging.getLogger(__name__)

SYSTEM = """Analise a mensagem do usuario e extraia dados de perfil de anime, manga, manhwa, filme, serie, dorama, musica ou livro.

Retorne APENAS um JSON com o formato:
{
  "acoes": [
    {"tipo": "assistido", "titulo": "...", "nota": null_ou_numero, "tipo_midia": "anime", "opiniao": "frase curta do usuario sobre a obra ou null"},
    {"tipo": "lido", "titulo": "...", "nota": null_ou_numero, "tipo_midia": "manga", "opiniao": null},
    {"tipo": "drop", "titulo": "...", "episodio": null_ou_numero, "tipo_midia": "serie"},
    {"tipo": "quer_ver", "titulo": "...", "tipo_midia": "filme"},
    {"tipo": "gostou_de", "titulo": "...", "tipo_midia": "dorama"},
    {"tipo": "ouviu", "titulo": "nome do album ou musica", "artista": "nome do artista", "tipo_midia": "musica"},
    {"tipo": "leu_livro", "titulo": "titulo do livro", "autor": "nome do autor", "tipo_midia": "livro"},
    {"tipo": "quer_ouvir", "titulo": "...", "artista": "...", "tipo_midia": "musica"},
    {"tipo": "quer_ler", "titulo": "...", "autor": "...", "tipo_midia": "livro"}
  ]
}

O campo "opiniao" e opcional. Preencha SOMENTE quando o usuario expressou uma opiniao clara sobre a obra
(ex: "amei o final", "achei lento mas o final compensou", "melhor anime do ano", "personagens rasos").
Nao invente opiniao. Se nao houver opiniao expressa, use null.

tipo_midia deve ser: "anime", "manga", "manhwa", "filme", "serie", "dorama", "musica" ou "livro"
- anime: animacao japonesa
- manga/manhwa: quadrinhos
- filme: longa-metragem (qualquer origem)
- serie: serie live-action ou animada (nao japonesa)
- dorama: serie coreana (k-drama)
- musica: album, musica, artista, banda
- livro: livro, romance, ebook, autor

Para musica: use "titulo" para o album/musica e "artista" para o artista/banda.
Para livro: use "titulo" para o titulo do livro e "autor" para o escritor.

Se nao houver nada relevante, retorne: {"acoes": []}
Retorne APENAS JSON.
"""

_STOPWORDS_TITULO = {
    "anime",
    "manga",
    "manhwa",
    "webtoon",
    "donghua",
    "obra",
    "serie",
    "temporada",
    "episodio",
    "capitulo",
    "etico",
    "sekai",
    "bom",
    "boa",
    "legal",
}


def _titulo_invalido(titulo: str) -> bool:
    text = (titulo or "").strip()
    if len(text) < 3:
        return True

    tokens = [t for t in re.split(r"\s+", text.lower()) if t]
    if not tokens:
        return True

    if len(tokens) == 1 and tokens[0] in _STOPWORDS_TITULO:
        return True

    if all(token in _STOPWORDS_TITULO for token in tokens):
        return True

    return False


def _similaridade(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


_TIPOS_MIDIA_VALIDOS = {"anime", "manga", "manhwa", "filme", "serie", "dorama", "musica", "livro"}
_TIPOS_TMDB = {"filme", "serie", "dorama"}
_TIPOS_MUSICA = {"musica"}
_TIPOS_LIVRO = {"livro"}


def _resolver_titulo_catalogo(titulo: str) -> tuple[str | None, dict | None]:
    """Tenta resolver para o titulo canonico no Jikan (anime/manga)."""
    clean = (titulo or "").strip()
    if _titulo_invalido(clean):
        return None, None

    try:
        from data.jikan import jikan

        resultados = jikan.buscar_anime(clean[:80])
        if not resultados:
            # Se nao encontrou no catalogo, aceita apenas titulos com 2+ tokens
            if len(clean.split()) >= 2:
                return clean, None
            return None, None

        best = resultados[0]
        best_title = (best.get("titulo") or "").strip() or clean
        sim = _similaridade(clean, best_title)

        # Aceita match bom, ou titulo longo com forte sobreposicao
        if sim >= 0.52 or len(clean.split()) >= 2:
            return best_title, best
        return clean, None
    except Exception as e:
        logger.debug("Extrator: resolver titulo falhou para '%s': %s", clean, e)
        return clean, None


def _resolver_titulo_tmdb(titulo: str, tipo_midia: str) -> tuple[str | None, dict | None]:
    """Tenta resolver para o titulo canonico no TMDB (filmes/series/doramas)."""
    clean = (titulo or "").strip()
    if _titulo_invalido(clean):
        return None, None

    try:
        from data.tmdb import tmdb

        if tipo_midia == "filme":
            resultados = tmdb.buscar_filme(clean[:80])
        else:
            resultados = tmdb.buscar_serie(clean[:80])

        if not resultados:
            if len(clean.split()) >= 2:
                return clean, None
            return None, None

        best = resultados[0]
        best_title = (best.get("titulo") or "").strip() or clean
        sim = _similaridade(clean, best_title)

        if sim >= 0.45 or len(clean.split()) >= 2:
            return best_title, best
        return clean, None
    except Exception as e:
        logger.debug("Extrator: resolver TMDB falhou para '%s': %s", clean, e)
        return clean, None


def _resolver_titulo_musica(titulo: str, artista: str = "") -> tuple[str | None, dict | None]:
    """Tenta resolver artista/album via MusicBrainz."""
    busca = artista or titulo
    clean = (busca or "").strip()
    if _titulo_invalido(clean):
        return None, None
    try:
        from data.musicbrainz import musicbrainz

        if artista:
            resultados = musicbrainz.buscar_artista(artista[:80])
        else:
            resultados = musicbrainz.buscar_album(clean[:80])

        if not resultados:
            return clean, None
        best = resultados[0]
        return best.get("titulo") or clean, best
    except Exception as e:
        logger.debug("Extrator: resolver musica falhou para '%s': %s", clean, e)
        return clean, None


def _resolver_titulo_livro(titulo: str, autor: str = "") -> tuple[str | None, dict | None]:
    """Tenta resolver livro via Open Library."""
    clean = (titulo or autor or "").strip()
    if _titulo_invalido(clean):
        return None, None
    try:
        from data.openlibrary import openlibrary

        if titulo:
            resultados = openlibrary.buscar_livro(titulo[:80])
        else:
            resultados = openlibrary.buscar_livro(autor[:80])

        if not resultados:
            return clean, None
        best = resultados[0]
        return best.get("titulo") or clean, best
    except Exception as e:
        logger.debug("Extrator: resolver livro falhou para '%s': %s", clean, e)
        return clean, None


async def extrair_e_salvar(user_id: str, user_message: str) -> None:
    """Extrai dados de perfil da mensagem e salva no Neo4j em background."""
    try:
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_message},
        ]

        raw = openrouter.orchestrate(messages)
        data = _parse_json(raw)
        acoes = data.get("acoes", [])
        if not acoes:
            return

        neo4j = get_neo4j()
        neo4j.get_or_create_user(user_id)

        for acao in acoes:
            tipo = (acao.get("tipo") or "").strip()
            titulo_raw = (acao.get("titulo") or "").strip()
            tipo_midia = (acao.get("tipo_midia") or "anime").strip().lower()
            if tipo_midia not in _TIPOS_MIDIA_VALIDOS:
                tipo_midia = "anime"
            if not titulo_raw:
                continue

            artista_raw = (acao.get("artista") or "").strip()
            autor_raw = (acao.get("autor") or "").strip()

            # Resolve titulo via catalogo correto
            if tipo_midia in _TIPOS_TMDB:
                titulo_resolvido, midia_resolvida = _resolver_titulo_tmdb(titulo_raw, tipo_midia)
            elif tipo_midia in _TIPOS_MUSICA:
                titulo_resolvido, midia_resolvida = _resolver_titulo_musica(titulo_raw, artista_raw)
            elif tipo_midia in _TIPOS_LIVRO:
                titulo_resolvido, midia_resolvida = _resolver_titulo_livro(titulo_raw, autor_raw)
            else:
                titulo_resolvido, midia_resolvida = _resolver_titulo_catalogo(titulo_raw)

            if not titulo_resolvido:
                logger.debug("Extrator: titulo ignorado por baixa qualidade: '%s'", titulo_raw)
                continue

            if tipo in ("assistido", "lido", "ouviu", "leu_livro"):
                nota = acao.get("nota")
                if isinstance(nota, (int, float)) and not (0 <= nota <= 10):
                    nota = None
                opiniao = (acao.get("opiniao") or "").strip() or None
                neo4j.registrar_assistido(user_id, titulo_resolvido, nota, opiniao=opiniao)
                logger.info(
                    "Extrator: %s user=%s titulo=%s nota=%s tipo_midia=%s",
                    tipo, user_id, titulo_resolvido, nota, tipo_midia,
                )
                await _enriquecer_midia(titulo_resolvido, tipo_midia, midia_resolvida)
                # Para musica: salva artista favorito para notificacoes futuras
                if tipo_midia == "musica" and artista_raw:
                    _salvar_artista_favorito(neo4j, user_id, artista_raw)
                elif tipo_midia == "livro" and autor_raw:
                    _salvar_autor_favorito(neo4j, user_id, autor_raw)

            elif tipo == "drop":
                episodio = acao.get("episodio")
                neo4j.registrar_drop(user_id, titulo_resolvido, episodio)
                logger.info(
                    "Extrator: drop user=%s titulo=%s ep=%s tipo_midia=%s",
                    user_id, titulo_resolvido, episodio, tipo_midia,
                )
                await _enriquecer_midia(titulo_resolvido, tipo_midia, midia_resolvida)

            elif tipo in ("quer_ver", "gostou_de", "quer_ouvir", "quer_ler"):
                neo4j.registrar_quer_ver(user_id, titulo_resolvido)
                logger.info(
                    "Extrator: %s user=%s titulo=%s tipo_midia=%s",
                    tipo, user_id, titulo_resolvido, tipo_midia,
                )
                await _enriquecer_midia(titulo_resolvido, tipo_midia, midia_resolvida)
                if tipo_midia == "musica" and artista_raw:
                    _salvar_artista_favorito(neo4j, user_id, artista_raw)
                elif tipo_midia == "livro" and autor_raw:
                    _salvar_autor_favorito(neo4j, user_id, autor_raw)

    except Exception as e:
        logger.warning("Extrator: erro ao salvar perfil user=%s: %s", user_id, e)


def _salvar_artista_favorito(neo4j, user_id: str, artista: str) -> None:
    """Adiciona artista à lista de favoritos do usuario para notificacoes."""
    try:
        neo4j.adicionar_artista_favorito(user_id, artista)
        logger.debug("Extrator: artista favorito salvo user=%s artista=%s", user_id, artista)
    except Exception as e:
        logger.debug("Extrator: erro salvar artista favorito: %s", e)


def _salvar_autor_favorito(neo4j, user_id: str, autor: str) -> None:
    """Adiciona autor à lista de favoritos do usuario para notificacoes."""
    try:
        neo4j.adicionar_autor_favorito(user_id, autor)
        logger.debug("Extrator: autor favorito salvo user=%s autor=%s", user_id, autor)
    except Exception as e:
        logger.debug("Extrator: erro salvar autor favorito: %s", e)


async def _enriquecer_midia(titulo: str, tipo_midia: str = "anime", dados: dict | None = None) -> None:
    """Enriquece Neo4j + Weaviate roteando para Jikan (anime/manga) ou TMDB (filme/serie/dorama)."""
    if tipo_midia in _TIPOS_TMDB:
        await _enriquecer_via_tmdb(titulo, tipo_midia, dados)
    else:
        await _enriquecer_via_jikan(titulo, dados)


async def _enriquecer_via_jikan(titulo: str, anime: dict | None = None) -> None:
    """Enriquece Neo4j + Weaviate com dados do Jikan."""
    try:
        from data.jikan import jikan

        payload = anime
        if not payload:
            resultados = jikan.buscar_anime(titulo)
            if not resultados:
                return
            payload = resultados[0]

        if not payload.get("id"):
            return

        if not payload.get("synopsis") and not payload.get("generos"):
            return

        neo4j = get_neo4j()
        neo4j.upsert_anime(payload)
        logger.info(
            "Extrator: anime enriquecido titulo=%s generos=%s",
            titulo,
            payload.get("generos", []),
        )

        if payload.get("synopsis"):
            weaviate = get_weaviate()
            weaviate.upsert_anime(payload)
            logger.info("Extrator: anime indexado Weaviate titulo=%s", titulo)

    except Exception as e:
        logger.debug("Extrator: enriquecimento Jikan falhou para '%s': %s", titulo, e)


async def _enriquecer_via_tmdb(titulo: str, tipo_midia: str, dados: dict | None = None) -> None:
    """Enriquece Neo4j + Weaviate com dados do TMDB (filmes/series/doramas)."""
    try:
        from data.tmdb import tmdb

        payload = dados
        if not payload:
            if tipo_midia == "filme":
                resultados = tmdb.buscar_filme(titulo)
            else:
                resultados = tmdb.buscar_serie(titulo)
            if not resultados:
                return
            payload = resultados[0]

        if not payload.get("id"):
            return

        if not payload.get("synopsis") and not payload.get("tipo"):
            return

        neo4j = get_neo4j()
        neo4j.upsert_midia(payload)
        logger.info(
            "Extrator: midia enriquecida titulo=%s tipo=%s",
            titulo,
            payload.get("tipo", tipo_midia),
        )

        if payload.get("synopsis"):
            weaviate = get_weaviate()
            weaviate.upsert_midia(payload)
            logger.info("Extrator: midia indexada Weaviate titulo=%s", titulo)

    except Exception as e:
        logger.debug("Extrator: enriquecimento TMDB falhou para '%s': %s", titulo, e)


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except Exception:
            pass
    return {"acoes": []}
