// GeekHunter — content script (geekhunter.com / .com.br). O site FOI REFORMULADO:
// os cards da lista agora são LINKS DIRETOS (https://www.geekhunter.com/pt/<empresa>/
// jobs/<slug>) e o botão "Visualizar vaga" que abria nova aba NÃO EXISTE MAIS — o fluxo
// antigo não encontrava nada ("não aplica nas vagas"). Fluxo atual (fila na MESMA aba,
// igual Gupy/Solides — sem popup-blocker e sem abrir inúmeras abas):
//   LISTA (/pt/vagas): coleta os hrefs dos cards (/jobs/<slug>) → FILA em storage →
//     navega de vaga em vaga na mesma aba (?page=N pra paginar).
//   VAGA  (/…/jobs/<slug>): match → preenche o form DA PÁGINA (contato/CV/LGPD) →
//     "Candidatar para a vaga" → modal "perguntinhas" (wizard) → sucesso → próxima.
(function () {
  const OA = window.OA, PLAT = "geekhunter", QK = "oaGeekQueue", PK = "oaGeekPage", LK = "oaGeekList";
  const running = async () => { const r = await OA.bg({ type: "run.isRunning" }); return r?.running && r?.platform === PLAT; };
  const status = (t, a) => OA.bg({ type: "status.push", platform: PLAT, status: t, action: a });
  const cfg = async () => (await OA.bg({ type: "config.get" })).config;
  const getQ = async () => (await chrome.storage.local.get(QK))[QK] || [];
  const setQ = (q) => chrome.storage.local.set({ [QK]: q });
  // sleeps HUMANOS: estava rápido demais (abria vaga atrás de vaga sem espaçar).
  const rand = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
  const rsleep = (a, b) => OA.sleep(rand(a, b));

  const CTA_APLICAR = ["candidatar para a vaga", "quero me candidatar", "candidatar-se", "candidatar", "finalizar candidatura", "enviar candidatura", "enviar minha candidatura", "aplicar para a vaga", "aplicar"];
  const FECHAR = ["entendi", "fechar", "ok"];

  // Vaga = URL de detalhe (/pt/<empresa>/jobs/<slug>). Confere ao vivo: a lista é
  // /pt/vagas e TODA vaga tem segmento "/jobs/" no pathname.
  const ehVaga = () => /\/jobs\/.+/.test(location.pathname);

  // ── FILA (mesma aba) ────────────────────────────────────────────────────────
  async function proximo() {
    if (!(await running())) { status("parado."); return; }
    const q = await getQ(); const next = q.shift(); await setQ(q);
    // espaçamento humano entre vagas
    await status("Aguardando um pouco antes da próxima vaga…");
    await rsleep(4000, 10000);
    if (!(await running())) { status("parado."); return; }
    if (next) { location.href = next; return; }
    // fila vazia → PRÓXIMA PÁGINA da busca (lista é /pt/vagas?page=N)
    const base = (await chrome.storage.local.get(LK))[LK];
    let page = (await chrome.storage.local.get(PK))[PK] || 1;
    page += 1;
    if (!base || page > 40) { status("Fim das páginas. ✅"); await OA.bg({ type: "run.stop" }); return; }
    await chrome.storage.local.set({ [PK]: page });
    const u = new URL(base);
    u.searchParams.set("page", String(page));
    status(`Próxima página (${page})…`);
    location.href = u.toString();
  }

  // ── VAGA (mesma aba): match → aplica ou pula. Fluxo REAL do GeekHunter:
  //   1) o form da PÁGINA tem o "Celular com DDD" (o "+55" é prefixo fixo do widget —
  //      NUNCA mexer nele, digita-se SÓ o número; o preencherTelefone do forms.js já
  //      faz isso), CV obrigatório e o "Candidatar para a vaga" é type=submit DESSE
  //      form → preenche ANTES de clicar, senão a validação segura o submit e o modal
  //      nunca abre;
  //   2) o clique abre o modal "…tem algumas perguntinhas pra você": Chakra
  //      NumberInputs (anos de experiência — IA responde pelo CV) + dropdowns/textarea
  //      + checkbox LGPD (genérico marca) + "Finalizar candidatura";
  //   3) sucesso = "Obrigado pela sua candidatura!" → fecha o modal e segue a fila.
  // Retorna 'enviado' | 'pausa' | 'pulada' | 'sem_match' | 'sem_cta' | 'incerto' | 'falhou'.
  let _tratou = false;

  const SUCESSO = /obrigado pela (sua )?candidatura/i;
  const temSucesso = () => SUCESSO.test(document.body.innerText || "");
  const esperaSucesso = async (ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (temSucesso()) return true; await OA.sleep(400); } return false; };
  // modal de perguntas VISÍVEL com campos (Chakra portala fora do <main> → o
  // melhorContainer podia preferir o form da página e o hook nunca via as perguntas)
  const modalPerguntas = () => [...document.querySelectorAll(".chakra-modal__body, [role='dialog'], [class*='chakra-modal']")]
    .find((m) => OA.isVisible(m) && m.querySelector("input, select, textarea")) || null;
  const containerVaga = () => { const m = modalPerguntas(); return (m && (m.querySelector("form") || m)) || OA.melhorContainer("[class*='chakra-modal'], form, [role='dialog'], [class*='modal'], main"); };

  // Enunciado de um campo do modal GeekHunter: o <p class="chakra-text"> (com o "*") é
  // IRMÃO do wrapper do campo — sem <label for>, sem heading. Estratégias em ordem, até 4
  // ancestrais: (1) <p>/<label> FILHO direto; (2) irmão ANTERIOR (o rótulo costuma vir logo
  // acima do wrapper — não é filho de um ancestral comum); (3) OA.labelFor (aria/heading/
  // placeholder). Ignora o placeholder "Selecione uma opção". Sem isso a IA recebia pergunta
  // vazia → respondia genérico/errado (textarea/dropdown/numberinput ficavam mal preenchidos).
  const limpaEnun = (s) => (s || "").replace(/^\*\s*/, "").replace(/\s*\*\s*$/, "").trim();
  const ehPlaceholderEnun = (s) => !s || s.length < 3 || /^(selecione|escolha|select|digite|informe|--)/i.test(s);
  const enunciadoChakra = (el) => {
    let node = el.parentElement;
    for (let i = 0; i < 4 && node; i++, node = node.parentElement) {
      // (1) <p>/<label> filho DIRETO do ancestral (estrutura original, comprovada)
      const filho = [...node.querySelectorAll(":scope > p, :scope > label")]
        .map((x) => limpaEnun(x.innerText)).find((s) => !ehPlaceholderEnun(s));
      if (filho) return filho;
      // (2) irmão ANTERIOR do ancestral: o rótulo costuma vir logo ACIMA do wrapper do campo
      // (não é filho de um ancestral comum) → nem o (1) nem o labelFor alcançavam, e o campo
      // recebia pergunta vazia → IA respondia genérico/errado.
      let prev = node.previousElementSibling;
      for (let j = 0; j < 3 && prev; j++, prev = prev.previousElementSibling) {
        if (!prev.matches?.("p, label, span, h1, h2, h3, h4, h5, h6")) continue;
        const t = limpaEnun(prev.innerText);
        if (!ehPlaceholderEnun(t) && t.length < 200) return t;
      }
    }
    // (3) helpers já testados do dom.js (aria-label / label[for] / heading / placeholder)
    const lbl = limpaEnun(OA.labelFor(el));
    return ehPlaceholderEnun(lbl) ? "" : lbl;
  };
  const perguntarCerebro = async (pergunta, tipo, opcoes, ctx) => {
    // Sem race próprio: o OA.bg já tem watchdog de 90s e o servidor (responderPergunta) faz
    // 1 retry. O antigo race de 30s era MENOR que o abort do fetch (40s) → descartava resposta
    // VÁLIDA lenta e o campo caía no genérico "tenho disponibilidade".
    const r = await OA.bg({ type: "brain.answer", payload: { pergunta, tipo, opcoes, vagaTitulo: ctx.titulo || "", vagaEmpresa: "", idioma: ctx.idioma || "pt" } });
    return (r?.resposta || "").trim();
  };
  // Opções do dropdown custom aberto (portal fora do container). Seletores LARGOS (o
  // componente varia: role=option/menuitem, chakra-menu, ids react-select "…-option-N",
  // classes "-option"/"menuitem"). Corte <120 chars evita casar um elemento gigante
  // concatenado; filtra o próprio placeholder ("Selecione…") pra ele nunca virar "opção".
  const opcoesDropdownGeek = () => [...document.querySelectorAll(
    "[role='option'], [role='menuitem'], .chakra-menu__menuitem, li[role='menuitem'], " +
    "[id*='option'], [class*='-option'], [class*='menuitem'], [class*='MenuList'] > div"
  )].filter((o) => { const t = (o.innerText || "").trim(); return OA.isVisible(o) && t && t.length < 120 && !/^(selecione|escolha|select)/i.test(t); });

  // Perguntas do modal ("…algumas perguntinhas pra você") — 3 tipos SEM label que o
  // preenchedor genérico não alcança. Salário/remuneração fica pro genérico (CONFIG,
  // nunca IA). Nada aqui derruba o wizard (cada campo em try/catch).
  async function preencherPerguntasChakra(container, ctx) {
    // CV vem da dashboard: os anos de experiência são CALCULADOS das datas do currículo
    // (lib/cv.js) — só consulta a IA se o CV não tiver nenhum período datado.
    const c = await cfg();
    const cvTxt = c?.perfil?.resumo_curriculo || "";
    // (a) Chakra NumberInput (type=text, role=spinbutton, name numérico ex. "135499"):
    // anos de experiência — CALCULADOS do CV por tecnologia; tecnologia fora do CV → 0.
    for (const inp of container.querySelectorAll(".chakra-numberinput input, input[role='spinbutton']")) {
      try {
        if ((inp.value || "").trim()) continue; // já respondido
        const pergunta = enunciadoChakra(inp);
        if (/(remunera|sal[aá]ri|pretens)/i.test(pergunta)) continue; // salário → forms.js/config
        const calc = window.OACV && cvTxt ? window.OACV.calcular(cvTxt, pergunta || "") : null;
        // TUDO PELA IA (pedido): manda a pergunta com instrução de CALCULAR os anos a
        // partir das DATAS dos períodos do currículo. O cálculo local (lib/cv.js) entra
        // como conferência — se a IA devolver algo inválido, usamos ele.
        const baseQ = pergunta || "Quantos anos de experiência você tem com a principal tecnologia da vaga?";
        const perguntaIA = baseQ +
          " Responda CALCULANDO: conte os ANOS entre as datas de início e fim dos períodos de trabalho no currículo em que essa tecnologia aparece." +
          (calc != null ? ` Conferência automática pelas datas: ${calc} ano(s).` : "");
        const resp = await perguntarCerebro(perguntaIA, "NUMERO", [], ctx);
        let n = parseFloat(String(resp).replace(",", ".").replace(/[^\d.-]/g, ""));
        if (!isFinite(n) || n < 0 || n > 45) n = (calc != null ? calc : 0); // IA falhou → cálculo local; senão 0
        try { console.log("[OA-CV]", baseQ.slice(0, 60), "→ IA:", resp, "| datas:", calc, "| usado:", n); } catch (_) {}
        OA.fillInput(inp, String(Number.isInteger(n) ? n : Math.round(n)));
        await OA.sleep(250);
      } catch (_) {}
    }
    // (b) TEXTAREA da pergunta (name numérico, id "tae-NNN", ex. "Você possui alguma
    // certificação AWS?"): sem label E sem required → o genérico a pulava como
    // "opcional sem rótulo". Obrigatória (o "*" está no <p>) → nunca deixa vazia.
    for (const ta of container.querySelectorAll("textarea")) {
      try {
        if ((ta.value || "").trim()) continue;
        const pergunta = enunciadoChakra(ta);
        if (!pergunta) continue; // sem enunciado → deixa pro genérico
        if (/(remunera|sal[aá]ri|pretens)/i.test(pergunta)) continue;
        const resp = await perguntarCerebro(pergunta, "TEXT", [], ctx);
        OA.fillInput(ta, resp || "Tenho interesse e disponibilidade para a vaga.");
        await OA.sleep(250);
      } catch (_) {}
    }
    // (c) DROPDOWN custom: <div name="NNN"> com <p>Selecione uma opção</p> + chevron
    // <svg> (NÃO é <select>, sem role/aria-haspopup → o loop de combobox do forms.js
    // não o vê; o valor fica num <input hidden> irmão). Abre, lê as opções no portal e
    // escolhe via IA; fallback = 1ª opção (nunca trava o "Finalizar candidatura").
    for (const dd of container.querySelectorAll("div[name]")) {
      try {
        if (!OA.isVisible(dd) || !dd.querySelector("svg")) continue; // sem chevron → não é dropdown
        // texto do trigger = TODO o dd (não só o 1º <p>: o valor escolhido pode ir pra
        // outro nó → o marcado() antigo dava falso-negativo). placeholderTxt inicial p/ comparar.
        const triggerTxt = () => (dd.innerText || "").replace(/\s+/g, " ").trim();
        const placeholderTxt = triggerTxt().toLowerCase();
        if (placeholderTxt && !/selecione|escolha|select/i.test(placeholderTxt)) continue; // já escolhido
        // <input> companheiro com o valor (hidden/chakra) — busca no dd, no pai E no avô
        // (antes só o pai → falso-negativo). É a confirmação mais confiável da escolha.
        const hiddenComValor = () => {
          for (const root of [dd, dd.parentElement, dd.parentElement?.parentElement]) {
            if (!root) continue;
            const h = root.querySelector("input[type='hidden'], input[hidden], input[class*='chakra-input']");
            if (h && (h.value || "").trim()) return true;
          }
          return false;
        };
        if (hiddenComValor()) continue; // já respondido
        const pergunta = enunciadoChakra(dd) || "Pergunta da empresa";
        // react-select/Chakra custom ABRE no mousedown → clickForte (sequência de ponteiro)
        // desde a 1ª; OA.click e um filho-trigger como fallbacks caso a raiz não seja o alvo.
        OA.clickForte(dd); await OA.sleep(700);
        let opts = opcoesDropdownGeek();
        if (!opts.length) { OA.click(dd); await OA.sleep(700); opts = opcoesDropdownGeek(); }
        if (!opts.length) {
          const inner = dd.querySelector("p, svg")?.closest("div") || dd.firstElementChild;
          if (inner && inner !== dd) { OA.clickForte(inner); await OA.sleep(700); opts = opcoesDropdownGeek(); }
        }
        if (!opts.length) {
          try { document.body.click(); } catch (_) {}
          // grava a estrutura real (o usuário não tem console) p/ um run revelar o dropdown
          OA.bg({ type: "debug.push", tag: "geek-dropdown-nao-abriu", data: { pergunta: pergunta.slice(0, 80), name: dd.getAttribute("name") || "", html: (dd.outerHTML || "").slice(0, 300) } });
          continue;
        }
        const textos = opts.map((o) => o.innerText.trim());
        const resp = (await perguntarCerebro(pergunta, "SELECT", textos, ctx)).toLowerCase();
        // re-busca a opção A CADA tentativa (o menu re-renderiza e o nó antigo morre).
        // os[0] já é opção REAL (opcoesDropdownGeek filtra o placeholder).
        const escolher = () => { const os = opcoesDropdownGeek(); return os.find((o) => o.innerText.trim().toLowerCase() === resp)
          || (resp && os.find((o) => { const t = o.innerText.trim().toLowerCase(); return t.includes(resp) || resp.includes(t); }))
          || os[0]; };
        // MARCOU? o trigger deixou de mostrar o placeholder OU o input companheiro tem valor.
        // (o antigo olhava só o 1º <p> + o pai → falso-negativo fazia o loop "desfazer" a escolha.)
        const marcado = () => {
          const t = triggerTxt().toLowerCase();
          return (t && t !== placeholderTxt && !/selecione|escolha|select/i.test(t)) || hiddenComValor();
        };
        for (let tent = 0; tent < 3 && !marcado(); tent++) {
          let alvo = escolher();
          if (!alvo) { OA.clickForte(dd); await OA.sleep(700); alvo = escolher(); if (!alvo) break; } // menu fechou sem marcar → reabre
          OA.clickForte(alvo); await OA.sleep(300);                    // react-select seleciona no mousedown → clickForte já na 1ª
          if (!marcado()) OA.clickForte(alvo.querySelector("p, span") || alvo); // reforça no texto interno
          await OA.sleep(600);
        }
        if (!marcado()) {
          try { document.body.click(); } catch (_) {} // fecha o menu (não deixa aberto)
          OA.bg({ type: "debug.push", tag: "geek-dropdown-nao-marcou", data: { pergunta: pergunta.slice(0, 80), resp, opcoes: textos.slice(0, 12) } });
        }
        await OA.sleep(300);
      } catch (_) {}
    }
  }

  async function tratarVaga() {
    if (_tratou) return "pulada"; // guarda: 1x por página (evita "2x na mesma vaga")
    _tratou = true;
    let r = "incerto";
    try {
      if (!(await running())) return "parou";
      // pausa leve e randômica ao abrir a vaga: a SPA assenta e o ritmo fica humano
      await rsleep(1000, 2000);
      // banner de cookies pode interceptar o submit do form → fecha cedo
      try { OA.fecharBanners(); } catch (_) {}
      // DEDUP por vaga (igual Gupy/Solides): nunca re-aplica a mesma vaga.
      const dup = await OA.bg({ type: "stats.isApplied", platform: PLAT, jobId: location.pathname });
      if (dup?.aplicou) { await status("Já aplicada — pulando."); return "pulada"; }
      const c = await cfg();
      const desc = (document.querySelector("[class*='description'], main, article")?.innerText || document.body.innerText || "").slice(0, 3500);
      const titulo = (document.querySelector("h1, [class*='title']")?.innerText || document.title || "").trim();
      const gate = await OA.deveAplicar(desc, { titulo, platform: PLAT, pagina: (document.body.innerText || "").slice(0, 2500) });
      if (!gate.aplicar) {
        await status(`Pulei (${gate.motivo}): ${titulo.slice(0, 35)}`);
        return "sem_match";
      }
      const idioma = gate.idioma || "pt";

      // 1) preenche o form da PÁGINA (contato/CV/remuneração CLT) ANTES do submit
      const cta = OA.findByText(CTA_APLICAR, { sel: "button, a, [role='button']" });
      if (!cta) { await status("Sem botão de candidatura — vaga encerrada/já aplicada? Pulando."); return "sem_cta"; }
      const formPg = cta.closest("form") || OA.melhorContainer("form, main");
      try { await OA.preencherCampos(formPg, { idioma, onStatus: (s) => status(s) }); } catch (_) {}
      await rsleep(700, 1300); // validação React (telefone) assenta antes do submit
      OA.click(cta);
      // espera o modal de perguntas OU o sucesso direto (vaga sem perguntinhas)
      let t0 = Date.now();
      while (Date.now() - t0 < 9000 && !modalPerguntas() && !temSucesso()) await OA.sleep(400);
      if (!modalPerguntas() && !temSucesso()) {
        // submit segurado (validação) → re-preenche e reforça com clique FORTE
        try { await OA.preencherCampos(formPg, { idioma, onStatus: (s) => status(s) }); } catch (_) {}
        await rsleep(500, 900);
        OA.clickForte(cta);
        t0 = Date.now();
        while (Date.now() - t0 < 9000 && !modalPerguntas() && !temSucesso()) await OA.sleep(400);
      }

      let enviado = temSucesso(); // sem perguntinhas: sucesso direto após o CTA
      if (!enviado && modalPerguntas()) {
        // 2) modal "…perguntinhas": NumberInputs (IA) + dropdowns + LGPD + "Finalizar".
        // finalizar SÓ com textos do MODAL — com "candidatar"/"aplicar" na lista o wizard
        // re-clicava o "Candidatar para a vaga" da página como se fosse o envio final.
        r = await OA.rodarWizard(containerVaga, {
          preencher: (cont) => preencherPerguntasChakra(cont, { titulo, idioma }),
          avancar: ["salvar e continuar", "continuar", "próximo", "próxima", "avançar", "next"],
          finalizar: ["finalizar candidatura", "enviar candidatura", "enviar minha candidatura", "finalizar", "concluir"],
          sucessoFrases: ["obrigado pela sua candidatura", "obrigado pela candidatura", "confirmar sua candidatura pelo e-mail", "confirmar sua candidatura pelo email", "sua candidatura foi enviada", "candidatura enviada com sucesso", "candidatura foi enviada", "recebemos sua candidatura", "sua candidatura foi realizada"],
          ctx: { idioma },
          pausarAntesEnvio: false, isRunning: running, onStatus: (s) => status(s),
        });
        if (r === "pausa") return "pausa";
        // o "Obrigado…" às vezes renderiza DEPOIS do check do wizard → re-confere
        enviado = r === "enviado" || (r !== "parou" && (await esperaSucesso(7000)));
      }

      if (enviado) {
        r = "enviado";
        await OA.bg({ type: "stats.applied", platform: PLAT, jobId: location.pathname, titulo });
        await status(`✅ Candidatura enviada: ${titulo.slice(0, 40)}`);
        // 3) fecha o modal de agradecimento ("Obrigado pela sua candidatura!")
        const fechar = OA.findByText(FECHAR, { sel: "button" }) || document.querySelector(".chakra-modal__close-btn, [aria-label='Close'], [aria-label*='fechar' i]");
        if (fechar) OA.click(fechar);
        await OA.sleep(800);
      }
    } catch (e) {
      try { console.log("[AutoApply][geek] erro na vaga:", e?.message); } catch (_) {}
      r = "erro";
    }
    return r;
  }

  // ── LISTA: coleta os links dos cards e enfileira (mesma aba) ────────────────
  async function iniciarLista() {
    const c = await cfg();
    if (!c.openrouter.apiKey) return status("⚠️ Configure a OpenRouter key na dashboard.");
    await chrome.storage.local.set({ [LK]: location.href }); // base p/ paginar (?page=N)
    // SPA carrega os cards aos poucos → espera + scroll antes de concluir "sem vagas".
    await status("Aguardando as vagas carregarem…");
    let achou = null;
    for (let tent = 0; tent < 4 && !achou; tent++) {
      for (let i = 0; i < 2; i++) { window.scrollTo(0, document.body.scrollHeight); await OA.sleep(700); }
      window.scrollTo(0, 0);
      achou = await OA.waitFor("a[href*='/jobs/']", { timeout: 3000 });
    }
    // Cards = links diretos p/ detalhe (/pt/<empresa>/jobs/<slug>) do PRÓPRIO GeekHunter.
    const cards = [...new Map([...document.querySelectorAll("a[href*='/jobs/']")]
      .map((a) => {
        try {
          const u = new URL(a.href, location.href);
          if (!(/geekhunter\.(com|com\.br)$/.test(u.hostname) && /\/jobs\/.+/.test(u.pathname))) return null;
          return [u.origin + u.pathname, { href: u.origin + u.pathname, titulo: ((a.innerText || "").split("\n")[0] || "").trim().slice(0, 120) }];
        } catch (_) { return null; }
      })
      .filter(Boolean))]
      .map(([_, v]) => v);
    if (!cards.length) {
      await status("Sem vagas nesta página (cards não carregaram — layout diferente?). ✅");
      await OA.bg({ type: "run.stop" });
      return;
    }
    // PRÉ-GATE POR IA NA LISTA: título → brain.title antes de enfileirar.
    await status(`Consultando a IA sobre ${cards.length} título(s)…`);
    const ok = await OA.filtrarTitulos(cards);
    if (!ok.length) { await status("Nenhuma vaga da página bate com o seu perfil (filtro por IA). ✅"); await OA.bg({ type: "run.stop" }); return; }
    await setQ(ok.map((c) => c.href));
    await status(`${ok.length} vaga(s) na fila (${cards.length - ok.length} fora do perfil). Aplicando (mesma aba)…`);
    proximo();
  }

  let _fluxo = false; // guarda de re-entrância: cs.kick + auto-start disparam juntos no load
  async function rodar() {
    if (_fluxo) return;
    _fluxo = true;
    try {
      if (!(await running())) return;
      await OA.sleep(1000);
      try { OA.fecharBanners(); } catch (_) {}
      if (ehVaga()) {
        let r;
        try { r = await tratarVaga(); } catch (e) { try { console.log("[AutoApply][geek] erro:", e?.message); } catch (_) {} r = "erro"; }
        if (r === "pausa") return status("⏸️ Confirme o envio no GeekHunter, depois ▶️ para seguir.");
        return proximo(); // SEMPRE segue pra próxima (pulada/sem match/erro não travam a fila)
      }
      await iniciarLista();
    } finally { _fluxo = false; }
  }

  chrome.runtime.onMessage.addListener((m, s, resp) => {
    if (m?.type === "cs.kick" && m.platform === PLAT) { _fluxo || rodar(); resp?.({ ok: true }); }
    return true;
  });
  OA.bg({ type: "run.isRunning" }).then((r) => { if (r?.running && r?.platform === PLAT) setTimeout(rodar, 1500); });
})();
