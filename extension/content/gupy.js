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
  // sleeps HUMANOS: a automação estava rápida demais (avançava antes de assentar).
  const rand = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
  const rsleep = (a, b) => OA.sleep(rand(a, b));

  const PK = "oaGupyPage";
  const listaUrl = () => { const u = new URL(location.href); return u.origin + u.pathname; }; // /job-search/term=...
  async function proximo() {
    if (!(await running())) { status("parado."); return; }
    const q = await getQ(); const next = q.shift(); await setQ(q);
    // espaçamento humano entre vagas (não abre uma logo atrás da outra)
    await status("Aguardando um pouco antes da próxima vaga…");
    await rsleep(5000, 13000);
    if (!(await running())) { status("parado."); return; }
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
  // Diálogo "Olá …, vamos continuar sua candidatura?" (botão sc-* que costuma IGNORAR
  // o .click() simples): antes eram só 1 clique + 1 reforço dentro do hook — se o botão
  // não reagisse, o wizard estourava o "travado" e PULAVA a vaga sem passar do diálogo.
  // Agora: acha o MENOR container do diálogo (não clica um "continuar" de fora), match
  // EXATO em "Continuar" preferindo o ÚLTIMO, e insiste com clique FORTE em loop até o
  // diálogo sumir. Roda no hook do wizard E na entrada do fluxo (o diálogo pode vir já
  // no load da vaga, antes de qualquer CTA).
  async function tratarDialogContinuar() {
    const aberto = () => /vamos continuar sua candidatura/i.test(document.body.innerText || "");
    if (!aberto()) return false;
    for (let t = 0; t < 4 && aberto(); t++) {
      // containers em ordem de documento: o ÚLTIMO que contém o texto é o mais interno
      const boxes = [...document.querySelectorAll("[role='dialog'], section, div")]
        .filter((d) => OA.isVisible(d) && /vamos continuar sua candidatura/i.test(d.innerText || "") && d.querySelector("button, a, [role='button']"));
      const box = boxes[boxes.length - 1] || document;
      const conts = [...box.querySelectorAll("button, a, [role='button']")]
        .filter((b) => OA.isVisible(b) && !b.disabled && /^continuar$/i.test(((b.innerText || b.textContent || b.getAttribute("aria-label") || "")).trim()));
      const alvo = conts[conts.length - 1];
      if (!alvo) break;
      if (t === 0) OA.click(alvo); else OA.clickForte(alvo); // 1º normal; depois só FORTE
      await OA.sleep(1800);
    }
    return !aberto();
  }

  async function preencherGupy(root) {
    // -2) Banner de cookies/LGPD (ACEITAR / NÃO, OBRIGADO) fica POR CIMA e intercepta
    //     cliques nos botões do fluxo → fecha antes de qualquer coisa.
    try { OA.fecharBanners(); } catch (_) {}
    // -1) Diálogo "vamos continuar sua candidatura?" → resolve JÁ AQUI (o wizard
    //    prioriza "salvar e continuar" e o sticky ATRÁS do modal continua visível →
    //    sem isto o Continuar do diálogo nunca era clicado).
    await tratarDialogContinuar();
    // 0) Prompt "Perguntas criadas pela empresa" → clica "Responder agora" JÁ AQUI.
    //    O wizard priorizava o "Salvar e continuar" (sticky do passo de trás, ainda
    //    visível) e nunca chegava no prompt → a automação travava nessa tela.
    const ra = document.querySelector("button[aria-label='Responder agora']") ||
      OA.findByText(["responder agora"], { sel: "a, button, [role='button']" });
    if (ra && OA.isVisible(ra) && !ra.disabled) {
      OA.click(ra); await OA.sleep(1500);
      if (OA.isVisible(ra)) { OA.clickForte(ra); await OA.sleep(1500); }
    }
    // 1) Radios de "Dados adicionais": força "Não".
    for (const r of root.querySelectorAll("input[type='radio']")) {
      const nm = (r.name || "").toLowerCase();
      const fs = r.closest("fieldset");
      const leg = (fs?.querySelector("legend")?.innerText || "").toLowerCase();
      // (era `"indicated" in nm` — TypeError em string; o catch do wizard engolia e
      // o hook morria aqui, pulando combobox + perguntas MUI neste passo)
      const ehReferral = nm.includes("indicated") || nm.includes("indicad") || leg.includes("indicad") || leg.includes("indicou");
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
    // 3) "Perguntas criadas pela empresa": as opções são CHECKBOX MUI (input oculto dentro
    //    de <label>), agrupadas por <h3> — inclusive Sim/Não. O preenchedor genérico trata
    //    cada opção como um booleano isolado → marca opção errada/várias OU re-clica e
    //    DESMARCA (toggle) → o grupo obrigatório fica vazio e o "Salvar e continuar" trava.
    await responderPerguntasMui(root);
  }

  // Responde as "Perguntas criadas pela empresa" (opções = CHECKBOX MUI, input oculto dentro
  // de <label>). AGRUPA pelo `name` do input ("checkbox-<idPergunta>-<i>", 1 idPergunta por
  // pergunta) — NÃO por <h3>, porque a Smarthis usa <h1> pro título do passo e o enunciado
  // nem sempre é <h3>. PULA perguntas já respondidas (evita o toggle que destrava→trava) e
  // escolhe UMA opção via IA. Marca todo input com data-oa-gupy-mui p/ o forms.js não tocar.
  async function responderPerguntasMui(root) {
    const grupos = new Map(); // key = idPergunta (prefixo do name) → { primeiro, opcoes:[] }
    let n = 0;
    for (const inp of root.querySelectorAll("input[type='checkbox']")) {
      const nm = inp.getAttribute("name") || "";
      const m = nm.match(/^(.+)-\d+$/); // tira o "-<i>" final → idPergunta
      const key = m ? m[1] : (OA.headingLabel(inp) || nm || ("cb" + (n++)));
      const lbl = inp.closest("label") || (inp.id && root.querySelector(`label[for="${CSS.escape(inp.id)}"]`)) || null;
      const txt = ((lbl && lbl.innerText) || inp.getAttribute("aria-label") || OA.labelFor(inp) || "").trim();
      if (!txt) continue;
      if (!grupos.has(key)) grupos.set(key, { primeiro: inp, opcoes: [] });
      grupos.get(key).opcoes.push({ inp, click: lbl || inp, txt });
    }
    let gi = 0;
    for (const [, g] of grupos) {
      const idx = gi++;
      for (const o of g.opcoes) o.inp.dataset.oaGupyMui = String(idx); // forms.js agrupa/pula por pergunta
      if (g.opcoes.some((o) => o.inp.checked)) continue; // já respondida → não mexe (evita toggle)
      const enun = OA.headingLabel(g.primeiro) || OA.labelFor(g.primeiro) || "Pergunta da empresa";
      const opcoesTxt = g.opcoes.map((o) => o.txt);
      let escolha = "";
      try {
        const r = await OA.bg({ type: "brain.answer", payload: { pergunta: enun, tipo: "SELECT", opcoes: opcoesTxt, idioma: "pt" } });
        escolha = (r?.resposta || "").trim();
      } catch (_) {}
      const e = escolha.toLowerCase();
      const alvo = g.opcoes.find((o) => o.txt.toLowerCase() === e)
        || (e && g.opcoes.find((o) => o.txt.toLowerCase().includes(e) || e.includes(o.txt.toLowerCase())))
        || g.opcoes[0];
      // Clica só se ainda não marcado → nunca faz toggle (re-clique desmarcaria e travaria).
      if (!alvo.inp.checked) {
        OA.click(alvo.click); await OA.sleep(rand(300, 700));
        if (!alvo.inp.checked) { try { OA.setChecked(alvo.inp, true, root); } catch (_) {} }
      }
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
      // "responder agora" ANTES de "salvar e continuar": no prompt das perguntas o
      // sticky "Salvar e continuar" do passo de trás continua visível e ganhava a
      // prioridade → o prompt nunca era clicado (automação travada em "Responder agora").
      avancar: ["responder agora", "salvar e continuar", "continuar", "próximo", "próxima", "avançar", "next"],
      avancarSel: ["button[aria-label='Responder agora']", "button[name='saveAndContinueButton']"],
      finalizar: ["finalizar candidatura", "finalizar", "concluir"],
      finalizarSel: ["#dialog-give-up-personalization-step"],
      // O diálogo final é TERMINAL, mas o "Salvar e continuar"/"Continuar" da tela de
      // trás continua "visível" ATRÁS do overlay → sem prioridade o wizard clicava o
      // fundo em loop e "travava em Finalizar candidatura".
      finalizarPrioridade: true,
      sucessoFrases: ["candidatura realizada", "candidatura foi realizada", "inscrição realizada", "inscrição concluída", "você se candidatou", "sua candidatura foi enviada", "candidatura enviada com sucesso", "recebemos sua candidatura", "application submitted", "you have applied"],
      ctx: { idioma: idioma || "pt", destravarGrupoCheckbox: true }, // perguntas da empresa com checkbox obrigatório
      preencher: preencherGupy,
      preferUltimo: true, // Gupy repete o botão (sticky + rodapé): clica o do rodapé
      pausarAntesEnvio: false, isRunning: running, onStatus: (s) => status(s),
    });
    if (r === "enviado") await OA.bg({ type: "stats.applied", platform: PLAT, jobId: location.pathname, titulo: document.title });
    return r;
  }

  async function aplicarVagaAtual(c) {
    // pausa leve e randômica ao abrir a vaga: a SPA assenta e o ritmo fica humano
    await rsleep(2000, 4000);
    // dedup: já aplicou nesta vaga? (pula em re-scrape de páginas)
    const jk = location.pathname;
    const dup = await OA.bg({ type: "stats.isApplied", platform: PLAT, jobId: jk });
    if (dup?.aplicou) return "pulada";
    // Gate: modalidade/região + match (o Gupy não tinha filtro na extensão).
    const desc = (document.querySelector("[data-testid='job-description'], main, article")?.innerText || document.body.innerText || "").slice(0, 3500);
    const gate = await OA.deveAplicar(desc, { titulo: document.title, platform: PLAT, pagina: (document.body.innerText || "").slice(0, 2500) });
    if (!gate.aplicar) { await status(`Pulei: ${gate.motivo}`.slice(0, 80)); return "sem_match"; }
    const cta = OA.findByText(CTA, { sel: "a, button, [role='button']" });
    if (cta && OA.isVisible(cta)) {
      OA.click(cta); await OA.sleep(2000);
      // botão sc-* do Gupy às vezes ignora o .click() simples → se a candidatura não
      // abriu, reforça com clique FORTE (senão o wizard rodava na página da vaga,
      // "falhava" sem botão e a vaga era pulada sem aplicar).
      if (!ehTelaCandidatura()) { try { OA.clickForte(cta); } catch (_) {} await OA.sleep(2000); }
    }
    return rodarWizardGupy(gate.idioma);
  }

  async function iniciar() {
    if (!(await running())) return;
    if (!(await cfg()).openrouter.apiKey) return status("⚠️ Configure a OpenRouter key na dashboard.");
    await OA.sleep(1200);
    // diálogo "vamos continuar sua candidatura?" já no load → resolve antes de rotear
    await tratarDialogContinuar();
    // Página de VAGA (a URL da LISTA é sempre /job-search/…; o resto veio da fila).
    // O CTA "Candidatar-se" demora a renderizar (SPA) → espera até ~12s. Sem CTA nem
    // tela de candidatura = vaga encerrada/já aplicada/login → PULA pra próxima.
    // (Antes o roteamento era por "tem CTA AGORA?": vaga lenta/encerrada caía no ramo
    // da LISTA, esperava 25s por cards inexistentes e dava run.stop "Sem mais vagas" —
    // matava o run inteiro no meio da fila: a Gupy "ficava parada".)
    if (!/job-search/.test(location.pathname)) {
      const c = await cfg();
      let cta = null;
      for (let i = 0; i < 12; i++) {
        if (ehTelaCandidatura()) break;
        cta = OA.findByText(CTA, { sel: "a, button, [role='button']" });
        if (cta && OA.isVisible(cta)) break;
        cta = null; await OA.sleep(1000);
      }
      let r;
      try { r = cta ? await aplicarVagaAtual(c) : (ehTelaCandidatura() ? await rodarWizardGupy("pt") : "sem_cta"); }
      catch (e) { try { console.log("[AutoApply][gupy] erro:", e?.message); } catch (_) {} r = "erro"; }
      if (r === "sem_cta") await status("Vaga sem 'Candidatar-se' (encerrada/já aplicada?) — pulando.");
      if (r === "pausa") return status("⏸️ Confirme o envio no Gupy, depois ▶️ para seguir.");
      return proximo(); // SEMPRE segue pra próxima (não trava/para a automação)
    }
    // Lista: guarda a URL da lista (p/ paginar), coleta os links e enfileira.
    await chrome.storage.local.set({ oaGupyList: listaUrl() });
    // SPA (ainda mais em aba de BACKGROUND do "Iniciar tudo") demora a renderizar os
    // cards → espera aparecerem antes de concluir "sem vagas" (o 0-cards prematuro
    // encerrava a plataforma na largada).
    await status("Aguardando as vagas carregarem…");
    await OA.waitFor(CARD_LINK, { timeout: 25000 });
    for (let i = 0; i < 3; i++) { window.scrollTo(0, document.body.scrollHeight); await OA.sleep(900); } window.scrollTo(0, 0);
    const cards = [...new Map([...document.querySelectorAll(CARD_LINK)]
      .filter((a) => a.href)
      .map((a) => [a.href, { href: a.href, titulo: ((a.closest("[data-testid='job-list__listitem']")?.querySelector("h3, h2")?.innerText || a.innerText || "").split("\n")[0] || "").trim().slice(0, 120) }]))
      .values()];
    if (!cards.length) {
      // sem cards nesta página → acabou (ou fim da paginação)
      await status("Sem mais vagas. ✅"); await OA.bg({ type: "run.stop" }); return;
    }
    // PRÉ-GATE POR IA NA LISTA: título → brain.title antes de enfileirar.
    await status(`Consultando a IA sobre ${cards.length} título(s)…`);
    const ok = await OA.filtrarTitulos(cards);
    if (!ok.length) { await status("Nenhuma vaga da página bate com o seu perfil (filtro por IA). ✅"); await OA.bg({ type: "run.stop" }); return; }
    await setQ(ok.map((c) => c.href));
    await status(`${ok.length} vaga(s) na fila (${cards.length - ok.length} fora do perfil). Aplicando (mesma aba)…`);
    proximo();
  }

  async function retomar() {
    if (!(await running())) return;
    const c = await cfg();
    await tratarDialogContinuar(); // diálogo pode reaparecer ao retomar no meio do fluxo
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

  // GUARDA de re-entrância: a cada load o cs.kick do SW E o auto-start abaixo disparam
  // JUNTOS → DOIS fluxos na mesma aba (fila avançava 2x pulando vagas, wizard duplicado
  // clicava/desmarcava). Só o primeiro entra; o outro vira no-op.
  let _fluxo = false;
  const umFluxo = async (fn) => { if (_fluxo) return; _fluxo = true; try { await fn(); } finally { _fluxo = false; } };
  chrome.runtime.onMessage.addListener((m, s, resp) => { if (m?.type === "cs.kick" && m.platform === PLAT) { umFluxo(iniciar); resp({ ok: true }); } return true; });
  OA.bg({ type: "run.isRunning" }).then(async (r) => { if (r?.running && r?.platform === PLAT) { const q = await getQ(); setTimeout(() => umFluxo(q.length ? retomar : iniciar), 1500); } });
})();
