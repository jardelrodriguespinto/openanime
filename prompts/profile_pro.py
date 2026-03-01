SYSTEM_EXTRACAO = """Analise a conversa e extraia TODOS os dados profissionais do usuario.
O usuario pode estar pedindo para salvar infos de um curriculo ou analise anterior na conversa.
Leia o historico completo da conversa para encontrar os dados.

Retorne APENAS JSON valido com todos os campos encontrados (deixe vazio se nao houver):
{
  "nome": "",
  "email": "",
  "telefone": "",
  "linkedin": "",
  "github": "",
  "localizacao": "",
  "cargo_atual": "",
  "nivel_senioridade": "",
  "pretensao_salarial": "",
  "modalidade_preferida": "",
  "cargos_desejados": [],
  "setores_interesse": [],
  "idiomas": [],
  "habilidades": [{"nome": "Python", "nivel": 4, "anos_exp": 3}],
  "experiencias": [{"cargo": "", "empresa": "", "inicio": "", "fim": "atual", "descricao": ""}],
  "formacao": [{"curso": "", "instituicao": "", "nivel": "graduacao", "ano": "2027"}],
  "idiomas": [{"idioma": "Inglês", "nivel": "fluente"}]
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


def build_extracao_messages(mensagem: str, history: list[dict] | None = None) -> list[dict]:
    messages = [{"role": "system", "content": SYSTEM_EXTRACAO}]
    # Inclui últimas 6 mensagens do histórico para capturar contexto de CV anterior
    for msg in (history or [])[-6:]:
        messages.append(msg)
    messages.append({"role": "user", "content": mensagem})
    return messages


def build_perfil_messages(perfil: dict, mensagem: str) -> list[dict]:
    import json
    perfil_str = json.dumps(perfil, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": SYSTEM_PERFIL},
        {"role": "user", "content": f"Perfil atual:\n{perfil_str}\n\nMensagem: {mensagem}"},
    ]
