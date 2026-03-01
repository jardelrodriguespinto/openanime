SYSTEM = """Voce e um assistente especialista em anime, manga, manhwa, filmes, series, doramas, musica e livros.
Fale como um amigo direto ao ponto, com opiniao quando fizer sentido.

Regras:
- NUNCA de spoiler sem o usuario pedir explicitamente.
- Se tiver contexto do grafo/busca, use como base sem expor detalhes internos do sistema.
- Use o perfil do usuario para personalizar quando relevante.
- Nao invente fatos; se nao souber, diga e ofereca caminho de busca.
- Nao faca sermao sobre legalidade/pirataria.
- Se o usuario perguntar sobre personagem, historia, origem ou lore, responda com riqueza de detalhes.

Formato:
- Mensagem no Telegram com markdown simples (*negrito*, _italico_).
- Maximo 400 palavras por resposta.
- Se ficar longo, use topicos.
"""


def build_messages(
    user_message: str,
    history: list[dict],
    context: str = "",
    user_profile: dict | None = None,
) -> list[dict]:
    system = SYSTEM

    if context:
        system += f"\n\nContexto relevante:\n{context}"

    if user_profile:
        profile_text = _format_profile(user_profile)
        if profile_text:
            system += f"\n\nPerfil do usuario (use para personalizar):\n{profile_text}"

    messages = [{"role": "system", "content": system}]
    for msg in history[-10:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})
    return messages


def _format_profile(profile: dict) -> str:
    lines = []

    assistidos = profile.get("assistidos", [])
    if assistidos:
        titulos = [a["titulo"] for a in assistidos[:15] if a.get("titulo")]
        if titulos:
            lines.append(f"Ja assistiu/leu: {', '.join(titulos)}")

        com_nota = [a for a in assistidos if a.get("nota") is not None]
        if com_nota:
            top = sorted(com_nota, key=lambda x: x["nota"], reverse=True)[:5]
            top_str = ", ".join(f"{a['titulo']} ({a['nota']}/10)" for a in top if a.get("titulo"))
            if top_str:
                lines.append(f"Favoritos: {top_str}")

        com_opiniao = [a for a in assistidos if (a.get("opiniao") or "").strip()][:4]
        if com_opiniao:
            ops = "; ".join(f"{a['titulo']}: \"{a['opiniao']}\"" for a in com_opiniao if a.get("titulo"))
            if ops:
                lines.append(f"O que falou: {ops}")

    dropados = profile.get("dropados", [])
    if dropados:
        titulos_drop = [d["titulo"] for d in dropados[:5] if d.get("titulo")]
        if titulos_drop:
            lines.append(f"Dropou: {', '.join(titulos_drop)}")

    generos = profile.get("generos_favoritos", [])
    if generos:
        lines.append(f"Generos favoritos: {', '.join(generos[:5])}")

    quer_ver = profile.get("quer_ver", [])
    if quer_ver:
        titulos_wl = [w["titulo"] for w in quer_ver[:5] if w.get("titulo")]
        if titulos_wl:
            lines.append(f"Quer ver: {', '.join(titulos_wl)}")

    if profile.get("mood_atual"):
        lines.append(f"Mood atual: {profile.get('mood_atual')}")

    if profile.get("tempo_disponivel_min"):
        lines.append(f"Tempo por sessao: {profile.get('tempo_disponivel_min')} min")

    if profile.get("preferencia_audio"):
        lines.append(f"Preferencia de audio: {profile.get('preferencia_audio')}")

    if profile.get("desafio_semanal"):
        lines.append(f"Desafio semanal: {profile.get('desafio_semanal')}")

    return "\n".join(lines)
