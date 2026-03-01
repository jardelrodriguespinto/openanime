import datetime

SYSTEM = """Voce e um assistente pessoal de anime, manga e manhwa.
Toda manha voce envia um resumo diario personalizado para o usuario.
Tom: animado, casual, como amigo que manja.

Regras:
- Seja direto e conciso (notificacao matinal).
- Use perfil + radar personalizado para priorizar o que faz sentido para este usuario.
- Se nao tiver perfil, use destaques da temporada.
- Maximo 350 palavras.
"""


def build_messages(
    temporada: list[dict],
    novidades_web: list[dict],
    novidades_reddit: list[dict],
    user_profile: dict,
    radar: dict | None = None,
) -> list[dict]:
    hoje = datetime.date.today().strftime("%d/%m/%Y")

    context = _montar_contexto(temporada, novidades_web, novidades_reddit)
    perfil_txt = _montar_perfil(user_profile)
    radar_txt = _montar_radar(radar or {})

    user_content = f"""Data de hoje: {hoje}

{context}

{perfil_txt}

{radar_txt}

Gere o digest diario com:
1. *Novidades* - 2 a 3 destaques (com fonte se houver)
2. *Temporada Atual* - 3 animes em destaque
3. *Radar Personalizado* - 2 picks sob medida com base no perfil
4. *Sugestao do Dia* - 1 recomendacao principal
5. frase final curta convidando para conversar

Seja objetivo e legivel."""

    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_content},
    ]


def _montar_contexto(
    temporada: list[dict],
    novidades_web: list[dict],
    novidades_reddit: list[dict],
) -> str:
    lines = []

    if temporada:
        lines.append("=== TEMPORADA ATUAL ===")
        for a in temporada[:8]:
            nota = a.get("nota_mal") or "?"
            eps = a.get("episodios") or "?"
            generos = ", ".join(a.get("generos", [])[:3])
            lines.append(f"- {a['titulo']} - nota {nota} - {eps} eps - {generos}")

    if novidades_web:
        lines.append("\n=== NOVIDADES WEB ===")
        for r in novidades_web[:5]:
            title = r.get("title", "")
            href = r.get("href", "")
            body = r.get("body", "")[:120]
            lines.append(f"- {title} ({href}): {body}")

    if novidades_reddit:
        lines.append("\n=== REDDIT HOT ===")
        for r in novidades_reddit[:4]:
            title = r.get("title", "")
            sub = r.get("subreddit", "")
            score = r.get("score", 0)
            lines.append(f"- r/{sub} (+{score}) {title}")

    return "\n".join(lines) if lines else "Sem dados coletados hoje."


def _montar_perfil(profile: dict) -> str:
    if not profile:
        return "Perfil: usuario novo, sem historico."

    lines = ["=== PERFIL DO USUARIO ==="]

    assistidos = profile.get("assistidos", [])
    if assistidos:
        titulos = [a["titulo"] for a in assistidos[:10] if a.get("titulo")]
        if titulos:
            lines.append(f"Assistiu/leu: {', '.join(titulos)}")

    progresso = profile.get("progresso", [])
    if progresso:
        p = [x["titulo"] for x in progresso[:5] if x.get("titulo")]
        if p:
            lines.append(f"Em progresso: {', '.join(p)}")

    generos = profile.get("generos_favoritos", [])
    if generos:
        lines.append(f"Generos favoritos: {', '.join(generos[:6])}")

    dropados = profile.get("dropados", [])
    if dropados:
        lines.append(f"Dropou: {', '.join(d['titulo'] for d in dropados[:3] if d.get('titulo'))}")

    mood = profile.get("mood_atual")
    if mood:
        lines.append(f"Mood atual: {mood}")

    if profile.get("alerta_generos"):
        lines.append(f"Alerta por genero: {', '.join(profile['alerta_generos'][:6])}")

    if profile.get("alerta_estudios"):
        lines.append(f"Alerta por estudio: {', '.join(profile['alerta_estudios'][:6])}")

    return "\n".join(lines)


def _montar_radar(radar: dict) -> str:
    if not radar:
        return "=== RADAR PERSONALIZADO ===\nSem radar calculado hoje."

    lines = ["=== RADAR PERSONALIZADO ==="]

    risk = radar.get("risk_level")
    if risk:
        lines.append(f"Risco de drop: {risk}")

    if radar.get("picks"):
        lines.append("Picks de hoje:")
        for item in radar["picks"][:3]:
            titulo = item.get("titulo", "?")
            motivo = item.get("motivo", "")
            lines.append(f"- {titulo}: {motivo}")

    if radar.get("evitar"):
        lines.append(f"Evitar excesso de: {', '.join(radar['evitar'][:4])}")

    if radar.get("progresso_alerta"):
        lines.append("Continuar pendencias:")
        for item in radar["progresso_alerta"][:3]:
            lines.append(f"- {item}")

    if radar.get("agenda_episodios"):
        lines.append("Agenda de episodios (TVMaze):")
        for ep in radar["agenda_episodios"][:4]:
            show = ep.get("show_name", "?")
            airdate = ep.get("airdate", "?")
            season = ep.get("season")
            episode = ep.get("episode")
            ep_ref = f"S{season}E{episode}" if season is not None and episode is not None else "ep ?"
            lines.append(f"- {show}: {airdate} ({ep_ref})")

    return "\n".join(lines)
