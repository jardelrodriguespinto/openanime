// Indeed — content script. Roda logado no seu Chrome. Usa a URL canônica
// viewjob?jk= (a href /rc/clk dá "Security Check"), fila em storage que sobrevive à
// navegação (MV3), e o SmartApply cross-domain (smartapply.indeed.com) assume o form.

(function () {
  const OA = window.OA;
  const PLAT = "indeed";
  const host = location.host;
  const path = location.pathname;
  const QK = "oaIndeedQueue";
  const JK = "oaIndeedJob";

  const running = async () => { const r = await OA.bg({ type: "run.isRunning" }); return r?.running && r?.platform === PLAT; };
  const status = (txt, action) => OA.bg({ type: "status.push", platform: PLAT, status: txt, action });
  const cfg = async () => (await OA.bg({ type: "config.get" })).config;
  const getQueue = async () => (await chrome.storage.local.get(QK))[QK] || [];
  const setQueue = (q) => chrome.storage.local.set({ [QK]: q });
  const cookies = () => { const b = document.querySelector("#onetrust-accept-btn-handler"); if (b) b.click(); };

  const SK = "oaIndeedStart";
  async function proximo() {
    if (!(await running())) { status("parado."); return; }
    const q = await getQueue();
    const next = q.shift();
    await setQueue(q);
    if (next) { location.href = next; return; }
    // fila vazia → PRÓXIMA PÁGINA de resultados (como o Selenium paginava)
    let start = (await chrome.storage.local.get(SK))[SK] || 0;
    start += 10;
    if (start > 200) { status("Fim (limite de páginas). ✅"); await OA.bg({ type: "run.stop" }); return; }
    await chrome.storage.local.set({ [SK]: start });
    const c = await cfg();
    const qn = encodeURIComponent(c.plataformas?.indeed?.query || "desenvolvedor");
    status(`Próxima página (start=${start})…`);
    location.href = `https://br.indeed.com/jobs?q=${qn}&l=&from=searchOnHP&start=${start}`;
  }

  // ── SmartApply (cross-domain) ──────────────────────────────────────────────
  if (host.includes("smartapply")) {
    (async () => {
      if (!(await running())) return;
      const c = await cfg();
      for (let step = 0; step < 14; step++) {
        if (!(await running())) return;
        await OA.sleep(1200);
        if (document.querySelector("#returnToSearchButton, .ia-PostApply-ContinueFooter-button") ||
            /candidatura (enviada|foi enviada)|application submitted/i.test(document.body.innerText)) {
          const job = (await chrome.storage.local.get(JK))[JK] || {};
          await OA.bg({ type: "stats.applied", platform: PLAT, jobId: job.jobId, titulo: job.titulo });
          await status("✅ enviada. Próxima…", "candidatou");
          return proximo();
        }
        const container = document.querySelector(".ia-Questions, [class*='apply-questions'], main") || document.body;
        const job0 = (await chrome.storage.local.get(JK))[JK] || {};
        await OA.preencherCampos(container, { idioma: job0.idioma || "pt" });
        await OA.sleep(400);
        const submit = document.querySelector("button[name='submit-application'], [data-testid='submit-application-button']");
        if (submit && OA.isVisible(submit)) {
          if (c.pausarAntesEnvio) {
            try {
              await status("🔓 Resolvendo CAPTCHA com AssemblyAI…");
              const r = await OA.bg({ type: "assemblyia.match", payload: { audioUrl: "" } });
              if (r?.texto) {
                await status("✅ CAPTCHA transcrito, enviando…");
                await OA.sleep(1500);
                OA.click(submit);
                await OA.sleep(3000);
                continue;
              }
            } catch (e) {
              console.error(e);
            }
            await status("🔒 Revise/CAPTCHA — clique 'Enviar sua candidatura' você mesmo.");
            return;
          }
          OA.click(submit); await OA.sleep(3000); continue;
        }
        const cont = document.querySelector("[data-testid='continue-button']") || OA.findByText(["continuar", "continue", "revisar", "verificar"]);
        if (cont && OA.isVisible(cont)) { OA.click(cont); await OA.sleep(1500); continue; }
        await status("Passo do SmartApply não reconhecido — finalize à mão."); return;
      }
    })();
    return;
  }

  // ── Página da vaga (viewjob) ───────────────────────────────────────────────
  if (path.startsWith("/viewjob")) {
    (async () => {
      if (!(await running())) return;
      await OA.sleep(1500); cookies();
      const c = await cfg();
      const jk = new URLSearchParams(location.search).get("jk") || "";
      if (jk) { const dup = await OA.bg({ type: "stats.isApplied", platform: PLAT, jobId: jk }); if (dup?.aplicou) return proximo(); }
      const can = await OA.bg({ type: "stats.canApply", platform: PLAT });
      if (can?.ok && !can.permitido) { await status(`Teto do dia (${can.teto}).`); await OA.bg({ type: "run.stop" }); return; }

      const desc = (document.querySelector("#jobDescriptionText, .jobsearch-JobComponent-description")?.innerText || "");
      const titulo = (document.querySelector("h1.jobsearch-JobInfoHeader-title, h1")?.innerText || "").trim();
      const empresa = (document.querySelector("[data-testid='inlineHeader-companyName'], [data-company-name]")?.innerText || "").trim();
      const gate = await OA.deveAplicar(desc, { titulo, empresa, platform: PLAT });
      if (!gate.aplicar) { await status(`pulei: ${gate.motivo}`.slice(0, 80)); return proximo(); }
      // guarda o idioma p/ a aba do SmartApply responder no idioma certo
      await chrome.storage.local.set({ [JK]: { jobId: jk, titulo, empresa, idioma: gate.idioma || "pt" } });
      const apply = document.querySelector("#indeedApplyButton, .jobsearch-IndeedApplyButton-newDesign") ||
        OA.findByText(["candidatar-se com o indeed", "candidatura simplificada", "candidate-se facilmente"]);
      if (!apply) { await status(`sem candidatura simplificada: ${titulo}`); return proximo(); }
      await status(`Aplicando: ${titulo}`);
      OA.click(apply);
      await OA.sleep(5000);
      // Se a aplicação abriu em NOVA aba (viewjob continua aqui), segue a fila; a aba
      // do SmartApply cuida do form em paralelo.
      if (location.pathname.startsWith("/viewjob")) { await OA.sleep(1500); return proximo(); }
      // senão navegou pro SmartApply (mesma aba) → o handler de smartapply assume.
    })();
    return;
  }

  // ── Busca (/jobs) ──────────────────────────────────────────────────────────
  async function iniciar() {
    if (!(await running())) return;
    await OA.sleep(1500); cookies();
    if (!(await cfg()).openrouter.apiKey) { status("⚠️ Configure a OpenRouter key na dashboard."); return; }
    await status("Lendo vagas…");
    for (let i = 0; i < 4; i++) { window.scrollTo(0, document.body.scrollHeight); await OA.sleep(900); } window.scrollTo(0, 0);
    const cards = [...document.querySelectorAll("div.job_seen_beacon, .jobsearch-ResultsList > li, [data-jk]")];
    const jks = [];
    for (const c of cards) {
      const jk = c.querySelector("[data-jk]")?.getAttribute("data-jk") || c.getAttribute("data-jk") ||
        c.querySelector("a[href*='jk=']")?.href.match(/[?&]jk=([0-9A-Za-z]+)/)?.[1];
      if (jk && !jks.includes(jk)) jks.push(jk);
    }
    if (!jks.length) { await status("0 vagas na página."); return; }
    await setQueue(jks.map((jk) => `https://br.indeed.com/viewjob?jk=${jk}`));
    await status(`${jks.length} vagas na fila. Aplicando…`);
    proximo();
  }

  if (path.startsWith("/jobs")) {
    chrome.runtime.onMessage.addListener((msg, s, resp) => {
      if (msg?.type === "cs.kick" && msg.platform === PLAT) { iniciar(); resp({ ok: true }); }
      return true;
    });
    OA.bg({ type: "run.isRunning" }).then((r) => { if (r?.running && r?.platform === PLAT) setTimeout(async () => { const q = await getQueue(); q.length ? proximo() : iniciar(); }, 1500); });
  }
})();
