# site_teste — alvo LOCAL de CAPTCHA + solvers (estudo)

Um site Flask que roda **só em `127.0.0.1`** e gera 3 tipos de CAPTCHA na hora, mais
dois solvers pra você comparar. Tudo local, tudo gerado por você — nenhum serviço de
terceiros envolvido.

## Tipos de CAPTCHA
- `math` — uma conta (ex.: `7+3`), resposta = o resultado.
- `texto` — 5 caracteres (a-z0-9), fonte fixa.
- `distorcido` — 5 caracteres com rotação + ruído + linhas (o difícil).

## Solvers
- **clássico** (`solvers.resolver_classico`) — template matching puro em numpy, sem IA.
- **IA de visão** (`solvers.resolver_llm`) — manda a imagem pra um modelo multimodal
  via OpenRouter. Lê a chave de `OPENROUTER_API_KEY` (env ou `.env` da raiz do projeto).
  Modelo trocável por `CAPTCHA_LLM_MODEL` (default `openai/gpt-4o-mini`).

## Rodar
```bash
cd estudos/captcha_cv && source .venv/bin/activate       # flask+numpy+pillow
# terminal 1 — sobe o site
python site_teste/app.py            # http://127.0.0.1:5000  (abra pra ver os CAPTCHAs)
# terminal 2 — testa
python site_teste/rodar_teste.py --solver classico --n 12
python site_teste/rodar_teste.py --solver llm --n 4       # usa OpenRouter (gasta crédito)
```

## Resultado medido (referência)
```
[classico] math        12/12    texto 12/12    distorcido 0/12
[llm]      math         3/3      texto 3/3      distorcido 2/3   (erro típico: o↔0)
```
Ou seja: template matching arrasa no que é limpo/fonte-fixa e **zera no distorcido**;
a IA de visão segura os três, inclusive o distorcido. É o retrato didático de por que
CAPTCHAs "de verdade" apostam em distorção/ruído — e por que os antigos de texto
caíram.

## Limite ético (o mesmo do projeto pai)
Isto resolve CAPTCHAs que **você gera no seu site local**. Não aponte para o controle
anti-bot de um serviço de terceiros (Indeed, reCAPTCHA/hCaptcha reais) — lá deixa de
ser estudo e vira circumvenção. O reCAPTCHA v2/v3 e o hCaptcha, aliás, não são "texto
numa imagem": usam seleção de objetos + sinais comportamentais, feitos pra resistir a
exatamente estas abordagens.
```
