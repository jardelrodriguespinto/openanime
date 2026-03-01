# Fase 4 — Vagas e Currículo ATS

## Objetivo
Buscar vagas em múltiplos portais, recomendar com base no perfil
profissional do Neo4j (Fase 3), e gerar currículos ATS otimizados
para cada vaga específica. O currículo é personalizado pelo LLM para
destacar exatamente o que aquela vaga pede.

---

## Dependências

- **Fase 2**: pdf_writer.py + templates HTML (usados para gerar PDF do currículo)
- **Fase 3**: perfil profissional completo no Neo4j

---

## Novos Arquivos

```
data/jobs.py               → scrapers/APIs de portais de emprego
utils/ats_optimizer.py     → LLM personaliza currículo para vaga específica
utils/templates/
  resume_ats.html          → template HTML ATS-friendly (uma coluna)
agents/jobs.py             → agente LangGraph de vagas
prompts/jobs.py            → prompts: busca, recomendação, currículo ATS
```

### Arquivos modificados

```
agents/orchestrator.py     → intents: "vaga" | "curriculo_ats"
graph/neo4j_client.py      → salvar/consultar vagas e candidaturas
bot/handlers.py            → /vagas, /curriculo_ats, /candidaturas
.env.example               → MODEL_JOBS (nenhuma chave paga necessária)
```

---

## data/jobs.py — Fontes de Vagas

### Estratégia de fontes — 100% gratuito, zero APIs pagas

| Fonte | Método | Custo | Cobertura |
|---|---|---|---|
| Indeed RSS | feedparser | Gratuito | Indeed BR/US |
| RemoteOK | API pública JSON | Gratuito | Remoto global |
| Gupy | API pública JSON | Gratuito | Brasil (startups/tech) |
| Catho | httpx scraping | Gratuito | Brasil |
| Infojobs | httpx scraping | Gratuito | Brasil |
| LinkedIn Jobs | httpx scraping público | Gratuito | Global (sem login) |

### Interface pública

```python
async def buscar_vagas(
    query: str,                  # "desenvolvedor python senior"
    localizacao: str = "",       # "São Paulo" | "remoto" | ""
    modalidade: str = "",        # "remoto" | "hibrido" | "presencial"
    salario_min: int = 0,
    fontes: list[str] = None,    # None = todas as fontes
    limite: int = 20
) -> list[Vaga]:
    """
    Agrega vagas de múltiplas fontes, remove duplicatas por título+empresa,
    normaliza o formato.
    """

@dataclass
class Vaga:
    id: str
    titulo: str
    empresa: str
    localizacao: str
    modalidade: str           # remoto | hibrido | presencial
    salario: str              # "R$ 8.000 - R$ 12.000" | "A combinar"
    descricao: str
    requisitos: list[str]
    url: str
    fonte: str
    data_publicacao: str
    score_match: float = 0.0  # calculado pelo agente, 0.0 a 1.0
```

### Fallback em cascata

```
Indeed RSS disponível? → usa Indeed RSS
     ↓ não
Gupy + RemoteOK → APIs públicas sempre disponíveis
     ↓ ambas fora
LinkedIn scraping + Catho/Infojobs → fallback final
```

---

## agents/jobs.py

### Responsabilidades

1. **Busca de vagas**:
   - Recebe query do usuário (ou usa perfil se não tiver query)
   - Busca em múltiplas fontes via `data/jobs.py`
   - Filtra por modalidade/salário/localização do perfil
   - Retorna lista ranqueada

2. **Recomendação personalizada**:
   - Lê perfil completo do Neo4j (Fase 3)
   - Calcula `score_match` para cada vaga:
     - Habilidades: % de match entre perfil e requisitos da vaga
     - Senioridade: alinhamento
     - Salário: dentro da faixa?
     - Modalidade: preferência respeitada?
   - LLM justifica por que cada vaga combina com o perfil

3. **Geração de currículo ATS** por vaga:
   - Recebe vaga escolhida
   - Lê perfil completo do Neo4j
   - `ats_optimizer.py` personaliza o currículo
   - `pdf_writer.py` gera o PDF final
   - Bot envia arquivo

