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
  // sleeps HUMANOS (mesmo padrão Gupy/GeekHunter)
  const rand = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
  const rsleep = (a, b) => OA.sleep(rand(a, b));

  const CARD_LINK = "a[href*='/vaga/']";
  // Boards white-label variam o texto do CTA ("Candidatura rápida" é o mais comum,
  // mas há "Quero me candidatar"/"Candidatar-se"). NÃO incluir "candidatura revisada".
  const CTA_RAPIDA = ["candidatura rápida", "candidatura rapida", "quero me candidatar", "candidatar-se", "candidatar", "inscrever-se"];
  const INTERMED = ["avançar", "avancar", "continuar", "próximo", "proximo", "próxima", "proxima"];
  const SUBMIT = ["efetuar candidatura", "finalizar candidatura", "enviar candidatura", "concluir candidatura"];
  const SUCESSO = ["candidatura realizada", "candidatura com sucesso", "candidatura foi realizada", "recebemos sua candidatura", "candidatou com sucesso"];

  // URL COMPLETA da lista (com query): a busca feita pela UI pode viver em
  // parâmetros — guardar só origin+pathname perdia o termo na paginação.
  const listaUrl = () => location.href;
  const assinatura = () => location.href + "|" + document.querySelectorAll("input,button,textarea,[role='radio']").length + "|" + (document.body.innerText || "").length;

  async function proximo() {
    if (!(await running())) { status("parado."); return; }
    const q = await getQ(); const next = q.shift(); await setQ(q);
    if (next) { location.href = next; return; } // volta pro "card" seguinte
    // fila vazia → próxima página da busca (?page=N)
    const base = (await chrome.storage.local.get(LK))[LK];
    let page = (await chrome.storage.local.get(PK))[PK] || 1;
    page += 1;
    if (!base || page > 40) { status("Fim das páginas. ✅"); await OA.bg({ type: "run.stop" }); return; }
    await chrome.storage.local.set({ [PK]: page });
    status(`Próxima página (${page})…`);
    try { const u = new URL(base); u.searchParams.set("page", String(page)); location.href = u.toString(); }
    catch (_) { location.href = `${base}${base.includes("?") ? "&" : "?"}page=${page}`; }
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
  // Match EXATO no texto do botão: findByText usa includes() e "sim" casa DENTRO de
  // outras palavras/botões → clicava o elemento errado, o diálogo ficava aberto e a
  // candidatura não seguia. Prefere o ÚLTIMO (o diálogo renderiza por último no DOM).
  async function tratarDialogRevisar() {
    const aberto = () => /deseja revisar seu currículo|você está a 1 passo|voce esta a 1 passo|revisar seu curr/i.test(document.body.innerText || "");
    if (!aberto()) return false;
    const sims = [...document.querySelectorAll("button, a, [role='button']")]
      .filter((b) => OA.isVisible(b) && !b.disabled && /^sim$/i.test(((b.innerText || b.textContent || b.getAttribute("aria-label") || "")).trim()));
    const sim = sims[sims.length - 1];
    if (!sim) return false;
    log("diálogo revisar currículo → Sim");
    OA.click(sim); await OA.sleep(1400);
    if (aberto()) { OA.clickForte(sim); await OA.sleep(1400); }
    return true;
  }

  // "Habilidades para a vaga" — DOM REAL confirmado: cada skill é um
  //   div.gap-2 > label.font-semibold (nome, SEM [for]) + linha com 4 radios
  //   id="skill-option-<rand>" value 0..3 (Nenhum/Básico/Intermediário/Avançado),
  // radios SEM name. Agrupa por fronteira de value==="0" (ordem 0→3 confirmada).
  // Nível vem do cérebro (1 chamada, avaliado pelo CV); fallback honesto = "Básico".
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
      // o <label> do NOME da skill (sem [for]) fica 6 níveis acima do <input>
      // (input → label[for] → .group → .flex-row → .flex-col → .border → .gap-2)
      // — confirmado no DOM real. Sobe até 8 e PARA no 1º label sem [for].
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

  // Estamos DENTRO do fluxo de candidatura (recarregou/navegou no meio)? Frases REAIS
  // (DOM confirmado): a revisão do currículo tem o footer "Salvar currículo | Cancelar |
  // Avançar" e a tela de skills diz "Essas habilidades são exigidas pela vaga e são
  // eliminatórias". As frases antigas eram CHUTES que não existem nessas telas — um
  // Avançar que navegava de verdade reiniciava o content script, o fluxo não era
  // reconhecido, caía no ramo da LISTA (0 cards) e dava run.stop = "automação parada".
  function ehFluxoCandidatura() {
    const t = (document.body.innerText || "").toLowerCase();
    return /possui indica|habilidades para a vaga|habilidades s[aã]o exigidas|s[aã]o eliminat[oó]rias|salvar curr[ií]culo|efetuar candidatura|finalizar candidatura|deseja revisar seu curr[ií]culo|candidatura realizada/.test(t) ||
      !!document.querySelector("input[type='radio'][id^='skill-option']");
  }

  // Diagnóstico quando o passo não anda (o usuário não tem console): grava URL +
  // botões visíveis (com disabled/type) + campos → o dashboard baixa o .txt.
  async function dumpTravado(tag, alvo) {
    try {
      const botoes = [...document.querySelectorAll("button, a, [role='button']")]
        .filter((b) => OA.isVisible(b)).slice(0, 30)
        .map((b) => ({ txt: (b.innerText || b.getAttribute("aria-label") || "").trim().slice(0, 40), off: !!(b.disabled || b.getAttribute("aria-disabled") === "true"), type: b.type || "" }));
      const campos = [...document.querySelectorAll("input, select, textarea")].slice(0, 80).map((el) => ({
        tag: el.tagName.toLowerCase(), type: (el.type || "").slice(0, 12), name: (el.getAttribute("name") || "").slice(0, 40),
        label: (OA.labelFor(el) || "").slice(0, 60), req: !!(el.required || el.getAttribute("aria-required") === "true"),
        val: (el.type === "checkbox" || el.type === "radio") ? (el.checked ? "MARCADO" : "-") : (el.value || "").slice(0, 25),
      }));
      await OA.bg({ type: "debug.push", tag, data: { url: location.href, alvo: (alvo?.innerText || "").trim().slice(0, 40), botoes, campos } });
    } catch (_) {}
  }

  // Loop do fluxo (custom — NÃO usa rodarWizard: a Solides tem envio em 2 fases —
  // "Efetuar candidatura" pode abrir o diálogo de currículo/habilidades e só depois
  // envia de fato). Re-consulta o DOM a cada passo (cobre modal e rota SPA).
  async function fluxoCandidatura(pausar) {
    let travas = 0; // passos seguidos em que a página NÃO mudou após clique normal+FORTE
    // 20 passos: diálogo "revisar currículo?" + os VÁRIOS "Avançar" da revisão do
    // currículo + envio em 2 fases (14 ficava curto e encerrava "incerto" no meio).
    for (let step = 0; step < 20; step++) {
      if (!(await running())) return "parou";
      await OA.sleep(900);
      // sucesso? → "Ok, entendi!" e encerra
      if (SUCESSO.some((f) => (document.body.innerText || "").toLowerCase().includes(f))) {
        const ok = OA.findByText(["ok, entendi", "entendi", "ok"], { sel: "button, a, [role='button']" });
        if (ok && OA.isVisible(ok)) { OA.click(ok); await OA.sleep(800); }
        return "enviado";
      }
      const container = OA.melhorContainer("[role='dialog'], form, main");
      // diálogo "1 passo / revisar currículo?" → Sim e REAVALIA o passo do zero (sem o
      // continue, o "Efetuar candidatura" ATRÁS do diálogo era re-clicado no mesmo passo)
      try { if (await tratarDialogRevisar()) continue; } catch (e) { log("dialog erro:", e?.message); }
      // preenche tudo que der (defensivo — nada aqui derruba o loop)
      try { marcarNaoIndicacao(container); } catch (e) { log("indicação erro:", e?.message); }
      try { await responderHabilidades(container); } catch (e) { log("habilidades erro:", e?.message); }
      try { await OA.preencherCampos(container, { idioma: "pt", onStatus: (s) => status(s) }); } catch (e) { log("preencher erro:", e?.message); }
      await OA.sleep(400);
      const sig = assinatura();

      // 1) botão INTERMEDIÁRIO (avançar/continuar) → clica e segue. DOM real: o wizard
      // usa um <footer fixed> "Salvar currículo | Cancelar | Avançar" — procura PRIMEIRO
      // no footer (evita clicar num "continuar" qualquer no meio da página) e só depois
      // no documento (diálogos renderizam fora do footer).
      const footer = [...document.querySelectorAll("footer")].find((f) => OA.isVisible(f));
      const inter = (footer && OA.findByText(INTERMED, { root: footer, sel: "button, a, [role='button']" }))
        || OA.findByText(INTERMED, { sel: "button, a, [role='button']" });
      if (inter && OA.isVisible(inter)) {
        // pode estar DESABILITADO até a validação React assentar → espera ~4s habilitar
        const off = (b) => b.disabled || b.getAttribute("aria-disabled") === "true";
        for (let w = 0; w < 8 && off(inter); w++) await OA.sleep(500);
        log("step", step, "intermediário:", (inter.innerText || "").trim().slice(0, 24), off(inter) ? "(DESABILITADO)" : "");
        OA.click(inter); await OA.sleep(1400);
        if (assinatura() === sig) { OA.clickForte(inter); await OA.sleep(1400); }
        // página não mudou nem com clique FORTE → conta; na 3ª grava diagnóstico e
        // pula a vaga (antes moía os 20 passos em silêncio e parecia "parada").
        if (assinatura() === sig) {
          travas++;
          log("step", step, "Avançar não mudou a página (", travas, "x)");
          if (travas >= 3) { await dumpTravado("solides-avancar-travado", inter); await status("“Avançar” não reage — pulei a vaga (baixe o Diagnóstico no dashboard)."); return "incerto"; }
        } else travas = 0;
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
        if (assinatura() === sig) {
          travas++;
          if (travas >= 3) { await dumpTravado("solides-submit-travado", submit); await status("Envio não reage — pulei a vaga (baixe o Diagnóstico no dashboard)."); return "incerto"; }
        } else travas = 0;
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
    // pausa leve e randômica ao abrir a vaga: a SPA assenta e o ritmo fica humano
    await rsleep(2000, 4000);
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
    const gate = await OA.deveAplicar(coletarDescricao(), { titulo: tituloVaga(), platform: PLAT, pagina: (document.body.innerText || "").slice(0, 2500) });
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

  // Digita a query no FORMULÁRIO de busca do portal e clica "Buscar vagas". O caminho
  // antigo (/vagas/todos/<termo>) não existe mais — a URL voltava "0 vaga(s)" e a
  // automação morria na largada ("não está se aplicando"). A busca confiável é a UI.
  async function buscarNaLista(query) {
    try {
      const inp = [...document.querySelectorAll("input")].find((i) =>
        /nome da vaga|cargo|procura\?|o que voc[êe] procura/i.test(i.placeholder || "") || /vaga/i.test(i.name || ""));
      const btn = OA.findByText(["buscar vagas", "buscar"], { sel: "button, [role='button'], input[type='submit']" });
      if (!inp || !btn) { log("form de busca não encontrado"); return false; }
      OA.fillInput(inp, query);
      await rsleep(500, 900);
      OA.click(btn); await rsleep(2500, 4000); // SPA recarrega os cards
      log("busca aplicada:", query);
      return true;
    } catch (e) { log("buscarNaLista erro:", e?.message); return false; }
  }

  let _kicked = false;
  async function iniciar() {
    if (_kicked) return; _kicked = true;
    if (!(await running())) return;
    if (!(await cfg()).openrouter.apiKey) return status("⚠️ Configure a OpenRouter key na dashboard.");
    await OA.sleep(1200);
    // detalhe da vaga OU dentro do fluxo?
    if (/\/vaga\/./.test(location.pathname) || ehFluxoCandidatura() || OA.findByText(CTA_RAPIDA, { sel: "button, a, [role='button']" })) {
      let r;
      try { r = await aplicarVaga(); } catch (e) { log("erro:", e?.message); r = "erro"; }
      if (r === "pausa") return status("⏸️ Confirme o envio na Solides, depois ▶️ para seguir.");
      if (r === "teto") return;
      return proximo();
    }
    // Só é LISTA se a URL tem cara de lista ("/vagas", "/vagas/…" ou raiz de board
    // white-label). Página desconhecida no meio do run caía aqui, clobberava a URL da
    // lista e virava "0 cards" → run.stop matava a automação inteira.
    if (!/\/vagas(\/|$)/.test(location.pathname) && location.pathname !== "/") { await status("Página fora do fluxo — seguindo a fila."); return proximo(); }
    // lista: guarda a URL (paginação), coleta os cards e enfileira. SPA carrega os
    // cards aos poucos → tenta algumas vezes (scroll + espera + BUSCA pela UI).
    await chrome.storage.local.set({ [LK]: listaUrl() });
    let cards = [];
    for (let tent = 0; tent < 5 && !cards.length; tent++) {
      for (let i = 0; i < 3; i++) { window.scrollTo(0, document.body.scrollHeight); await OA.sleep(900); }
      window.scrollTo(0, 0);
      const vistos = new Set();
      cards = [...document.querySelectorAll(CARD_LINK)]
        .filter((a) => /\/vaga\/./.test(a.href) && !vistos.has(a.href) && vistos.add(a.href))
        .map((a) => ({ href: a.href, titulo: ((a.innerText || "").split("\n")[0] || "").trim().slice(0, 120) }));
      if (!cards.length) {
        log("cards não carregaram (tentativa", tent + 1, ") em", location.href);
        // 2ª tentativa vazia → digita a query configurada no form de busca do portal
        // (default "desenvolvedor" — a busca NUNCA fica sem termo)
        if (tent === 1) {
          const q = (((await cfg()).plataformas?.solides?.query || "").trim()) || "desenvolvedor";
          await status(`Buscando “${q}” no portal…`);
          await buscarNaLista(q);
        }
        if (!cards.length) await OA.sleep(1200);
      }
    }
    if (!cards.length) { await status("Sem vagas nesta página (cards não carregaram — layout diferente?). ✅"); await OA.bg({ type: "run.stop" }); return; }
    // PRÉ-GATE POR IA NA LISTA: só entra na fila o título que faz sentido c/ o perfil.
    await status(`Consultando a IA sobre ${cards.length} título(s)…`);
    const ok = await OA.filtrarTitulos(cards);
    if (!ok.length) { await status("Nenhuma vaga da página bate com o seu perfil (filtro por IA). ✅"); await OA.bg({ type: "run.stop" }); return; }
    await setQ(ok.map((c) => c.href));
    await status(`${ok.length} vaga(s) na fila (${cards.length - ok.length} fora do perfil). Aplicando (mesma aba)…`);
    log("fila:", ok.length, "vagas");
    proximo();
  }

  log("carregado:", location.href); // se você NÃO vê esta linha no console, o script não foi injetado → recarregue a extensão + a aba
  // GUARDA de re-entrância: cs.kick do SW + auto-start disparam juntos no load →
  // dois fluxos na mesma aba. Só o primeiro entra.
  let _fluxo = false;
  const umFluxo = async (fn) => { if (_fluxo) return; _fluxo = true; try { await fn(); } finally { _fluxo = false; } };
  chrome.runtime.onMessage.addListener((m, s, resp) => { if (m?.type === "cs.kick" && m.platform === PLAT) { umFluxo(iniciar); resp?.({ ok: true }); } return true; });
  OA.bg({ type: "run.isRunning" }).then((r) => { if (r?.running && r?.platform === PLAT) setTimeout(() => umFluxo(iniciar), 1500); });
})();
