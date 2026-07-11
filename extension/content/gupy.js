// Gupy — content script (portal.gupy.io + *.gupy.io das empresas). SPA.
// "Abrir outra aba" via clique é BLOQUEADO pelo Chrome (window.open sem gesto do
// usuário). Então usamos o padrão robusto do Indeed: coleta os links dos cards →
// FILA em storage → navega de vaga em vaga na MESMA aba (sem popup-blocker).
// Em cada vaga: clica "Candidatar-se" e roda o wizard. Pausa antes do "Finalizar".
(function () {
  const OA = window.OA, PLAT = "gupy", QK = "oaGupyQueue";
  const running = async () => { const r = await OA.bg({ type: "run.isRunning" }); return r?.running && r?.platform === PLAT; };
  const status = (t, a) => OA.bg({ type: "status.push", platform: PLAT, status: t, action: a });
  const cfg = async () => (await OA.bg({ type: "config.get" })).config;
  const getQ = async () => (await chrome.storage.local.get(QK))[QK] || [];
  const setQ = (q) => chrome.storage.local.set({ [QK]: q });

  const CARD_LINK = "[data-testid='job-list__listitem'] a[href], a[data-testid='job-cta-link'][href], a[href*='/job/'], a[href*='/jobs/'], a[href*='/vaga']";
  const CTA = ["candidatar-se", "candidatar", "aplicar", "quero me candidatar"];

  const PK = "oaGupyPage";
  const listaUrl = () => { const u = new URL(location.href); return u.origin + u.pathname; }; // /job-search/term=...
  async function proximo() {
    if (!(await running())) { status("parado."); return; }
    const q = await getQ(); const next = q.shift(); await setQ(q);
    if (next) { location.href = next; return; }
    // fila vazia → PRÓXIMA PÁGINA da busca (Gupy: ?page=N). Guardamos a URL da lista.
    const base = (await chrome.storage.local.get("oaGupyList")).oaGupyList;
    let page = (await chrome.storage.local.get(PK))[PK] || 1;
    page += 1;
    if (!base || page > 50) { status("Fim das páginas. ✅"); await OA.bg({ type: "run.stop" }); return; }
    await chrome.storage.local.set({ [PK]: page });
    status(`Próxima página (${page})…`);
    location.href = `${base}?page=${page}`;
  }

  // Pré-preenchimento ESPECÍFICO do Gupy, rodado ANTES do genérico a cada step do
  // wizard. Espelha o gupy_selenium.py passo a passo:
  //  - "Dados adicionais": referral ("Alguém indicou você?") e "Você trabalha na
  //    empresa?" → SEMPRE "Não" (honesto; não inventa indicação/relação).
  //  - "Onde você encontrou essa vaga? (Opcional)": combobox react-aria → escolhe a
  //    1ª opção do listbox (semelhante ao Selenium), nunca trava (é opcional).
  //  - As perguntas da empresa (MUI radio + textarea) são respondidas pelo genérico.
  async function preencherGupy(root) {
    // 1) Radios de "Dados adicionais": força "Não".
    for (const r of root.querySelectorAll("input[type='radio']")) {
      const nm = (r.name || "").toLowerCase();
      const fs = r.closest("fieldset");
      const leg = (fs?.querySelector("legend")?.innerText || "").toLowerCase();
      const ehReferral = "indicated" in nm || nm.includes("indicad") || leg.includes("indicad") || leg.includes("indicou");
      const ehFuncionario = nm.includes("companyemployee") || nm.includes("funcion") || leg.includes("trabalha na empresa");
      if (ehReferral || ehFuncionario) {
        const querNao = r.value === "no" || (r.getAttribute("data-testid") || "").toLowerCase().endsWith("no");
        if (querNao && !r.checked) { try { OA.setChecked(r, true, root); } catch (_) {} }
      }
    }
    // 2) Combobox opcional "Onde você encontrou essa vaga?" → 1ª opção do listbox.
    for (const cb of root.querySelectorAll("input[role='combobox'], [aria-haspopup='listbox']")) {
      const v = (cb.value || "").trim();
      const nome = (cb.getAttribute("name") || "").toLowerCase();
      const ph = (cb.getAttribute("placeholder") || "").toLowerCase();
      if (v || !(nome.includes("heard") || nome.includes("howdidyouhear") || ph.includes("onde você encontrou"))) continue;
      try {
        OA.click(cb); await OA.sleep(700);
        const opts = [...document.querySelectorAll("li[role='option'], [role='option'], .MuiAutocomplete-popper li")].filter((o) => OA.isVisible(o) && (o.innerText || "").trim());
        if (opts.length) OA.click(opts[0]); else document.body.click();
      } catch (_) { try { document.body.click(); } catch (_) {} }
    }
  }

  // Reconhece que JÁ estamos DENTRO do fluxo de candidatura (não é a página inicial da
  // vaga com "Candidatar-se"): a tela "Olá Jardel, vamos continuar sua candidatura?" só
  // tem "Continuar" (sem "Candidatar-se"), e os steps têm "Salvar e continuar"/"Responder
  // agora"/"Finalizar candidatura". Sem isto, iniciar/retomar tratava como LISTA e parava.
  const APP_RE = /vamos continuar sua candidatura|dados adicionais|perguntas criadas pela empresa|finalizar candidatura|personalizar candidatura/i;
  const ehTelaCandidatura = () =>
    APP_RE.test(document.body.innerText || "") ||
    !!OA.findByText(["salvar e continuar", "responder agora", "finalizar candidatura"], { sel: "a, button, [role='button']" }) ||
    (!!OA.findByText(["continuar"], { sel: "button, a, [role='button']" }) && !document.querySelector(CARD_LINK));

  // Roda SÓ o wizard (quando já estamos na candidatura). Envio confirmado → stats.applied.
  async function rodarWizardGupy(idioma) {
    const r = await OA.rodarWizard(() => OA.melhorContainer("form, [role='dialog'], main"), {
      avancar: ["salvar e continuar", "responder agora", "continuar", "próximo", "próxima", "avançar", "next"],
      avancarSel: ["button[name='saveAndContinueButton']", "button[aria-label='Responder agora']"],
      finalizar: ["finalizar candidatura", "finalizar", "concluir"],
      finalizarSel: ["#dialog-give-up-personalization-step"],
      sucessoFrases: ["candidatura realizada", "candidatura foi realizada", "inscrição realizada", "você se candidatou", "sua candidatura foi enviada", "recebemos sua candidatura", "application submitted", "you have applied"],
      ctx: { idioma: idioma || "pt" },
      preencher: preencherGupy,
      preferUltimo: true, // Gupy repete o botão (sticky + rodapé): clica o do rodapé
      pausarAntesEnvio: false, isRunning: running, onStatus: (s) => status(s),
    });
    if (r === "enviado") await OA.bg({ type: "stats.applied", platform: PLAT, jobId: location.pathname, titulo: document.title });
    return r;
  }

  async function aplicarVagaAtual(c) {
    // dedup: já aplicou nesta vaga? (pula em re-scrape de páginas)
    const jk = location.pathname;
    const dup = await OA.bg({ type: "stats.isApplied", platform: PLAT, jobId: jk });
    if (dup?.aplicou) return "pulada";
    // Gate: modalidade/região + match (o Gupy não tinha filtro na extensão).
    const desc = (document.querySelector("[data-testid='job-description'], main, article")?.innerText || document.body.innerText || "").slice(0, 3500);
    const gate = await OA.deveAplicar(desc, { titulo: document.title, platform: PLAT });
    if (!gate.aplicar) { await status(`Pulei: ${gate.motivo}`.slice(0, 80)); return "sem_match"; }
    const cta = OA.findByText(CTA, { sel: "a, button, [role='button']" });
    if (cta && OA.isVisible(cta)) { OA.click(cta); await OA.sleep(2000); }
    return rodarWizardGupy(gate.idioma);
  }

  async function iniciar() {
    if (!(await running())) return;
    if (!(await cfg()).openrouter.apiKey) return status("⚠️ Configure a OpenRouter key na dashboard.");
    await OA.sleep(1200);
    // Já é uma página de vaga? (tem CTA candidatar-se) → aplica e segue a fila.
    if (OA.findByText(CTA, { sel: "a, button, [role='button']" })) {
      const c = await cfg();
      let r;
      try { r = await aplicarVagaAtual(c); } catch (e) { try { console.log("[AutoApply][gupy] erro:", e?.message); } catch (_) {} r = "erro"; }
      if (r === "pausa") return status("⏸️ Confirme o envio no Gupy, depois ▶️ para seguir.");
      return proximo(); // SEMPRE segue pra próxima (não trava/para a automação)
    }
    // JÁ dentro da candidatura ("vamos continuar", dados adicionais, perguntas)? → wizard.
    if (ehTelaCandidatura()) {
      let r;
      try { r = await rodarWizardGupy("pt"); } catch (e) { try { console.log("[AutoApply][gupy] erro:", e?.message); } catch (_) {} r = "erro"; }
      if (r === "pausa") return status("⏸️ Confirme o envio no Gupy, depois ▶️ para seguir.");
      return proximo();
    }
    // Lista: guarda a URL da lista (p/ paginar), coleta os links e enfileira.
    await chrome.storage.local.set({ oaGupyList: listaUrl() });
    for (let i = 0; i < 3; i++) { window.scrollTo(0, document.body.scrollHeight); await OA.sleep(900); } window.scrollTo(0, 0);
    const links = [...new Set([...document.querySelectorAll(CARD_LINK)].map((a) => a.href).filter(Boolean))];
    if (!links.length) {
      // sem cards nesta página → acabou (ou fim da paginação)
      await status("Sem mais vagas. ✅"); await OA.bg({ type: "run.stop" }); return;
    }
    await setQ(links);
    await status(`${links.length} vagas na fila (página). Aplicando (mesma aba)…`);
    proximo();
  }

  async function retomar() {
    if (!(await running())) return;
    const c = await cfg();
    if (OA.findByText(CTA, { sel: "a, button, [role='button']" })) {
      let r;
      try { r = await aplicarVagaAtual(c); } catch (e) { try { console.log("[AutoApply][gupy] erro:", e?.message); } catch (_) {} r = "erro"; }
      if (r === "pausa") return status("⏸️ Confirme o envio no Gupy, depois ▶️ para seguir.");
      return proximo();
    }
    if (ehTelaCandidatura()) {
      let r;
      try { r = await rodarWizardGupy("pt"); } catch (e) { try { console.log("[AutoApply][gupy] erro:", e?.message); } catch (_) {} r = "erro"; }
      if (r === "pausa") return status("⏸️ Confirme o envio no Gupy, depois ▶️ para seguir.");
      return proximo();
    }
    iniciar();
  }

  chrome.runtime.onMessage.addListener((m, s, resp) => { if (m?.type === "cs.kick" && m.platform === PLAT) { iniciar(); resp({ ok: true }); } return true; });
  OA.bg({ type: "run.isRunning" }).then(async (r) => { if (r?.running && r?.platform === PLAT) { const q = await getQ(); setTimeout(q.length ? retomar : iniciar, 1500); } });
})();
