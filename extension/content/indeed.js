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

  // Delays HUMANOS: sleeps randômicos p/ as ações não saírem em cadência de robô e as
  // candidaturas ficarem ESPAÇADAS (anti-bloqueio). rint = inteiro aleatório em [a,b].
  const rint = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
  const rsleep = (a, b) => OA.sleep(rint(a, b));

  const SK = "oaIndeedStart";
  async function proximo() {
    if (!(await running())) { status("parado."); return; }
    const q = await getQueue();
    const next = q.shift();
    await setQueue(q);
    // Espaçamento HUMANO entre vagas: pausa randômica (~6–16s) antes de abrir a próxima —
    // é o principal freio anti-bloqueio (não dispara uma candidatura logo atrás da outra).
    await status("Aguardando um pouco antes da próxima vaga…");
    await rsleep(6000, 16000);
    if (!(await running())) { status("parado."); return; }
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
        await rsleep(1100, 2600);
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
        await rsleep(600, 1700);
        const submit = document.querySelector("button[name='submit-application'], [data-testid='submit-application-button']");
        if (submit && OA.isVisible(submit)) {
          // Indeed pode exigir reCAPTCHA no envio. O desafio abre num iframe cross-origin
          // (google.com/recaptcha/bframe): o áudio e o #audio-response ficam LÁ e são
          // preenchidos por content/recaptcha.js, injetado NESSE frame. Daqui só dá pra
          // ler o token no doc principal (#g-recaptcha-response) — esperamos ele aparecer
          // (= desafio resolvido) e então enviamos.
          if (OA.captchaPresente() && !OA.captchaResolvido()) {
            await status("🔓 Resolvendo CAPTCHA (áudio) com AssemblyAI…");
            let ok = false;
            for (let w = 0; w < 24 && !(ok = OA.captchaResolvido()); w++) {
              if (!(await running())) return;
              await OA.sleep(1500); // ~36s aguardando o solver do iframe
            }
            if (!ok) { await status("🔒 CAPTCHA — resolva você mesmo e clique 'Enviar sua candidatura'."); return; }
            await status("✅ CAPTCHA resolvido, enviando…");
            await OA.sleep(600);
          }
          // Envio AUTOMÁTICO no Indeed (a pedido): clica 'Enviar sua candidatura' mesmo com
          // "pausar antes do envio" LIGADO — o captcha já foi resolvido acima. Na 1ª passada
          // o captcha ainda não apareceu → clica, o desafio abre, o loop reavalia e reenvia.
          // Reforça com clique FORTE se o botão mosaic (styled-components) ignorar o .click().
          await status("Enviando candidatura…");
          await rsleep(900, 2400); // "revisão humana" antes de enviar
          OA.click(submit); await rsleep(2400, 4200);
          if (OA.isVisible(submit) && !OA.captchaPresente()) { OA.clickForte(submit); await rsleep(2200, 3600); }
          continue;
        }
        const cont = document.querySelector("[data-testid='continue-button']") || OA.findByText(["continuar", "continue", "revisar", "verificar"]);
        if (cont && OA.isVisible(cont)) { await rsleep(700, 2000); OA.click(cont); await rsleep(1300, 2800); continue; }
        await status("Passo do SmartApply não reconhecido — finalize à mão."); return;
      }
    })();
    return;
  }

  // ── Página da vaga (viewjob) ───────────────────────────────────────────────
  if (path.startsWith("/viewjob")) {
    (async () => {
      if (!(await running())) return;
      await rsleep(1400, 3400); cookies();
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
      // "lê a vaga" antes de clicar (rolagem leve + pausa) — parece humano, não robô instantâneo.
      await status(`Lendo a vaga: ${titulo}`.slice(0, 80));
      try { window.scrollTo(0, rint(200, 700)); } catch (_) {}
      await rsleep(1800, 5000);
      await status(`Aplicando: ${titulo}`);
      OA.click(apply);
      await rsleep(4000, 7000);
      // Se a aplicação abriu em NOVA aba (viewjob continua aqui), segue a fila; a aba
      // do SmartApply cuida do form em paralelo.
      if (location.pathname.startsWith("/viewjob")) { await rsleep(1200, 2600); return proximo(); }
      // senão navegou pro SmartApply (mesma aba) → o handler de smartapply assume.
    })();
    return;
  }

  // ── Busca (/jobs) ──────────────────────────────────────────────────────────
  async function iniciar() {
    if (!(await running())) return;
    await rsleep(1400, 3200); cookies();
    if (!(await cfg()).openrouter.apiKey) { status("⚠️ Configure a OpenRouter key na dashboard."); return; }
    await status("Lendo vagas…");
    for (let i = 0; i < 4; i++) { window.scrollTo(0, document.body.scrollHeight); await rsleep(700, 1600); } window.scrollTo(0, 0);
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