4. **Salvar vagas favoritas**:
   - Usuário pode salvar vagas de interesse no Neo4j
   - Bot monitora mudanças de status (vaga fechada, nova similar)

### Cálculo de score_match

```python
def calcular_score(perfil: dict, vaga: Vaga) -> float:
    score = 0.0

    # habilidades (peso 40%)
    skills_perfil = {h["nome"].lower() for h in perfil["habilidades"]}
    skills_vaga = {r.lower() for r in vaga.requisitos}
    if skills_vaga:
        match = len(skills_perfil & skills_vaga) / len(skills_vaga)
        score += match * 0.40

    # senioridade (peso 25%)
    if perfil["nivel_senioridade"] in vaga.titulo.lower():
        score += 0.25

    # modalidade (peso 20%)
    if perfil["modalidade_preferida"] == vaga.modalidade:
        score += 0.20
    elif vaga.modalidade == "hibrido":
        score += 0.10

    # salário (peso 15%)
    # parse da faixa salarial e comparação com pretensão
    score += calcular_score_salario(perfil, vaga) * 0.15

    return round(score, 2)
```

---

## utils/ats_optimizer.py

### O que é ATS-friendly?

ATS (Applicant Tracking System) são sistemas que as empresas usam para
filtrar currículos antes do RH ver. Regras para passar no ATS:

```
✅ Layout de uma coluna (sem colunas paralelas)
✅ Fontes padrão: Arial, Calibri, Times New Roman
✅ Seções com títulos padrão em texto (não imagem)
✅ Keywords da descrição da vaga no currículo
✅ Texto selecionável (não imagem/scan)
✅ Sem tabelas complexas, sem ícones como texto
✅ Formato PDF com texto embutido
✅ Datas em formato consistente (MM/AAAA)

❌ Colunas laterais com skills como bolinhas
❌ Foto (não processada por ATS)
❌ Cabeçalho/rodapé com informações críticas
❌ Tabelas para layout visual
❌ Fontes decorativas
```

### Interface pública

```python
async def otimizar_para_vaga(
    perfil: dict,      # perfil completo do Neo4j
    vaga: Vaga,        # vaga target
    modelo: str        # MODEL_JOBS
) -> dict:
    """
    Usa LLM para:
    1. Identificar keywords da descrição da vaga
    2. Selecionar experiências mais relevantes do perfil
    3. Reescrever bullets de experiência com keywords da vaga
    4. Ordenar habilidades por relevância para a vaga
    5. Ajustar objetivo profissional para a vaga

    Retorna dict com conteúdo pronto para o template HTML.
    """
```

### Prompt de otimização ATS

```
Você é um especialista em currículos ATS. Dado o perfil do candidato
e a descrição da vaga, personalize o currículo:

PERFIL:
{perfil_json}

VAGA:
Título: {vaga.titulo}
Empresa: {vaga.empresa}
Descrição: {vaga.descricao}
Requisitos: {vaga.requisitos}

TAREFA:
1. Extraia as 10-15 keywords mais importantes da vaga
2. Selecione as 3 experiências mais relevantes do perfil
3. Para cada experiência, escreva 3-4 bullets usando as keywords da vaga
   (use verbos de ação + resultado quantificado quando possível)
4. Liste as habilidades ordenadas por relevância para esta vaga
5. Escreva um objetivo profissional de 2 linhas específico para esta vaga

REGRAS:
- Nunca invente experiências ou habilidades que o candidato não tem
- Use as keywords da vaga naturalmente (não stuffing)
- Bullets no formato: "Verbo + o que fez + resultado/impacto"
- Seja específico, não genérico ("aumentei performance em 40%" > "melhorei sistemas")

Retorne JSON com: objetivo, experiencias, habilidades, keywords_usadas
```

---

## utils/templates/resume_ats.html

### Estrutura do template (ATS-friendly)

