SYSTEM = """Voce e um agente de leitura de paginas web.
Sua tarefa e responder ao pedido do usuario usando APENAS o texto extraido das paginas fornecidas.

Regras:
- Se o usuario pedir resumo, entregue resumo claro e direto.
- Se o usuario pedir itens especificos (ex: prazos, obrigacoes, definicoes), extraia exatamente esses itens.
- Se a pagina nao tiver informacao suficiente para responder com seguranca, diga isso claramente.
- Nao invente dados e nao use conhecimento externo.
- Cite a URL da pagina usada no fim da resposta.
- Responda em portugues.
"""


def build_messages(user_message: str, history: list[dict], pages: list[dict]) -> list[dict]:
    blocos = []
    for idx, page in enumerate(pages, start=1):
        blocos.append(
            "\n".join(
                [
                    f"Pagina {idx}:",
                    f"URL: {page.get('resolved_url') or page.get('url')}",
                    f"Titulo: {page.get('title', '')}",
                    f"Conteudo extraido:",
                    page.get("text", ""),
                ]
            )
        )

    context = "\n\n".join(blocos) if blocos else "Nenhuma pagina valida foi extraida."

    messages = [{"role": "system", "content": f"{SYSTEM}\n\nContexto:\n{context}"}]
    for msg in history[-4:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})
    return messages
