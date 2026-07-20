// GeekHunter — content script (geekhunter.com). SPA Chakra. Cards SEM href: clica-se
// "Visualizar vaga" (abre NOVA ABA). Fluxo UMA ABA POR VEZ (não abre inúmeras):
//   LISTA: clica 1 "Visualizar vaga" → abre a aba da vaga.
//   VAGA:  checa match → se NÃO for pra aplicar, pede pro SW FECHAR a aba; se for,
//          aplica (wizard) e depois pede pro SW fechar. O SW fecha a aba e manda a
//          LISTA abrir a próxima (mensagem cs.next).
(function () {
  const OA = window.OA, PLAT = "geekhunter";
  const running = async () => { const r = await OA.bg({ type: "run.isRunning" }); return r?.running && r?.platform === PLAT; };
  const status = (t, a) => OA.bg({ type: "status.push", platform: PLAT, status: t, action: a });
  const cfg = async () => (await OA.bg({ type: "config.get" })).config;
  // sleeps HUMANOS: estava rápido demais (abria vaga atrás de vaga sem espaçar).
  const rand = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
  const rsleep = (a, b) => OA.sleep(rand(a, b));

  const CTA_APLICAR = ["candidatar para a vaga", "quero me candidatar", "candidatar-se", "candidatar", "finalizar candidatura", "enviar candidatura", "enviar minha candidatura", "aplicar para a vaga", "aplicar"];
  const FECHAR = ["entendi", "fechar", "ok"];

  let _started = false, _vers = [], _idx = 0;

  const ehVaga = () => !!OA.findByText(CTA_APLICAR, { sel: "button, a, [role='button']" }) &&
    !OA.findByText(["visualizar vaga"], { sel: "button, a, p, [role='button']" });

  // ── LISTA: abre uma vaga por vez ────────────────────────────────────────────
  async function iniciarLista() {
    const c = await cfg();
    if (!c.openrouter.apiKey) return status("⚠️ Configure a OpenRouter key na dashboard.");
    for (let i = 0; i < 3; i++) { window.scrollTo(0, document.body.scrollHeight); await OA.sleep(900); } window.scrollTo(0, 0);
    // O clicável costuma ser <button|a><p>Visualizar vaga</p></…> → botão E <p> casavam
    // no filtro e a MESMA vaga entrava 2x em _vers (abria o card duas vezes). Mantém só
    // o elemento MAIS INTERNO de cada gatilho e pula os já clicados (data-oa-visto —
    // cobre o re-scrape de "próxima página" que não paginou de verdade).
    const els = [...document.querySelectorAll("a, button, [role='button'], p")]
      .filter((e) => OA.isVisible(e) && /visualizar vaga/i.test(e.innerText || "") && !e.dataset.oaVisto);
    _vers = els.filter((e) => !els.some((o) => o !== e && e.contains(o)));
    _idx = 0;
    if (!_vers.length) return status("Não achei 'Visualizar vaga'. Abra a lista do GeekHunter e clique ▶️.");
    await status(`${_vers.length} vaga(s). Abrindo 1 por vez…`);
    abrirAtual();
  }
  let _abrindo = false; // reentrância: um cs.next duplicado não pode abrir 2 vagas (ou 2x a mesma)
  async function abrirAtual() {
    if (_abrindo) return;
    _abrindo = true;
    try { await abrirAtualInterno(); } finally { _abrindo = false; }
  }
  async function abrirAtualInterno() {
    if (!(await running())) return;
    if (_idx >= _vers.length) {
      // acabou a página → tenta a PRÓXIMA (como o Selenium paginava)
      const next = OA.findByText(["próxima", "próximo", "next"], { sel: "button, a, [aria-label]" }) ||
        document.querySelector("[aria-label*='próxima' i], [aria-label*='next' i], .pagination-next, nav [rel='next']");
      if (next && OA.isVisible(next) && !next.disabled) {
        await status("Próxima página…"); OA.click(next); await OA.sleep(2800);
        return iniciarLista(); // re-raspa a nova página
      }
      return status("Fim das vagas. ✅");
    }
    const can = await OA.bg({ type: "stats.canApply", platform: PLAT });
    if (can?.ok && !can.permitido) { await status(`Teto do dia (${can.teto}).`); return OA.bg({ type: "run.stop" }); }
    await status(`Abrindo vaga ${_idx + 1}/${_vers.length}…`);
    await rsleep(2000, 5000); // espaçamento humano entre vagas
    if (!(await running())) return;
    const el = _vers[_idx];
    try { el.dataset.oaVisto = "1"; } catch (_) {} // marca ANTES do clique: nunca re-entra num re-scrape
    OA.click(el); // abre nova aba (o content script da vaga assume)
  }
  async function proxima() { _idx++; if (await running()) { await rsleep(1000, 2500); abrirAtual(); } }

  // ── VAGA (nova aba): match → aplica ou fecha. SEMPRE fecha no fim (try/finally),
  // mesmo se der erro — era o bug "a aba não fechava". Fluxo REAL do GeekHunter:
  //   1) o form da PÁGINA tem o "Celular com DDD" (o "+55" é prefixo fixo do widget —
  //      NUNCA mexer nele, digita-se SÓ o número; o preencherTelefone do forms.js já
  //      faz isso) e o "Candidatar para a vaga" é type=submit DESSE form → preenche
  //      ANTES de clicar, senão a validação segura o submit e o modal nunca abre;
  //   2) o clique abre o modal "…tem algumas perguntinhas pra você": Chakra
  //      NumberInputs (anos de experiência — IA responde pelo CV) + checkbox LGPD
  //      (genérico marca: "privacidade" é consent) + "Finalizar candidatura";
  //   3) sucesso = "Obrigado pela sua candidatura!" → fecha o modal e a aba.
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
    // (a) Chakra NumberInput (type=text, role=spinbutton, name numérico ex. "135499"):
    // anos de experiência — IA responde pelo CV; tecnologia fora do CV → 0.
    for (const inp of container.querySelectorAll(".chakra-numberinput input, input[role='spinbutton']")) {
      try {
        if ((inp.value || "").trim()) continue; // já respondido
        const pergunta = enunciadoChakra(inp);
        if (/(remunera|sal[aá]ri|pretens)/i.test(pergunta)) continue; // salário → forms.js/config
        const resp = await perguntarCerebro(pergunta || "Quantos anos de experiência você tem com a principal tecnologia da vaga?", "NUMERO", [], ctx);
        let n = parseFloat(resp.replace(",", ".").replace(/[^\d.-]/g, ""));
        if (!isFinite(n) || n < 0) n = 0; // timeout/recusa → 0 (nunca trava nem descarta a vaga)
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
    if (!(await running()) || _tratou) return; // guarda: 1x por página (evita "2x na mesma vaga")
    _tratou = true;
    try {
      // pausa leve e randômica ao abrir a vaga: a SPA assenta e o ritmo fica humano
      await rsleep(1000, 2000);
      // DEDUP por vaga (igual Gupy/Solides/Senior): se a lista re-abrir a mesma vaga
      // (gatilho duplicado, re-scrape), fecha sem re-aplicar.
      const dup = await OA.bg({ type: "stats.isApplied", platform: PLAT, jobId: location.pathname });
      if (dup?.aplicou) { await status("Já aplicada — fechando."); return; } // finally fecha a aba
      const c = await cfg();
      const desc = (document.querySelector("[class*='description'], main, article")?.innerText || document.body.innerText || "").slice(0, 3500);
      const titulo = (document.querySelector("h1, [class*='title']")?.innerText || document.title || "").trim();
      const gate = await OA.deveAplicar(desc, { titulo, platform: PLAT });
      if (!gate.aplicar) {
        await status(`Descartando e fechando (${gate.motivo}): ${titulo.slice(0, 35)}`);
        return; // finally fecha a aba
      }
      const idioma = gate.idioma || "pt";

      // 1) preenche o form da PÁGINA (Celular com DDD etc.) ANTES do submit
      const cta = OA.findByText(CTA_APLICAR, { sel: "button, a, [role='button']" });
      const formPg = (cta && cta.closest("form")) || OA.melhorContainer("form, main");
      try { await OA.preencherCampos(formPg, { idioma, onStatus: (s) => status(s) }); } catch (_) {}
      await rsleep(700, 1300); // validação React (telefone) assenta antes do submit
      if (cta) {
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
      }

      let enviado = temSucesso(); // sem perguntinhas: sucesso direto após o CTA
      if (!enviado) {
        // 2) modal "…perguntinhas": NumberInputs (IA) + LGPD + "Finalizar candidatura".
        // finalizar SÓ com textos do MODAL — com "candidatar"/"aplicar" na lista o wizard
        // re-clicava o "Candidatar para a vaga" da página como se fosse o envio final.
        const r = await OA.rodarWizard(containerVaga, {
          preencher: (cont) => preencherPerguntasChakra(cont, { titulo, idioma }),
          avancar: ["salvar e continuar", "continuar", "próximo", "próxima", "avançar", "next"],
          finalizar: ["finalizar candidatura", "enviar candidatura", "enviar minha candidatura"],
          sucessoFrases: ["obrigado pela sua candidatura", "obrigado pela candidatura", "confirmar sua candidatura pelo e-mail", "confirmar sua candidatura pelo email", "sua candidatura foi enviada", "candidatura enviada com sucesso", "candidatura foi enviada", "recebemos sua candidatura", "sua candidatura foi realizada"],
          ctx: { idioma },
          pausarAntesEnvio: false, isRunning: running, onStatus: (s) => status(s),
        });
        // o "Obrigado…" às vezes renderiza DEPOIS do check do wizard → re-confere
        enviado = r === "enviado" || (r !== "parou" && (await esperaSucesso(7000)));
      }

      if (enviado) {
        await OA.bg({ type: "stats.applied", platform: PLAT, jobId: location.pathname, titulo });
        await status(`✅ Candidatura enviada: ${titulo.slice(0, 40)}`);
        // 3) fecha o modal de agradecimento ("Obrigado pela sua candidatura!")
        const fechar = OA.findByText(FECHAR, { sel: "button" }) || document.querySelector(".chakra-modal__close-btn, [aria-label='Close'], [aria-label*='fechar' i]");
        if (fechar) OA.click(fechar);
        await OA.sleep(800);
      }
    } catch (e) {
      try { console.log("[AutoApply][geek] erro na vaga:", e?.message); } catch (_) {}
    } finally {
      await OA.bg({ type: "tab.doneClose" }); // SEMPRE: fecha a aba + avança a lista
    }
  }

  // GUARDA de re-entrância (igual Gupy/Solides): cs.kick do SW + auto-start disparam
  // JUNTOS no load, e o check de _started ficava DEPOIS de dois awaits → os dois fluxos
  // passavam e a lista iniciava 2x (mesma vaga aberta duas vezes). Só o 1º entra.
  let _fluxo = false;
  async function rodar() {
    if (_fluxo) return;
    _fluxo = true;
    try {
      if (!(await running())) return;
      await OA.sleep(1000);
      if (ehVaga()) return tratarVaga();     // aba de vaga
      if (_started) return;                   // lista só inicia uma vez
      _started = true;
      await iniciarLista();
    } finally { _fluxo = false; }
  }

  chrome.runtime.onMessage.addListener((m, s, resp) => {
    if (m?.platform && m.platform !== PLAT) return;
    if (m?.type === "cs.kick") rodar();
    else if (m?.type === "cs.next") proxima(); // SW mandou abrir a próxima
    resp?.({ ok: true });
    return true;
  });
  OA.bg({ type: "run.isRunning" }).then((r) => { if (r?.running && r?.platform === PLAT) setTimeout(rodar, 1500); });
})();
