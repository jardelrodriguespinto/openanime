// Senior — content script (portaldetalentos.senior.com.br). SPA Angular Material,
// master-detail na MESMA página. Fiel ao senior_selenium.py: PERCORRE todos os cards
// (idx, re-busca a cada iteração), dedup por Cód/texto, gate (modalidade/região +
// match), wizard "Avançar" (mat-select em cdk-overlay + mat-checkbox consentimento) →
// Enviar/Finalizar. Paginação: quando esgota, clica próxima/carrega mais.
(function () {
  const OA = window.OA, PLAT = "senior";
  const running = async () => { const r = await OA.bg({ type: "run.isRunning" }); return r?.running && r?.platform === PLAT; };
  const status = (t, a) => OA.bg({ type: "status.push", platform: PLAT, status: t, action: a });
  const cfg = async () => (await OA.bg({ type: "config.get" })).config;
  const rand = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
  const rsleep = (a, b) => OA.sleep(rand(a, b));
  // Card = título `p.customized-card-job-function` (do senior_selenium.py, testado). Os
  // "mat-card/[class*=vaga]" antigos eram chute e não achavam nada.
  const CARDS = "p.customized-card-job-function, app-vacancy-card p.customized-card-job-function, [class*='customized-card-job-function']";

  const acharCards = () => [...document.querySelectorAll(CARDS)].filter(OA.isVisible);

  // Hook por-passo: marca o consentimento "Li e estou ciente com o termo de uso e
  // privacidade" (mat-checkbox formcontrolname=termOfUse, input cdk-visually-hidden) ANTES de
  // Avançar — senão o botão fica desabilitado e trava. Varre o DOCUMENTO (não só o container).
  async function preencherSenior() {
    let cbs = [...document.querySelectorAll("mat-checkbox[formcontrolname*='term' i] input[type='checkbox'], mat-checkbox[formcontrolname*='Term'] input[type='checkbox']")];
    if (!cbs.length) {
      cbs = [...document.querySelectorAll("mat-checkbox input[type='checkbox'], input[type='checkbox']")].filter((c) => {
        const t = (c.closest("mat-checkbox, label, .card-container, form")?.innerText || "").toLowerCase();
        return /li e estou|ciente|termo de uso|privacidade|aceito o termo/.test(t);
      });
    }
    for (const cb of cbs) {
      if (cb.checked) continue;
      try { OA.setChecked(cb, true, document); } catch (_) {}
      await OA.sleep(rand(250, 600));
      if (cb.checked) continue;
      // mat-checkbox às vezes ignora o .click() simples (handler só reage à sequência
      // real de ponteiro) → clique FORTE no label/host/input até o input marcar.
      const host = cb.closest("mat-checkbox, mat-mdc-checkbox");
      for (const alvo of [host?.querySelector("label"), host, cb]) {
        if (!alvo) continue;
        OA.clickForte(alvo); await OA.sleep(400);
        if (cb.checked) break;
      }
      if (!cb.checked) await status("⚠️ Não consegui marcar o termo de uso — marque manualmente e a automação segue.");
    }
  }
  // Pós-"Enviar": a Senior abre o diálogo "Deseja salvar seus dados?" (Não salvar /
  // Salvar seus dados) e SÓ DEPOIS a tela de sucesso .applied-with-success ("Parabéns!
  // Você se candidatou…") com "Fechar". O wizard já saiu nesse ponto (no clique do
  // Enviar a frase de sucesso ainda não existia → "incerto") e ninguém clicava nos
  // diálogos — a automação parava aqui. Trata os dois e CONFIRMA o envio de verdade.
  async function fecharPosEnvio() {
    let sucesso = false, ocioso = 0;
    for (let i = 0; i < 10; i++) {
      await OA.sleep(1200);
      const body = document.body.innerText || "";
      if (/deseja salvar seus dados/i.test(body)) {
        ocioso = 0;
        // "Salvar seus dados" agiliza as próximas candidaturas; fallback "Não salvar".
        const b = OA.findByText(["salvar seus dados", "salvar meus dados"], { sel: "button" }) ||
          OA.findByText(["não salvar", "nao salvar"], { sel: "button, a, [role='button']" });
        if (b) { OA.click(b); await OA.sleep(900); if (/deseja salvar seus dados/i.test(document.body.innerText || "")) { OA.clickForte(b); await OA.sleep(900); } }
        continue;
      }
      const box = document.querySelector(".applied-with-success");
      if (box || /você se candidatou para uma vaga|parab[ée]ns! você se candidatou/i.test(body)) {
        sucesso = true;
        const fechar = (box && [...box.querySelectorAll("button")].find((b) => /fechar/i.test(b.innerText || ""))) ||
          OA.findByText(["fechar"], { sel: "button" });
        if (fechar) { OA.click(fechar); await OA.sleep(900); if (document.querySelector(".applied-with-success")) { OA.clickForte(fechar); await OA.sleep(900); } }
        break;
      }
      if (++ocioso >= 2) break; // nenhum dos diálogos apareceu → nada a fazer aqui
    }
    return sucesso;
  }

  function chaveVaga(card, titulo) {
    // Cód da vaga (dedup) ou fallback pelo título.
    const cod = (card.innerText.match(/c[óo]d[.:]?\s*([A-Za-z0-9\-]+)/i) || [])[1];
    return cod ? `senior-${cod}` : `senior-${(titulo || card.innerText || "").slice(0, 40).trim()}`;
  }

  // GUARDA de re-entrância: cs.kick do SW + auto-start disparam JUNTOS no load → DOIS
  // loops na mesma aba (um clicava outro card enquanto o outro estava no wizard e a
  // etapa do termo/checkbox ficava abandonada). Só o primeiro entra.
  let _loop = false;
  async function loop() {
    if (_loop) return;
    _loop = true;
    try { await loopInterno(); } finally { _loop = false; }
  }

  async function loopInterno() {
    if (!(await running())) return;
    const c = await cfg();
    if (!c.openrouter.apiKey) return status("⚠️ Configure a OpenRouter key na dashboard.");
    await OA.sleep(1200);
    // SPA em aba de background renderiza devagar → espera os cards antes de concluir
    // "fim" com 0 cards (no "Iniciar tudo" o loop encerrava na largada).
    await OA.waitFor(CARDS, { timeout: 25000 });

    let idx = 0, aplicadas = 0;
    const vistos = new Set();
    while (await running()) {
      window.scrollTo(0, document.body.scrollHeight); await OA.sleep(1000);
      const cards = acharCards();
      if (idx >= cards.length) {
        // esgotou a página → próxima (botão) ou fim
        const next = OA.findByText(["próxima", "próximo", "next"], { sel: "button, a, [aria-label]" }) ||
          document.querySelector("[aria-label*='próxima' i], [aria-label*='next' i], .mat-paginator-navigation-next:not([disabled])");
        if (next && OA.isVisible(next) && !next.disabled) { await status("Próxima página…"); OA.click(next); await OA.sleep(2500); idx = 0; continue; }
        break;
      }
      const can = await OA.bg({ type: "stats.canApply", platform: PLAT });
      if (can?.ok && !can.permitido) { await status(`Teto do dia (${can.teto}).`); break; }

      const card = cards[idx]; idx++;
      const tituloCard = (card.innerText || "").slice(0, 60);
      const chave = chaveVaga(card, tituloCard);
      if (vistos.has(chave)) continue;
      vistos.add(chave);
      const dup = await OA.bg({ type: "stats.isApplied", platform: PLAT, jobId: chave });
      if (dup?.aplicou) continue;

      await rsleep(1500, 4000); // "ler o card" antes de abrir (humano)
      OA.click(card); await rsleep(2000, 4000); // abre o detalhe (master-detail) + pausa leve pra SPA assentar

      const desc = (document.querySelector("[class*='description'], [class*='detail'], main")?.innerText || document.body.innerText || "").slice(0, 3500);
      const titulo = (document.querySelector("h1, [class*='title']")?.innerText || tituloCard || "").trim();
      const gate = await OA.deveAplicar(desc, { titulo, platform: PLAT, pagina: (document.body.innerText || "").slice(0, 2500) });
      if (!gate.aplicar) { await status(`Pulei (${gate.motivo}): ${titulo.slice(0, 35)}`.slice(0, 80)); continue; }

      // Candidatar-se: seletores testados do senior_selenium.py + fallback por texto.
      const cta = document.querySelector("#applyToVacancy button, button.apply-to-candidature-button") ||
        OA.findByText(["candidatar-se", "candidatar"], { sel: "button, a, [role='button']" });
      if (!cta) { await status("Sem botão Candidatar-se nesta vaga — pulei."); continue; }
      await rsleep(800, 2000); OA.click(cta); await rsleep(1800, 3500);

      // melhorContainer (não querySelector do 1º form, que pode ser um form OCULTO/errado).
      // preencherSenior marca o consentimento "Li e estou ciente…" antes de Avançar.
      const r = await OA.rodarWizard(() => OA.melhorContainer("mat-dialog-container, form, [role='dialog'], main"), {
        avancar: ["avançar", "próximo", "próxima", "continuar", "salvar seus dados", "salvar meus dados", "next"],
        finalizar: ["enviar candidatura", "enviar", "finalizar", "concluir", "confirmar"],
        sucessoFrases: ["candidatura realizada", "candidatura foi realizada", "candidatura enviada com sucesso", "sua candidatura foi enviada", "inscrição realizada", "recebemos sua candidatura", "application submitted", "você se candidatou"],
        preencher: preencherSenior,
        ctx: { idioma: gate.idioma || "pt", destravarGrupoCheckbox: true },
        pausarAntesEnvio: false, isRunning: running, onStatus: (s) => status(s),
      });
      if (r === "pausa") { await status("⏸️ Confirme o envio na Senior e clique ▶️."); break; }
      // Depois do Enviar vêm o "Deseja salvar seus dados?" e a tela "Parabéns! Você se
      // candidatou…" — o wizard sai antes deles (r vira "incerto"/"falhou"), então o
      // envio real é confirmado AQUI pela tela de sucesso.
      const okPos = await fecharPosEnvio();
      if (r === "enviado" || okPos) { await OA.bg({ type: "stats.applied", platform: PLAT, jobId: chave, titulo }); aplicadas++; await status(`✅ ${aplicadas} enviada(s).`, "candidatou"); }
      // fecha eventual modal/drawer remanescente e VOLTA pra listagem das vagas
      const fechar = OA.findByText(["entendi", "fechar", "ok", "voltar"], { sel: "button" });
      if (fechar) OA.click(fechar);
      await OA.sleep(1200);
      if (!acharCards().length) { try { history.back(); } catch (_) {} await OA.sleep(2200); }
    }
    await status(`Fim. ${aplicadas} candidatura(s).`);
  }

  chrome.runtime.onMessage.addListener((m, s, resp) => { if (m?.type === "cs.kick" && m.platform === PLAT) { loop(); resp({ ok: true }); } return true; });
  OA.bg({ type: "run.isRunning" }).then((r) => { if (r?.running && r?.platform === PLAT) setTimeout(loop, 1500); });
})();
