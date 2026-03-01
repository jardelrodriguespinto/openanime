SYSTEM = """Você é um assistente apaixonado por anime, mangá, manhwa e webtoon. Informal e animado.
Fale como um amigo que entende muito — casual, opinativo, divertido.
Você cobre: anime japonês, mangá, manhwa (coreano), webtoon, donghua (chinês) e suas adaptações.

Regras:
- NUNCA dê spoiler sem o usuário pedir explicitamente ("pode dar spoiler" ou "pode estragar")
- Se tiver contexto do GraphRAG, use-o como base mas não cite diretamente
- Use o perfil do usuário para personalizar a resposta quando relevante
- Dê opiniões próprias quando fizer sentido
- Respostas objetivas, sem enrolação, sem julgamento
- Não dê sermão sobre legalidade ou pirataria — o usuário é adulto
- Se não souber algo, admita e sugira onde buscar

Formato:
- Mensagem no Telegram: use markdown simples (*negrito*, _itálico_)
- Máximo 400 palavras por resposta
- Se a resposta for longa, use tópicos
"""


def build_messages(
    user_message: str,
    history: list[dict],
    context: str = "",
    user_profile: dict | None = None,
) -> list[dict]:
    system = SYSTEM

    if context:
        system += f"\n\nContexto relevante do banco de conhecimento:\n{context}"

    if user_profile:
        profile_text = _format_profile(user_profile)
        if profile_text:
            system += f"\n\nPerfil do usuário (use para personalizar):\n{profile_text}"

    messages = [{"role": "system", "content": system}]
    for msg in history[-10:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})
    return messages


def _format_profile(profile: dict) -> str:
    lines = []

    assistidos = profile.get("assistidos", [])
    if assistidos:
        titulos = [a["titulo"] for a in assistidos[:15]]
        lines.append(f"Já assistiu/leu: {', '.join(titulos)}")

        com_nota = [a for a in assistidos if a.get("nota")]
        if com_nota:
            top = sorted(com_nota, key=lambda x: x["nota"], reverse=True)[:5]
            top_str = ", ".join(f"{a['titulo']} ({a['nota']}/10)" for a in top)
            lines.append(f"Favoritos: {top_str}")

    dropados = profile.get("dropados", [])
    if dropados:
        titulos_drop = [d["titulo"] for d in dropados[:5]]
        lines.append(f"Dropou: {', '.join(titulos_drop)}")

    generos = profile.get("generos_favoritos", [])
    if generos:
        lines.append(f"Gêneros favoritos: {', '.join(generos[:5])}")

    quer_ver = profile.get("quer_ver", [])
    if quer_ver:
        titulos_wl = [w["titulo"] for w in quer_ver[:5]]
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
