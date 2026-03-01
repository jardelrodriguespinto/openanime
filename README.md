# Anime Multi-Assistant Bot

Bot de Telegram para anime, mangá, manhwa, filmes, séries, doramas, música e livros.
Funciona como um assistente pessoal com memória real: aprende seus gostos ao longo do tempo
e usa isso para recomendar, analisar e notificar.

## Visão Geral

Cada mensagem passa por um orquestrador LangGraph que roteia para o agente certo:

| Intenção | O que faz |
|---|---|
| `conversa` | lore, personagens, curiosidades, spoilers |
| `recomendacao` | sugestões personalizadas com base no seu histórico |
| `analise` | review, comparação de obras, mapa de personagens |
| `busca` | novidades, links, lançamentos, turnês, novos livros |
| `perfil` | histórico, notas, progresso, mood, preferências |
| `maratona` | watch order completo de franquias |

Um extrator roda em background após cada mensagem, detecta obras mencionadas
e atualiza automaticamente o perfil no Neo4j.

## Features

- **Chat natural** — sem comandos obrigatórios, conversa como com um amigo
- **Recomendação personalizada** — cruza histórico, gêneros favoritos e busca semântica
- **Memória persistente** — assistidos, dropados, quer_ver, progresso, notas, artistas e autores favoritos
- **Cobertura total** — anime, mangá, manhwa, webtoon, donghua, filmes, séries, doramas, música e livros
- **Busca semântica** via Weaviate (atmosfera, sentimento, vibe)
- **Grafo de preferências** via Neo4j (relações entre obras, gêneros, estúdios)
- **Entrada por áudio** com transcrição (AssemblyAI)
- **Modo maratona** — guia de watch order com contexto da franquia
- **Digest diário** às 08h com radar personalizado da temporada
- **Alerta de episódios** às 20h para séries em progresso (via TVMaze)
- **Alerta cultural semanal** às sextas 12h — novos álbuns (MusicBrainz), turnês (DDG) e livros (Open Library)

## Fontes de Dados

| Fonte | Tipo | Uso |
|---|---|---|
| Jikan (MyAnimeList) | Anime/Mangá | Catálogo, notas, gêneros, sinopses |
| TMDB | Filmes/Séries/Doramas | Catálogo, sinopses, metadados |
| AniList | Anime | Trending da temporada |
| TVMaze | Séries | Agenda de episódios |
| MusicBrainz | Música | Artistas, álbuns, lançamentos recentes |
| Open Library | Livros | Catálogo, autores, lançamentos |
| Reddit | Todos | Discussões, opiniões da comunidade |
| RSS (ANN/MAL) | Anime | Notícias e lançamentos |
| Wikipedia | Todos | Contexto e resumos |
| YouTube | Todos | Vídeos com legenda (recap, review) |
| DuckDuckGo Lite | Todos | Busca web, turnês, sites para ler/assistir |

## Stack

- Python 3.12
- `python-telegram-bot` + JobQueue (agendamento)
- LangGraph (orquestrador multi-agente)
- OpenRouter (todos os LLMs em uma API)
- Neo4j (grafo de perfil e relações)
- Weaviate (busca semântica, embeddings via OpenRouter)
- Redis (histórico de conversa com TTL de 7 dias)
- Docker Compose

## Estrutura

```text
bot/       → handlers Telegram, notificador (3 jobs), formatter
agents/    → orchestrator (LangGraph), conversa, recomendacao, analise, busca, perfil, maratona, extrator
graph/     → neo4j_client, weaviate_client, graphrag
data/      → jikan, tmdb, musicbrainz, openlibrary, reddit, anilist, tvmaze, rss, wikipedia, youtube, scheduler
ai/        → openrouter, assemblyai, config (modelos via .env)
prompts/   → prompt de cada agente
logs/      → logs rotativos
```

## Requisitos

