# Anime Multi-Assistant Bot

Bot de Telegram pessoal multi-propósito: anime, filmes, séries, doramas, música, livros,
notícias gerais, PDFs, perfil profissional, vagas de emprego e candidatura automática.
Aprende seus gostos ao longo do tempo via grafo no Neo4j.

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
| `noticias` | notícias gerais — tech, IA, mercado, games, ciência, programação |
| `documento` | analisar PDFs recebidos, responder perguntas sobre documentos, gerar PDFs |
| `perfil_pro` | gerenciar perfil profissional: habilidades, experiência, pretensão salarial |
| `vaga` | buscar vagas de emprego (Indeed, Gupy, RemoteOK, LinkedIn) — tudo gratuito |
| `curriculo_ats` | gerar currículo ATS otimizado para vaga específica |
| `candidatura` | candidatura automática via Playwright (LinkedIn Easy Apply, Gupy) |

Um extrator roda em background após cada mensagem, detecta obras mencionadas
e atualiza automaticamente o perfil no Neo4j.

## Features

**Entretenimento**
- Chat natural — sem comandos obrigatórios, conversa como com um amigo
- Recomendação personalizada — cruza histórico, gêneros favoritos e busca semântica
- Memória persistente — assistidos, dropados, quer_ver, progresso, notas
- Cobertura total — anime, mangá, manhwa, webtoon, donghua, filmes, séries, doramas, música e livros
- Busca semântica via Weaviate (atmosfera, sentimento, vibe)
- Grafo de preferências via Neo4j
- Entrada por áudio com transcrição (AssemblyAI)
- Modo maratona — guia de watch order com contexto da franquia
- Digest diário às 08h com radar personalizado da temporada
- Alerta de episódios às 20h para séries em progresso (via TVMaze)
- Alerta cultural semanal às sextas 12h

**Notícias (Fase 1)**
- Notícias gerais por categoria: tech, IA, mercado, games, ciência, brasil, programação, startup
- Fontes RSS públicas + DuckDuckGo Lite — sem API paga
- Interesses persistidos por usuário no Neo4j

**Documentos PDF (Fase 2)**
- Recebe PDFs pelo Telegram e analisa o conteúdo
- Responde perguntas sobre documentos já enviados
- Detecta currículos e extrai perfil profissional automaticamente
- Gera PDFs (relatórios, resumos) via Jinja2 + weasyprint
- Armazena documentos no Weaviate para busca semântica

**Perfil Profissional (Fase 3)**
- Registra habilidades, experiências, formação e pretensão salarial
- Exibe score de completude do perfil
- Detecta dados profissionais em linguagem natural

**Vagas e Currículo ATS (Fase 4)**
- Busca vagas em múltiplas fontes gratuitas: Indeed RSS, Gupy API, RemoteOK, LinkedIn
- Score de compatibilidade candidato × vaga
- Gera currículo ATS personalizado para vaga específica
- Template ATS-compliant: coluna única, fonte padrão, texto selecionável, sem tabelas de layout

**Candidatura Automática (Fase 5)**
- LinkedIn Easy Apply via Playwright
- Gupy via Playwright
- Limite diário configurável (padrão 10 candidaturas)
- Detecção de perguntas customizadas com sugestão de resposta via LLM
- Confirmação obrigatória antes de qualquer envio

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
| RSS (ANN/MAL/tech/games/etc.) | Notícias | Notícias por categoria, anime news |
| Indeed RSS | Vagas | Vagas de emprego gratuitas |
| Gupy API | Vagas | Vagas empresas brasileiras |
| RemoteOK API | Vagas | Vagas remotas internacionais |
| Wikipedia | Todos | Contexto e resumos |
| YouTube | Todos | Vídeos com legenda (recap, review) |
| DuckDuckGo Lite | Todos | Busca web, turnês, sites para ler/assistir, notícias |

## Stack

