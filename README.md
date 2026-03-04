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
| `vaga` | buscar vagas em 12+ fontes (Indeed, Gupy, Glassdoor, LinkedIn, RemoteOK...) |
| `curriculo_ats` | gerar currículo ATS em PDF otimizado com LLM especialista |
| `candidatura` | candidatura automática via Playwright (LinkedIn Easy Apply, Gupy, Greenhouse, Lever) |

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

**Notícias**
- Notícias gerais por categoria: tech, IA, mercado, games, ciência, brasil, programação, startup
- Fontes RSS públicas + DuckDuckGo Lite — sem API paga
- Interesses persistidos por usuário no Neo4j

**Documentos PDF**
- Recebe PDFs pelo Telegram e analisa o conteúdo
- Responde perguntas sobre documentos já enviados
- Detecta currículos e extrai perfil profissional automaticamente
- Gera PDFs (relatórios, resumos) via Jinja2 + weasyprint
- Armazena documentos no Weaviate para busca semântica

**Perfil Profissional**
- Registra habilidades, experiências, formação e pretensão salarial
- Exibe score de completude do perfil
- Detecta dados profissionais em linguagem natural

**Vagas e Currículo ATS**
- Busca vagas em 12+ fontes simultâneas e gratuitas em paralelo:
  Indeed, Gupy (38 empresas BR), Glassdoor, LinkedIn, RemoteOK, WorkingNomads,
  Trampos, Revelo, Programathor, Remotar, Inhire, Vagas.com.br, Catho, InfoJobs e dorks DDG
- Filtro inteligente por senioridade (detecta da mensagem, não do perfil)
- Score de compatibilidade candidato × vaga com breakdown de skills
- Currículo ATS gerado com LLM especialista (método CAR, verbos de ação, impacto quantificado)
- Template PDF ATS-compliant: coluna única, fonte padrão, texto selecionável, sem tabelas

**Candidatura Automática**
- Lê a página da vaga antes de confirmar — extrai requisitos, salário e modalidade
- Mostra score de compatibilidade e skills que batem/faltam antes de aplicar
- Gera currículo ATS **personalizado para a vaga** na hora de enviar
- LinkedIn Easy Apply: multi-step, upload de currículo, LLM responde perguntas customizadas
- Gupy: login, multi-step, upload de currículo, LLM responde perguntas abertas
- Greenhouse e Lever: suporte genérico (nome, email, CV upload, submit)
- **Persistência de sessão**: cookies salvos após o primeiro login — não precisa logar de novo
- Playwright com stealth anti-detecção: user-agent rotacionado, sem sinais de automação
- Limite diário configurável (padrão 10 candidaturas)
- Confirmação obrigatória com score antes de qualquer envio

## Fontes de Vagas

| Fonte | Tipo | Notas |
|---|---|---|
| Indeed RSS | BR + Internacional | Scraping HTML + RSS fallback |
| Gupy API | 38 empresas BR | Nubank, iFood, Stone, VTEX, Hotmart... |
| Gupy Portal | Busca geral | Portal público gupy.io |
| Glassdoor | BR + Internacional | DDG dork dedicado |
| LinkedIn | BR + Internacional | Scraping páginas públicas |
| RemoteOK | Internacional remoto | API JSON pública |
| WorkingNomads | Internacional remoto | API JSON pública |
| Trampos | BR tech | Scraping |
| Revelo | BR tech | Scraping |
| Programathor | BR tech | Scraping |
| Remotar | BR remoto | Scraping |
| Inhire | BR tech | Scraping |
| Vagas.com.br | BR geral | Scraping |
| Catho | BR geral | DDG dork |
| InfoJobs | BR geral | DDG dork |
| DDG Dorks | Qualquer | 8 combinações paralelas de sites × queries |

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
| RSS (ANN/MAL/tech/games/etc.) | Notícias | Notícias por categoria |
| Wikipedia | Todos | Contexto e resumos |
| YouTube | Todos | Vídeos com legenda (recap, review) |
| DuckDuckGo Lite | Todos | Busca web sem API |

## Stack

- Python 3.12
- `python-telegram-bot` + JobQueue (agendamento)
- LangGraph (orquestrador multi-agente, 12 intenções)
- OpenRouter (todos os LLMs em uma API)
- Neo4j (grafo de perfil e relações)
- Weaviate (busca semântica, embeddings via OpenRouter)
- Redis (histórico de conversa + estado pendente, TTL 7d)
- Playwright (automação de candidaturas com stealth)
- weasyprint + pydyf==0.10.0 + Jinja2 (geração de PDFs)
- pdfplumber (extração de texto de PDFs)
- Docker Compose