- Docker + Docker Compose
- Token do Telegram Bot — [@BotFather](https://t.me/BotFather)
- Chave do OpenRouter — [openrouter.ai](https://openrouter.ai)
- Chave TMDB *(gratuita)* — [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api)
- Chave AssemblyAI *(para áudio)* — [assemblyai.com](https://www.assemblyai.com)

## Configuração

```bash
cp .env.example .env
# edite .env com suas chaves
```

Variáveis obrigatórias:

```env
OPENROUTER_API_KEY=sk-or-...
TELEGRAM_BOT_TOKEN=...
NEO4J_PASSWORD=sua_senha
TMDB_API_KEY=...           # filmes, séries e doramas
```

Variáveis de modelo (troque sem mexer no código):

```env
MODEL_ORCHESTRATOR=meta-llama/llama-3-8b-instruct   # barato, só classifica
MODEL_CHAT=anthropic/claude-sonnet-4-6               # conversa e recomendação
MODEL_SEARCH=meta-llama/llama-3-70b-instruct         # busca e síntese
MODEL_PROFILE=meta-llama/llama-3-8b-instruct         # perfil, leve
```

## Subindo o Projeto

```bash
# 1. configurar variáveis
cp .env.example .env

# 2. subir tudo
docker compose up -d --build

# 3. carga inicial de dados (recomendado — popula Neo4j e Weaviate)
docker exec anime-bot python -m data.scheduler --init

# 4. acompanhar logs
docker logs -f anime-bot
```

Serviços esperados após o `up`:

- `anime-neo4j` — ports `7474` (browser) e `7687`
- `anime-weaviate` — port `8080`
- `anime-redis` — port `6379`
- `anime-bot`

## Uso no Telegram

Comandos disponíveis:

| Comando | Descrição |
|---|---|
| `/start` | Boas-vindas |
| `/help` | Exemplos de uso |
| `/historico` | Sua watchlist/readlist |
| `/stats` | Estatísticas do seu perfil |
| `/maratona <franquia>` | Watch order completo |
| `/novidades` | Digest de novidades manual |
| `/limpar` | Limpa histórico de conversa |

Exemplos de mensagens naturais:

```
me recomenda algo como solo leveling
analisa attack on titan
tem temporada nova de chainsaw man?
acabei de assistir interstellar, nota 9
li o senhor dos aneis, nota 10
ouvi o novo album do radiohead
tem turnê do coldplay no brasil?
qual a ordem para assistir fate?
prefiro dublado, tenho 30 minutos por dia
me lembra onde eu parei em one piece
```

## Jobs Automáticos

| Job | Horário | O que faz |
|---|---|---|
| `digest_diario` | 08:00 BRT (diário) | Radar da temporada + novidades personalizadas |
| `alerta_episodios` | 20:00 BRT (diário) | Avisa sobre episódios nos próximos 2 dias |
| `lancamentos_culturais` | 12:00 BRT (sextas) | Novos álbuns, turnês e livros dos seus favoritos |

## Modelos Recomendados (OpenRouter)

```
BARATOS   — llama-3-8b, gemma-2-9b, mistral-7b
MÉDIOS    — llama-3-70b, gpt-4o-mini, gemini-flash-1.5
PREMIUM   — claude-sonnet-4-6, gpt-4o, gemini-pro-1.5
```

## Troubleshooting

**Bot não responde**
- Valide `TELEGRAM_BOT_TOKEN`
- `docker logs --tail 200 anime-bot`

**Weaviate sem resultados semânticos**
- Rode a carga inicial: `docker exec anime-bot python -m data.scheduler --init`
- Confirme que `OPENROUTER_API_KEY` está válida (usada para embeddings)

**TMDB não encontra filmes/séries**
- Valide `TMDB_API_KEY` no `.env`

**Erro de áudio**
- Valide `ASSEMBLY_IA_KEY`

**Neo4j não conecta**
- Confirme `NEO4J_PASSWORD` igual ao definido no `docker-compose.yml`

## Segurança

- Nunca commite chaves reais em `.env`
- O `.gitignore` já exclui `.env` — use `.env.example` para documentar
- Se uma chave foi exposta, revogue e gere uma nova imediatamente
