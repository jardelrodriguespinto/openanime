SYSTEM_CONFIRMACAO = """O usuario quer se candidatar a uma vaga. Apresente um resumo claro para confirmacao.

Mostre:
- Titulo e empresa da vaga
- Curriculo que sera usado (versao ATS ou padrao)
- Plataforma de candidatura detectada
- O que sera preenchido automaticamente

Tom: direto, profissional. Aguarda confirmacao do usuario antes de prosseguir.
"""

SYSTEM_PERGUNTAS = """O usuario precisa responder perguntas customizadas de um formulario de candidatura.

Apresente cada pergunta de forma clara e sugira uma resposta baseada no perfil do usuario.
O usuario pode aprovar a sugestao ou editar antes de enviar.

Seja honesto — nunca sugira respostas que o candidato nao pode comprovar.
"""


def build_confirmacao_messages(vaga: dict, perfil: dict, plataforma: str) -> list[dict]:
    import json
    conteudo = f"""Vaga: {vaga.get('titulo', '?')} — {vaga.get('empresa', '?')}
URL: {vaga.get('url', '?')}
Plataforma: {plataforma}

Candidato: {perfil.get('nome', '?')}
Nivel: {perfil.get('nivel_senioridade', '?')}
Habilidades principais: {', '.join(h.get('nome', '') for h in perfil.get('habilidades', [])[:5])}"""

    return [
        {"role": "system", "content": SYSTEM_CONFIRMACAO},
        {"role": "user", "content": conteudo},
    ]


def build_perguntas_messages(perguntas: list[str], perfil: dict, vaga: dict) -> list[dict]:
    import json
    perfil_resumido = {
        "habilidades": [h.get("nome", "") for h in perfil.get("habilidades", [])[:8]],
        "nivel": perfil.get("nivel_senioridade", ""),
        "pretensao": perfil.get("pretensao_salarial", ""),
        "modalidade": perfil.get("modalidade_preferida", ""),
        "experiencias": [f"{e.get('cargo', '')} na {e.get('empresa', '')}" for e in perfil.get("experiencias", [])[:2]],
    }

    conteudo = f"""Vaga: {vaga.get('titulo', '?')} — {vaga.get('empresa', '?')}
Perfil: {json.dumps(perfil_resumido, ensure_ascii=False)}

Perguntas do formulario:
{chr(10).join(f'{i+1}. {p}' for i, p in enumerate(perguntas))}

Sugira respostas honestas para cada pergunta."""

    return [
        {"role": "system", "content": SYSTEM_PERGUNTAS},
        {"role": "user", "content": conteudo},
    ]
