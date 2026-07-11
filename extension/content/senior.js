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
  const CARDS = "mat-card, [class*='vacancy'], [class*='vaga'], a[href*='/vacanc'], li[class*='card']";

  const acharCards = () => [...document.querySelectorAll(CARDS)].filter(OA.isVisible);
  function chaveVaga(card, titulo) {
    // Cód da vaga (dedup) ou fallback pelo título.
    const cod = (card.innerText.match(/c[óo]d[.:]?\s*([A-Za-z0-9\-]+)/i) || [])[1];
    return cod ? `senior-${cod}` : `senior-${(titulo || card.innerText || "").slice(0, 40).trim()}`;
  }

  async function loop() {
    if (!(await running())) return;
    const c = await cfg();
    if (!c.openrouter.apiKey) return status("⚠️ Configure a OpenRouter key na dashboard.");
    await OA.sleep(1200);

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

      OA.click(card); await OA.sleep(1500); // abre o detalhe (master-detail)

      const desc = (document.querySelector("[class*='description'], [class*='detail'], main")?.innerText || document.body.innerText || "").slice(0, 3500);
      const titulo = (document.querySelector("h1, [class*='title']")?.innerText || tituloCard || "").trim();
      const gate = await OA.deveAplicar(desc, { titulo, platform: PLAT });
      if (!gate.aplicar) { await status(`Pulei (${gate.motivo}): ${titulo.slice(0, 35)}`.slice(0, 80)); continue; }

      const cta = OA.findByText(["candidatar-se", "candidatar"], { sel: "button, a, [role='button']" });
      if (!cta) { await status("Sem botão Candidatar-se nesta vaga — pulei."); continue; }
      OA.click(cta); await OA.sleep(1800);

      const r = await OA.rodarWizard(() => document.querySelector("mat-dialog-container, form, [role='dialog'], main") || document.body, {
        avancar: ["avançar", "próximo", "próxima", "continuar", "salvar seus dados", "salvar meus dados", "next"],
        finalizar: ["enviar candidatura", "enviar", "finalizar", "concluir", "confirmar"],
        sucessoFrases: ["candidatura realizada", "candidatura foi realizada", "candidatura enviada com sucesso", "sua candidatura foi enviada", "inscrição realizada", "recebemos sua candidatura", "application submitted"],
        ctx: { idioma: gate.idioma || "pt" },
        pausarAntesEnvio: false, isRunning: running, onStatus: (s) => status(s),
      });
      if (r === "enviado") { await OA.bg({ type: "stats.applied", platform: PLAT, jobId: chave, titulo }); aplicadas++; await status(`✅ ${aplicadas} enviada(s).`, "candidatou"); }
      else if (r === "pausa") { await status("⏸️ Confirme o envio na Senior e clique ▶️."); break; }
      // fecha eventual modal/drawer pra voltar à lista
      const fechar = OA.findByText(["entendi", "fechar", "ok", "voltar"], { sel: "button" });
      if (fechar) OA.click(fechar);
      await OA.sleep(1200);
    }
    await status(`Fim. ${aplicadas} candidatura(s).`);
  }

  chrome.runtime.onMessage.addListener((m, s, resp) => { if (m?.type === "cs.kick" && m.platform === PLAT) { loop(); resp({ ok: true }); } return true; });
  OA.bg({ type: "run.isRunning" }).then((r) => { if (r?.running && r?.platform === PLAT) setTimeout(loop, 1500); });
})();
