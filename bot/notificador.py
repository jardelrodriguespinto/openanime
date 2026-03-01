"""
Notificador diario - envia digest de novidades e sugestoes para todos os usuarios.
Roda via JobQueue do python-telegram-bot.
"""

import logging
import re
import time

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
    import datetime

    hoje = datetime.date.today().strftime("%d/%m/%Y")

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
            "anime news today temporada 2026",
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


async def enviar_diario(context: CallbackContext) -> None:
    """Job diario - coleta novidades e envia digest personalizado por usuario."""
    logger.info("Notificador: iniciando digest diario")

    temporada, novidades_web, novidades_reddit = _coletar_dados_diarios()

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
            digest = openrouter.converse(messages)

            texto = formatar_telegram(digest)
            await context.bot.send_message(
                chat_id=user_id,
                text=texto,
                parse_mode="HTML",
            )
            enviados += 1
            logger.info("Notificador: digest enviado para user=%s", user_id)

        except Exception as e:
            erros += 1
            logger.warning("Notificador: erro ao notificar user=%s: %s", user_id, e)

    logger.info("Notificador: digest concluido - enviados=%d erros=%d", enviados, erros)