- Python 3.12
- `python-telegram-bot` + JobQueue (agendamento)
- LangGraph (orquestrador multi-agente)
- OpenRouter (todos os LLMs em uma API)
- Neo4j (grafo de perfil e relações)
- Weaviate (busca semântica, embeddings via OpenRouter)
- Redis (histórico de conversa + estado de candidatura pendente, TTL 7d)
- Playwright (automação de candidaturas)
- weasyprint + Jinja2 (geração de PDFs)
- pdfplumber (extração de texto de PDFs)
- Docker Compose

## Estrutura

```text
bot/         → handlers Telegram, notificador (3 jobs), formatter, redis_history
agents/      → orchestrator (LangGraph), conversa, recomendacao, analise, busca, perfil,
               maratona, extrator, news, documents, profile_pro, jobs, apply
graph/       → neo4j_client, weaviate_client, graphrag
data/        → jikan, tmdb, reddit, news, scheduler e demais fontes
ai/          → openrouter, assemblyai, config (modelos via .env)
automation/  → browser (Playwright), linkedin_apply, gupy_apply, form_filler
utils/       → pdf_writer, ats_optimizer, templates/
prompts/     → prompt de cada agente
logs/        → logs rotativos
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

Variáveis opcionais (fases 4 e 5):

```env
# Candidatura automática
LINKEDIN_EMAIL=seu@email.com
LINKEDIN_PASSWORD=sua_senha
APPLY_DAILY_LIMIT=10
PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_TIMEOUT_MS=30000

# PDFs
PDF_MAX_SIZE_MB=20
PDF_MAX_PAGES_FULL=30

# Vagas
JOBS_LIMITE_POR_FONTE=5
JOBS_LIMITE_TOTAL=15
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
| `/noticias [categoria]` | Notícias por área (tech, ia, games...) |
| `/vagas [query]` | Buscar vagas de emprego |
| `/curriculo_ats` | Gerar currículo ATS personalizado |
| `/perfil_pro` | Ver/editar perfil profissional |
| `/candidaturas` | Pipeline de candidaturas |
| `/limpar` | Limpa histórico de conversa |

Exemplos de mensagens naturais:

```
# Entretenimento
me recomenda algo como solo leveling
analisa attack on titan
tem temporada nova de chainsaw man?
acabei de assistir interstellar, nota 9
qual a ordem para assistir fate?

# Notícias
noticias de tech hoje
tem novidade de IA?
o que aconteceu no mercado?

# PDF
[enviar arquivo .pdf pelo Telegram]
o que esse contrato diz sobre multa?
gera um PDF do resumo

# Perfil profissional
sou desenvolvedor senior com 5 anos de Python
minha pretensao e 12k, quero remoto

# Vagas e currículo
busca vagas de dev python remoto
personaliza meu curriculo para a vaga da Nubank
gera meu curriculo ats

# Candidatura
me candidata nessa vaga       → bot pede confirmação
sim                           → executa candidatura
```

## Fluxo de Candidatura

1. Usuário diz "me candidata nessa vaga" (ou clica em uma vaga buscada)
2. Bot mostra resumo: vaga, currículo que será usado, plataforma detectada
3. Bot aguarda confirmação: responda **"sim"** para confirmar ou **"não"** para cancelar
4. Se confirmado: Playwright abre a página, preenche formulário e submete
5. Se houver perguntas customizadas: bot sugere respostas baseadas no perfil
6. Resultado é registrado no Neo4j (status: candidatado / tentativa_falhou)

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

**Candidatura automática não funciona**
- Confirme `LINKEDIN_EMAIL` e `LINKEDIN_PASSWORD` no `.env`
- Verifique `PLAYWRIGHT_HEADLESS=false` para debugar visualmente
- LinkedIn e Gupy podem exigir verificação de 2FA — faça login manual uma vez

**PDF não processa**
- Verifique tamanho (máx `PDF_MAX_SIZE_MB`, padrão 20MB)
- weasyprint requer libs do sistema instaladas — confirme o `docker build` concluiu sem erros

## Segurança

- Nunca commite chaves reais em `.env`
- O `.gitignore` já exclui `.env` — use `.env.example` para documentar
- Se uma chave foi exposta, revogue e gere uma nova imediatamente
- Credenciais de LinkedIn ficam apenas no `.env` (nunca no código)
