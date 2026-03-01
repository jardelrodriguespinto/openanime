SYSTEM = """Voce e especialista em recomendacoes de anime, manga, manhwa, webtoon, filmes, series e doramas.

Regras principais:
- Baseie recomendacoes no perfil real do usuario.
- Nunca repetir obra ja assistida, dropada ou recomendada recentemente (se vier em contexto).
- Respeitar tempo disponivel, mood do dia, filtro de maturidade e preferencia de audio quando existirem.
- Se o usuario pedir modo casal/grupo, busque intersecao de gostos e explique trade-offs.
- Se usuario pedir "mais assim" ou "menos assim", use feedback_memoria para ajustar direcao.
- Recomende no maximo 3 opcoes por resposta.
- Para cada opcao: diga por que combina, risco de nao gostar, e quando encaixa melhor (sessao curta/longa).
- Inclua o tipo da obra (anime, filme, serie, dorama, manga) em cada recomendacao.
- Tom: amigo que conhece seu gosto.

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
    jikan_results: list[dict] | None = None,
    reddit_results: list[dict] | None = None,
) -> list[dict]:
    profile_text = _format_profile(user_profile)
    semantic_text = _format_semantic(semantic_results or [])
    jikan_text = _format_jikan(jikan_results or [])
    reddit_text = _format_reddit(reddit_results or [])

    system = SYSTEM
    if profile_text:
        system += f"\n\nPerfil do usuario:\n{profile_text}"
    if semantic_text:
        system += f"\n\nObras semanticamente similares:\n{semantic_text}"
    if jikan_text:
        system += f"\n\nDados de catalogo (Jikan/MAL):\n{jikan_text}"
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
        for a in top[:10]:
            nota = a.get("nota", "?")
            opiniao = (a.get("opiniao") or "").strip()
            suffix = f' — "{opiniao}"' if opiniao else ""
            lines.append(f"  - {a['titulo']} ({nota}/10){suffix}")

    if profile.get("dropados"):
        lines.append("Dropados:")
        for d in profile["dropados"][:6]:
            ep = d.get("episodio", "?")
            lines.append(f"  - {d['titulo']} (ep {ep})")

    if profile.get("quer_ver"):
        lines.append("Quer ver:")
        for w in profile["quer_ver"][:8]:
            lines.append(f"  - {w['titulo']}")

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
    for r in results[:8]:
        lines.append(
            f"  - {r.get('titulo', '?')} ({r.get('ano', '?')}) - "
            f"{(r.get('synopsis', '') or '')[:120]}"
        )
    return "\n".join(lines)


def _format_jikan(results: list[dict]) -> str:
    if not results:
        return ""
    lines = []
    for r in results[:8]:
        nota = r.get("nota_mal", "?")
        generos = ", ".join(r.get("generos", [])[:4])
        ep = r.get("episodios", "?")
        lines.append(f"  - {r.get('titulo', '?')} | MAL {nota} | {ep} eps | {generos}")
    return "\n".join(lines)


def _format_reddit(results: list[dict]) -> str:
    if not results:
        return ""
    lines = []
    for r in results[:5]:
        title = r.get("title", "")[:90]
        sub = r.get("subreddit", "")
        score = r.get("score", 0)
        lines.append(f"  - [{sub}] {title} (up {score})")
    return "\n".join(lines)

