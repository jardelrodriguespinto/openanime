import re


def formatar_telegram(texto: str) -> str:
    """
    Adapta texto do LLM para HTML do Telegram.
    Escapa entidades HTML antes de converter markdown para tags.
    """
    if not texto:
        return "..."

    link_placeholders = {}
    url_placeholders = {}
    link_counter = [0]
    url_counter = [0]

    def _save_md_link(match):
        key = f"CODXMLINK{link_counter[0]}TOKEN"
        label = match.group(1)
        url = match.group(2)
        link_placeholders[key] = (label, url)
        link_counter[0] += 1
        return key

    def _save_url(match):
        key = f"CODXURL{url_counter[0]}TOKEN"
        url_placeholders[key] = match.group(1)
        url_counter[0] += 1
        return key

    # Salva links antes das conversoes de markdown para nao quebrar URLs.
    texto = re.sub(r"\[([^\]\n]{1,200})\]\((https?://[^\s)]+)\)", _save_md_link, texto)
    texto = re.sub(r"(?<![\"'=])(https?://[^\s<]+)", _save_url, texto)

    placeholders = {}
    counter = [0]

    def _save(match):
        # Token alfanumerico sem markdown para evitar colisoes.
        key = f"CODXPH{counter[0]}TOKEN"
        placeholders[key] = match.group(0)
        counter[0] += 1
        return key

    # Salva blocos markdown antes de escapar.
    texto = re.sub(r"\*\*(.+?)\*\*", _save, texto, flags=re.DOTALL)
    texto = re.sub(r"\*(.+?)\*", _save, texto, flags=re.DOTALL)
    texto = re.sub(r"_(.+?)_", _save, texto, flags=re.DOTALL)
    texto = re.sub(r"`(.+?)`", _save, texto, flags=re.DOTALL)

    # Escapa HTML bruto.
    texto = texto.replace("&", "&amp;")
    texto = texto.replace("<", "&lt;")
    texto = texto.replace(">", "&gt;")

    # Restaura placeholders para HTML final.
    for key, original in placeholders.items():
        if original.startswith("**"):
            inner = original[2:-2].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            texto = texto.replace(key, f"<b>{inner}</b>")
        elif original.startswith("*"):
            inner = original[1:-1].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            texto = texto.replace(key, f"<b>{inner}</b>")
        elif original.startswith("_"):
            inner = original[1:-1].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            texto = texto.replace(key, f"<i>{inner}</i>")
        elif original.startswith("`"):
            inner = original[1:-1].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            texto = texto.replace(key, f"<code>{inner}</code>")

    # Restaura links markdown e URLs puras para links clicaveis no Telegram.
    for key, (label, url) in link_placeholders.items():
        safe_label = (label or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_url = (url or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        texto = texto.replace(key, f'<a href="{safe_url}">{safe_label}</a>')

    for key, url in url_placeholders.items():
        safe_url = (url or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        texto = texto.replace(key, f'<a href="{safe_url}">{safe_url}</a>')

    # Nunca deixar token interno ou artefato de placeholder chegar ao usuario.
    texto = re.sub(r"CODXPH\d+TOKEN", "", texto)
    texto = re.sub(r"CODXMLINK\d+TOKEN", "", texto)
    texto = re.sub(r"CODXURL\d+TOKEN", "", texto)
    texto = _sanitize_placeholder_artifacts(texto)

    if len(texto) > 4000:
        texto = texto[:4000] + "\n\n<i>... (resposta truncada)</i>"

    return texto


def _sanitize_placeholder_artifacts(texto: str) -> str:
    if not texto:
        return texto

    patterns = [
        r"(?<!\w)_?PH\d+_?(?!\w)",
        r"(?<!\w)PLACEHOLDER\d+(?!\w)",
        r"\[\s*PH\d+_?\s*\|?",
        r"\[\s*PLACEHOLDER\d+\s*\|?",
    ]
    for pattern in patterns:
        texto = re.sub(pattern, "", texto, flags=re.IGNORECASE)

    texto = re.sub(r"[ \t]{2,}", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def formatar_historico(assistidos: list, dropados: list, progresso: list | None = None) -> str:
    """Formata historico do usuario."""
    linhas = ["<b>Seu historico:</b>"]

    if assistidos:
        linhas.append("\n<b>Assistidos:</b>")
        for item in sorted(assistidos, key=lambda x: x.get("nota", 0) or 0, reverse=True):
            nota = item.get("nota")
            nota_str = f" - {nota}/10" if nota is not None else ""
            linhas.append(f"  - {item['titulo']}{nota_str}")

    progresso = progresso or []
    if progresso:
        linhas.append("\n<b>Em progresso:</b>")
        for item in progresso[:10]:
            partes = []
            if item.get("episodio"):
                partes.append(f"ep {item['episodio']}")
            if item.get("capitulo"):
                partes.append(f"cap {item['capitulo']}")
            if item.get("porcentagem"):
                partes.append(f"{item['porcentagem']}%")
            detalhe = f" ({' | '.join(partes)})" if partes else ""
            linhas.append(f"  - {item['titulo']}{detalhe}")

    if dropados:
        linhas.append("\n<b>Dropados:</b>")
        for item in dropados:
            ep = item.get("episodio")
            ep_str = f" (parei no ep {ep})" if ep else ""
            linhas.append(f"  - {item['titulo']}{ep_str}")

    if not assistidos and not dropados and not progresso:
        linhas.append("\nNenhum registro ainda. Conta pra mim o que voce ja assistiu!")

    return "\n".join(linhas)
