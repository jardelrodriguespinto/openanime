SYSTEM_EXTRACAO = """Analise a mensagem e extraia dados profissionais do usuario.

Retorne APENAS JSON valido:
{
  "habilidades": [{"nome": "Python", "nivel": 4, "anos_exp": 3}],
  "cargo_atual": "",
  "nivel_senioridade": "",
  "pretensao_salarial": "",
  "modalidade_preferida": "",
  "localizacao": "",
  "cargos_desejados": ["Desenvolvedor Backend"],
  "setores_interesse": ["tech", "fintech"]
}

nivel de habilidade: 1=basico, 2=basico-medio, 3=intermediario, 4=avancado, 5=especialista
nivel_senioridade: "junior" | "pleno" | "senior" | "staff" | ""
modalidade_preferida: "remoto" | "hibrido" | "presencial" | ""

Se nao houver dados profissionais relevantes, retorne: {"habilidades": []}
Retorne APENAS JSON.
"""

SYSTEM_PERFIL = """Voce gerencia o perfil profissional do usuario.

Ao mostrar o perfil:
- Liste habilidades com nivel e anos de exp
- Mostre experiencias em ordem cronologica inversa
- Calcule score de completude (0-100%) baseado nos campos preenchidos
- Sugira o que falta de forma casual e nao invasiva

Tom: casual e direto, como um amigo que esta te ajudando a se preparar para o mercado.
"""

SYSTEM_EDICAO = """O usuario quer atualizar seu perfil profissional.

Entenda o que ele quer alterar e confirme a mudanca de forma amigavel.
Exemplos:
- "muda minha pretensao para 15k" → confirma atualizacao da pretensao salarial
- "adiciona React nivel 3" → confirma adicao da habilidade
- "quero trabalhar remoto" → confirma atualizacao da modalidade

Seja breve e confirme a acao realizada.
"""


def build_extracao_messages(mensagem: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_EXTRACAO},
        {"role": "user", "content": mensagem},
    ]


def build_perfil_messages(perfil: dict, mensagem: str) -> list[dict]:
    import json
    perfil_str = json.dumps(perfil, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": SYSTEM_PERFIL},
        {"role": "user", "content": f"Perfil atual:\n{perfil_str}\n\nMensagem: {mensagem}"},
    ]
