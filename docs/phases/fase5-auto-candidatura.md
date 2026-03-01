# Fase 5 — Auto-Candidatura

## Objetivo
Automatizar o processo de candidatura a vagas selecionadas. O bot
pergunta confirmação, preenche formulários via Playwright (LinkedIn
Easy Apply, Gupy), e registra o status de cada candidatura no Neo4j.
Para sites sem automação viável, abre o link com o currículo já gerado.

---

## Dependências

- **Fase 3**: perfil profissional completo no Neo4j
- **Fase 4**: vagas indexadas + geração de currículo ATS

---

## Realismo — O que é possível vs. o que não é

```
POSSÍVEL com Playwright:
  ✅ LinkedIn Easy Apply (formulário padrão sem captcha)
  ✅ Gupy (plataforma brasileira, formulário simples)
  ✅ Workday jobs (muitas empresas usam)
  ✅ Greenhouse (startups internacionais)
  ✅ Lever (startups)

LIMITADO / MANUAL:
  ⚠️  LinkedIn com perguntas customizadas → bot pede resposta ao usuário
  ⚠️  Sites com reCAPTCHA v3 → detecta, para e abre link para o usuário
  ⚠️  Sites com SSO obrigatório → abre link, usuário completa

FORA DO ESCOPO:
  ❌ Sites com captcha visual agressivo
  ❌ Formulários com upload de currículo em formato proprietário
  ❌ Sites que detectam e bloqueiam automação
```

---

## Novos Arquivos

```
automation/
  browser.py            → instância Playwright gerenciada (singleton)
  linkedin_apply.py     → candidatura LinkedIn Easy Apply
  gupy_apply.py         → candidatura via Gupy
  workday_apply.py      → candidatura via Workday
  form_filler.py        → preenchedor genérico de formulários com LLM
agents/apply.py         → agente LangGraph de candidatura
prompts/apply.py        → prompts: decisão de candidatura, perguntas de formulário
```

### Arquivos modificados

```
agents/orchestrator.py    → intent "candidatura"
graph/neo4j_client.py     → tracking de candidaturas (status, histórico)
bot/handlers.py           → /candidatar, /candidaturas (histórico)
docker-compose.yml        → serviço playwright (headless chromium)
.env.example              → LINKEDIN_EMAIL, LINKEDIN_PASSWORD, PLAYWRIGHT_HEADLESS
```

---

## Fluxo Principal — Candidatura

```
Usuário: "me candidata naquela vaga da Nubank"
    ↓
agents/apply.py busca vaga no histórico Neo4j
    ↓
verifica se currículo ATS já foi gerado para essa vaga
  → não: gera automaticamente (chama Fase 4)
    ↓
BOT PERGUNTA CONFIRMAÇÃO:
"Vou me candidatar como [Cargo] na [Empresa].
 Currículo gerado e pronto. Confirma? [Sim / Não / Ver currículo]"
    ↓
usuário confirma
    ↓
detecta plataforma da URL da vaga
    ↓
┌─ LinkedIn Easy Apply → linkedin_apply.py
├─ Gupy              → gupy_apply.py
├─ Workday           → workday_apply.py
└─ Desconhecido      → envia link + currículo, usuário aplica manualmente
    ↓
registra no Neo4j: status "candidatado", data, plataforma
    ↓
resposta: "Candidatura enviada! Vou te avisar se tiver novidades."
```

---

## automation/browser.py

### Gerenciamento do Playwright

```python
class BrowserManager:
    """
    Singleton que gerencia instância do Playwright.
    Reutiliza browser entre candidaturas para performance.
    Fecha automaticamente após inatividade de 30 min.
    """

    async def get_page(self) -> Page:
        """Retorna página pronta para uso."""

    async def screenshot(self, page: Page) -> bytes:
        """Screenshot para debug e confirmação visual."""

    async def close(self):
        """Fecha browser graciosamente."""
```

### Configuração headless

```python
# headless=True em produção (Docker)
# headless=False para debug local (ver o que está acontecendo)
PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true") == "true"
```

---

## automation/linkedin_apply.py

