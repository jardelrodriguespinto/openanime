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

  const CTA_APLICAR = ["quero me candidatar", "candidatar-se", "candidatar", "finalizar candidatura", "enviar candidatura", "enviar minha candidatura", "aplicar para a vaga", "aplicar"];
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
    OA.click(_vers[_idx]); // abre nova aba (o content script da vaga assume)
  }
  async function proxima() { _idx++; if (await running()) abrirAtual(); }

  // ── VAGA (nova aba): match → aplica ou fecha. SEMPRE fecha no fim (try/finally),
  // mesmo se der erro — era o bug "a aba não fechava". ─────────────────────────
  let _tratou = false;
  async function tratarVaga() {
    if (!(await running()) || _tratou) return; // guarda: 1x por página (evita "2x na mesma vaga")
    _tratou = true;
    try {
      await OA.sleep(1500);
      const c = await cfg();
      const desc = (document.querySelector("[class*='description'], main, article")?.innerText || document.body.innerText || "").slice(0, 3500);
      const titulo = (document.querySelector("h1, [class*='title']")?.innerText || document.title || "").trim();
      const gate = await OA.deveAplicar(desc, { titulo, platform: PLAT });
      if (!gate.aplicar) {
        await status(`Descartando e fechando (${gate.motivo}): ${titulo.slice(0, 35)}`);
        return; // finally fecha a aba
      }
      const cta = OA.findByText(CTA_APLICAR, { sel: "button, a, [role='button']" });
      if (cta) { OA.click(cta); await OA.sleep(2500); } // espera o form abrir
      const r = await OA.rodarWizard(() => OA.melhorContainer("[class*='chakra-modal'], form, [role='dialog'], [class*='modal'], main"), {
        avancar: ["salvar e continuar", "continuar", "próximo", "próxima", "avançar", "next"],
        finalizar: ["finalizar candidatura", "quero me candidatar", "enviar candidatura", "enviar minha candidatura", "candidatar-se", "candidatar", "aplicar para a vaga", "aplicar", "enviar"],
        sucessoFrases: ["confirmar sua candidatura pelo e-mail", "confirmar sua candidatura pelo email", "para que ela seja enviada", "sua candidatura foi enviada", "candidatura enviada com sucesso", "candidatura foi enviada", "recebemos sua candidatura", "sua candidatura foi realizada"],
        ctx: { idioma: gate.idioma || "pt" },
        pausarAntesEnvio: false, isRunning: running, onStatus: (s) => status(s),
      });
      if (r === "enviado") {
        await OA.bg({ type: "stats.applied", platform: PLAT, jobId: location.pathname, titulo });
        const fechar = OA.findByText(FECHAR, { sel: "button" }) || document.querySelector(".chakra-modal__close-btn");
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
