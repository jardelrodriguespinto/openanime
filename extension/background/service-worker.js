// Service worker MV3 (efêmero!). NÃO guarda estado de candidatura nem loops:
// só roteia mensagens, chama a OpenRouter (o "cérebro") e mexe no storage.
// O loop por-vaga mora no content script.

import { getConfig, setConfig, getState, setState, contador, registrarCandidatura, jaAplicou, getResume } from "../lib/store.js";
import { avaliarMatch, avaliarMatchTitulo, transcreverAudio, responderPergunta, avaliarHabilidades } from "../lib/openrouter.js";

const PLATAFORMAS = {
  linkedin: { search: (q) => `https://www.linkedin.com/jobs/search/?keywords=${encodeURIComponent(q)}&f_AL=true` },
  indeed: { search: (q) => `https://br.indeed.com/jobs?q=${encodeURIComponent(q)}&l=&from=searchOnHP` },
  gupy: { search: (q) => `https://portal.gupy.io/job-search/term=${encodeURIComponent(q)}` },
  // searchTerm é o formato oficial da busca do GeekHunter (schema.org SearchAction).
  geekhunter: { search: (q) => `https://www.geekhunter.com/pt/vagas?searchTerm=${encodeURIComponent(q)}` },
  senior: { search: (q) => `https://www.portaldetalentos.senior.com.br/search/vacancies?jobFunction=${encodeURIComponent(q)}` },
  // /vagas/todos/<termo> NÃO existe mais (voltava "0 vagas") → abre a lista e o
  // content script digita a query no próprio formulário de busca do portal.
  solides: { search: () => `https://vagas.solides.com.br/vagas` },
  // Modo REDE: busca de PESSOAS (recrutadores) no LinkedIn — conecta com todos os
  // cards que tiverem "Conectar", página por página, sem IA. Termo vem da dashboard.
  rede: { search: (q) => `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(q || "tech recruiter")}&origin=CLUSTER_EXPANSION` },
};
// "Iniciar tudo" NÃO inclui a rede (convites em massa não devem disparar junto
// com as candidaturas — o usuário inicia a rede de propósito).
const TODAS = Object.keys(PLATAFORMAS).filter((p) => p !== "rede");
// Chaves de fila/paginação por plataforma (resetadas ao (re)iniciar aquela plataforma).
const QUEUE_KEYS = {
  indeed: ["oaIndeedQueue", "oaIndeedStart"],
  gupy: ["oaGupyQueue", "oaGupyPage", "oaGupyList"],
  // GeekHunter agora usa fila na mesma aba (+ página/base p/ paginar como o Gupy).
  geekhunter: ["oaGeekQueue", "oaGeekPage", "oaGeekList"],
  solides: ["oaSolidesQueue", "oaSolidesPage", "oaSolidesList"],
  linkedin: [], senior: [],
  rede: ["oaRedeCount"], // contador de convites da rodada (resetado ao iniciar)
};
// Descobre a plataforma pela URL da aba que chamou → run.isRunning funciona por-plataforma
// (várias rodando ao mesmo tempo) SEM tocar nos content scripts. Frames do reCAPTCHA
// (google/recaptcha.net) não casam → caem no fallback "algum run ativo".
function platformFromUrl(url) {
  const u = (url || "").toLowerCase();
  // ANTES do linkedin genérico: a busca de pessoas é o modo REDE, não o de vagas.
  if (u.includes("linkedin.com/search/results/people")) return "rede";
  if (u.includes("linkedin.com")) return "linkedin";
  if (u.includes("indeed.com")) return "indeed";
  if (u.includes("gupy.io")) return "gupy";
  if (u.includes("geekhunter.com")) return "geekhunter";
  if (u.includes("senior.com.br")) return "senior";
  if (u.includes("solides.com.br")) return "solides";
  return "";
}
// Estado multi-plataforma, retrocompatível com o antigo singular {platform,tabId}.
const platsDe = (st) => st.platforms || (st.platform ? [st.platform] : []);
const tabsDe = (st) => st.tabs || (st.platform && st.tabId ? { [st.platform]: st.tabId } : {});