### Fluxo LinkedIn Easy Apply

```python
async def aplicar(page: Page, vaga_url: str, perfil: dict,
                  curriculo_path: str) -> dict:
    """
    Retorna: {
        "sucesso": bool,
        "motivo_falha": str | None,
        "perguntas_customizadas": list[str] | None,
        "screenshot": bytes
    }
    """

# Passos:
# 1. Navega para URL da vaga
# 2. Clica "Easy Apply"
# 3. Detecta se há perguntas customizadas
#    → sem perguntas: preenche automaticamente
#    → com perguntas: retorna lista para o bot perguntar ao usuário
# 4. Upload do currículo PDF
# 5. Preenche telefone, email, localização do perfil
# 6. Submit
# 7. Confirma sucesso (verifica página de confirmação)
```

### Detecção de bloqueio

```python
# Se LinkedIn detectar automação:
# → bot avisa e abre link para candidatura manual
# → nunca tenta contornar detecção (viola ToS)

SINAIS_BLOQUEIO = [
    "verify you're a human",
    "unusual activity",
    "captcha",
    "security check"
]
```

---

## automation/form_filler.py

### Preenchimento inteligente com LLM

Para perguntas customizadas de formulários (ex: "Por que quer trabalhar aqui?",
"Qual sua expectativa salarial?", "Tem experiência com X?"):

```python
async def responder_pergunta(
    pergunta: str,
    perfil: dict,
    vaga: Vaga,
    modelo: str
) -> str:
    """
    LLM responde a pergunta de formulário com base no perfil do usuário.
    Respostas honestas, baseadas no que o usuário realmente tem.
    Nunca inventa qualificações.
    """
```

### Prompt de resposta a perguntas de formulário

```
Responda a pergunta do formulário de candidatura com base no perfil
do candidato. Seja honesto, específico e direto.

Pergunta: {pergunta}
Cargo desejado: {vaga.titulo}
Empresa: {vaga.empresa}
Perfil relevante: {perfil_resumido}

Regras:
- Máximo 200 palavras
- Tom profissional mas não robótico
- Nunca afirme habilidades que o candidato não tem
- Se não tiver experiência com algo, seja honesto e mostre disposição
```

---

## agents/apply.py

### Responsabilidades

1. **Validação antes de candidatar**:
   - Perfil completo o suficiente? (score > 60%)
   - Currículo gerado para esta vaga?
   - Já se candidatou antes? (evita duplicata)

2. **Orquestração da candidatura**:
   - Detecta plataforma
   - Chama automação correta
   - Lida com perguntas customizadas (pergunta ao usuário via bot)
   - Registra resultado

3. **Gerenciamento de status**:
   - Rastreia: candidatado → visualizado → entrevista → oferta → recusado
   - Notifica quando vaga muda de status (fecha, nova etapa)
   - `/candidaturas` mostra pipeline completo

### Pipeline de candidaturas (kanban via texto)

```
📋 SUAS CANDIDATURAS

EM ANDAMENTO:
  🟡 Nubank — Senior Backend Dev      (candidatado 28/02)
  🟡 iFood — Engenheiro de Dados      (candidatado 27/02)

PRÓXIMA ETAPA:
  🔵 Mercado Livre — Dev Python       (entrevista marcada 05/03)

FINALIZADAS:
  ✅ Creditas — Backend Eng           (oferta recebida!)
  ❌ Rappi — Staff Engineer           (recusado 25/02)
```

---

## Expansão Neo4j — Tracking Completo

```cypher
// Expandindo relação de candidatura
(Usuario)-[:SE_CANDIDATOU {
    data,
    plataforma,           // "linkedin" | "gupy" | "workday" | "manual"
    status,               // "candidatado" | "visualizado" | "entrevista" | "oferta" | "recusado"
    curriculo_versao,     // weaviate_id do currículo gerado
    notas,                // anotações do usuário sobre a vaga
    data_ultima_atualizacao
}]->(Vaga)

// Novas queries úteis
MATCH (u:Usuario {telegram_id: $uid})-[c:SE_CANDIDATOU]->(v:Vaga)
WHERE c.status IN ["candidatado", "visualizado", "entrevista"]
RETURN v, c ORDER BY c.data DESC
```

