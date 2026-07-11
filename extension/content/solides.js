// Solides — content script (vagas.solides.com.br + *.vagas.solides.com.br). SPA Next.js.
// Fluxo (igual ao padrão Gupy/Indeed: FILA na mesma aba, sem popup):
//   LISTA (vagas/todos/<query>): coleta os links dos cards (/vaga/ID/...) → fila →
//     navega de vaga em vaga na MESMA aba.
//   DETALHE (/vaga/ID/...): lê a descrição/badges → gate (modalidade/região + match).
//     Se bate → clica "Candidatura rápida" e roda o fluxo de candidatura:
//       - "Você possui indicação?" → Não
//       - "Deseja revisar seu currículo?" (Você está a 1 passo) → Sim
//       - "Habilidades para a vaga" (radios eliminatórios) → nível pelo CV (1 chamada)
//       - avança ("Avançar") / envia ("Efetuar candidatura")
//     Sucesso ("Candidatura realizada!") → "Ok, entendi!" → próxima vaga.
// pausarAntesEnvio (config) é respeitado: nas primeiras rodadas PARA antes do envio
// terminal pra você conferir as habilidades; depois desliga na dashboard.
(function () {
  const OA = window.OA, PLAT = "solides", QK = "oaSolidesQueue", PK = "oaSolidesPage", LK = "oaSolidesList";
  const running = async () => { const r = await OA.bg({ type: "run.isRunning" }); return r?.running && r?.platform === PLAT; };
  const status = (t, a) => OA.bg({ type: "status.push", platform: PLAT, status: t, action: a });
  const cfg = async () => (await OA.bg({ type: "config.get" })).config;
  const getQ = async () => (await chrome.storage.local.get(QK))[QK] || [];
  const setQ = (q) => chrome.storage.local.set({ [QK]: q });
  const log = (...a) => { try { console.log("[AutoApply][solides]", ...a); } catch (_) {} };

  const CARD_LINK = "a[href*='/vaga/']";
  const CTA_RAPIDA = ["candidatura rápida", "candidatura rapida"]; // NÃO "candidatura revisada"
  const INTERMED = ["avançar", "avancar", "continuar", "próximo", "proximo", "próxima", "proxima"];
  const SUBMIT = ["efetuar candidatura", "finalizar candidatura", "enviar candidatura", "concluir candidatura"];
  const SUCESSO = ["candidatura realizada", "candidatura com sucesso", "candidatura foi realizada", "recebemos sua candidatura", "candidatou com sucesso"];

  const listaUrl = () => { const u = new URL(location.href); return u.origin + u.pathname; };
  const assinatura = () => location.href + "|" + document.querySelectorAll("input,button,textarea,[role='radio']").length + "|" + (document.body.innerText || "").length;

  async function proximo() {
    if (!(await running())) { status("parado."); return; }
    const q = await getQ(); const next = q.shift(); await setQ(q);
    if (next) { location.href = next; return; } // volta pro "card" seguinte
    // fila vazia → próxima página da busca (?page=N — CHUTE; com fallback no botão)
    const base = (await chrome.storage.local.get(LK))[LK];
    let page = (await chrome.storage.local.get(PK))[PK] || 1;
    page += 1;
    if (!base || page > 40) { status("Fim das páginas. ✅"); await OA.bg({ type: "run.stop" }); return; }
    await chrome.storage.local.set({ [PK]: page });
    status(`Próxima página (${page})…`);
    location.href = `${base}?page=${page}`;
  }

  // ── Descrição/título p/ o gate ────────────────────────────────────────────────
  function coletarDescricao() {
    const partes = [];
    const loc = document.querySelector("[data-icon='location_on']")?.closest("p, div")?.innerText;
    if (loc) partes.push(loc);
    for (const b of document.querySelectorAll("[data-cy^='badges_']")) partes.push(b.innerText); // CLT/Presencial/Pleno/área
    const desc = document.querySelector("[data-cy='description'], .vacancy-description");
    if (desc) partes.push(desc.innerText);
    if (!partes.length) partes.push(((document.querySelector("main, article") || document.body).innerText || "").slice(0, 3500));
    return partes.join("\n").slice(0, 3500);
  }
  function tituloVaga() {
    const bc = [...document.querySelectorAll("nav li, ul li")].map((e) => e.innerText.trim()).filter(Boolean).pop();
    return (document.querySelector("h1")?.innerText || bc || document.title || "").trim().slice(0, 120);
  }

  // ── Preenchimentos específicos da Solides ─────────────────────────────────────
  // "Você possui indicação?" → Não. Acha um clicável "Não" cujo ancestral menciona
  // "indicação" (não temos o HTML exato — heurística defensiva).
  function marcarNaoIndicacao(root) {
    const candidatos = [...root.querySelectorAll("label, [role='radio'], button")].filter((e) => /^\s*n[ãa]o\s*$/i.test((e.innerText || "").trim()));
    for (const el of candidatos) {
      let ctx = el, achou = false;
      for (let i = 0; i < 4 && ctx; i++, ctx = ctx.parentElement) { if (/indicaç|possui indica/i.test(ctx.innerText || "")) { achou = true; break; } }
      if (!achou) continue;
      const forId = el.getAttribute && el.getAttribute("for");
      const rad = (forId && root.querySelector(`#${CSS.escape(forId)}`)) || el.querySelector?.("input[type='radio']");
      try { if (rad) { if (!rad.checked) OA.setChecked(rad, true, root); } else OA.click(el); } catch (_) {}
      log("indicação → Não");
      return true;
    }
    return false;
  }

  // Diálogo "Você está a 1 passo… Deseja revisar seu currículo?" → Sim (abre o passo
  // de habilidades/revisão). Cancelar NÃO conclui a candidatura, então clicamos Sim.
  function tratarDialogRevisar() {
    const t = (document.body.innerText || "").toLowerCase();
    if (!/deseja revisar seu currículo|você está a 1 passo|voce esta a 1 passo|revisar seu curr/.test(t)) return false;
    const sim = OA.findByText(["sim"], { sel: "button, a, [role='button']" });
    if (sim && OA.isVisible(sim)) { log("diálogo revisar currículo → Sim"); OA.click(sim); return true; }
    return false;
  }

  // "Habilidades para a vaga" — radios SEM name (id="skill-option-…", value 0..3, um
  // grupo por skill). Agrupa por fronteira de value==="0"; o nome da skill é um <label>
  // SEM [for] (as opções têm for="skill-option-…"). Nível vem do cérebro (1 chamada,
  // avaliado pelo CV); fallback honesto = "Básico".
  function gruposHabilidades(root) {
    const radios = [...root.querySelectorAll("input[type='radio'][id^='skill-option'], input[type='radio'][id*='skill-option']")];
    if (!radios.length) return [];
    const grupos = [];
    let atual = null;
    for (const r of radios) {
      if (r.value === "0" || !atual) { atual = { radios: [], label: "" }; grupos.push(atual); }
      atual.radios.push(r);
    }
    for (const g of grupos) {
      // o <label> do NOME da skill (sem [for]) fica ~6 níveis acima do <input>
      // (input → label[for] → .group → .flex-row → .flex-col → .border → .items-start).
      // Sobe até 8 e PARA no 1º label sem [for] (= o do grupo daquela skill).
      let node = g.radios[0];
      for (let i = 0; i < 8 && node; i++, node = node.parentElement) {
        const lab = [...(node.querySelectorAll?.("label") || [])].find((l) => !l.getAttribute("for") && (l.innerText || "").trim());
        if (lab) { g.label = lab.innerText.trim(); break; }
      }
    }
    return grupos.filter((g) => g.radios.length);
  }
  // Texto da OPÇÃO (Nenhum/Básico/Intermediário/Avançado): cada radio tem DOIS
  // label[for=id] — o que embrulha o input (vazio) e o com o texto. Pega o com texto.
  function textoOpcao(r) {
    if (r.id) { for (const l of document.querySelectorAll(`label[for="${CSS.escape(r.id)}"]`)) { const t = (l.innerText || "").trim(); if (t) return t; } }
    return (OA.labelFor(r) || r.value || "").trim();
  }
  async function responderHabilidades(root) {
    const grupos = gruposHabilidades(root);
    if (!grupos.length) return 0;
    const opcoesRef = grupos[0].radios.map(textoOpcao); // Nenhum/Básico/Intermediário/Avançado
    const skills = grupos.map((g) => g.label || "habilidade");
    let niveis = {};
    try { const r = await OA.bg({ type: "brain.skills", payload: { skills, opcoes: opcoesRef, vagaTitulo: tituloVaga() } }); niveis = (r && r.niveis) || {}; } catch (_) {}
    let n = 0;
    grupos.forEach((g, i) => {
      if (g.radios.some((r) => r.checked)) return;
      const opcoes = g.radios.map((r) => textoOpcao(r).toLowerCase());
      const alvo = String(niveis[i] || "").toLowerCase();
      let idx = alvo ? opcoes.findIndex((o) => o && (o.includes(alvo) || alvo.includes(o))) : -1;
      if (idx < 0) idx = Math.min(1, g.radios.length - 1); // fallback honesto = "Básico"
      try { OA.setChecked(g.radios[idx], true, root); n++; } catch (_) {}
    });
    log("habilidades:", n, "/", grupos.length, "| skills:", skills.slice(0, 3), "| opções:", opcoesRef);
    return n;
  }

  // Estamos DENTRO do fluxo de candidatura (recarregou no meio)?
  function ehFluxoCandidatura() {
    const t = (document.body.innerText || "").toLowerCase();
    return /possui indica|habilidades para a vaga|efetuar candidatura|finalizar candidatura|deseja revisar seu currículo|candidatura realizada/.test(t) ||
      !!document.querySelector("input[type='radio'][id^='skill-option']");
  }

  // Loop do fluxo (custom — NÃO usa rodarWizard: a Solides tem envio em 2 fases —
  // "Efetuar candidatura" pode abrir o diálogo de currículo/habilidades e só depois
  // envia de fato). Re-consulta o DOM a cada passo (cobre modal e rota SPA).
  async function fluxoCandidatura(pausar) {
    for (let step = 0; step < 14; step++) {
      if (!(await running())) return "parou";
      await OA.sleep(900);
      // sucesso? → "Ok, entendi!" e encerra
      if (SUCESSO.some((f) => (document.body.innerText || "").toLowerCase().includes(f))) {
        const ok = OA.findByText(["ok, entendi", "entendi", "ok"], { sel: "button, a, [role='button']" });
        if (ok && OA.isVisible(ok)) { OA.click(ok); await OA.sleep(800); }
        return "enviado";
      }
      const container = OA.melhorContainer("[role='dialog'], form, main");
      // preenche tudo que der (defensivo — nada aqui derruba o loop)
      try { tratarDialogRevisar(); } catch (e) { log("dialog erro:", e?.message); }
      try { marcarNaoIndicacao(container); } catch (e) { log("indicação erro:", e?.message); }
      try { await responderHabilidades(container); } catch (e) { log("habilidades erro:", e?.message); }
      try { await OA.preencherCampos(container, { idioma: "pt", onStatus: (s) => status(s) }); } catch (e) { log("preencher erro:", e?.message); }
      await OA.sleep(400);
      const sig = assinatura();

      // 1) botão INTERMEDIÁRIO (avançar/continuar) → clica e segue.
      const inter = OA.findByText(INTERMED, { sel: "button, a, [role='button']" });
      if (inter && OA.isVisible(inter)) {
        log("step", step, "intermediário:", (inter.innerText || "").trim().slice(0, 24));
        OA.click(inter); await OA.sleep(1400);
        if (assinatura() === sig) { OA.clickForte(inter); await OA.sleep(1400); }
        continue;
      }
      // 2) SUBMIT (efetuar/finalizar). Se houver "Revisar" ao lado = passo do referral
      // (2 fases: pode abrir o diálogo depois) → NÃO é o envio terminal → clica e segue.
      const submit = OA.findByText(SUBMIT, { sel: "button, a, [role='button']" });
      if (submit && OA.isVisible(submit)) {
        const revisar = OA.findByText(["revisar"], { sel: "button, a, [role='button']" });
        const terminal = !(revisar && OA.isVisible(revisar));
        if (terminal && pausar) { status("🔒 Revise (inclusive as habilidades) e clique “Efetuar candidatura”. Depois ▶️ para a próxima."); return "pausa"; }
        log("step", step, "submit:", (submit.innerText || "").trim().slice(0, 24), "| terminal:", terminal);
        OA.click(submit); await OA.sleep(1700);
        if (assinatura() === sig) { OA.clickForte(submit); await OA.sleep(1700); }
        continue;
      }
      // nada pra clicar → talvez já enviou
      if (SUCESSO.some((f) => (document.body.innerText || "").toLowerCase().includes(f))) continue;
      log("step", step, "sem botão de avançar/enviar — encerrando incerto");
      return "incerto";
    }
    return "incerto";
  }

  async function aplicarVaga() {
    const jk = location.pathname;
    const dup = await OA.bg({ type: "stats.isApplied", platform: PLAT, jobId: jk });
    if (dup?.aplicou) return "pulada";
    const can = await OA.bg({ type: "stats.canApply", platform: PLAT });
    if (can?.ok && !can.permitido) { await status(`Teto do dia (${can.teto}).`); await OA.bg({ type: "run.stop" }); return "teto"; }
    // AUTO-ENVIO (igual Gupy/GeekHunter, que ignoram o pausarAntesEnvio). Antes eu
    // pausava antes do "Efetuar candidatura"; mas o ▶️ REINICIA o run e LIMPA a fila →
    // voltava pra mesma vaga e pausava de novo, travando na 1ª ("não continua"). As
    // habilidades são avaliadas pelo CV e logadas no console; pra conferir antes de
    // disparar em massa, use um teto/dia baixo na dashboard.
    const pausar = false;

    // recarregou no meio do fluxo? → só roda o loop
    if (ehFluxoCandidatura() && !OA.findByText(CTA_RAPIDA, { sel: "button, a, [role='button']" })) return finalizar(await fluxoCandidatura(pausar));

    // detalhe: gate primeiro (modalidade/região + match)
    const gate = await OA.deveAplicar(coletarDescricao(), { titulo: tituloVaga(), platform: PLAT });
    if (!gate.aplicar) { await status(`Pulei: ${gate.motivo}`.slice(0, 80)); return "sem_match"; }

    // espera o botão aparecer (SPA carrega a vaga aos poucos) até ~6s
    let cta = null;
    for (let i = 0; i < 6 && !cta; i++) { cta = OA.findByText(CTA_RAPIDA, { sel: "button, a, [role='button']" }); if (cta && OA.isVisible(cta)) break; cta = null; await OA.sleep(1000); }
    if (!cta) { await status("Não achei 'Candidatura rápida' — me manda o HTML do botão."); log("CTA 'Candidatura rápida' não encontrado em", location.href); return "sem_cta"; }
    OA.click(cta); await OA.sleep(2400);
    return finalizar(await fluxoCandidatura(pausar));
  }
  function finalizar(r) {
    if (r === "enviado") OA.bg({ type: "stats.applied", platform: PLAT, jobId: location.pathname, titulo: tituloVaga() });
    return r;
  }

  let _kicked = false;
  async function iniciar() {
    if (_kicked) return; _kicked = true;
    if (!(await running())) return;
    if (!(await cfg()).openrouter.apiKey) return status("⚠️ Configure a OpenRouter key na dashboard.");
    await OA.sleep(1200);
    // detalhe da vaga OU dentro do fluxo?
    if (/\/vaga\//.test(location.pathname) || ehFluxoCandidatura() || OA.findByText(CTA_RAPIDA, { sel: "button, a, [role='button']" })) {
      let r;
      try { r = await aplicarVaga(); } catch (e) { log("erro:", e?.message); r = "erro"; }
      if (r === "pausa") return status("⏸️ Confirme o envio na Solides, depois ▶️ para seguir.");
      if (r === "teto") return;
      return proximo();
    }
    // lista: guarda a URL (paginação), coleta os cards e enfileira. SPA carrega os
    // cards aos poucos → tenta algumas vezes (scroll + espera) antes de desistir.
    await chrome.storage.local.set({ [LK]: listaUrl() });
    let links = [];
    for (let tent = 0; tent < 4 && !links.length; tent++) {
      for (let i = 0; i < 3; i++) { window.scrollTo(0, document.body.scrollHeight); await OA.sleep(900); }
      window.scrollTo(0, 0);
      links = [...new Set([...document.querySelectorAll(CARD_LINK)].map((a) => a.href).filter((h) => /\/vaga\//.test(h)))];
      if (!links.length) { log("cards não carregaram (tentativa", tent + 1, ") em", location.href); await OA.sleep(1200); }
    }
    if (!links.length) { await status("Sem vagas nesta página (cards não carregaram — layout diferente?). ✅"); await OA.bg({ type: "run.stop" }); return; }
    await setQ(links);
    await status(`${links.length} vaga(s) na fila. Aplicando (mesma aba)…`);
    log("fila:", links.length, "vagas");
    proximo();
  }

  log("carregado:", location.href); // se você NÃO vê esta linha no console, o script não foi injetado → recarregue a extensão + a aba
  chrome.runtime.onMessage.addListener((m, s, resp) => { if (m?.type === "cs.kick" && m.platform === PLAT) { iniciar(); resp?.({ ok: true }); } return true; });
  OA.bg({ type: "run.isRunning" }).then((r) => { if (r?.running && r?.platform === PLAT) setTimeout(iniciar, 1500); });
})();
