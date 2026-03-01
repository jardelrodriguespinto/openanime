# CLAUDE.md — Anime Multi-Assistant

## Visão Geral

Assistente pessoal de anime e mangá no Telegram.
Multi-agente com orquestrador LangGraph que roteia
para agentes especializados conforme a intenção
do usuário. Aprende o gosto do usuário ao longo
do tempo via grafo no Neo4j.

---

## Stack

| Camada | Tecnologia | Função |
|---|---|---|
| Runtime | Python 3.12 | linguagem principal |
| Orquestração | LangGraph | multi-agente com estado |
| IA | OpenRouter API | todos os modelos em uma API |
| Grafo | Neo4j | perfil do usuário e relações entre obras |
| Semântico | Weaviate | busca por atmosfera e sensação |
| Dados | Jikan API | catálogo anime e mangá (gratuita) |
| Dados | Reddit API | discussões reais da comunidade |
| Busca | DuckDuckGo API | notícias e lançamentos em tempo real |
| Interface | python-telegram-bot | interface com o usuário |
| Infra | Docker Compose | todos os serviços containerizados |

---

## Arquitetura Multi-Agente (5 sub-agentes)

```
Telegram
    ↓
ORQUESTRADOR (LangGraph)
detecta intenção: conversa | recomendacao | analise | busca | perfil
    ↓
┌─────────────────────────────────────────┐
│                                         │
│  AGENTE DE CONVERSA                     │
│  lore, personagens, história, opinião   │
│  usa Neo4j (perfil) + Weaviate          │
│                                         │
│  AGENTE DE RECOMENDAÇÃO                 │
│  cruza Neo4j + Weaviate (gêneros boost) │
│  + Jikan + Reddit                       │
│  recomenda com justificativa pessoal    │
│                                         │
│  AGENTE DE ANÁLISE          ← novo      │
│  review profundo de obra específica     │
│  usa Jikan + Weaviate + Reddit          │
│  análise de personagens, temas, notas   │
│                                         │
│  AGENTE DE BUSCA                        │
│  notícias e lançamentos em tempo real   │
│  sites para ler/assistir sem restrição  │
│  usa DDG Lite (httpx) + Reddit          │
│                                         │
│  AGENTE DE PERFIL                       │
│  registra animes/mangás/manhwas         │
│  registra drops com episódio            │
│  mostra histórico e padrões de gosto    │
│                                         │
└─────────────────────────────────────────┘
    ↓
EXTRATOR (background)
extrai dados de perfil de toda mensagem
salva no Neo4j + enriquece com Jikan
    ↓
resposta unificada no Telegram
```

---

## Modelos OpenRouter — Totalmente Configurável

Nenhum modelo é hardcoded no código.
Tudo via variáveis de ambiente no .env.
Troque qualquer modelo sem tocar em nenhum arquivo Python.

### Variáveis de modelo no .env

```env
# ORQUESTRADOR
# classifica intenção — use modelo barato e rápido
# sugestão: llama-3-8b, mistral-7b, gemma-2-9b
MODEL_ORCHESTRATOR=meta-llama/llama-3-8b-instruct

# CONVERSA E RECOMENDAÇÃO
# raciocínio profundo — use o melhor que quiser pagar
# sugestão: claude-sonnet, gpt-4o, llama-3-70b, gemini-pro
MODEL_CHAT=anthropic/claude-sonnet-4-6

# BUSCA E SÍNTESE
# síntese rápida de resultados — modelo médio resolve
# sugestão: llama-3-70b, mistral-large, gpt-4o-mini
MODEL_SEARCH=meta-llama/llama-3-70b-instruct

# AGENTE DE PERFIL
# registra dados estruturados — modelo leve resolve
# sugestão: llama-3-8b, gemma-2-9b, mistral-7b
MODEL_PROFILE=meta-llama/llama-3-8b-instruct
```

### Referência de modelos disponíveis no OpenRouter