```html
<!-- Uma coluna, sem tabelas, fontes padrão, hierarquia clara -->

[NOME COMPLETO]
[email] | [telefone] | [linkedin] | [localização]

OBJETIVO PROFISSIONAL
[texto personalizado para a vaga]

HABILIDADES
[lista simples, sem bolinhas gráficas]

EXPERIÊNCIA PROFISSIONAL
[Empresa] — [Cargo]                          [MM/AAAA – MM/AAAA]
• [bullet 1 com keyword + resultado]
• [bullet 2 com keyword + resultado]
• [bullet 3 com keyword + resultado]

FORMAÇÃO
[Curso] — [Instituição]                      [AAAA]

IDIOMAS
[Idioma]: [Nível]
```

---

## Expansão Neo4j — Vagas e Candidaturas

```cypher
(:Vaga {
    id,
    titulo,
    empresa,
    url,
    fonte,
    salario,
    modalidade,
    data_publicacao,
    descricao,
    status         // "aberta" | "fechada" | "expirada"
})

// relações
(Usuario)-[:FAVORITOU {data}]->(Vaga)
(Usuario)-[:VISUALIZOU {data}]->(Vaga)
(Usuario)-[:GEROU_CURRICULO_PARA {data}]->(Vaga)
```

---

## Comandos e Conversação Natural

### Comandos

```
/vagas                         → vagas baseadas no perfil completo
/vagas python remoto           → busca específica
/curriculo_ats [url_da_vaga]   → gera currículo para vaga
/candidaturas                  → histórico de vagas salvas/geradas
```

### Conversacional

```
"tem vaga de dev python remoto hoje?"
→ intent: vaga, query: "dev python remoto"

"me recomenda vagas para o meu perfil"
→ intent: vaga, usa perfil Neo4j completo

"gera meu currículo para aquela vaga da Nubank"
→ intent: curriculo_ats, busca vaga recente no histórico

"qual vaga combina mais comigo?"
→ intent: vaga, busca top match por score

"salva essa vaga para mim"
→ favorita vaga no Neo4j
```

---

## Variáveis de Ambiente

```env
# Modelo para vagas (justificativas personalizadas — modelo bom)
MODEL_JOBS=anthropic/claude-sonnet-4-6

# Nenhuma chave paga necessária — todas as fontes são gratuitas

# Limite de vagas por busca
JOBS_LIMITE_POR_FONTE=10
JOBS_LIMITE_TOTAL=30
```

---

## Dependências Novas

Nenhuma além das já instaladas nas fases anteriores.
- feedparser (Fase 1) → lê Indeed RSS
- httpx (já existe) → Gupy API, RemoteOK API, scrapers (LinkedIn, Catho, Infojobs)
- weasyprint (Fase 2) → gera PDF do currículo

---

## Ordem de Implementação Interna

```
1. data/jobs.py              → Indeed RSS + RemoteOK + Gupy + LinkedIn scraping + Catho + Infojobs
2. utils/templates/resume_ats.html  → template HTML ATS-friendly
3. utils/ats_optimizer.py    → personalização via LLM
4. prompts/jobs.py           → prompts: busca, recomendação, ATS
5. agents/jobs.py            → agente completo
6. graph/neo4j_client.py     → métodos de vagas e candidaturas
7. agents/orchestrator.py    → intents: vaga, curriculo_ats
8. bot/handlers.py           → /vagas, /curriculo_ats, /candidaturas
9. .env.example              → novas variáveis
```

---

## Critérios de Conclusão

- [ ] `/vagas python remoto` retorna lista ranqueada com score_match
- [ ] "tem vaga pra mim?" usa perfil Neo4j e retorna top 5 com justificativa
- [ ] `/curriculo_ats` gera PDF com currículo personalizado para a vaga
- [ ] Currículo gerado tem texto selecionável (não imagem)
- [ ] Keywords da vaga aparecem naturalmente no currículo gerado
- [ ] Vaga favoritada aparece em `/candidaturas`
- [ ] Fallback em cascata funciona: Indeed RSS → Gupy/RemoteOK → scraping
- [ ] Score_match calculado corretamente (habilidades + senioridade + modalidade)