## Estrutura

```text
bot/         → handlers Telegram, notificador (3 jobs), formatter, redis_history
agents/      → orchestrator (LangGraph), conversa, recomendacao, analise, busca, perfil,
               maratona, extrator, news, documents, profile_pro, jobs, apply, responder
graph/       → neo4j_client, weaviate_client, graphrag
data/        → jikan, tmdb, reddit, news, scheduler e demais fontes, jobs (12+ fontes)
ai/          → openrouter, assemblyai, config (modelos via .env)
automation/  → browser (Playwright stealth + sessões), linkedin_apply, gupy_apply, form_filler
utils/       → pdf_writer, ats_optimizer (LLM especialista), templates/
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

Variáveis opcionais:

```env
# Candidatura automática
LINKEDIN_EMAIL=seu@email.com
LINKEDIN_PASSWORD=sua_senha
GUPY_EMAIL=seu@email.com      # usa LINKEDIN_EMAIL se vazio
GUPY_PASSWORD=sua_senha
APPLY_DAILY_LIMIT=10
PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_TIMEOUT_MS=45000

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

Serviços após o `up`:

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
| `/comandos` | Lista completa de comandos |
| `/ajuda` | Alias de ajuda |
| `/historico` | Sua watchlist/readlist |
| `/stats` | Estatísticas do seu perfil |
| `/maratona <franquia>` | Watch order completo |
| `/novidades` | Digest de novidades manual |
| `/noticias [categoria]` | Notícias por área (tech, ia, games...) |
| `/vagas [query]` | Buscar vagas de emprego |
| `/curriculo_ats` | Gerar currículo ATS em PDF |
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

# PDF
[enviar arquivo .pdf pelo Telegram]
o que esse contrato diz sobre multa?

# Perfil profissional
sou desenvolvedor pleno com 4 anos de Python
minha pretensão é 12k, quero remoto

# Vagas e currículo
busca vagas de dev python pleno remoto
me dá meu currículo em PDF
gera meu currículo ATS para vaga de backend

# Candidatura
me candidata nessa vaga           → bot lê a vaga, mostra score e pede confirmação
sim                               → gera CV personalizado e aplica via Playwright
minhas candidaturas               → mostra pipeline
```

## Fluxo de Candidatura

1. Usuário diz "me candidata nessa vaga"
2. Bot **lê a página da vaga** (httpx) — extrai descrição, requisitos, salário, modalidade
3. Calcula **score de compatibilidade** com o perfil do usuário
4. Mostra confirmação: `🟢 75% compatibilidade`, skills que batem, skills que faltam, plataforma
5. Usuário confirma com **"sim"**
6. Bot gera **currículo ATS personalizado** para essa vaga específica
7. Playwright abre Chrome headless, faz login (ou usa sessão salva), navega multi-step
8. Preenche formulário, faz upload do currículo, responde perguntas com LLM
9. Submete e confirma no Telegram: "Candidatura enviada!"
10. Registra no Neo4j (status: candidatado / tentativa_falhou)

**Sessão persistente**: após o primeiro login, os cookies são salvos em `/app/data/sessions/`.
Candidaturas seguintes pulam o login automaticamente até a sessão expirar.

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

**Neo4j não conecta**
- Confirme `NEO4J_PASSWORD` igual ao definido no `docker-compose.yml`

**Candidatura automática não funciona**
- Confirme `LINKEDIN_EMAIL` e `LINKEDIN_PASSWORD` no `.env`
- `PLAYWRIGHT_HEADLESS=false` para ver o browser em ação e debugar
- LinkedIn com 2FA: faça login manual uma vez, o bot salva a sessão automaticamente
- Sessão expirou: o bot detecta, remove o arquivo e refaz login sozinho
- Logs: `docker logs anime-bot | grep -i "linkedin\|gupy\|sessao"`

**Currículo PDF não é enviado**
- Verifique se o perfil profissional está preenchido (`/perfil_pro`)
- `docker logs anime-bot | grep -i "curriculo\|pdf\|ats"`

**PDF não processa**
- Verifique tamanho (máx `PDF_MAX_SIZE_MB`, padrão 20MB)
- weasyprint requer `pydyf==0.10.0` — versões mais novas são incompatíveis

## Segurança

- Nunca commite chaves reais em `.env`
- O `.gitignore` já exclui `.env` — use `.env.example` para documentar
- Se uma chave foi exposta, revogue e gere uma nova imediatamente
- Credenciais de LinkedIn/Gupy ficam apenas no `.env` (nunca no código)
- Cookies de sessão ficam em `/app/data/sessions/` dentro do container (não versionados)