```
BARATOS (orquestrador, perfil, tarefas simples)
  meta-llama/llama-3-8b-instruct
  google/gemma-2-9b-it
  mistralai/mistral-7b-instruct

MÉDIOS (busca, síntese, tarefas moderadas)
  meta-llama/llama-3-70b-instruct
  mistralai/mistral-large
  openai/gpt-4o-mini
  google/gemini-flash-1.5

PREMIUM (conversa, recomendação, raciocínio)
  anthropic/claude-sonnet-4-6
  anthropic/claude-opus-4-6
  openai/gpt-4o
  google/gemini-pro-1.5
```

### Como trocar modelo sem mexer no código

```bash
# editar .env
MODEL_CHAT=openai/gpt-4o

# reiniciar apenas o bot
docker compose restart bot

# pronto — nenhuma linha de código alterada
```

---

## ai/config.py — Único Ponto de Leitura dos Modelos

```python
# ai/config.py
# todos os agentes importam os modelos daqui
# nunca usar string de modelo direto nos agentes

import os
from dataclasses import dataclass

@dataclass
class ModelConfig:
    orchestrator: str
    chat:         str
    search:       str
    profile:      str

def load_model_config() -> ModelConfig:
    return ModelConfig(
        orchestrator = os.getenv("MODEL_ORCHESTRATOR", "meta-llama/llama-3-8b-instruct"),
        chat         = os.getenv("MODEL_CHAT",         "anthropic/claude-sonnet-4-6"),
        search       = os.getenv("MODEL_SEARCH",       "meta-llama/llama-3-70b-instruct"),
        profile      = os.getenv("MODEL_PROFILE",      "meta-llama/llama-3-8b-instruct"),
    )

# instância global — importar em todos os agentes
models = load_model_config()
```

---

## Estrutura de Pastas

```
anime-assistant/
├── docker-compose.yml
├── .env                             ← chaves e modelos configurados aqui
├── .env.example                     ← template com todas as opções
├── CLAUDE.md                        ← este arquivo
│
├── bot/
│   ├── main.py                      → inicia o bot + registra jobs (JobQueue)
│   ├── handlers.py                  → recebe mensagens + comandos
│   ├── notificador.py               → digest diário automático às 8h
│   └── formatter.py                 → formata respostas (escape HTML + markdown→HTML)
│
├── agents/
│   ├── orchestrator.py              → LangGraph — 5 sub-agentes + extrator background
│   ├── conversation.py              → conversa: Neo4j perfil + Weaviate
│   ├── recommendation.py            → recomendação: Neo4j + Weaviate (gêneros) + Jikan + Reddit
│   ├── analysis.py                  → análise/review: Jikan + Weaviate + Reddit
│   ├── search.py                    → busca: DDG Lite (httpx) + Reddit + LLM
│   ├── profile.py                   → perfil: Neo4j read/write
│   ├── extrator.py                  → extração background + enriquece Jikan→Neo4j+Weaviate
│   └── responder.py                 → nó final do grafo
│
├── graph/
│   ├── neo4j_client.py              → conexão e queries Neo4j
│   ├── weaviate_client.py           → conexão e busca semântica (text2vec-openai)
│   └── graphrag.py                  → extração de temas/sentimento via LLM
│
├── data/
│   ├── jikan.py                     → coleta Jikan API
│   ├── reddit.py                    → coleta Reddit (normaliza query PT→EN)
│   └── scheduler.py                 → coleta semanal automática
│
├── ai/
│   ├── openrouter.py                → cliente OpenRouter unificado
│   └── config.py                    → lê modelos do .env (único ponto)
│
└── prompts/
    ├── orchestrator.py              → prompt do orquestrador
    ├── conversation.py              → prompt conversa (com perfil do usuário)
    ├── recommendation.py            → prompt recomendação
    ├── analysis.py                  → prompt análise/review
    ├── search.py                    → prompt busca (SYSTEM_SITES para links)
    ├── profile.py                   → prompt perfil
    └── notificador.py               → prompt digest diário
```

---

## Docker Compose — Serviços