// Broadcast de status (popup escuta). Best-effort.
function broadcast(msg) {
  chrome.runtime.sendMessage({ type: "status.update", ...msg }).catch(() => {});
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      switch (msg?.type) {
        case "config.get":
          return sendResponse({ ok: true, config: await getConfig() });
        case "config.set":
          await setConfig(msg.config);
          return sendResponse({ ok: true });
        case "assemblyia.match": {
          const cfg = await getConfig();
          const r = await transcreverAudio(cfg, msg.payload || {});
          return sendResponse({ ok: true, ...r });
        }
        case "brain.match": {
          const cfg = await getConfig();
          const r = await avaliarMatch(cfg, msg.payload || {});
          return sendResponse({ ok: true, ...r });
        }
        // Pré-gate por TÍTULO (na lista, antes de abrir a vaga): área + senioridade
        // vs. perfil. É a 1ª consulta de IA da vaga — "analisar tudo antes de aplicar".
        case "brain.title": {
          const cfg = await getConfig();
          const r = await avaliarMatchTitulo(cfg, msg.payload || {});
          return sendResponse({ ok: true, ...r });
        }
        case "brain.answer": {
          const cfg = await getConfig();
          const r = await responderPergunta(cfg, msg.payload || {});
          // `erro` = por que (se) a IA falhou e caiu no fallback — o content script
          // mostra no popup pra deixar claro QUE a resposta não veio da IA e por quê.
          return sendResponse({ ok: true, resposta: r.resposta, erroIA: r.erro || "" });
        }
        case "brain.skills": {
          const cfg = await getConfig();
          const niveis = await avaliarHabilidades(cfg, msg.payload || {});
          return sendResponse({ ok: true, niveis });
        }
        case "resume.get":
          return sendResponse({ ok: true, resume: await getResume() });

        // Aba de vaga (ex.: GeekHunter) terminou → fecha ESSA aba e manda a aba da
        // LISTA (state.tabId) abrir a próxima. É como fechamos abas (content script
        // não pode) e evita "abrir inúmeras abas" (uma por vez).
        case "tab.doneClose": {
          const st = await getState();
          const plat = platformFromUrl(sender?.tab?.url) || st.platform;
          const listTab = tabsDe(st)[plat];
          if (listTab && platsDe(st).includes(plat)) chrome.tabs.sendMessage(listTab, { type: "cs.next", platform: plat }).catch(() => {});
          if (sender?.tab?.id && sender.tab.id !== listTab) { try { await chrome.tabs.remove(sender.tab.id); } catch (_) {} }
          return sendResponse({ ok: true });
        }

        case "stats.canApply": {
          const cfg = await getConfig();
          const plat = cfg.plataformas[msg.platform] || {};
          const n = await contador(msg.platform);
          const teto = plat.tetoDia || 0;
          return sendResponse({ ok: true, permitido: teto <= 0 || n < teto, count: n, teto });
        }
        case "stats.isApplied":
          return sendResponse({ ok: true, aplicou: await jaAplicou(msg.platform, msg.jobId) });
        case "stats.applied": {
          const n = await registrarCandidatura(msg.platform, msg.jobId);
          broadcast({ platform: msg.platform, action: "candidatou", vaga: msg.titulo || "", count: n });
          return sendResponse({ ok: true, count: n });
        }

        case "run.start": {
          // Abre a aba da busca e ADICIONA a plataforma ao conjunto em execução (permite
          // várias ao mesmo tempo). Reseta só a fila DESTA plataforma.
          const cfg = await getConfig();
          const plat = PLATAFORMAS[msg.platform];
          if (!plat) return sendResponse({ ok: false, erro: "Plataforma ainda não implementada nesta versão." });
          const query = cfg.plataformas[msg.platform]?.query || (msg.platform === "rede" ? "tech recruiter" : "desenvolvedor");
          await chrome.storage.local.remove(QUEUE_KEYS[msg.platform] || []);
          // fecha a aba do run ANTERIOR desta plataforma (senão a antiga continua
          // rodando — a checagem é por URL — e briga com a nova pela fila zerada)
          const prevTab = tabsDe(await getState())[msg.platform];
          if (prevTab) { try { await chrome.tabs.remove(prevTab); } catch (_) {} }
          const tab = await chrome.tabs.create({ url: plat.search(query), active: true });
          const st = await getState();
          const platforms = [...new Set([...platsDe(st), msg.platform])];
          const tabs = { ...tabsDe(st), [msg.platform]: tab.id };
          await setState({ running: true, platforms, tabs, platform: msg.platform, tabId: tab.id, status: "abrindo busca…" });
          return sendResponse({ ok: true, tabId: tab.id });
        }
        case "run.startAll": {
          // Dispara TODAS as plataformas ao mesmo tempo (uma aba de busca por plataforma).
          const cfg = await getConfig();
          await chrome.storage.local.remove([].concat(...Object.values(QUEUE_KEYS)));
          // fecha as abas do run ANTERIOR (clicar "Iniciar tudo" de novo deixava as
          // abas velhas rodando — checagem por URL — brigando com as novas pela fila)
          for (const tid of Object.values(tabsDe(await getState()))) { try { await chrome.tabs.remove(tid); } catch (_) {} }
          // Grava running/platforms ANTES de abrir as abas: o content script boota junto
          // com a página e consultava run.isRunning antes do setState → via "parado" e
          // nunca começava (o "Iniciar tudo fica parado"). As abas entram no estado depois.
          await setState({ running: true, platforms: TODAS, tabs: {}, platform: TODAS[0], tabId: 0, status: "abrindo TODAS as buscas…" });
          const tabs = {};
          for (const p of TODAS) {
            const query = cfg.plataformas?.[p]?.query || "desenvolvedor";
            try { const tab = await chrome.tabs.create({ url: PLATAFORMAS[p].search(query), active: false }); tabs[p] = tab.id; } catch (_) {}
          }
          await setState({ tabs, tabId: tabs[TODAS[0]] || 0 });
          return sendResponse({ ok: true, platforms: TODAS });
        }
        case "run.stop": {
          // Chamado por um CONTENT SCRIPT (teto do dia / fim das páginas) → remove SÓ
          // aquela plataforma do conjunto; as outras seguem. Antes era stop GLOBAL: no
          // "Iniciar tudo", a 1ª plataforma a terminar derrubava as outras 5. O stop
          // global fica pro ⏹ do popup (sem sender.tab) ou quando é a última plataforma.
          const plat = msg.platform || platformFromUrl(sender?.tab?.url);
          const st = await getState();
          const plats = platsDe(st);
          if (plat && plats.includes(plat) && plats.length > 1) {
            const platforms = plats.filter((p) => p !== plat);
            const tabs = { ...tabsDe(st) };
            delete tabs[plat];
            const first = platforms[0];
            await setState({ running: true, platforms, tabs, platform: first, tabId: tabs[first] || 0, status: `${plat}: finalizado — ${platforms.length} rodando` });
            broadcast({ platform: plat, action: "finalizado" });
            return sendResponse({ ok: true, global: false });
          }
          await setState({ running: false, platforms: [], tabs: {}, status: "parado" });
          broadcast({ action: "parado" });
          return sendResponse({ ok: true, global: true });
        }
        case "run.isRunning": {
          const st = await getState();
          const plats = platsDe(st);
          const callerPlat = platformFromUrl(sender?.tab?.url);
          const running = callerPlat ? plats.includes(callerPlat) : plats.length > 0;
          return sendResponse({ ok: true, running, platform: callerPlat || plats[0] || "", status: st.status || "" });
        }

        case "util.sleep": {
          // Timer no SW p/ content script de aba em BACKGROUND ("Iniciar tudo" deixa 5
          // abas ocultas e o Chrome agrupa timers de aba oculta em ~1/min → automação
          // "parada"). O canal de resposta aberto mantém o SW vivo até responder.
          const ms = Math.min(Math.max(Number(msg.ms) || 0, 0), 60000);
          setTimeout(() => { try { sendResponse({ ok: true }); } catch (_) {} }, ms);
          return;
        }

        // Diagnóstico copiável/baixável (o usuário não tem console numa extensão): a
        // automação grava aqui passo + campos do form quando trava; o dashboard baixa .txt.
        case "debug.push": {
          const KEY = "oaDebugLog";
          const cur = (await chrome.storage.local.get(KEY))[KEY] || [];
          cur.push({ t: new Date().toISOString(), platform: platformFromUrl(sender?.tab?.url) || msg.platform || "", tag: msg.tag || "", data: msg.data });
          while (cur.length > 300) cur.shift();
          await chrome.storage.local.set({ [KEY]: cur });
          return sendResponse({ ok: true });
        }
        case "debug.get":
          return sendResponse({ ok: true, log: (await chrome.storage.local.get("oaDebugLog")).oaDebugLog || [] });
        case "debug.clear":
          await chrome.storage.local.remove("oaDebugLog");
          return sendResponse({ ok: true });

        case "status.push": {
          // content script reportando progresso → repassa ao popup e salva
          await setState({ status: msg.status || "" });
          broadcast({ platform: msg.platform, status: msg.status, action: msg.action });
          return sendResponse({ ok: true });
        }

        default:
          return sendResponse({ ok: false, erro: "tipo desconhecido: " + msg?.type });
      }
    } catch (e) {
      return sendResponse({ ok: false, erro: String(e?.message || e) });
    }
  })();
  return true; // resposta assíncrona
});

// Quando a aba da busca termina de carregar e estamos "running", dá um kick no CS.
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  if (info.status !== "complete") return;
  const st = await getState();
  const tabs = tabsDe(st), plats = platsDe(st);
  for (const [plat, tid] of Object.entries(tabs)) {
    if (tid === tabId && plats.includes(plat)) chrome.tabs.sendMessage(tabId, { type: "cs.kick", platform: plat }).catch(() => {});
  }
});
