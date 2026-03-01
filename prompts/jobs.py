SYSTEM_RECOMENDACAO = """Voce e um assistente especializado em recomendacao de vagas de emprego.

Dado o perfil do usuario e uma lista de vagas encontradas, selecione as mais relevantes
e justifique cada recomendacao de forma personalizada.

Tom: casual e direto, como um amigo que conhece bem o mercado.

REGRA CRITICA DE SENIORIDADE:
- Se o usuario pediu um nivel especifico (ex: "pleno", "junior", "senior"), NUNCA recomende
  vagas de outro nivel. Se a lista so tiver vagas de outro nivel, avise honestamente e
  explique que nao encontrou vagas do nivel solicitado nessa busca.
- Vagas sem nivel no titulo podem ser recomendadas independente do nivel pedido.

Para cada vaga recomendada inclua:
- Titulo e empresa
- Por que combina com o perfil (especifico, nao generico)
- Match de habilidades encontradas
- Salario se disponivel
- Link

Maximo 5 vagas. Se nenhuma combinar bem, seja honesto e explique o que falta.
"""

SYSTEM_BUSCA = """Sintetize os resultados de busca de vagas de forma clara e util.

Para cada vaga:
- Titulo - Empresa
- Localidade/Modalidade
- Salario (se disponivel)
- Link

Seja direto. Se encontrou poucas vagas, sugira refinar a busca.
"""


def build_recomendacao_messages(perfil: dict, vagas: list, mensagem: str = "", senioridade_filtro: str = "") -> list[dict]:
    import json
    perfil_resumido = {
        "nivel_senioridade": senioridade_filtro or perfil.get("nivel_senioridade", ""),
        "habilidades": [h.get("nome", "") for h in perfil.get("habilidades", [])[:10]],
        "modalidade_preferida": perfil.get("modalidade_preferida", ""),
        "pretensao_salarial": perfil.get("pretensao_salarial", ""),
        "cargos_desejados": perfil.get("cargos_desejados", []),
    }

    vagas_texto = []
    for i, v in enumerate(vagas[:15], 1):
        linha = [f"{i}. {v.titulo} — {v.empresa}"]
        if v.localizacao:
            linha.append(f"   Local: {v.localizacao} ({v.modalidade or 'presencial'})")
        if v.salario and v.salario != "A combinar":
            linha.append(f"   Salario: {v.salario}")
        if v.requisitos:
            linha.append(f"   Requisitos: {', '.join(v.requisitos[:8])}")
        if v.url:
            linha.append(f"   Link: {v.url}")
        vagas_texto.append("\n".join(linha))

    conteudo = f"Perfil:\n{json.dumps(perfil_resumido, ensure_ascii=False)}\n\nVagas encontradas:\n\n" + "\n\n".join(vagas_texto)
    if senioridade_filtro:
        conteudo = f"NIVEL SOLICITADO PELO USUARIO: {senioridade_filtro.upper()} — recomende apenas vagas desse nivel ou sem nivel indicado no titulo.\n\n{conteudo}"
    if mensagem:
        conteudo = f"Mensagem do usuario: {mensagem}\n\n{conteudo}"

    return [
        {"role": "system", "content": SYSTEM_RECOMENDACAO},
        {"role": "user", "content": conteudo},
    ]


def build_busca_messages(vagas: list, query: str) -> list[dict]:
    vagas_texto = []
    for i, v in enumerate(vagas[:20], 1):
        linha = [f"{i}. {v.titulo} — {v.empresa}"]
        if v.localizacao:
            linha.append(f"   {v.localizacao} | {v.modalidade or '?'}")
        if v.salario and v.salario != "A combinar":
            linha.append(f"   {v.salario}")
        if v.url:
            linha.append(f"   {v.url}")
        vagas_texto.append("\n".join(linha))

    conteudo = f"Busca: {query}\n\nResultados:\n\n" + "\n\n".join(vagas_texto)
    return [
        {"role": "system", "content": SYSTEM_BUSCA},
        {"role": "user", "content": conteudo},
    ]