```yaml
services:

  neo4j:
    image: neo4j:5
    ports: 7474, 7687
    volumes: ./data/neo4j
    env: NEO4J_AUTH

  weaviate:
    image: semitechnologies/weaviate
    ports: 8080
    modules: text2vec-openai          ← embeddings via OpenRouter (sem modelo local)
    env: OPENAI_APIKEY, OPENAI_BASEURL=https://openrouter.ai/api/v1
    volumes: ./data/weaviate

  bot:
    build: .
    depends_on: neo4j (healthy), weaviate (healthy)
    env_file: .env
    volumes: ./:/app
```

Nota: `t2v-transformers` foi removido — embeddings agora via OpenRouter (text-embedding-3-small).

---

## Variáveis de Ambiente (.env completo)

```env
# ── OpenRouter ──────────────────────────────────────────
OPENROUTER_API_KEY=sk-or-...

# ── Modelos (troque sem mexer no código) ────────────────
# orquestrador — barato, só classifica intenção
MODEL_ORCHESTRATOR=meta-llama/llama-3-8b-instruct

# conversa e recomendação — use o melhor que quiser
MODEL_CHAT=anthropic/claude-sonnet-4-6

# busca e síntese — modelo médio já resolve
MODEL_SEARCH=meta-llama/llama-3-70b-instruct

# perfil — registra dados estruturados, modelo leve
MODEL_PROFILE=meta-llama/llama-3-8b-instruct

# ── Telegram ────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=...

# ── Neo4j ───────────────────────────────────────────────
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=...

# ── Weaviate ────────────────────────────────────────────
WEAVIATE_URL=http://weaviate:8080

# ── Reddit ──────────────────────────────────────────────
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=anime-assistant/1.0
```

---

## Schema Neo4j

```
Nós:
(:Anime)     → id, titulo, tipo, episodios,
               status, estudio, diretor,
               ano, nota_mal

(:Manga)     → id, titulo, capitulos,
               status, autor, ano, nota_mal

(:Genero)    → nome

(:Tema)      → nome (extraído pelo LLM ao processar reviews)

(:Estudio)   → nome

(:Usuario)   → telegram_id

Relações:
(Usuario)-[:ASSISTIU {nota, data}]->(Anime)
(Usuario)-[:DROPOU {episodio, data}]->(Anime)
(Usuario)-[:LEU {nota, data}]->(Manga)
(Usuario)-[:QUER_VER]->(Anime)

(Anime)-[:TEM_GENERO]->(Genero)
(Anime)-[:TEM_TEMA]->(Tema)
(Anime)-[:PRODUZIDO_POR]->(Estudio)
(Anime)-[:PARECIDO_COM {score}]->(Anime)
(Manga)-[:TEM_GENERO]->(Genero)
```

---

## Schema Weaviate

```
Vectorizer: text2vec-openai (model: text-embedding-3-small via OpenRouter)

Class: Anime
  titulo:    text  (skip vectorize)
  synopsis:  text  ← embedding gerado aqui
  temas:     text[] (skip)
  generos:   text[] (skip)
  estudio:   text  (skip)
  ano:       int   (skip)
  nota:      float (skip)
  anime_id:  text  (skip)
  vector:    auto

Class: Review
  anime_id:  text  (skip)
  texto:     text  ← embedding gerado aqui
  fonte:     text  (skip)
  sentimento: text (skip)
  vector:    auto
```

---

## Fluxo LangGraph — Estados

```python
class State(TypedDict):
    messages:      list   # histórico da conversa
    user_id:       str    # telegram user id
    intent:        str    # conversa|recomendacao|analise|busca|perfil
    user_profile:  dict   # perfil do Neo4j
    context:       str    # contexto relevante buscado
    response:      str    # resposta final
    raw_input:     str    # última mensagem do usuário

# nós do grafo
orchestrator   → detecta intent com MODEL_ORCHESTRATOR
router         → direciona para agente certo (conditional edges)
conversation   → conversa: Neo4j perfil + Weaviate + MODEL_CHAT
recommendation → recomendação: Neo4j + Weaviate (gêneros boost) + Jikan + Reddit
analise        → review: Jikan + Weaviate + Reddit + MODEL_CHAT
search         → DDG Lite + Reddit + MODEL_SEARCH
profile        → gerencia histórico com MODEL_PROFILE
responder      → nó final (passa response adiante)

# background (fora do grafo, após resposta)
extrator       → extrai perfil da mensagem + enriquece Jikan→Neo4j+Weaviate
```

