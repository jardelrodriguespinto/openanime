# Fase 1 — Agente de Notícias

## Objetivo
Permitir que o usuário peça notícias gerais ou por área de interesse
(tech, IA, mercado, games, ciência, etc.) de forma conversacional,
sem precisar de comandos. O bot aprende quais áreas o usuário prefere
e filtra automaticamente ao longo do tempo.

---

## Novos Arquivos

```
data/news.py              → coleta notícias (RSS + NewsAPI + DDG)
agents/news.py            → agente LangGraph de notícias
prompts/news.py           → prompt do agente
```

### Arquivos modificados

```
agents/orchestrator.py    → adicionar intent "noticias"
bot/handlers.py           → adicionar /noticias comando
bot/notificador.py        → expandir digest diário com notícias
graph/neo4j_client.py     → salvar/ler interesses de notícias do usuário
.env.example              → MODEL_NEWS, NEWS_API_KEY, RSS_FEEDS
```

---

## data/news.py

### Fontes (em ordem de prioridade)

| Fonte | Como | Custo |
|---|---|---|
| RSS feeds públicos | feedparser | Gratuito |
| DuckDuckGo News | httpx (já existe) | Gratuito |
| NewsAPI.org | httpx + chave | Gratuito (100 req/dia) |

### Categorias suportadas

```python
CATEGORIAS = {
    "tech":      ["https://feeds.feedburner.com/TechCrunch",
                  "https://www.theverge.com/rss/index.xml"],
    "ia":        ["https://aiweekly.co/issues.rss",
                  "https://www.deeplearning.ai/the-batch/feed/"],
    "mercado":   ["https://feeds.folha.uol.com.br/mercado/rss091.xml"],
    "games":     ["https://www.ign.com/feeds/games.rss"],
    "ciencia":   ["https://feeds.newscientist.com/full-strength-news"],
    "brasil":    ["https://feeds.folha.uol.com.br/folha/brasil/rss091.xml"],
    "geral":     ["https://feeds.bbci.co.uk/news/rss.xml"],
}
```

### Interface pública

```python
async def buscar_noticias(categorias: list[str], limite: int = 10) -> list[dict]:
    """
    Retorna lista de notícias das categorias pedidas.
    Cada item: {titulo, url, fonte, data, resumo}
    """

async def buscar_noticias_ddg(query: str, limite: int = 5) -> list[dict]:
    """
    Busca notícias com DDG para queries livres.
    Ex: "lançamento iPhone 2025", "greve professores Brasil"
    """
```

---

## agents/news.py

### Responsabilidades
1. Recebe `state` do LangGraph com `intent = "noticias"`
2. Lê perfil de interesses do usuário no Neo4j
3. Se usuário pediu área específica: usa essa área
4. Se não pediu: usa áreas salvas no perfil (fallback: "geral")
5. Chama `data/news.py` para coletar
6. LLM sintetiza e formata com MODEL_NEWS
7. Retorna resposta com links clicáveis

### Fluxo interno

```
entrada: "me dá notícias de IA hoje"
    ↓
extrai área: "ia"
    ↓
busca RSS feeds de IA (feedparser)
    ↓
complementa com DDG "IA notícias hoje"
    ↓
LLM sintetiza top 5 mais relevantes
    ↓
resposta: título + 1 linha + link para cada notícia
```

---

## prompts/news.py

### Comportamento esperado do LLM

```
- Sintetiza 5 notícias em linguagem casual
- Cada notícia: título curto + 1 frase de contexto + link
- Se tiver notícia muito técnica: simplifica sem perder substância
- Tom: "olha essa aqui é interessante porque..."
- Destaca se algo é breaking news ou relevante pro perfil do usuário
- Não inventa — só usa o que veio das fontes
```

---

## Expansão Neo4j — Interesses de Notícias

### Novo campo no nó Usuario

```cypher
// salvar interesse
MATCH (u:Usuario {telegram_id: $user_id})
SET u.interesses_noticias = $lista_categorias

// ler interesse
MATCH (u:Usuario {telegram_id: $user_id})
RETURN u.interesses_noticias
```

### Como o bot aprende

- Usuário pede "notícias de tech" → salva "tech" nos interesses
- Usuário reage positivamente (pede mais) → reforça categoria
- Extrator background detecta menção a área de interesse → atualiza Neo4j
- `/noticias` sem argumento usa interesses salvos automaticamente

---

## Novos Comandos no Telegram

```
/noticias              → notícias das suas áreas salvas
/noticias tech         → notícias de tech agora
/noticias ia mercado   → múltiplas áreas
```

### Conversacional (sem comando)

```
"tem novidade de IA hoje?"          → intent: noticias, área: ia
"o que aconteceu no mercado?"       → intent: noticias, área: mercado
"me atualiza sobre games"           → intent: noticias, área: games
"notícias gerais"                   → intent: noticias, área: geral
```

---

## Expansão do Digest Diário (notificador.py)

O digest atual só manda anime. Com esta fase, expande para:

```
Bom dia! Aqui está seu resumo de hoje:

🎌 Anime: [episódios de hoje via TVMaze]
📰 Tech: [top 3 notícias de tech]
💼 Mercado: [top 2 notícias de mercado]

[gerado com base nas suas preferências]
```

Só mostra categorias que o usuário tem salvas no perfil.

---

## Variáveis de Ambiente

```env
# Modelo para notícias (síntese rápida — modelo médio resolve)
MODEL_NEWS=meta-llama/llama-3-70b-instruct

# NewsAPI.org (opcional — RSS já funciona sem isso)
NEWS_API_KEY=

# Categorias ativas por padrão (separadas por vírgula)
RSS_FEEDS_DEFAULT=tech,geral
```

---

## Dependências Novas

```
feedparser==6.0.11    → leitura de RSS feeds
```

Só uma biblioteca nova. httpx já está no projeto para DDG.

---

## Ordem de Implementação Interna

```
1. data/news.py         → coleta RSS + DDG (sem LLM, só dados)
2. prompts/news.py      → prompt do agente
3. agents/news.py       → agente completo
4. orchestrator.py      → adicionar intent "noticias"
5. neo4j_client.py      → salvar/ler interesses
6. handlers.py          → /noticias comando
7. notificador.py       → expandir digest
8. .env.example         → novas variáveis
```

---

## Critérios de Conclusão

- [ ] `/noticias tech` retorna 5 notícias com links reais
- [ ] `"tem novidade de IA?"` funciona sem comando
- [ ] Interesse salvo no Neo4j após primeira interação
- [ ] `/noticias` (sem área) usa preferências salvas
- [ ] Digest diário inclui notícias quando há interesses configurados
- [ ] Fallback gracioso quando RSS está fora: tenta DDG antes de avisar erro
