SYSTEM = """Voce e um assistente que sintetiza noticias de forma casual e direta.

Dado um conjunto de noticias brutas, selecione as mais relevantes e apresente de forma amigavel.

Regras:
- Tom casual, como um amigo compartilhando novidades
- Maximo 5 noticias por resposta
- Para cada noticia: titulo curto + 1 frase de contexto + link
- Se a noticia for muito tecnica, simplifica sem perder substancia
- Destaca se algo e urgente, surpreendente ou muito relevante
- Nao invente informacoes — use apenas o que veio das fontes
- Inclua o link sempre que disponivel
"""


def build_messages(noticias: list[dict], categorias: list[str], query: str = "") -> list[dict]:
    """Monta mensagens para o agente de noticias."""
    if not noticias:
        conteudo = "Nenhuma noticia encontrada no momento."
    else:
        linhas = [f"Categoria(s): {', '.join(categorias)}\n"]
        for i, n in enumerate(noticias[:10], 1):
            linhas.append(f"{i}. {n.get('titulo', 'Sem titulo')}")
            if n.get("resumo"):
                linhas.append(f"   Resumo: {n['resumo'][:200]}")
            if n.get("url"):
                linhas.append(f"   Link: {n['url']}")
            if n.get("data"):
                linhas.append(f"   Data: {n['data']}")
            linhas.append("")
        conteudo = "\n".join(linhas)

    user_content = f"Noticias coletadas:\n\n{conteudo}"
    if query:
        user_content = f"Query do usuario: {query}\n\n{user_content}"

    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_content},
    ]
