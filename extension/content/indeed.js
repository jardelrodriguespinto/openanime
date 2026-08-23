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

  // reCAPTCHA v2/Enterprise: o DESAFIO (bframe) está ABERTO? (iframe grande e visível). O
  // Enterprise NÃO popula #g-recaptcha-response, então "resolvido" = o desafio FECHOU
  // (recaptcha.js clicou Verificar) — detectamos por aqui, não pelo token.
  const desafioCaptcha = () => [...document.querySelectorAll("iframe")].some((f) =>
    /recaptcha\/(api2|enterprise)\/bframe/.test(f.src || "") && OA.isVisible(f) && f.getBoundingClientRect().height > 120);

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

  // Fluxo de envio do SmartApply (loop de passos: completou? → registra+próxima; senão
  // preenche, resolve captcha e clica "Enviar sua candidatura"/"Continuar"). Reutilizado em
  // DOIS contextos: (a) aba/página do smartapply.indeed.com; (b) INLINE na própria vaga
  // (viewjob), quando o Indeed Apply renderiza o preview + envio na MESMA página (mesma origem).
  async function fluxoSmartApply() {
    let tentEnvio = 0; // tentativas com o 'Enviar' DESABILITADO (revisão de campos)
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
      await OA.preencherCampos(container, { idioma: job0.idioma || "pt", onStatus: (s) => status(s) });
      await rsleep(600, 1700);
      const selSubmit = "button[name='submit-application'], [data-testid='submit-application-button']";
      let submit = document.querySelector(selSubmit);
      if (submit && OA.isVisible(submit)) {
        // CAPTCHA primeiro: pode ser a razão do botão estar desabilitado. O desafio abre
        // num iframe cross-origin e é resolvido pelo content/recaptcha.js DENTRO dele.
        // Enterprise (recaptcha.net) nunca gera token no doc principal → esperamos só o
        // DESAFIO ABRIR/FECHAR; checkbox parado sem desafio espera pouco e tenta clicar.
        if (desafioCaptcha() || (OA.captchaPresente() && !OA.captchaResolvido())) {
          await status("🔓 Resolvendo CAPTCHA (áudio) com AssemblyAI…");
          for (let w = 0; w < 32 && (desafioCaptcha() || (w < 12 && OA.captchaPresente() && !OA.captchaResolvido())); w++) {
            if (!(await running())) return;
            await OA.sleep(1500);
          }
          if (desafioCaptcha()) { await status("🔒 CAPTCHA aberto — resolva você mesmo e clique 'Enviar sua candidatura'."); return; }
          await status("✅ CAPTCHA ok, seguindo para o envio…");
          await rsleep(900, 1800);
        }
        submit = document.querySelector(selSubmit) || submit;
        // Botão DESABILITADO: ou captcha pendente (já esperado acima) ou CAMPO OBRIGATÓRIO
        // vazio/inválido. Antes a gente clicava mesmo assim, nada acontecia, os 14 passos
        // esgotavam em silêncio e caía pra próxima vaga SEM enviar. Agora: refaz o
        // preenchimento com IA a cada volta e, se persistir, LISTA os campos pendentes.
        if (submit.disabled || submit.getAttribute("aria-disabled") === "true") {
          tentEnvio++;
          if (tentEnvio > 5) {
            const pend = [...document.querySelectorAll("[aria-invalid='true'], input:invalid, select:invalid, textarea:invalid")]
              .filter(OA.isVisible).map((e) => OA.labelFor(e)).filter(Boolean).slice(0, 4);
            await status(`⚠️ 'Enviar' segue desabilitado${pend.length ? " — pendente: " + pend.join(" | ") : ""}. Finalize à mão.`);
            return;
          }
          await status(`'Enviar' desabilitado (${tentEnvio}/5) — revisando campos obrigatórios com IA…`);
          await rsleep(1200, 2400);
          continue;
        }
        await status("Enviando candidatura…");
        await rsleep(900, 2400); // "revisão humana" antes de enviar
        const s2 = document.querySelector(selSubmit) || submit;
        OA.click(s2); await rsleep(2400, 4200);
        if (OA.isVisible(s2) && !desafioCaptcha()) { OA.clickForte(s2); await rsleep(2200, 3600); }
        continue;
      }
      const cont = document.querySelector("[data-testid='continue-button']") || OA.findByText(["continuar", "continue", "revisar", "verificar"]);
      if (cont && OA.isVisible(cont)) { await rsleep(700, 2000); OA.click(cont); await rsleep(1300, 2800); continue; }
      await status("Passo do SmartApply não reconhecido — finalize à mão."); return;
    }
  }

  // ── SmartApply em aba/página própria (smartapply.indeed.com) ────────────────
  if (host.includes("smartapply")) {
    (async () => { if (!(await running())) return; await fluxoSmartApply(); })();
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
      const gate = await OA.deveAplicar(desc, { titulo, empresa, platform: PLAT, pagina: (document.body.innerText || "").slice(0, 2500) });
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
      // Ainda em /viewjob depois de clicar Aplicar → DOIS casos:
      // (a) INLINE (mesma origem): o Indeed Apply renderiza o preview + "Enviar sua
      //     candidatura" NESTA página (módulo mosaic-provider-apply-preview), sem navegar nem
      //     abrir aba. Se o botão de envio/continuar aparecer no doc, roda o fluxo AQUI mesmo.
      // (b) Abriu em NOVA ABA → aqui não há esse botão no doc → segue a fila (a aba do
      //     smartapply cuida do envio e registra a candidatura).
      if (location.pathname.startsWith("/viewjob")) {
        const inline = await OA.waitFor("button[name='submit-application'], [data-testid='submit-application-button'], [data-testid='continue-button']", { timeout: 6000 });
        if (inline) { await status(`Aplicando (na própria vaga): ${titulo}`.slice(0, 80)); return fluxoSmartApply(); }
        await rsleep(1200, 2600); return proximo();
      }
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
    // PRÉ-GATE POR IA NA LISTA: cada TÍTULO vai pro cérebro (brain.title) ANTES de
    // enfileirar — só entra na fila o que faz sentido com o perfil (área/senioridade).
    const vgs = [];
    for (const c of cards) {
      const jk = c.querySelector("[data-jk]")?.getAttribute("data-jk") || c.getAttribute("data-jk") ||
        c.querySelector("a[href*='jk=']")?.href.match(/[?&]jk=([0-9A-Za-z]+)/)?.[1];
      if (!jk || vgs.some((v) => v.jk === jk)) continue;
      vgs.push({ jk, titulo: (c.querySelector("h2.jobTitle, h2")?.innerText || "").trim() });
    }
    if (!vgs.length) { await status("0 vagas na página."); return; }
    await status(`Consultando a IA sobre ${vgs.length} título(s)…`);
    const ok = await OA.filtrarTitulos(vgs.map((v) => ({ titulo: v.titulo, ref: v.jk })));
    if (!ok.length) { await status("Nenhuma vaga da página bate com o seu perfil (filtro por IA). ✅"); return; }
    await setQueue(ok.map((v) => `https://br.indeed.com/viewjob?jk=${v.ref}`));
    await status(`${ok.length} vaga(s) na fila (${vgs.length - ok.length} fora do perfil). Aplicando…`);
    proximo();
  }

  if (path.startsWith("/jobs")) {
    // GUARDA de re-entrância: cs.kick do SW + auto-start disparam juntos no load →
    // dois fluxos na mesma aba (fila raspada/avançada 2x). Só o primeiro entra.
    let _fluxo = false;
    const umFluxo = async (fn) => { if (_fluxo) return; _fluxo = true; try { await fn(); } finally { _fluxo = false; } };
    chrome.runtime.onMessage.addListener((msg, s, resp) => {
      if (msg?.type === "cs.kick" && msg.platform === PLAT) { umFluxo(iniciar); resp({ ok: true }); }
      return true;
    });
    OA.bg({ type: "run.isRunning" }).then((r) => { if (r?.running && r?.platform === PLAT) setTimeout(() => umFluxo(async () => { const q = await getQueue(); return q.length ? proximo() : iniciar(); }), 1500); });
  }
})();
