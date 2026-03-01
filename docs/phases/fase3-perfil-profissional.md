# Fase 3 — Perfil Profissional

## Objetivo
Criar o perfil profissional do usuário no Neo4j a partir de conversa
natural e/ou PDF de currículo enviado. Este perfil é a base para
recomendação de vagas (Fase 4) e geração de currículo ATS (Fase 4).
O usuário nunca precisa preencher formulário — o bot aprende conversando.

---

## Dependência

Esta fase depende da **Fase 2** estar concluída, pois o currículo PDF
já alimenta parte do perfil automaticamente.

---

## Novos Arquivos

```
agents/profile_pro.py     → agente de perfil profissional (separado do profile.py de anime)
prompts/profile_pro.py    → prompts para extração e gestão de perfil profissional
```

### Arquivos modificados

```
agents/orchestrator.py    → adicionar intent "perfil_pro"
graph/neo4j_client.py     → métodos para perfil profissional
agents/extrator.py        → detectar dados profissionais em qualquer mensagem
bot/handlers.py           → /curriculo (mostra perfil atual)
.env.example              → MODEL_PROFILE_PRO (pode reusar MODEL_PROFILE)
```

---

## Schema Neo4j — Expansão Completa

### Novos nós

```cypher
(:Habilidade {nome, categoria})
// categoria: "linguagem" | "framework" | "ferramenta" | "soft skill" | "idioma"

(:Cargo {titulo})
// ex: "Desenvolvedor Python", "Product Manager", "Engenheiro de Dados"

(:Empresa {nome, setor})
// setor: "tech" | "financeiro" | "saúde" | "educação" | etc.

(:Formacao {curso, instituicao, nivel, ano_conclusao})
// nivel: "tecnico" | "graduacao" | "pos" | "mba" | "bootcamp" | "certificacao"
```

### Novas relações no nó Usuario

```cypher
(Usuario)-[:TEM_HABILIDADE {nivel, anos_exp}]->(Habilidade)
// nivel: 1-5 (1=básico, 5=especialista)

(Usuario)-[:TRABALHOU_EM {cargo, data_inicio, data_fim, descricao}]->(Empresa)

(Usuario)-[:CURSOU]->(Formacao)

(Usuario)-[:QUER_CARGO]->(Cargo)
// cargos que o usuário quer ocupar

(Usuario)-[:PREFERE_SETOR]->(Empresa)
// setores de interesse para próximo emprego

// campos diretos no nó Usuario
SET u.pretensao_salarial = "R$ 8.000 - R$ 12.000"
SET u.modalidade_preferida = "remoto"    // remoto | hibrido | presencial
SET u.localizacao = "São Paulo, SP"
SET u.disponibilidade = "imediata"       // imediata | 30 dias | 60 dias
SET u.nivel_senioridade = "senior"       // junior | pleno | senior | staff
SET u.linkedin_url = "..."
SET u.github_url = "..."
SET u.portfolio_url = "..."
```

---

## agents/profile_pro.py

### Responsabilidades

1. **Extração de PDF de currículo** (integração com Fase 2):
   - Quando Fase 2 detecta tipo "CURRÍCULO", transfere dados para cá
   - Popula Neo4j com todas as informações extraídas
   - Confirma com usuário o que foi extraído

2. **Aprendizado conversacional**:
   - Extrator background detecta menções profissionais em qualquer mensagem
   - "Trabalho com Python há 3 anos" → habilidade Python, nível 3, 3 anos
   - "Quero trabalhar remoto" → modalidade_preferida = remoto
   - "Minha pretensão é 10k" → pretensao_salarial = R$ 10.000

3. **Exibição do perfil** (`/curriculo`):
   - Mostra perfil profissional atual completo
   - Indica o que está faltando para melhorar recomendações
   - Permite editar por conversa ("muda minha pretensão para 12k")

4. **Completude do perfil**:
   - Calcula score de completude (0-100%)
   - Quanto mais completo, mais precisas são as recomendações de vagas
   - Bot sugere proativamente preencher campos faltantes

### Score de completude

```
campo                  peso
──────────────────────────────
habilidades (3+)       20%
experiência (1+)       20%
formação               10%
cargo desejado         15%
pretensão salarial     10%
modalidade             10%
localização            10%
senioridade            5%
──────────────────────────────
total                  100%
```

---

## agents/extrator.py — Expansão

O extrator já roda em background para detectar animes/mangás. Expandir
para detectar dados profissionais em qualquer mensagem:

### Padrões a detectar

```python
PADROES_PROFISSIONAIS = [
    # habilidades técnicas
    r"(trabalho|uso|conheço|sei|domino|aprendi)\s+(bem\s+)?(\w+)",
    # anos de experiência
    r"(\d+)\s+anos?\s+(de\s+)?(experiência\s+com|trabalhando com|usando)\s+(\w+)",
    # pretensão
    r"(pretensão|salário|ganho|quero ganhar)[^\d]*R?\$?\s*([\d.,]+[kK]?)",
    # modalidade
    r"(quero|prefiro|trabalho|busco)\s+(trabalhar\s+)?(remoto|hibrido|presencial)",
    # senioridade
    r"sou\s+(junior|pleno|sênior|senior|staff|lead)",
    # cargo
    r"(trabalho como|sou|atuo como)\s+([A-Z][a-z]+(\s+[A-Z][a-z]+)*)",
]
```

