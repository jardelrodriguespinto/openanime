// content/linkedin-rede.js — MODO REDE (recrutadores). Roda na busca de PESSOAS do
// LinkedIn (/search/results/people). Para cada card que tiver "Conectar": clica, gera uma
// NOTA PERSONALIZADA com a IA (OpenRouter) e envia; se a IA falhar, cai pro "Enviar sem
// nota". No fim da página vira pra próxima (?page=N) até acabar. Teto de segurança por
// rodada + delays humanos (convite em cadência de robô derruba a conta).
(function () {
  const OA = window.OA;
  const PLAT = "rede";
  const CK = "oaRedeCount"; // total de convites da rodada (resetado pelo run.start)
  const MAX_RODADA = 30;    // teto de segurança por execução (limite semanal do LinkedIn)

  const running = async () => { const r = await OA.bg({ type: "run.isRunning" }); return r?.running && r?.platform === PLAT; };
  const status = (txt, action) => OA.bg({ type: "status.push", platform: PLAT, status: txt, action });
  const config = async () => (await OA.bg({ type: "config.get" })).config;

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
  const limiteAtingido = () =>
    !!document.querySelector(".artdeco-modal") &&
    /limite|m[aá]ximo de convites|maximum number of invitations|invitation limit/i.test(document.querySelector(".artdeco-modal")?.innerText || "");

  // ── IA: nota curta de convite personalizada pelo cargo/perfil da dashboard ──────
  // Retorna { texto, erro } — o erro aparece no status pra ficar CRISTALINO que a
  // OpenRouter foi (ou não foi) chamada e por quê falhou.
  async function notaDaIa(nome, cargo) {
    try {
      const r = await OA.bg({
        type: "brain.answer",
        payload: {
          tipo: "TEXT",
          vagaTitulo: nome,
          idioma: "pt",
          pergunta:
            `Escreva a MENSAGEM que acompanha um convite de conexão no LinkedIn para ${nome}, ` +
            `recrutador(a) na área de tecnologia. 1 ou 2 frases (máximo 250 caracteres), em português, ` +
            `tom amigável e profissional, mencionando interesse em oportunidades como ${cargo || "desenvolvedor(a)"}. ` +
            "Responda SOMENTE o texto da mensagem, sem aspas, sem 'Olá,' genérico duplicado e sem markdown.",
        },
      });
      const txt = String(r?.resposta || "").trim();
      const erro = String(r?.erroIA || r?.erro || "");
      try { console.log("[OA-IA] nota p/", nome, "→", txt ? `"${txt.slice(0, 60)}…"` : "(vazio)", erro ? "| ERRO: " + erro : ""); } catch (_) {}
      if (!txt || erro) return { texto: "", erro: erro || "resposta vazia da IA" };
      return { texto: txt.slice(0, 280), erro: "" };
    } catch (e) {
      return { texto: "", erro: String(e?.message || e) };
    }
  }

  async function clicarBotaoModal(regex) {
    const modal = document.querySelector(".artdeco-modal");
    if (!modal || !OA.isVisible(modal)) return false;
    const btn = [...modal.querySelectorAll("button")].find((b) => OA.isVisible(b) && regex.test(((b.innerText || "") + " " + (b.getAttribute("aria-label") || "")).trim()));
    if (!btn) return false;
    OA.click(btn);
    await rsleep(600, 1500);
    return true;
  }
  async function fecharModal() {
    const x = document.querySelector(".artdeco-modal [aria-label='Descartar'], .artdeco-modal [aria-label='Dismiss']");
    if (x && OA.isVisible(x)) { try { OA.click(x); await OA.sleep(400); } catch (_) {} }
  }

  // Caminho COM nota: "Adicionar nota" → textarea ← mensagem da IA → "Enviar".
  async function enviarComNota(mensagem) {
    if (!(await clicarBotaoModal(/adicionar nota|add a note/i))) return false;
    const ta = await OA.waitFor(".artdeco-modal textarea", { timeout: 4000 });
    if (!ta) return false;
    OA.fillInput(ta, mensagem);
    await rsleep(500, 1200);
    // Com a nota digitada o botão primário vira "Enviar"/"Send" (exato — NÃO casar
    // com "Enviar sem nota").
    let ok = false;
    const modal = document.querySelector(".artdeco-modal");
    const enviar = modal && [...modal.querySelectorAll("button")].find((b) => OA.isVisible(b) && /^(enviar|send)$/i.test((b.innerText || "").trim()));
    if (enviar) { OA.click(enviar); ok = true; await rsleep(900, 2000); }
    await fecharModal();
    return ok;
  }
  // Caminho SEM nota (fallback se a IA falhou): "Enviar sem nota"/"Send without a note".
  async function enviarSemNota() {
    const ok = await clicarBotaoModal(/enviar sem nota|send without a note|^enviar agora$|^send now$/i);
    await fecharModal();
    return ok;
  }

  // Processa TODOS os connects da página atual. Retorna: nº enviados | "limite".
  async function pagina(totalRef, cargo) {
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
      await status(`🤖 Chamando a OpenRouter para gerar a nota de ${nome}…`);
      const { texto: nota, erro: erroIa } = await notaDaIa(nome, cargo);
      if (erroIa) await status(`⚠️ IA não respondeu p/ ${nome} (${erroIa}) — enviando sem nota.`);
      const ok = nota ? await enviarComNota(nota) : false;
      const enviado = ok || (await enviarSemNota());
      if (enviado) {
        enviados++; totalRef.n++;
        await chrome.storage.local.set({ [CK]: totalRef.n });
        await status(`✅ Convite enviado${nota ? " COM nota (IA)" : " sem nota (IA falhou)"}: ${nome} (${totalRef.n}/${MAX_RODADA})`, "conectou");
      } else {
        await status(`⏭️ ${nome}: não consegui enviar — pulando.`);
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
    // Espera o estado do run propagar (o SW grava o estado DEPOIS de criar a aba —
    // sem essa espera o script bootava, via "parado" e nunca começava).
    let ativo = false;
    for (let i = 0; i < 12 && !(ativo = await running()); i++) await OA.sleep(1000);
    if (!ativo) return;
    const cfg = await config();
    const cargo = cfg?.perfil?.cargo_atual || "";
    const totalRef = { n: (await chrome.storage.local.get(CK))[CK] || 0 };
    const termo = decodeURIComponent(new URLSearchParams(location.search).get("keywords") || "");
    await status(`🔗 Modo rede: conectando com "${termo}" (IA escreve as notas)…`);
    let paginas = parseInt(new URLSearchParams(location.search).get("page") || "1", 10);
    let ociosas = 0; // páginas seguidas sem nenhum convite possível → fim
    while (await running() && ociosas < 2 && paginas < 30 && totalRef.n < MAX_RODADA) {
      const r = await pagina(totalRef, cargo);
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
