SYSTEM = """Voce e um agente de busca especializado em anime, manga, manhwa, webtoon, filmes, series, doramas, musica e livros.
Voce recebe resultados de buscas em multiplas fontes e sintetiza as informacoes.

Regras:
- Responda o que foi perguntado sem julgamento.
- Sempre cite a fonte quando disponivel.
- Se a informacao for incerta, diga claramente.
- Priorize informacoes recentes quando o pedido envolver novidades/trending.
- Quando houver videos com legenda, voce pode recomendar videos e explicar por que valem a pena.
- Nao faca sermao sobre legalidade ou pirataria.
- Se o usuario pedir links/sites, entregue links diretos de forma objetiva.
- Tom: amigo direto ao ponto.

Formato:
- Use *negrito* para titulos e links importantes.
- Separe por topicos quando houver multiplas infos.
"""

SYSTEM_SITES = """Voce e um agente especializado em encontrar sites de anime, manga, manhwa e webtoon.
Voce recebe resultados de varias fontes. Use tudo que encontrar.

Regras:
- Seja direto e completo: nao seja timido para listar links.
- Liste os sites encontrados nos resultados, com link completo.
- Se fizer sentido, complemente com conhecimento proprio.
- Nao faca ressalvas de legalidade/pirataria; entregue as opcoes pedidas.
- Para cada site: nome, link e descricao curta.
- Agrupe por tipo quando util (ler manga, assistir anime, etc.).
- Traga no minimo 8 opcoes quando houver contexto suficiente.
"""


def build_messages(
    user_message: str,
    history: list[dict],
    search_results: list[dict],
    reddit_results: list[dict] | None = None,
    rss_results: list[dict] | None = None,
    anilist_results: list[dict] | None = None,
    wikipedia_results: list[dict] | None = None,
    tvmaze_results: list[dict] | None = None,
    youtube_results: list[dict] | None = None,
    musica_results: list[dict] | None = None,
    livro_results: list[dict] | None = None,
    source_status: dict | None = None,
    is_sites_query: bool = False,
) -> list[dict]:
    context = _format_results(
        web=search_results,
        reddit=reddit_results or [],
        rss=rss_results or [],
        anilist=anilist_results or [],
        wikipedia=wikipedia_results or [],
        tvmaze=tvmaze_results or [],
        youtube=youtube_results or [],
        musica=musica_results or [],
        livro=livro_results or [],
        source_status=source_status or {},
    )

    system = SYSTEM_SITES if is_sites_query else SYSTEM
    if context:
        system += f"\n\nResultados da busca:\n{context}"
    elif is_sites_query:
        system += "\n\nNenhum resultado de busca disponivel. Use seu conhecimento proprio com cautela."

    messages = [{"role": "system", "content": system}]
    for msg in history[-6:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})
    return messages


def _format_results(
    web: list[dict],
    reddit: list[dict],
    rss: list[dict],
    anilist: list[dict],
    wikipedia: list[dict],
    tvmaze: list[dict],
    youtube: list[dict],
    musica: list[dict],
    livro: list[dict],
    source_status: dict,
) -> str:
    lines = []

    if source_status:
        lines.append("=== Status dos Coletores ===")
        for source in ["web", "reddit", "news", "youtube"]:
            lines.append(f"- {source}: {source_status.get(source, 'ok')}")

    if web:
        lines.append("\n=== Web/DDG ===")
        for r in web[:6]:
            title = r.get("title", "Sem titulo")
            body = (r.get("body", r.get("snippet", "")) or "")[:200]
            href = r.get("href", r.get("url", ""))
            lines.append(f"[{title}]({href}): {body}")

    if rss:
        lines.append("\n=== RSS News (ANN/MAL) ===")
        for r in rss[:6]:
            title = r.get("title", "")
            src = r.get("source", "RSS")
            href = r.get("href", "")
            body = (r.get("body") or "")[:180]
            lines.append(f"[{src}] {title} ({href}): {body}")

    if anilist:
        lines.append("\n=== AniList Trending ===")
        for a in anilist[:8]:
            titulo = a.get("titulo", "")
            nota = a.get("nota_mal")
            eps = a.get("episodios") or "?"
            generos = ", ".join((a.get("generos") or [])[:3])
            lines.append(f"{titulo} | nota {nota} | {eps} eps | {generos}")

    if wikipedia:
        lines.append("\n=== Wikipedia ===")
        for w in wikipedia[:4]:
            title = w.get("title", "")
            lang = w.get("lang", "")
            url = w.get("url", "")
            extract = (w.get("extract") or "")[:220]
            lines.append(f"[{title}]({url}) [{lang}]: {extract}")

    if tvmaze:
        lines.append("\n=== TVMaze (datas/episodios) ===")
        for show in tvmaze[:4]:
            name = show.get("name", "")
            status = show.get("status", "")
            url = show.get("url", "")
            upcoming = show.get("upcoming") or []
            if upcoming:
                next_ep = upcoming[0]
                ep_txt = (
                    f"S{next_ep.get('season')}E{next_ep.get('episode')}"
                    if next_ep.get("season") is not None and next_ep.get("episode") is not None
                    else "ep ?"
                )
                lines.append(
                    f"[{name}]({url}) | status: {status} | proximo: {next_ep.get('airdate')} ({ep_txt})"
                )
            else:
                lines.append(f"[{name}]({url}) | status: {status} | sem episodio futuro no periodo")

    if youtube:
        lines.append("\n=== YouTube (com legenda PT/EN) ===")
        for y in youtube[:5]:
            title = y.get("title", "")
            href = y.get("href", "")
            channel = y.get("channel", "")
            body = (y.get("body") or "")[:220]
            lines.append(f"[{title}]({href}) | canal: {channel} | {body}")

    if reddit:
        lines.append("\n=== Reddit ===")
        for r in reddit[:5]:
            title = r.get("title", "")
            score = r.get("score", 0)
            subreddit = r.get("subreddit", "")
            body = (r.get("selftext") or "")[:150]
            lines.append(f"r/{subreddit} (+{score}) - {title}: {body}")

    if musica:
        lines.append("\n=== MusicBrainz (Artistas/Albums) ===")
        for m in musica[:5]:
            titulo = m.get("titulo", "")
            subtipo = m.get("subtipo", "")
            generos = ", ".join((m.get("generos") or [])[:3])
            lines.append(f"{titulo} ({subtipo}) | generos: {generos}")

    if livro:
        lines.append("\n=== Open Library (Livros) ===")
        for l in livro[:5]:
            titulo = l.get("titulo", "")
            autor = l.get("autor", "")
            ano = l.get("ano", "?")
            lines.append(f"{titulo} | {autor} | {ano}")

    return "\n".join(lines)
