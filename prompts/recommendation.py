SYSTEM = """Voce e especialista em recomendacoes de anime, manga, manhwa, webtoon, filmes, series, doramas, musica e livros.

Regras principais:
- Baseie recomendacoes no perfil real do usuario.
- Nunca repetir obra ja assistida, dropada ou recomendada recentemente (se vier em contexto).
- Respeitar tempo disponivel, mood do dia, filtro de maturidade e preferencia de audio quando existirem.
- Se usuario pedir \"mais assim\" ou \"menos assim\", use feedback_memoria para ajustar direcao.
- Recomende no maximo 3 opcoes por resposta.
- Para cada opcao: diga por que combina, risco de nao gostar, e quando encaixa melhor.
- Inclua o tipo da recomendacao (anime, filme, serie, dorama, musica, livro).
- Se o pedido for explicito de dominio (ex: so filmes, so musicas, so livros), nao misture dominios.
- Para musica, voce pode recomendar artista, album ou faixa quando houver contexto.
- Para livro, priorize livro e autor, e cite o perfil de leitura (fantasia, thriller, etc) quando der.
- Tom: amigo que conhece o gosto do usuario.

Formato sugerido:
*TITULO* (tipo) - Por que combina: ...
Risco: ...
Melhor para: ...
"""


def build_messages(
    user_message: str,
    history: list[dict],
    user_profile: dict,
    semantic_results: list[dict] | None = None,
    catalog_results: list[dict] | None = None,
    reddit_results: list[dict] | None = None,
    target_domains: list[str] | None = None,
) -> list[dict]:
    profile_text = _format_profile(user_profile)
    semantic_text = _format_semantic(semantic_results or [])
    catalog_text = _format_catalog(catalog_results or [])
    reddit_text = _format_reddit(reddit_results or [])

    system = SYSTEM
    if target_domains:
        system += f"\n\nDominios-alvo do pedido: {', '.join(target_domains)}"
    if profile_text:
        system += f"\n\nPerfil do usuario:\n{profile_text}"
    if semantic_text:
        system += f"\n\nObras semanticamente similares:\n{semantic_text}"
    if catalog_text:
        system += f"\n\nDados de catalogo cruzado:\n{catalog_text}"
    if reddit_text:
        system += f"\n\nComunidade (Reddit):\n{reddit_text}"

    messages = [{"role": "system", "content": system}]
    for msg in history[-8:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})
    return messages



def _format_profile(profile: dict) -> str:
    if not profile:
        return "Usuario novo, sem historico ainda."

    lines = []

    if profile.get("assistidos"):
        top = sorted(
            profile["assistidos"],
            key=lambda x: (x.get("nota") is not None, float(x.get("nota") or 0)),
            reverse=True,
        )
        lines.append("Assistidos (resumo):")
        for item in top[:10]:
            nota = item.get("nota", "?")
            opiniao = (item.get("opiniao") or "").strip()
            suffix = f' - "{opiniao}"' if opiniao else ""
            lines.append(f"  - {item['titulo']} ({nota}/10){suffix}")

    if profile.get("dropados"):
        lines.append("Dropados:")
        for item in profile["dropados"][:6]:
            ep = item.get("episodio", "?")
            lines.append(f"  - {item['titulo']} (ep {ep})")

    if profile.get("quer_ver"):
        lines.append("Quer ver:")
        for item in profile["quer_ver"][:8]:
            lines.append(f"  - {item['titulo']}")

    if profile.get("generos_favoritos"):
        lines.append(f"Generos favoritos: {', '.join(profile['generos_favoritos'][:6])}")

    if profile.get("temas_favoritos"):
        lines.append(f"Temas favoritos: {', '.join(profile['temas_favoritos'][:6])}")

    if profile.get("mood_atual"):
        lines.append(f"Mood atual: {profile['mood_atual']}")

    if profile.get("tempo_disponivel_min"):
        lines.append(f"Tempo disponivel por sessao: {profile['tempo_disponivel_min']} min")

    if profile.get("preferencia_audio"):
        lines.append(f"Preferencia de audio: {profile['preferencia_audio']}")

    filtros = profile.get("filtros_maturidade", {}) or {}
    if filtros:
        lines.append(
            "Filtro maturidade: "
            f"nsfw={filtros.get('permitir_nsfw')} "
            f"violencia={filtros.get('limite_violencia')} "
            f"ecchi={filtros.get('limite_ecchi')}"
        )

    recs = profile.get("recomendados_recentes", []) or []
    if recs:
        lines.append(f"Nao repetir (recomendados recentes): {', '.join(recs[:10])}")

    fb = profile.get("feedback_memoria", {}) or {}
    if fb.get("curtidos"):
        lines.append(f"Feedback positivo: {', '.join(fb['curtidos'][:6])}")
    if fb.get("evitar"):
        lines.append(f"Feedback negativo (evitar estilo): {', '.join(fb['evitar'][:6])}")

    if profile.get("desafio_semanal"):
        lines.append(f"Desafio semanal ativo: {profile['desafio_semanal']}")

    queda = profile.get("queda_interesse", {}) or {}
    if queda.get("nivel"):
        lines.append(f"Queda de interesse: {queda.get('nivel')} ({queda.get('sugestao', '')})")

    wl = profile.get("watchlist_inteligente", []) or []
    if wl:
        titulos = [item.get("titulo") for item in wl[:5] if item.get("titulo")]
        if titulos:
            lines.append(f"Watchlist inteligente sugerida: {', '.join(titulos)}")

    return "\n".join(lines)



def _format_semantic(results: list[dict]) -> str:
    if not results:
        return ""
    lines = []
    for item in results[:8]:
        lines.append(
            f"  - {item.get('titulo', '?')} ({item.get('ano', '?')}) - "
            f"{(item.get('synopsis', '') or '')[:120]}"
        )
    return "\n".join(lines)



def _format_catalog(results: list[dict]) -> str:
    if not results:
        return ""

    lines = []
    for item in results[:10]:
        tipo = (item.get("tipo") or "?").strip().lower()
        titulo = item.get("titulo", "?")

        if tipo == "musica":
            subtipo = item.get("subtipo", "musica")
            artista = item.get("artista") or item.get("pais") or ""
            ano = item.get("ano") or "?"
            lines.append(f"  - {titulo} ({tipo}/{subtipo}) | artista/info: {artista} | ano: {ano}")
            continue

        if tipo == "livro":
            autor = item.get("autor", "")
            ano = item.get("ano", "?")
            paginas = item.get("paginas") or "?"
            lines.append(f"  - {titulo} ({tipo}) | autor: {autor} | ano: {ano} | paginas: {paginas}")
            continue

        nota = item.get("nota_mal") or item.get("nota") or "?"
        eps = item.get("episodios") or "?"
        generos = ", ".join((item.get("generos") or [])[:4])
        lines.append(f"  - {titulo} ({tipo}) | nota: {nota} | eps: {eps} | generos: {generos}")

    return "\n".join(lines)



def _format_reddit(results: list[dict]) -> str:
    if not results:
        return ""
    lines = []
    for item in results[:5]:
        title = item.get("title", "")[:90]
        sub = item.get("subreddit", "")
        score = item.get("score", 0)
        lines.append(f"  - [{sub}] {title} (up {score})")
    return "\n".join(lines)
