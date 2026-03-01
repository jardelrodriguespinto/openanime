# Anime Multi-Assistant Bot

Bot de Telegram focado em anime/manga/manhwa com experiencia de "assistente pessoal":
conversa natural, recomendacoes personalizadas, analises de obras, novidades e memoria de perfil.

## Visao Geral

O projeto usa um orquestrador em LangGraph para rotear cada mensagem para um agente especializado:

- `conversa`: lore, personagens, curiosidades, spoilers
- `recomendacao`: sugestoes com base no seu perfil
- `analise`: review profundo e comparacoes de obras
- `busca`: novidades, links, lancamentos e contexto atual
- `perfil`: historico, notas, progresso, mood e preferencias

Tambem existe um extrator em background que aprende com as mensagens do usuario
e atualiza o perfil no Neo4j.

## Principais Features

- Chat natural (sem precisar de comandos para tudo)
- Recomendacao personalizada por historico real
- Memoria de usuario (assistidos, dropados, quer_ver, progresso, notas)
- Busca semantica via Weaviate
- Relacoes e preferencias no Neo4j
- Digest diario automatico no Telegram
- Entrada por audio com transcricao (AssemblyAI)
- Coleta de dados de fontes externas:
  - Jikan (catalogo anime)
  - AniList (trending)
  - RSS de noticias
  - Reddit
  - Wikipedia
  - TVMaze
  - YouTube (videos com legenda)

## Stack

- Python 3.12
- `python-telegram-bot`
- LangGraph / LangChain
- OpenRouter (LLMs)
- Neo4j
- Weaviate
- Docker Compose

## Estrutura

```text
bot/       # entrada Telegram, handlers e notificador diario
agents/    # orquestrador e agentes especializados
graph/     # clientes Neo4j / Weaviate + utilitarios GraphRAG
data/      # conectores externos e scheduler de ingestao
ai/        # clientes OpenRouter e AssemblyAI
prompts/   # prompts por agente
logs/      # logs do bot
```

## Requisitos

- Docker + Docker Compose
- Token do Telegram Bot
- Chave do OpenRouter
- Chave AssemblyAI (para audio)

## Configuracao

Crie/edite o arquivo `.env` na raiz com:

```env
# OpenRouter
OPENROUTER_API_KEY=sk-or-...

# Telegram
TELEGRAM_BOT_TOKEN=...

# Neo4j
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=sua_senha

# Weaviate
WEAVIATE_URL=http://weaviate:8080

# Modelos OpenRouter
MODEL_ORCHESTRATOR=meta-llama/llama-3-8b-instruct
MODEL_CHAT=anthropic/claude-sonnet-4-6
MODEL_SEARCH=meta-llama/llama-3-70b-instruct

# Audio
ASSEMBLY_IA_KEY=...

# Logs
LOG_LEVEL=INFO
```

## Subindo o Projeto

```bash
docker compose up -d --build
```

Servicos esperados:

- `anime-neo4j` (ports `7474` e `7687`)
- `anime-weaviate` (port `8080`)
- `anime-bot`

Para acompanhar logs:

```bash
docker logs -f anime-bot
```

## Carga Inicial de Dados (Recomendado)

Sem carga inicial, a busca semantica pode ficar fraca.

Execute uma ingestao inicial:

```bash
docker exec anime-bot python -m data.scheduler --init
```

Isso coleta temporada atual + dados de Reddit e indexa em Neo4j/Weaviate.

## Uso no Telegram

Comandos:

- `/start`
- `/help`
- `/historico`
- `/novidades`
- `/limpar`

Exemplos de mensagens naturais:

- "me recomenda algo como solo leveling"
- "analisa attack on titan"
- "tem temporada nova de chainsaw man?"
- "acabei de assistir steins gate, nota 10"
- "prefiro dublado"
- "me lembra onde eu parei em one piece"

## Operacao

- O bot agenda digest diario as 08:00 (`America/Sao_Paulo`)
- Logs em arquivo: `logs/bot.log`
- Retries para erros de rede e flood do Telegram

## Seguranca

- Nunca commite chaves reais em `.env`
- Use placeholders em exemplos/documentacao
- Se uma chave foi exposta, gere uma nova imediatamente

## Troubleshooting Rapido

- Bot nao responde:
  - valide `TELEGRAM_BOT_TOKEN`
  - cheque logs: `docker logs --tail 200 anime-bot`
- Weaviate sem resultados:
  - rode a carga inicial (`--init`)
  - confirme `OPENROUTER_API_KEY` valido (embeddings)
- Erro de audio:
  - valide `ASSEMBLY_IA_KEY`