---

## Comportamento dos Agentes

### Orquestrador
- Recebe toda mensagem primeiro
- Classifica intenção com MODEL_ORCHESTRATOR (barato)
- Mantém contexto da conversa via LangGraph state
- Se ambíguo: usa histórico recente para decidir

### Agente de Conversa
- Responde sobre lore, personagens, história, opinião
- Consulta Weaviate para contexto semântico relevante
- Tom: casual, opinativo, como um amigo que entende de anime
- Nunca dá spoiler sem aviso explícito

### Agente de Recomendação
- Sempre consulta Neo4j antes de recomendar
- Busca semântica no Weaviate para "atmosfera"
- Justifica a recomendação com base no histórico do usuário
- Após recomendar: pergunta se quer registrar quando assistir

### Agente de Busca
- Usa DuckDuckGo para informação atual
- Complementa com Reddit API para opinião da comunidade
- Sempre indica a fonte da informação
- Prioriza fontes oficiais (estúdio, produtora)

### Agente de Perfil
- Detecta quando usuário menciona que assistiu algo
- Pergunta nota de 1-10 se não foi informada
- Registra drops com episódio automaticamente
- Mostra padrões de gosto quando solicitado

---

## Coleta de Dados — Scheduler

```
semanal (toda segunda às 6h):
  → puxa temporada atual do Jikan
  → atualiza animes em andamento
  → coleta top discussions Reddit
  → LLM extrai temas e entidades
  → atualiza Weaviate e Neo4j

por temporada (jan/abr/jul/out):
  → coleta temporada completa nova
  → processa todas as sinopses
  → gera embeddings no Weaviate
  → mapeia relações no Neo4j
```

---

## Regras do Projeto

1. **Modelos nunca hardcoded** — sempre via ai/config.py lendo do .env
2. **Nunca dar spoiler** sem o usuário pedir explicitamente
3. **Sempre justificar** recomendações com base no perfil
4. **Tom casual** — como um amigo, não um assistente formal
5. **Fallback gracioso** — se API falhar, avisar e tentar de outra forma
6. **Aprender sempre** — qualquer feedback do usuário atualiza o Neo4j
7. **Sem comando obrigatório** — usuário conversa natural, bot entende

---

## Como Iniciar o Projeto

```bash
# 1. copiar template de variáveis e configurar
cp .env.example .env
# editar .env com suas chaves e modelos preferidos

# 2. subir infraestrutura (sem t2v-transformers — usa OpenRouter para embeddings)
docker compose up -d neo4j weaviate

# 3. aguardar serviços ficarem saudáveis
docker compose ps

# 4. popular dados iniciais (Jikan temporada atual → Neo4j + Weaviate)
docker compose run bot python data/scheduler.py --init

# 5. subir o bot
docker compose up bot

# Comandos disponíveis no Telegram:
# /start      — boas-vindas
# /help       — lista de exemplos
# /historico  — watchlist do usuário
# /novidades  — digest diário manual
# /limpar     — limpa histórico de conversa
```

---

## Ordem de Implementação Sugerida

```
semana 1:
  docker-compose.yml com Neo4j e Weaviate
  ai/config.py lendo modelos do .env
  .env.example com todos os campos
  conexão com Jikan API
  bot Telegram respondendo /start

semana 2:
  LangGraph com orquestrador simples
  agente de perfil (registra histórico)
  Neo4j salvando animes e notas

semana 3:
  agente de recomendação
  Weaviate com embeddings
  busca semântica funcionando

semana 4:
  agente de busca (DuckDuckGo + Reddit)
  conversa natural sem comandos
  scheduler automático

depois:
  refinamento dos prompts
  benchmark de modelos por tarefa e custo
  ajuste fino de MODEL_* no .env
```