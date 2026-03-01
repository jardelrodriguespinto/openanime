SYSTEM = """Voce e um critico e analista especializado em anime, manga, manhwa e webtoon.

Modos suportados:
- Analise/review tradicional
- Explicador de final (quando pedido)
- Comparador lado a lado (A vs B)
- Mapa de personagens (relacoes, papeis e dinamica)

Diretrizes:
- Sem spoiler, exceto se usuario pedir explicitamente.
- Se usuario pedir "explica o final", pode entrar em spoiler com aviso curto.
- Em comparacoes, use criterios: roteiro, personagens, pacing, acao, trilha/arte, payoff final.
- Em mapa de personagens, priorize protagonistas e relacoes centrais.
- Se houver perfil do usuario, conclua com qual obra encaixa melhor nele.
- Tom: critico amigavel, direto.
- Maximo 500 palavras.
- Use *negrito* para secoes.
"""


def build_messages(
    user_message: str,
    history: list[dict],
    jikan_data: list[dict],
    weaviate_data: list[dict],
    reddit_data: list[dict],
    user_profile: dict,
    character_data: list[dict] | None = None,
    compare_mode: bool = False,
) -> list[dict]:
    system = SYSTEM

    if compare_mode:
        system += "\n\nModo detectado: COMPARADOR. Termine com veredito de melhor encaixe para este usuario."

    if _pedido_final_explicado(user_message):
        system += "\n\nModo detectado: EXPLICADOR DE FINAL. Entregue leitura do desfecho com causalidade e simbolismo."

    if _pedido_mapa_personagens(user_message):
        system += "\n\nModo detectado: MAPA DE PERSONAGENS. Liste personagens-chave e relacoes principais."

    context = _montar_contexto(jikan_data, weaviate_data, reddit_data, character_data or [])
    if context:
        system += f"\n\nDados sobre a obra:\n{context}"

    perfil = _montar_perfil(user_profile)
    if perfil:
        system += f"\n\nPerfil do usuario:\n{perfil}"

    messages = [{"role": "system", "content": system}]
    for msg in history[-6:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})
    return messages


def _pedido_final_explicado(text: str) -> bool:
    t = (text or "").lower()
    return "final" in t and any(k in t for k in ["explica", "explain", "entendi", "significa"])


def _pedido_mapa_personagens(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in ["mapa de personagens", "relacao dos personagens", "quem e quem", "quem é quem"])


def _montar_contexto(
    jikan_data: list[dict],
    weaviate_data: list[dict],
    reddit_data: list[dict],
    character_data: list[dict],
) -> str:
    lines = []

    if jikan_data:
        lines.append("=== Dados Oficiais (Jikan/MAL) ===")
        for a in jikan_data[:3]:
            nota = a.get("nota_mal") or "N/A"
            eps = a.get("episodios") or "?"
            status = a.get("status", "")
            generos = ", ".join(a.get("generos", []))
            synopsis = (a.get("synopsis") or "")[:300]
            lines.append(
                f"{a['titulo']} - nota {nota} - {eps} eps - {status}\n"
                f"Generos: {generos}\nSinopse: {synopsis}"
            )

    if character_data:
        lines.append("\n=== Personagens (Jikan) ===")
        for cd in character_data[:2]:
            lines.append(f"{cd.get('titulo', '?')}:")
            for ch in (cd.get("personagens") or [])[:8]:
                role = ch.get("role") or "?"
                lines.append(f"  - {ch.get('nome', '?')} ({role})")

    if weaviate_data:
        lines.append("\n=== Contexto Semantico ===")
        for r in weaviate_data[:4]:
            titulo = r.get("titulo", "")
            synopsis = (r.get("synopsis") or "")[:200]
            if titulo:
                lines.append(f"{titulo}: {synopsis}")

    if reddit_data:
        lines.append("\n=== Comunidade (Reddit) ===")
        for r in reddit_data[:5]:
            title = r.get("title", "")
            body = (r.get("selftext") or "")[:160]
            score = r.get("score", 0)
            lines.append(f"+{score} | {title}: {body}")

    return "\n".join(lines)


def _montar_perfil(profile: dict) -> str:
    if not profile:
        return ""

    assistidos = profile.get("assistidos", [])
    generos = profile.get("generos_favoritos", [])
    mood = profile.get("mood_atual")

    parts = []
    if assistidos:
        titulos = [a["titulo"] for a in assistidos[:8] if a.get("titulo")]
        parts.append(f"Ja assistiu: {', '.join(titulos)}")
    if generos:
        parts.append(f"Generos favoritos: {', '.join(generos)}")
    if mood:
        parts.append(f"Mood atual: {mood}")

    return "\n".join(parts)

