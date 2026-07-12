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
    _vers = [...document.querySelectorAll("a, button, [role='button'], p")].filter((e) => OA.isVisible(e) && /visualizar vaga/i.test(e.innerText || ""));
    _idx = 0;
    if (!_vers.length) return status("Não achei 'Visualizar vaga'. Abra a lista do GeekHunter e clique ▶️.");
    await status(`${_vers.length} vaga(s). Abrindo 1 por vez…`);
    abrirAtual();
  }
  async function abrirAtual() {
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
    await rsleep(4000, 10000); // espaçamento humano entre vagas
    if (!(await running())) return;
    OA.click(_vers[_idx]); // abre nova aba (o content script da vaga assume)
  }
  async function proxima() { _idx++; if (await running()) { await rsleep(2000, 5000); abrirAtual(); } }

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

  // Perguntas do modal: Chakra NumberInput (input type=text, role=spinbutton, name
  // numérico ex.: "135499") SEM <label> — o enunciado é o <p class="chakra-text"> irmão
  // do .chakra-numberinput → o labelFor() genérico não acha e mandaria TEXTO pro campo
  // (pattern [0-9] recusa → "Finalizar candidatura" travava). Aqui: acha o enunciado,
  // pergunta pro cérebro (NUMERO — responde pelo CV; tecnologia fora do CV → 0) e
  // preenche. Salário/remuneração fica pro genérico (vem do CONFIG, nunca da IA).
  async function preencherPerguntasChakra(container, ctx) {
    for (const inp of container.querySelectorAll(".chakra-numberinput input, input[role='spinbutton']")) {
      try {
        if ((inp.value || "").trim()) continue; // já respondido
        const bloco = inp.closest(".chakra-numberinput")?.parentElement || inp.parentElement;
        const pergunta = (bloco?.querySelector("p, label")?.innerText || "").replace(/^\*\s*/, "").trim();
        if (/(remunera|sal[aá]ri|pretens)/i.test(pergunta)) continue; // salário → forms.js/config
        const call = OA.bg({ type: "brain.answer", payload: { pergunta: pergunta || "Quantos anos de experiência você tem com a principal tecnologia da vaga?", tipo: "NUMERO", opcoes: [], vagaTitulo: ctx.titulo || "", vagaEmpresa: "", idioma: ctx.idioma || "pt" } });
        const r = await Promise.race([call, new Promise((res) => setTimeout(() => res(null), 30000))]);
        let n = parseFloat(String(r?.resposta || "").replace(",", ".").replace(/[^\d.-]/g, ""));
        if (!isFinite(n) || n < 0) n = 0; // timeout/recusa → 0 (nunca trava nem descarta a vaga)
        OA.fillInput(inp, String(Number.isInteger(n) ? n : Math.round(n)));
        await OA.sleep(250);
      } catch (_) {}
    }
  }

  async function tratarVaga() {
    if (!(await running()) || _tratou) return; // guarda: 1x por página (evita "2x na mesma vaga")
    _tratou = true;
    try {
      // pausa leve e randômica ao abrir a vaga: a SPA assenta e o ritmo fica humano
      await rsleep(2000, 4000);
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
      await rsleep(1400, 2600); // validação React (telefone) assenta antes do submit
      if (cta) {
        OA.click(cta);
        // espera o modal de perguntas OU o sucesso direto (vaga sem perguntinhas)
        let t0 = Date.now();
        while (Date.now() - t0 < 9000 && !modalPerguntas() && !temSucesso()) await OA.sleep(400);
        if (!modalPerguntas() && !temSucesso()) {
          // submit segurado (validação) → re-preenche e reforça com clique FORTE
          try { await OA.preencherCampos(formPg, { idioma, onStatus: (s) => status(s) }); } catch (_) {}
          await rsleep(1000, 1800);
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

  async function rodar() {
    if (!(await running())) return;
    await OA.sleep(1000);
    if (ehVaga()) return tratarVaga();       // aba de vaga
    if (_started) return;                     // lista só inicia uma vez
    _started = true;
    iniciarLista();
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