---

## docker-compose.yml — Novo Serviço Playwright

```yaml
playwright:
  image: mcr.microsoft.com/playwright/python:v1.44.0
  depends_on:
    - bot
  environment:
    - PLAYWRIGHT_HEADLESS=true
  volumes:
    - ./:/app
    - /tmp:/tmp
  # Playwright roda dentro do container bot via exec
  # Este serviço só provê os binários do Chromium
```

Alternativa mais simples: instalar Playwright direto no container `bot`
adicionando ao Dockerfile:

```dockerfile
RUN pip install playwright && playwright install chromium --with-deps
```

---

## Comandos e Conversação Natural

### Comandos

```
/candidatar [url_vaga]     → inicia candidatura para URL específica
/candidaturas              → pipeline completo de candidaturas
/candidaturas aberto       → só candidaturas em andamento
```

### Conversacional

```
"me candidata naquela vaga da Nubank"
→ intent: candidatura, busca vaga recente da Nubank no histórico

"quero me candidatar para vagas de python hoje"
→ intent: candidatura, busca + recomendar + perguntar qual aplicar

"como estão minhas candidaturas?"
→ intent: candidatura, mostra pipeline

"atualiza status da Nubank para entrevista"
→ atualiza Neo4j manualmente

"cancelar candidatura no iFood"
→ remove do pipeline (não cancela no site externo)
```

---

## Segurança e Ética

```
SEMPRE:
  ✅ Pedir confirmação explícita antes de qualquer candidatura
  ✅ Mostrar preview do currículo que será enviado
  ✅ Informar quando automação não é possível e abrir link manual
  ✅ Nunca inventar qualificações no currículo ou formulário
  ✅ Respeitar ToS dos sites (não contornar captcha, não fazer flood)

NUNCA:
  ❌ Candidatar sem confirmação do usuário
  ❌ Tentar contornar sistemas anti-bot (captcha, device fingerprint)
  ❌ Candidatar para a mesma vaga duas vezes
  ❌ Salvar senha do LinkedIn em texto plano (usar env var criptografada)
```

---

## Variáveis de Ambiente

```env
# Modelo para candidatura (raciocínio sobre perguntas de formulário)
MODEL_APPLY=anthropic/claude-sonnet-4-6

# Credenciais LinkedIn (para Easy Apply)
LINKEDIN_EMAIL=
LINKEDIN_PASSWORD=

# Playwright
PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_TIMEOUT_MS=30000

# Limite de candidaturas por dia (proteção contra spam)
APPLY_DAILY_LIMIT=10
```

---

## Dependências Novas

```
playwright==1.44.0    → automação de browser (Chromium)
# instalar binários: playwright install chromium --with-deps
```

---

## Ordem de Implementação Interna

```
1. graph/neo4j_client.py     → tracking completo de candidaturas
2. automation/browser.py     → singleton Playwright
3. automation/form_filler.py → LLM responde perguntas de formulário
4. automation/linkedin_apply.py
5. automation/gupy_apply.py
6. prompts/apply.py
7. agents/apply.py           → agente completo com pipeline
8. agents/orchestrator.py    → intent "candidatura"
9. bot/handlers.py           → /candidatar, /candidaturas
10. docker-compose.yml       → Playwright no Dockerfile do bot
11. .env.example             → novas variáveis
```

---

## Critérios de Conclusão

- [ ] "me candidata na vaga X" pergunta confirmação antes de agir
- [ ] LinkedIn Easy Apply funciona para vagas sem perguntas customizadas
- [ ] Perguntas customizadas são respondidas via LLM com dados reais do perfil
- [ ] Captcha detectado → bot abre link para candidatura manual
- [ ] `/candidaturas` mostra pipeline organizado por status
- [ ] Candidatura registrada no Neo4j com data, plataforma e status
- [ ] Não candidata duas vezes para mesma vaga
- [ ] APPLY_DAILY_LIMIT respeitado
- [ ] Currículo ATS correto é enviado em cada candidatura
