// content/linkedin-rede.js — MODO REDE (recrutadores). Roda na busca de PESSOAS do
// LinkedIn (/search/results/people). Para cada card que tiver "Conectar": clica e, no
// modal, clica DIRETO em "Enviar sem nota" (pedido do usuário — sem nota, sem IA).
// Depois vai pro próximo card; no fim da página vira pra próxima (?page=N) até acabar.
// Teto de segurança por rodada + delays humanos (convite em cadência de robô derruba a conta).
(function () {
  const OA = window.OA;
  const PLAT = "rede";
  const CK = "oaRedeCount"; // total de convites da rodada (resetado pelo run.start)
  const MAX_RODADA = 30;    // teto de segurança por execução (limite semanal do LinkedIn)

  const running = async () => { const r = await OA.bg({ type: "run.isRunning" }); return r?.running && r?.platform === PLAT; };
  const status = (txt, action) => OA.bg({ type: "status.push", platform: PLAT, status: txt, action });

  // Delays HUMANOS entre convites.
  const rint = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
  const rsleep = (a, b) => OA.sleep(rint(a, b));

  // ── Seletores ancorados em atributos estáveis (o LinkedIn troca as classes sempre):
  //   link novo:      a[href*='/preload/search-custom-invite/'] ("Convidar X para se conectar")
  //   botão clássico: button[aria-label^='Convidar'] / button[aria-label^='Invite']
  function alvosConectar() {
    return [
      ...document.querySelectorAll("a[href*='/preload/search-custom-invite/']"),
      ...document.querySelectorAll("button[aria-label^='Convidar'], button[aria-label^='Invite']"),
    ].filter((el) => OA.isVisible(el));
  }

  const nomeDoAlvo = (el) =>
    ((el.getAttribute("aria-label") || "").replace(/^(convidar|invite)\s+/i, "").replace(/\s+para se conectar.*$/i, "").trim()) ||
    ((el.closest("div")?.innerText || "").split("\n")[0] || "recrutador").slice(0, 40);

  // Diálogo de LIMITE de convites ("Você já enviou o número máximo...") → encerra.
  // A UI nova do LinkedIn não usa .artdeco-modal em toda tela de convite → procuramos
  // QUALQUER dialog/modal visível (role=dialog cobre os overlays atuais).
  const dlgSel = "[role='dialog'], .artdeco-modal";
  const dialogoAberto = () => [...document.querySelectorAll(dlgSel)].find((d) => OA.isVisible(d));

  const limiteAtingido = () => {
    const d = dialogoAberto();
    return !!d && /limite|m[aá]ximo de convites|maximum number of invitations|invitation limit/i.test(d.innerText || "");
  };

  async function fecharModal() {
    const d = dialogoAberto();
    const x = d && [...d.querySelectorAll("[aria-label='Descartar'], [aria-label='Dismiss'], [aria-label='Fechar'], [aria-label='Dismiss'], button[aria-label*='fechar' i], button[aria-label*='close' i]")]
      .find(OA.isVisible);
    if (x) { try { OA.click(x); await OA.sleep(400); } catch (_) {} }
  }

  // Clica "Enviar sem nota"/"Send without a note" no modal do convite. Procura em QUALQUER
  // dialog visível (a UI nova do search-custom-invite não é .artdeco-modal) — era o motivo
  // de "não conectar com ninguém": modal existia, mas o seletor antigo não achava.
  async function enviarSemNota() {
    let modal = null;
    for (let i = 0; i < 16 && !modal; i++) { // até ~8s: overlay do convite demora a montar
      if (!(await running())) return false;
      await OA.sleep(500);
      modal = dialogoAberto();
    }
    if (!modal) return false;
    const btn = [...modal.querySelectorAll("button")].find((b) => OA.isVisible(b) &&
      /enviar sem nota|send without a note/i.test(((b.innerText || "") + " " + (b.getAttribute("aria-label") || ""))));
    if (!btn) { await fecharModal(); return false; }
    await rsleep(500, 1400);
    OA.click(btn);
    await rsleep(900, 2000);
    await fecharModal(); // às vezes sobra overlay do modal
    return true;
  }

  // Processa TODOS os connects da página atual. Retorna: nº enviados | "limite".
  async function pagina(totalRef) {
    for (let i = 0; i < 4; i++) { window.scrollTo(0, document.body.scrollHeight); await OA.sleep(900); }
    window.scrollTo(0, 0);
    const feitos = new Set();
    let enviados = 0;
    while (await running()) {
      if (totalRef.n >= MAX_RODADA) return enviados;
      const alvos = alvosConectar().filter((el) => !feitos.has(el.href || el.getAttribute("aria-label")));
      if (!alvos.length) break;
      const alvo = alvos[0];
      const nome = nomeDoAlvo(alvo);
      feitos.add(alvo.href || alvo.getAttribute("aria-label"));
      // Rede de segurança: se o clique NAVEGAR (sair da busca de pessoas), volta.
      const urlAntes = location.href;
      OA.clickForte(alvo);
      await rsleep(1300, 2800);
      if (!location.href.startsWith(urlAntes.split("?")[0])) { history.back(); await rsleep(1500, 2500); continue; }
      if (limiteAtingido()) return "limite";
      if (await enviarSemNota()) {
        enviados++; totalRef.n++;
        await chrome.storage.local.set({ [CK]: totalRef.n });
        await status(`✅ Convite enviado: ${nome} (${totalRef.n}/${MAX_RODADA})`, "conectou");
      } else {
        await status(`⏭️ ${nome}: não achei "Enviar sem nota" — pulando.`);
        await fecharModal();
      }
      await rsleep(3500, 8000); // espaçamento anti-bloqueio entre convites
    }
    return enviados;
  }

  // Próxima página: botão "Avançar" da paginação; fallback pela URL ?page=N.
  async function proximaPagina(nAtual) {
    const next = [...document.querySelectorAll("button")].find((b) => OA.isVisible(b) && !b.disabled &&
      /avan[çc]ar|pr[oó]xim|next/i.test((b.innerText || "") + " " + (b.getAttribute("aria-label") || "")));
    if (next) {
      OA.click(next);
      await rsleep(2500, 4500);
      return true;
    }
    const u = new URL(location.href);
    u.searchParams.set("page", String(nAtual + 1));
    await status(`Indo para a página ${nAtual + 1}…`);
    location.href = u.toString(); // recarrega → main() retoma (run.isRunning persiste)
    return true;
  }

  async function main() {
    await OA.fecharBanners();
    // Espera o estado do run propagar (o SW grava o estado ANTES de criar a aba, mas
    // essa espera cobre qualquer atraso de propagação sem deixar o script "mudo").
    let ativo = false;
    for (let i = 0; i < 12 && !(ativo = await running()); i++) await OA.sleep(1000);
    if (!ativo) return;
    const totalRef = { n: (await chrome.storage.local.get(CK))[CK] || 0 };
    const termo = decodeURIComponent(new URLSearchParams(location.search).get("keywords") || "");
    await status(`🔗 Modo rede: conectando com "${termo}" (enviar sem nota)…`);
    let paginas = parseInt(new URLSearchParams(location.search).get("page") || "1", 10);
    let ociosas = 0; // páginas seguidas sem nenhum convite possível → fim
    while (await running() && ociosas < 2 && paginas < 30 && totalRef.n < MAX_RODADA) {
      const r = await pagina(totalRef);
      if (r === "limite") {
        await status(`🛑 LinkedIn bloqueou novos convites (limite semanal). ${totalRef.n} convite(s) nesta rodada.`);
        await OA.bg({ type: "run.stop" });
        return;
      }
      if (!(await running())) break;
      ociosas = r === 0 ? ociosas + 1 : 0;
      // Próxima página: botão "Avançar" OU fallback pela URL (?page=N).
      const temBotao = !!document.querySelector("button[aria-label='Avançar'], .artdeco-pagination__button--next:not([disabled])");
      if (!temBotao && paginas >= 30) break;
      await proximaPagina(paginas);
      paginas++;
      // Botão = SPA atualiza na hora (loop continua); URL = recarregou e este
      // processo já terminou (o main() roda de novo com run.isRunning persistindo).
    }
    if (await running()) {
      await status(`Fim. ${totalRef.n} convite(s) enviado(s) nesta rodada. ✅`);
      await OA.bg({ type: "run.stop" });
    }
  }

  main();
})();
