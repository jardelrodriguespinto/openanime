"""
Notificador diario - envia digest de novidades e sugestoes para todos os usuarios.
Roda via JobQueue do python-telegram-bot.
"""

import logging
import re
import time
import datetime
import asyncio
import html
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from telegram.ext import CallbackContext

from ai.openrouter import openrouter
from bot.formatter import formatar_telegram
from data.anilist import anilist
from data.jikan import jikan
from data.reddit import reddit
from data.rss import rss_news
from data.tvmaze import tvmaze
from graph.neo4j_client import get_neo4j
import prompts.notificador as notif_prompt

logger = logging.getLogger(__name__)

_DDG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://lite.duckduckgo.com/",
}


def _escape_html(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def _sanitize_href(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return _escape_html(raw)


def _format_html_link(title: str, href: str, max_len: int = 80) -> str:
    text = (str(title or "").strip() or "link")[:max(1, max_len)]
    safe_text = _escape_html(text)
    safe_href = _sanitize_href(href)
    if not safe_href:
        return safe_text
    return f"<a href='{safe_href}'>{safe_text}</a>"


def _buscar_novidades_ddg(query: str, max_results: int = 6) -> list[dict]:
    """Busca novidades via DDG Lite."""
    import urllib.parse

    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            resp = client.post(
                "https://lite.duckduckgo.com/lite/",
                data={"q": query},
                headers=_DDG_HEADERS,
            )
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            seen = set()

            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                title = a.get_text(strip=True)

                if not href or not title:
                    continue
                if "duckduckgo.com/y.js" in href:
                    continue
                if href.startswith("/") or href.startswith("?"):
                    continue
                if "duckduckgo.com/l/" in href:
                    m = re.search(r"uddg=([^&]+)", href)
                    if m:
                        href = urllib.parse.unquote(m.group(1))
                    else:
                        continue

                if href in seen:
                    continue
                seen.add(href)

                snippet = ""
                parent = a.find_parent("td")
                if parent:
                    next_td = parent.find_next_sibling("td")
                    if next_td:
                        snippet = next_td.get_text(strip=True)[:150]

                results.append({"title": title, "href": href, "body": snippet})
                if len(results) >= max_results:
                    break

            return results
    except Exception as e:
        logger.warning("Notificador DDG erro: %s", e)
        return []


def _coletar_dados_diarios() -> tuple[list, list, list]:
    """Coleta temporada atual, novidades web e Reddit."""
    hoje_date = datetime.date.today()
    hoje = hoje_date.strftime("%d/%m/%Y")
    ano_atual = hoje_date.year

    temporada = []
    try:
        temporada = jikan.get_temporada_atual()
        temporada = sorted(
            [a for a in temporada if a.get("nota_mal")],
            key=lambda x: x["nota_mal"],
            reverse=True,
        )[:12]
        logger.info("Notificador: %d animes coletados da temporada", len(temporada))
    except Exception as e:
        logger.warning("Notificador: erro Jikan: %s", e)

    # AniList trending para reforcar radar da temporada.
    try:
        anilist_trending = anilist.get_trending(limit=10)
        if anilist_trending:
            merged = {}
            for item in temporada + anilist_trending:
                key = (item.get("titulo") or "").strip().lower()
                if not key:
                    continue
                if key not in merged:
                    merged[key] = item
            temporada = list(merged.values())[:14]
        logger.info("Notificador: temporada apos AniList=%d", len(temporada))
    except Exception as e:
        logger.warning("Notificador: erro AniList: %s", e)

    novidades_web = []
    try:
        # RSS primeiro (mais estavel para noticias).
        rss = rss_news.get_latest_news(query="anime", limit=6, days=7)
        novidades_web.extend(rss)

        queries = [
            f"anime novidades lancamentos {hoje}",
            f"anime news today temporada {ano_atual}",
            "manhwa webtoon novidades semana",
        ]
        for q in queries:
            res = _buscar_novidades_ddg(q, max_results=4)
            novidades_web.extend(res)
            if len(novidades_web) >= 6:
                break
            time.sleep(1)
        logger.info("Notificador: %d novidades web", len(novidades_web))
    except Exception as e:
        logger.warning("Notificador: erro DDG: %s", e)

    novidades_reddit = []
    try:
        for sub in ["anime", "manhwa", "manga"]:
            posts = reddit.get_top_semana(sub, limit=5)
            novidades_reddit.extend(posts)
        novidades_reddit = novidades_reddit[:8]
        logger.info("Notificador: %d posts Reddit", len(novidades_reddit))
    except Exception as e:
        logger.warning("Notificador: erro Reddit: %s", e)

    return temporada, novidades_web, novidades_reddit


def _build_personal_radar(user_profile: dict, temporada: list[dict]) -> dict:
    """Calcula radar personalizado sem LLM (base para prompt final)."""
    if not user_profile:
        return {}

    assistidos = {(x.get("titulo") or "").strip().lower() for x in user_profile.get("assistidos", [])}
    dropados = {(x.get("titulo") or "").strip().lower() for x in user_profile.get("dropados", [])}
    generos_favoritos = {(g or "").strip().lower() for g in user_profile.get("generos_favoritos", [])}

    drop_patterns = user_profile.get("drop_patterns", {})
    risk_level = drop_patterns.get("risk_level", "baixo")
    evitar = [
        item.get("genero")
        for item in drop_patterns.get("top_drop_genres", [])
        if item.get("genero")
    ]
    evitar_set = {(g or "").strip().lower() for g in evitar}

    picks = []
    for anime in temporada:
        titulo = (anime.get("titulo") or "").strip()
        key = titulo.lower()
        if not titulo or key in assistidos or key in dropados:
            continue

        anime_gen = {(g or "").strip().lower() for g in anime.get("generos", [])}
        if risk_level in {"alto", "medio"} and (anime_gen & evitar_set):
            continue

        motivo = []
        if anime_gen & generos_favoritos:
            inter = list(anime_gen & generos_favoritos)
            motivo.append(f"bate com seus generos: {', '.join(inter[:2])}")
        if anime.get("nota_mal"):
            motivo.append(f"nota MAL {anime.get('nota_mal')}")
        if anime.get("episodios") and anime.get("episodios") <= 13:
            motivo.append("formato curto (bom para evitar drop)")

        picks.append({"titulo": titulo, "motivo": "; ".join(motivo) or "bom fit geral"})
        if len(picks) >= 3:
            break

    progresso_alerta = []
    for item in user_profile.get("progresso", [])[:3]:
        t = item.get("titulo")
        if not t:
            continue
        if item.get("episodio"):
            progresso_alerta.append(f"{t} (voce parou no ep {item['episodio']})")
        elif item.get("capitulo"):
            progresso_alerta.append(f"{t} (voce parou no cap {item['capitulo']})")
        else:
            progresso_alerta.append(t)

    return {
        "risk_level": risk_level,
        "evitar": evitar,
        "picks": picks,
        "progresso_alerta": progresso_alerta,
    }


def _profile_titles_for_schedule(profile: dict, limit: int = 8) -> list[str]:
    titles = []
    for key in ("progresso", "quer_ver", "assistidos"):
        items = profile.get(key, []) or []
        for item in items:
            titulo = (item.get("titulo") or "").strip()
            if titulo and titulo not in titles:
                titles.append(titulo)
            if len(titles) >= max(1, limit):
                return titles
    return titles


def _build_tvmaze_episode_alerts(
    user_profile: dict,
    cache: dict[str, list[dict]],
    days: int = 10,
    limit: int = 5,
) -> list[dict]:
    titles = _profile_titles_for_schedule(user_profile, limit=8)
    if not titles:
        return []

    collected = []
    seen = set()
    for title in titles:
        if title not in cache:
            try:
                cache[title] = tvmaze.get_upcoming_episodes(query=title, days=days, limit=2)
            except Exception as e:
                logger.debug("Notificador: TVMaze falhou para '%s': %s", title, e)
                cache[title] = []
        for ep in cache[title]:
            key = f"{ep.get('show_name')}|{ep.get('airdate')}|{ep.get('season')}|{ep.get('episode')}"
            if key in seen:
                continue
            seen.add(key)
            collected.append(ep)

    collected.sort(key=lambda x: x.get("airdate") or "")
    return collected[: max(1, limit)]


def _filtrar_temporada_por_alertas(temporada: list[dict], user_profile: dict) -> list[dict]:
    """Aplica alertas de genero/estudio por usuario; fallback para temporada completa."""
    if not user_profile:
        return temporada

    alert_generos = {(g or "").strip().lower() for g in user_profile.get("alerta_generos", []) if g}
    alert_estudios = {(e or "").strip().lower() for e in user_profile.get("alerta_estudios", []) if e}

    if not alert_generos and not alert_estudios:
        return temporada

    filtrados = []
    for anime in temporada:
        generos = {(g or "").strip().lower() for g in anime.get("generos", []) if g}
        estudio = (anime.get("estudio") or "").strip().lower()
        if (alert_generos and generos.intersection(alert_generos)) or (
            alert_estudios and estudio in alert_estudios
        ):
            filtrados.append(anime)

    return filtrados or temporada


async def _enviar_digest_usuario(
    context: CallbackContext,
    neo4j,
    user_id: str,
    temporada: list[dict],
    novidades_web: list[dict],
    novidades_reddit: list[dict],
    tvmaze_cache: dict[str, list[dict]],
) -> bool:
    """Gera e envia digest para um unico usuario."""
    user_profile = {}
    try:
        user_profile = neo4j.get_user_profile(user_id)
    except Exception:
        pass

    temporada_user = _filtrar_temporada_por_alertas(temporada, user_profile)
    radar = _build_personal_radar(user_profile, temporada_user)
    radar["agenda_episodios"] = _build_tvmaze_episode_alerts(
        user_profile,
        cache=tvmaze_cache,
        days=10,
        limit=4,
    )

    messages = notif_prompt.build_messages(
        temporada_user,
        novidades_web,
        novidades_reddit,
        user_profile,
        radar=radar,
    )
    digest = await asyncio.to_thread(openrouter.converse, messages)

    # Salva os animes do radar como recomendados para nao repetir no proximo digest
    if radar.get("picks"):
        try:
            titulos_picks = [p["titulo"] for p in radar["picks"] if p.get("titulo")]
            if titulos_picks:
                await asyncio.to_thread(neo4j.registrar_recomendacoes, user_id, titulos_picks)
        except Exception as e:
            logger.debug("Notificador: erro ao registrar recomendados user=%s: %s", user_id, e)

    texto = formatar_telegram(digest)
    await context.bot.send_message(
        chat_id=user_id,
        text=texto,
        parse_mode="HTML",
    )
    return True


async def enviar_diario(context: CallbackContext) -> None:
    """Job diario - coleta novidades e envia digest personalizado por usuario."""
    logger.info("Notificador: iniciando digest diario")

    temporada, novidades_web, novidades_reddit = await asyncio.to_thread(_coletar_dados_diarios)

    try:
        neo4j = get_neo4j()
        user_ids = neo4j.get_all_user_ids()
        logger.info("Notificador: %d usuarios para notificar", len(user_ids))
    except Exception as e:
        logger.error("Notificador: erro ao buscar usuarios: %s", e)
        return

    enviados = 0
    erros = 0
    tvmaze_cache: dict[str, list[dict]] = {}

    for user_id in user_ids:
        try:
            await _enviar_digest_usuario(
                context=context,
                neo4j=neo4j,
                user_id=user_id,
                temporada=temporada,
                novidades_web=novidades_web,
                novidades_reddit=novidades_reddit,
                tvmaze_cache=tvmaze_cache,
            )
            enviados += 1
            logger.info("Notificador: digest enviado para user=%s", user_id)

        except Exception as e:
            erros += 1
            logger.warning("Notificador: erro ao notificar user=%s: %s", user_id, e)

    logger.info("Notificador: digest concluido - enviados=%d erros=%d", enviados, erros)


async def enviar_diario_usuario(context: CallbackContext, user_id: str) -> bool:
    """Gera digest imediatamente para um unico usuario (uso no comando /novidades)."""
    user_id = str(user_id)
    logger.info("Notificador: digest on-demand user=%s", user_id)

    temporada, novidades_web, novidades_reddit = await asyncio.to_thread(_coletar_dados_diarios)

    try:
        neo4j = get_neo4j()
    except Exception as e:
        logger.error("Notificador: erro ao abrir Neo4j no modo usuario=%s: %s", user_id, e)
        return False

    try:
        await _enviar_digest_usuario(
            context=context,
            neo4j=neo4j,
            user_id=user_id,
            temporada=temporada,
            novidades_web=novidades_web,
            novidades_reddit=novidades_reddit,
            tvmaze_cache={},
        )
        logger.info("Notificador: digest on-demand enviado user=%s", user_id)
        return True
    except Exception as e:
        logger.warning("Notificador: erro digest on-demand user=%s: %s", user_id, e)
        return False


async def verificar_novos_episodios(context: CallbackContext) -> None:
    """
    Job de alerta de episodios (20h) - notifica series em progresso com
    episodios chegando nos proximos 2 dias. Sem LLM, direto ao ponto.
    """
    logger.info("Notificador: verificando novos episodios")

    try:
        neo4j = get_neo4j()
        user_ids = neo4j.get_all_user_ids()
    except Exception as e:
        logger.error("Notificador episodios: erro ao buscar usuarios: %s", e)
        return

    tvmaze_cache: dict[str, list[dict]] = {}
    enviados = 0

    for user_id in user_ids:
        try:
            titulos = neo4j.get_progresso_ativo(user_id)
            if not titulos:
                continue

            alertas = []
            seen = set()
            for titulo in titulos[:6]:
                if titulo not in tvmaze_cache:
                    try:
                        tvmaze_cache[titulo] = tvmaze.get_upcoming_episodes(query=titulo, days=2, limit=1)
                    except Exception as e:
                        logger.debug("TVMaze falhou para '%s': %s", titulo, e)
                        tvmaze_cache[titulo] = []
                for ep in tvmaze_cache[titulo]:
                    key = f"{ep.get('show_name')}|{ep.get('airdate')}|{ep.get('season')}|{ep.get('episode')}"
                    if key not in seen:
                        seen.add(key)
                        alertas.append(ep)

            if not alertas:
                continue

            alertas.sort(key=lambda x: x.get("airdate") or "")
            linhas = ["<b>Episodios chegando (proximos 2 dias):</b>\n"]
            for ep in alertas[:4]:
                show = ep.get("show_name", "?")
                airdate = ep.get("airdate", "?")
                season = ep.get("season")
                episode = ep.get("episode")
                ep_label = f" S{season:02d}E{episode:02d}" if season and episode else ""
                linhas.append(f"- <b>{_escape_html(show)}</b>{ep_label} · {_escape_html(airdate)}")

            await context.bot.send_message(
                chat_id=user_id,
                text="\n".join(linhas),
                parse_mode="HTML",
            )
            enviados += 1
            logger.info("Notificador episodios: alerta enviado para user=%s", user_id)

        except Exception as e:
            logger.warning("Notificador episodios: erro user=%s: %s", user_id, e)

    logger.info("Notificador episodios: concluido - enviados=%d", enviados)


async def coordinator_notificacoes(context: CallbackContext) -> None:
    """
    Roda a cada minuto. Verifica quais usuarios querem notificacao agora
    e envia os tipos correspondentes (digest, episodios, vagas, noticias).
    - Digest/episodios/vagas: disparam apenas no minuto 0 de cada hora (comportamento horario)
    - Noticias: disparam no hora:minuto exato configurado pelo usuario
    """
    import pytz
    agora = datetime.datetime.now(pytz.timezone("America/Sao_Paulo"))
    hora_atual = agora.hour
    minuto_atual = agora.minute
    logger.debug("Coordinator notificacoes: hora=%02d minuto=%02d", hora_atual, minuto_atual)

    try:
        neo4j = get_neo4j()
    except Exception as e:
        logger.error("Coordinator: erro Neo4j: %s", e)
        return

    # ── Digest (novidades anime/manga) — dispara so no minuto 0 ──────────────
    if minuto_atual != 0:
        # Pula digest/episodios/vagas fora do minuto exato da hora
        ids_digest = []
    else:
        ids_digest = neo4j.get_usuarios_por_hora_notificacao(hora_atual, "digest")
    if ids_digest:
        logger.info("Coordinator: digest para %d usuarios na hora %d", len(ids_digest), hora_atual)
        temporada, novidades_web, novidades_reddit = await asyncio.to_thread(_coletar_dados_diarios)
        tvmaze_cache: dict[str, list[dict]] = {}
        for user_id in ids_digest:
            try:
                await _enviar_digest_usuario(
                    context=context,
                    neo4j=neo4j,
                    user_id=user_id,
                    temporada=temporada,
                    novidades_web=novidades_web,
                    novidades_reddit=novidades_reddit,
                    tvmaze_cache=tvmaze_cache,
                )
                logger.info("Coordinator: digest enviado user=%s", user_id)
            except Exception as e:
                logger.warning("Coordinator: erro digest user=%s: %s", user_id, e)

    # ── Episodios — dispara so no minuto 0 ────────────────────────────────────
    ids_ep = [] if minuto_atual != 0 else neo4j.get_usuarios_por_hora_notificacao(hora_atual, "episodios")
    if ids_ep:
        logger.info("Coordinator: episodios para %d usuarios na hora %d", len(ids_ep), hora_atual)
        # Reusar logica do verificar_novos_episodios mas so para esses usuarios
        tvmaze_cache_ep: dict[str, list[dict]] = {}
        for user_id in ids_ep:
            try:
                titulos = neo4j.get_progresso_ativo(user_id)
                if not titulos:
                    continue
                alertas = []
                seen = set()
                for titulo in titulos[:6]:
                    if titulo not in tvmaze_cache_ep:
                        try:
                            from data.tvmaze import tvmaze
                            tvmaze_cache_ep[titulo] = tvmaze.get_upcoming_episodes(query=titulo, days=2, limit=1)
                        except Exception:
                            tvmaze_cache_ep[titulo] = []
                    for ep in tvmaze_cache_ep[titulo]:
                        key = f"{ep.get('show_name')}|{ep.get('airdate')}|{ep.get('season')}|{ep.get('episode')}"
                        if key not in seen:
                            seen.add(key)
                            alertas.append(ep)
                if not alertas:
                    continue
                alertas.sort(key=lambda x: x.get("airdate") or "")
                linhas = ["<b>Episodios chegando (proximos 2 dias):</b>\n"]
                for ep in alertas[:4]:
                    show = ep.get("show_name", "?")
                    airdate = ep.get("airdate", "?")
                    season = ep.get("season")
                    episode = ep.get("episode")
                    ep_label = f" S{season:02d}E{episode:02d}" if season and episode else ""
                    linhas.append(f"- <b>{_escape_html(show)}</b>{ep_label} · {_escape_html(airdate)}")
                await context.bot.send_message(chat_id=user_id, text="\n".join(linhas), parse_mode="HTML")
            except Exception as e:
                logger.warning("Coordinator: erro episodios user=%s: %s", user_id, e)

    # ── Vagas — dispara so no minuto 0 ────────────────────────────────────────
    ids_vagas = [] if minuto_atual != 0 else neo4j.get_usuarios_por_hora_notificacao(hora_atual, "vagas")
    if ids_vagas:
        logger.info("Coordinator: vagas para %d usuarios na hora %d", len(ids_vagas), hora_atual)
        for user_id in ids_vagas:
            try:
                from agents.jobs import jobs_node
                state = {"user_id": user_id, "raw_input": "vagas para mim hoje", "intent": "vagas"}
                resultado = await asyncio.to_thread(jobs_node, state)
                texto_vagas = resultado.get("response", "")
                if texto_vagas:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"<b>Vagas do dia para voce:</b>\n\n{texto_vagas}",
                        parse_mode="HTML",
                    )
            except Exception as e:
                logger.warning("Coordinator: erro vagas user=%s: %s", user_id, e)

    # ── Noticias personalizadas — hora:minuto exato ───────────────────────────
    ids_noticias = neo4j.get_usuarios_noticias_agendadas(hora_atual, minuto_atual)
    if ids_noticias:
        logger.info("Coordinator: noticias para %d usuarios em %02d:%02d", len(ids_noticias), hora_atual, minuto_atual)
        for user_id in ids_noticias:
            try:
                interesses = neo4j.get_interesses_noticias(user_id)
                query = " ".join(interesses[:3]) if interesses else "noticias hoje"
                from data.news import buscar_por_google_news
                noticias = await asyncio.to_thread(buscar_por_google_news, query, 5)
                if not noticias:
                    continue
                linhas = [f"<b>Noticias de {', '.join(interesses[:3]) if interesses else 'hoje'}:</b>\n"]
                for n in noticias[:5]:
                    titulo = _escape_html(n.get("titulo", ""))
                    url = n.get("url", "")
                    safe_link = _format_html_link(titulo, url) if url else titulo
                    linhas.append(f"- {safe_link}")
                await context.bot.send_message(
                    chat_id=user_id,
                    text="\n".join(linhas),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning("Coordinator: erro noticias user=%s: %s", user_id, e)


async def verificar_lancamentos_culturais(context: CallbackContext) -> None:
    """
    Job semanal (sexta 12h) - notifica usuarios sobre novos lancamentos de artistas
    e autores favoritos: albums, turnês e novos livros.
    """
    logger.info("Notificador cultural: iniciando verificacao de lancamentos")

    try:
        neo4j = get_neo4j()
        usuarios = neo4j.get_usuarios_com_preferencias_culturais()
        logger.info("Notificador cultural: %d usuarios com preferencias culturais", len(usuarios))
    except Exception as e:
        logger.error("Notificador cultural: erro ao buscar usuarios: %s", e)
        return

    if not usuarios:
        return

    try:
        from data.musicbrainz import musicbrainz
        from data.openlibrary import openlibrary
    except ImportError as e:
        logger.error("Notificador cultural: imports falharam: %s", e)
        return

    mb_cache: dict[str, list[dict]] = {}
    ol_cache: dict[str, list[dict]] = {}
    enviados = 0
    ano_atual = datetime.date.today().year

    for usuario in usuarios:
        user_id = usuario.get("telegram_id")
        artistas = usuario.get("artistas_favoritos", [])
        autores = usuario.get("autores_favoritos", [])

        if not user_id:
            continue

        linhas = []

        # Verifica lancamentos de artistas favoritos
        for artista in artistas[:5]:
            if artista not in mb_cache:
                try:
                    mb_cache[artista] = musicbrainz.get_lancamentos_recentes_artista(artista, dias=30)
                except Exception as e:
                    logger.debug("Notificador cultural: MusicBrainz erro para '%s': %s", artista, e)
                    mb_cache[artista] = []

            lancamentos = mb_cache[artista]
            for lc in lancamentos[:2]:
                titulo = lc.get("titulo", "?")
                data = lc.get("data", "")
                tipo = lc.get("subtipo", "album")
                safe_artist = _escape_html(artista)
                safe_titulo = _escape_html(titulo)
                safe_tipo = _escape_html(tipo)
                safe_data = _escape_html(data) if data else ""
                linhas.append(
                    f"🎵 <b>{safe_artist}</b> - {safe_titulo} ({safe_tipo})"
                    f"{' · ' + safe_data if safe_data else ''}"
                )

        # Busca tambem por DDG para turnês e shows ao vivo
        for artista in artistas[:3]:
            try:
                anos_busca = [ano_atual, ano_atual + 1]
                achou = False
                for ano in anos_busca:
                    query = f"{artista} turne show {ano}"
                    resultados = _buscar_novidades_ddg(query, max_results=2)
                    for r in resultados:
                        title = r.get("title", "")
                        href = r.get("href", "")
                        if any(k in title.lower() for k in ["tour", "turne", "show", "concert", "live"]):
                            safe_artist = _escape_html(artista)
                            safe_link = _format_html_link(title, href, max_len=80)
                            linhas.append(f"🎤 <b>{safe_artist}</b>: {safe_link}")
                            achou = True
                            break
                    if achou:
                        break
            except Exception as e:
                logger.debug("Notificador cultural: DDG turne erro: %s", e)

        # Verifica novos livros de autores favoritos
        for autor in autores[:5]:
            if autor not in ol_cache:
                try:
                    ol_cache[autor] = openlibrary.get_livros_recentes_autor(autor)
                except Exception as e:
                    logger.debug("Notificador cultural: OpenLibrary erro para '%s': %s", autor, e)
                    ol_cache[autor] = []

            livros = ol_cache[autor]
            for livro in livros[:1]:
                titulo = livro.get("titulo", "?")
                ano = livro.get("ano", "")
                safe_autor = _escape_html(autor)
                safe_titulo = _escape_html(titulo)
                safe_ano = _escape_html(str(ano)) if ano else ""
                linhas.append(f"📚 <b>{safe_autor}</b> - {safe_titulo}{' (' + safe_ano + ')' if safe_ano else ''}")

        if not linhas:
            continue

        try:
            header = "<b>Novidades dos seus artistas e autores favoritos esta semana:</b>\n"
            texto = header + "\n".join(linhas[:10])
            await context.bot.send_message(
                chat_id=user_id,
                text=texto,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            enviados += 1
            logger.info("Notificador cultural: lancamentos enviados para user=%s", user_id)
        except Exception as e:
            logger.warning("Notificador cultural: erro ao enviar para user=%s: %s", user_id, e)

    logger.info("Notificador cultural: concluido - enviados=%d", enviados)
