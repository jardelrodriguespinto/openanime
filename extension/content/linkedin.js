// LinkedIn Easy Apply — content script. Roda na sua sessão logada (sem Selenium,
// sem CAPTCHA de bot, sem login manual). Loop: raspa cards → abre → Candidatura
// simplificada → preenche (perguntas via cérebro/OpenRouter) → Avançar/Revisar →
// PARA no envio p/ você confirmar (config pausarAntesEnvio, default true).
(function () {
  const OA = window.OA;
  const PLAT = "linkedin";
  let rodando = false;

  const SEL = {
    cards: ".scaffold-layout__list li, .jobs-search-results__list-item, li.jobs-search-results__list-item, [data-occludable-job-id], .job-card-container",
    cardLink: "a.job-card-container__link, a.job-card-list__title, a[href*='/jobs/view/']",
    easyApply: ".jobs-apply-button, button.jobs-apply-button--top-card",
    modal: ".jobs-easy-apply-modal, [data-test-modal], div.artdeco-modal--layer-default",
    next: "[data-easy-apply-next-button], button[aria-label*='Avançar'], button[aria-label*='Continue to next'], button[aria-label*='Continuar']",
    review: "button[aria-label*='Revisar'], button[aria-label*='Review']",
    submit: "button[aria-label*='Enviar candidatura'], button[aria-label*='Submit application'], button[aria-label*='Enviar aplicação']",
    followCheckbox: "#follow-company-checkbox, input[id*='follow-company']",
    dismiss: "button[aria-label*='Dispensar'], button[aria-label*='Dismiss'], button[aria-label*='Fechar']",
    discardBtn: "button[data-control-name='discard_application_confirm_btn'], button[data-test-dialog-secondary-btn]",
  };

  const CONTATO = ["nome", "name", "sobrenome", "last name", "first name", "email", "e-mail", "telefone", "phone", "cidade", "city", "país", "pais", "country", "endereço", "address", "cep"];
  // Frases de confirmação (do _FRASES_SUCESSO do linkedin_selenium.py) — verifica o
  // envio p/ não marcar falso-sucesso no dedup.
  const SUCESSO = ["candidatura enviada", "sua candidatura foi enviada", "candidatura foi enviada", "application submitted", "application sent", "your application was sent", "candidatura recebida", "você se candidatou", "you've applied", "you applied"];

  async function status(txt, action) {
    await OA.bg({ type: "status.push", platform: PLAT, status: txt, action });
  }

  async function config() {
    const r = await OA.bg({ type: "config.get" });
    return r.config;
  }

  function jobIdDoCard(card) {
    return (
      card.getAttribute("data-occludable-job-id") ||
      card.getAttribute("data-job-id") ||
      card.querySelector("[data-job-id]")?.getAttribute("data-job-id") ||
      (card.querySelector("a[href*='/jobs/view/']")?.href.match(/jobs\/view\/(\d+)/)?.[1]) ||
      ""
    );
  }

  function descricaoVaga() {
    const d = document.querySelector(".jobs-description__content, #job-details, .jobs-box__html-content, article");
    return (d?.innerText || "").trim();
  }
  function tituloVaga() {
    return (document.querySelector(".job-details-jobs-unified-top-card__job-title, .jobs-unified-top-card__job-title, h1")?.innerText || "").trim();
  }
  function empresaVaga() {
    return (document.querySelector(".job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name a, a[href*='/company/']")?.innerText || "").trim();
  }

  // Preenche as perguntas ainda não respondidas dentro do modal.
  async function preencherPerguntas(modal, vagaTitulo, vagaEmpresa) {
    const grupos = modal.querySelectorAll(".fb-dash-form-element, .jobs-easy-apply-form-section__grouping, fieldset, div[data-test-form-element]");
    const alvos = grupos.length ? grupos : [modal];
    for (const g of alvos) {
      // radios
      const radios = g.querySelectorAll("input[type='radio']");
      if (radios.length && ![...radios].some((r) => r.checked)) {
        const label = OA.labelFor(radios[0]) || g.querySelector("legend, label")?.innerText || "";
        if (label && !ehContato(label)) {
          const opcoes = [...radios].map((r) => OA.labelFor(r) || r.value).filter(Boolean);
          const resp = await responder(label, "RADIO", opcoes, vagaTitulo, vagaEmpresa);
          const escolha = [...radios].find((r) => (OA.labelFor(r) || r.value).toLowerCase().includes((resp || "").toLowerCase()));
          if (escolha) OA.click(escolha);
        }
        continue;
      }
      // selects
      const sel = g.querySelector("select");
      if (sel && (!sel.value || /selecione|select|choose|--/i.test(sel.options[sel.selectedIndex]?.text || ""))) {
        const label = OA.labelFor(sel);
        if (label && !ehContato(label)) {
          const opcoes = [...sel.options].map((o) => o.text).filter((t) => t && !/selecione|select|choose|--/i.test(t));
          const resp = await responder(label, "SELECT", opcoes, vagaTitulo, vagaEmpresa);
          OA.selectOption(sel, resp);
        }
        continue;
      }
      // texto/numero
      const inp = g.querySelector("input.artdeco-text-input--input, input[type='text'], input[type='number'], textarea");
      if (inp && !inp.value) {
        const label = OA.labelFor(inp);
        if (label && !ehContato(label)) {
          const tipo = inp.type === "number" || /quantos|anos|years|how many/i.test(label) ? "NUMERO" : "TEXT";
          const resp = await responder(label, tipo, [], vagaTitulo, vagaEmpresa);
          OA.fillInput(inp, resp);
        }
      }
    }
  }

  function ehContato(label) {
    const l = (label || "").toLowerCase();
    return l.length < 40 && CONTATO.some((c) => l.includes(c));
  }

  async function responder(pergunta, tipo, opcoes, vagaTitulo, vagaEmpresa) {
    const r = await OA.bg({ type: "brain.answer", payload: { pergunta, tipo, opcoes, vagaTitulo, vagaEmpresa, idioma: "pt" } });
    return r?.resposta || "";
  }

  const sigModal = (m) => m ? m.innerText.length + "|" + m.querySelectorAll("input,select,textarea").length : "";

  // Processa o modal Easy Apply multi-step. Retorna 'enviado' | 'pausa_envio' | 'parou' | 'falhou'.
  async function processarModal(cfg, vagaTitulo, vagaEmpresa, idioma) {
    let semAvanco = 0;
    for (let step = 0; step < 16; step++) {
      if (!(await estaRodando())) return "parou";
      const modal = await OA.waitFor(SEL.modal, { timeout: 6000 });
      if (!modal) return document.querySelector(".artdeco-modal") ? "falhou" : "enviado";

      // desmarca "seguir empresa" (checkbox React escondido → setChecked)
      const follow = modal.querySelector(SEL.followCheckbox);
      if (follow && follow.checked) OA.setChecked(follow, false, modal);

      // Preenchedor ROBUSTO compartilhado: pega TODOS os campos do modal (text/number/
      // select/radio/checkbox/combobox), não um por grupo — era isso que deixava campos
      // em branco e travava o "Avançar" na validação.
      await OA.preencherCampos(modal, { vagaTitulo, vagaEmpresa, idioma, onStatus: (s) => status(s) });
      await OA.sleep(500);
      const sig = sigModal(modal);

      const submit = modal.querySelector(SEL.submit);
      if (submit && OA.isVisible(submit)) {
        // DESABILITADO = pergunta obrigatória pendente → NÃO clica às cegas (o clique
        // não faz nada e virava "incerto"): re-preenche com IA e tenta no próximo loop.
        if (semAvanco < 5 && (submit.disabled || submit.getAttribute("aria-disabled") === "true")) {
          semAvanco++;
          await status(`Pergunta obrigatória pendente — IA respondendo de novo… (${semAvanco})`);
          await OA.preencherCampos(modal, { vagaTitulo, vagaEmpresa, idioma, onStatus: (s) => status(s) });
          await OA.sleep(800);
          continue;
        }
        // LinkedIn Easy Apply NÃO tem CAPTCHA → envia de verdade. Depois VERIFICA:
        // sumiu o botão de enviar (modal avançou p/ confirmação) OU frase de sucesso =
        // enviado; senão "incerto" (não conta no dedup — evita falso-sucesso).
        OA.click(submit); await OA.sleep(2800);
        const txt = (document.body.innerText || "").toLowerCase();
        const confirmado = SUCESSO.some((f) => txt.includes(f)) || !document.querySelector(SEL.submit);
        return confirmado ? "enviado" : "incerto";
      }
      // Revisar / Avançar. Fallback por TEXTO (nem sempre tem aria-label). Pula botão
      // DESABILITADO (campo obrigatório pendente) → re-preenche em vez de morrer nele.
      const habil = (b) => b && OA.isVisible(b) && !b.disabled && b.getAttribute("aria-disabled") !== "true";
      const review = modal.querySelector(SEL.review) || OA.findByText(["revisar", "review"], { root: modal, sel: "button" });
      const next = modal.querySelector(SEL.next) || OA.findByText(["avançar", "next", "continuar", "continue", "próxima"], { root: modal, sel: "button" });
      const btn = habil(review) ? review : habil(next) ? next : null;
      if (!btn) {
        const presenteDesab = (review && OA.isVisible(review)) || (next && OA.isVisible(next));
        if (presenteDesab) {
          // botão existe mas está DESABILITADO → campo obrigatório faltando: re-preenche
          semAvanco++;
          await status(`Campo pendente (botão desabilitado) — re-preenchendo… (${semAvanco})`);
          await OA.preencherCampos(modal, { vagaTitulo, vagaEmpresa, idioma, onStatus: (s) => status(s) });
          await OA.sleep(600);
          if (semAvanco >= 5) { await status("Um campo obrigatório não consegui preencher — finalize no LinkedIn e ▶️.", "manual"); return "falhou"; }
          continue;
        }
        await status("Sem botão de avançar (upload obrigatório?) — pulei esta vaga.", "manual");
        return "falhou";
      }
      OA.click(btn);
      await OA.sleep(1500);

      const modal2 = document.querySelector(SEL.modal);
      if (modal2 && sigModal(modal2) === sig) {
        semAvanco++;
        await OA.preencherCampos(modal2, { vagaTitulo, vagaEmpresa, idioma, onStatus: (s) => status(s) });
        await OA.sleep(500);
        if (semAvanco >= 5) { await status("Campos pendentes que não consegui resolver — pulei a vaga.", "manual"); return "falhou"; }
      } else {
        semAvanco = 0;
      }
    }
    return "falhou";
  }

  // Fecha o modal de SUCESSO (pós-envio "Candidatura enviada"). Só clica o X/Concluído —
  // NUNCA o "Descartar" (isso jogava a candidatura fora, o "está descartando"). Se
  // aparecer o diálogo "Salvar candidatura?/Descartar", clica SALVAR (mantém), não descarta.
  function fecharModalSucesso() {
    const done = OA.findByText(["concluído", "done", "concluir"]) || document.querySelector(SEL.dismiss);
    if (done) OA.click(done);
    setTimeout(() => {
      const salvar = OA.findByText(["salvar", "save"], { sel: "button" });
      if (salvar) OA.click(salvar);
    }, 500);
  }

  async function estaRodando() {
    const r = await OA.bg({ type: "run.isRunning" });
    return r?.running && r?.platform === PLAT;
  }

  async function aplicarNaVaga(card, cfg) {
    const jobId = jobIdDoCard(card);
    if (jobId) {
      const dup = await OA.bg({ type: "stats.isApplied", platform: PLAT, jobId });
      if (dup?.aplicou) return "pulada";
    }
    const link = card.querySelector(SEL.cardLink) || card;
    OA.click(link);
    await OA.sleep(1800);

    const easy = await OA.waitFor(SEL.easyApply, { timeout: 5000 });
    if (!easy) return "sem_easy_apply";

    // Gate: filtro modalidade/região + match com currículo (limiar da plataforma).
    const desc = descricaoVaga();
    const vagaTitulo = tituloVaga();
    const vagaEmpresa = empresaVaga();
    const gate = await OA.deveAplicar(desc, { titulo: vagaTitulo, empresa: vagaEmpresa, platform: PLAT, pagina: (document.body.innerText || "").slice(0, 2500) });
    if (!gate.aplicar) { await status(`Pulei: ${gate.motivo}`.slice(0, 80)); return "sem_match"; }

    OA.click(easy);
    await OA.sleep(1500);
    const res = await processarModal(cfg, vagaTitulo, vagaEmpresa, gate.idioma || "pt");
    if (res === "enviado") {
      await OA.bg({ type: "stats.applied", platform: PLAT, jobId, titulo: vagaTitulo, empresa: vagaEmpresa });
      fecharModalSucesso();
      return "enviado";
    }
    // NÃO enviou → NUNCA descarta (era o bug "está descartando"). Deixa o modal aberto
    // pro humano finalizar. O loop pausa nesse caso.
    return res;
  }

  async function loop() {
    if (rodando) return;
    rodando = true;
    try {
      const cfg = await config();
      if (!cfg.openrouter.apiKey) { await status("⚠️ Configure a OpenRouter API key na dashboard da extensão."); return; }
      // SPA em aba de background renderiza devagar → espera os cards antes do loop
      // (com 0 cards ele concluía "Fim das páginas" na largada do "Iniciar tudo").
      await OA.waitFor(SEL.cards, { timeout: 25000 });

      let aplicadas = 0, pagina = 1;
      // PAGINAÇÃO: percorre as páginas de resultados (como o Selenium). Só vira de
      // página quando terminou a atual sem travar/pausar.
      while (await estaRodando()) {
        await status(`Página ${pagina}: buscando vagas…`);
        for (let i = 0; i < 4; i++) { window.scrollTo(0, document.body.scrollHeight); await OA.sleep(1000); }
        window.scrollTo(0, 0);
        const cards = [...document.querySelectorAll(SEL.cards)].filter((c) => jobIdDoCard(c));
        await status(`${cards.length} vagas na página ${pagina}. Aplicando…`);

        let parou = false;
        for (const card of cards) {
          if (!(await estaRodando())) { await status("Parado."); parou = true; break; }
          const can = await OA.bg({ type: "stats.canApply", platform: PLAT });
          if (can?.ok && !can.permitido) { await status(`Teto do dia atingido (${can.teto}).`); parou = true; break; }

          let r;
          try { r = await aplicarNaVaga(card, cfg); } catch (e) { r = "erro:" + e.message; }
          if (r === "enviado") { aplicadas++; await status(`✅ ${aplicadas} enviada(s). Próxima…`, "candidatou"); }
          else if (r === "pausa_envio" || r === "falhou" || r === "incerto") {
            await status("⏸️ Uma vaga precisou de ajuste — finalize no LinkedIn e clique ▶️ para seguir. (Não descartei.)");
            parou = true; break;
          }
          else await status(`Pulei uma vaga (${r}). Próxima…`);
          await OA.sleep(1500 + Math.random() * 1500);
        }
        if (parou) break; // travou/pausou/teto/parado → não vira de página

        // próxima página
        const next = document.querySelector("button[aria-label='View next page'], button[aria-label*='próxima'], .jobs-search-pagination__button--next, button.artdeco-pagination__button--next");
        if (!next || next.disabled || !OA.isVisible(next)) { await status(`Fim das páginas. ${aplicadas} candidatura(s).`); break; }
        OA.click(next); pagina++; await OA.sleep(3000);
      }
    } finally {
      rodando = false;
    }
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg?.type === "cs.kick" && msg.platform === PLAT) {
      loop();
      sendResponse({ ok: true });
    }
    return true;
  });

  // Se a página recarregar durante um run, retoma sozinho.
  OA.bg({ type: "run.isRunning" }).then((r) => { if (r?.running && r?.platform === PLAT) setTimeout(loop, 1500); });
})();
