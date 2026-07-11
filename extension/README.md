# AutoApply — extensão Chrome

Migração do sistema de candidaturas (antes Python + Selenium) para uma **extensão Chrome MV3**.
Roda no seu navegador **já logado** — sem Selenium, sem login manual, sem Cloudflare/undetected,
sem CAPTCHA de bot. Dashboard e configuração (OpenRouter + preferências) ficam **dentro da extensão**.

## Como carregar

1. Abra `chrome://extensions`.
2. Ligue **Modo do desenvolvedor** (canto superior direito).
3. **Carregar sem compactação** → selecione a pasta `extension/`.
4. Fixe o ícone na barra (opcional).

> Ícones não estão inclusos (o Chrome usa um padrão). É só cosmético.

## Configurar (a "dashboard")

Clique no ícone → **⚙️ Dashboard** (ou botão direito → Opções). Lá você configura:

- **OpenRouter**: API key + modelo (o "cérebro" que faz match e responde perguntas). Botão **Testar chave**.
- **Perfil**: nome, e-mail, telefone, **resumo do currículo** (base das respostas e do match).
- **Remuneração** (CLT/PJ/USD) — nunca gerada por IA, vem daqui.
- **Filtros**: modalidades, regiões, e **pausar antes de enviar** (recomendado: você confirma o envio).
- **Plataformas**: liga/desliga, palavra-chave, nota mínima de match (0-100), teto por dia.

Nada de senha das plataformas — você já está logado no Chrome.

## Usar

1. Faça login no LinkedIn normalmente no navegador.
2. Clique no ícone da extensão → escolha **LinkedIn** → **▶️ Iniciar**.
3. A extensão abre a busca de vagas, raspa os cards, e para cada Easy Apply: avalia match,
   preenche o formulário (perguntas via IA, salário do config), avança os passos e **para no
   envio** para você conferir e clicar "Enviar candidatura" (se `pausar antes de enviar` estiver ligado).

## Arquitetura

```
manifest.json           MV3
background/              service worker (efêmero): roteia mensagens, chama OpenRouter, storage
  service-worker.js
lib/
  store.js              config + estado (chrome.storage) — substitui o .env
  openrouter.js         o "cérebro": match vaga↔CV + respostas (salário nunca por IA)
  dom.js                helpers de content script (React-safe setNativeValue, waitFor, etc.)
content/
  linkedin.js           LinkedIn Easy Apply: raspa → aplica → preenche → avança → pausa no envio
popup/                  iniciar/parar + status
options/                a dashboard (config)
```

**Fluxo de mensagens:** content script → `chrome.runtime.sendMessage` → service worker (cérebro/OpenRouter/storage).
O loop por-vaga mora no content script (o SW MV3 é efêmero, ~30s).

## Status desta versão

- ✅ Dashboard/config completa (OpenRouter + perfil + salário + filtros + plataformas).
- ✅ Cérebro OpenRouter (match + respostas) chamado direto da extensão.
- ✅ Preenchedor genérico (`lib/forms.js`) + wizard genérico (`lib/wizard.js`) reusados por todas.
- ✅ **LinkedIn Easy Apply** — o mais maduro (modal same-page, o loop inteiro cabe no content script).
- ✅ **Indeed** — fila em storage (sobrevive à navegação), `viewjob?jk=` (evita "Security Check"), cookie OneTrust, SmartApply cross-domain (`smartapply.indeed.com` assume o form), pausa no reCAPTCHA do envio.
- ✅ **Gupy / GeekHunter / Senior** — 1ª versão: raspa cards, abre a vaga, roda o wizard genérico (radio/combobox/mat-select/consentimento), pausa antes do "Finalizar".

> Indeed/Gupy/GeekHunter/Senior são **1ª versão, sem teste ao vivo** — os seletores de SPA mudam;
> abra o console da aba e ajuste conforme necessário. LinkedIn é o mais confiável para começar.

## Notas de manutenção

- **Seletores do LinkedIn mudam** — os de `content/linkedin.js` são fundamentados no `linkedin_selenium.py`,
  mas precisam de ajuste no 1º run real (abra o DevTools da página e veja o console).
- **Inputs React**: usar `OA.setNativeValue` (setter nativo + dispatch), nunca `el.value = x`.
- **Detecção de sucesso**: escopada no modal, não escaneia a página toda (senão o rótulo "Candidatura enviada"
  de vagas já aplicadas dá falso-positivo).
- O código Python + Selenium continua no repo (não foi removido).