---

## prompts/profile_pro.py

### Prompt de extração de currículo

```
Você recebeu um currículo em texto. Extraia as seguintes informações
em formato JSON estruturado:

{
  "nome": "",
  "email": "",
  "telefone": "",
  "linkedin": "",
  "github": "",
  "cargo_atual": "",
  "nivel_senioridade": "",   // junior|pleno|senior|staff
  "habilidades": [{"nome": "", "nivel": 1-5, "anos_exp": 0}],
  "experiencias": [{"empresa": "", "cargo": "", "inicio": "", "fim": "", "descricao": ""}],
  "formacao": [{"curso": "", "instituicao": "", "nivel": "", "ano": ""}],
  "idiomas": [{"idioma": "", "nivel": ""}],
  "localizacao": "",
  "pretensao_salarial": "",
  "modalidade_preferida": ""
}

Se algum campo não estiver no currículo, deixe como null.
Não invente informações.
```

### Prompt de conversa sobre perfil

```
Você é um assistente que ajuda o usuário a manter seu perfil
profissional atualizado. Quando o usuário mencionar informações
sobre sua carreira, extraia e confirme os dados.

Tom casual: "Anotei que você tem 3 anos com Python. Quer adicionar
o nível de experiência? (básico, intermediário, avançado, especialista)"

Mostre o perfil de forma clara quando solicitado. Sugira campos
faltantes de forma não invasiva.
```

---

## Comandos e Conversação Natural

### Comandos

```
/perfil_pro              → mostra perfil profissional completo
/curriculo               → alias de /perfil_pro
```

### Conversacional

```
"atualiza minha pretensão para 15k"          → atualiza no Neo4j
"adiciona React no meu perfil, nível 3"      → upsert habilidade
"quero trabalhar remoto só"                  → atualiza modalidade
"o que falta no meu perfil?"                 → score de completude
"me mostra meu perfil profissional"          → exibe tudo
"remove a experiência da empresa X"          → deleta relação Neo4j
```

---

## Integração com Fase 2

Quando `agents/documents.py` detecta tipo `CURRÍCULO`:

```python
# dentro de agents/documents.py
if tipo_documento == "CURRÍCULO":
    dados_extraidos = await extrair_curriculo_llm(texto)
    await neo4j.salvar_perfil_profissional(user_id, dados_extraidos)
    # transfere contexto para profile_pro
    state["intent"] = "perfil_pro"
    state["dados_curriculo"] = dados_extraidos
```

---

## neo4j_client.py — Novos Métodos

```python
async def salvar_perfil_profissional(user_id: str, dados: dict) -> None:
    """Salva perfil completo extraído de currículo."""

async def upsert_habilidade(user_id: str, habilidade: str,
                             nivel: int, anos_exp: int) -> None:

async def upsert_experiencia(user_id: str, empresa: str,
                              cargo: str, periodo: dict) -> None:

async def salvar_preferencias_emprego(user_id: str, prefs: dict) -> None:
    """Salva: modalidade, pretensão, localização, senioridade."""

async def get_perfil_profissional(user_id: str) -> dict:
    """Retorna perfil completo para usar na Fase 4."""

async def get_score_completude(user_id: str) -> float:
    """Calcula score de completude 0.0 a 1.0."""
```

---

## Variáveis de Ambiente

```env
# Reutiliza MODEL_PROFILE (mesmo nível de complexidade)
# MODEL_PROFILE=meta-llama/llama-3-8b-instruct
# Nenhuma variável nova necessária
```

---

## Dependências Novas

Nenhuma. Tudo já disponível com pydantic (já instalado) para validação
dos dados extraídos.

---

## Ordem de Implementação Interna

```
1. graph/neo4j_client.py     → novos métodos de perfil profissional
2. prompts/profile_pro.py    → prompts de extração e conversa
3. agents/profile_pro.py     → agente completo
4. agents/extrator.py        → expandir detecção de dados profissionais
5. agents/orchestrator.py    → intent "perfil_pro"
6. agents/documents.py       → integração: currículo PDF → perfil_pro
7. bot/handlers.py           → /perfil_pro, /curriculo
```

---

## Critérios de Conclusão

- [ ] Enviar currículo PDF → perfil populado no Neo4j automaticamente
- [ ] "Tenho 3 anos de Python" → habilidade salva sem comando
- [ ] `/perfil_pro` exibe perfil completo com score de completude
- [ ] "Atualiza minha pretensão para 12k" funciona conversacionalmente
- [ ] Perfil com 80%+ de completude retorna recomendações de vagas precisas (Fase 4)
- [ ] Score de completude calculado corretamente
- [ ] Extrator detecta dados profissionais em conversas normais
