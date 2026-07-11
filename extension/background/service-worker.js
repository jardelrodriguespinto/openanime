// Service worker MV3 (efêmero!). NÃO guarda estado de candidatura nem loops:
// só roteia mensagens, chama a OpenRouter (o "cérebro") e mexe no storage.
// O loop por-vaga mora no content script.

import { getConfig, setConfig, getState, setState, contador, registrarCandidatura, jaAplicou, getResume } from "../lib/store.js";
import { avaliarMatch, transcreverAudio, responderPergunta, avaliarHabilidades } from "../lib/openrouter.js";

const PLATAFORMAS = {
  linkedin: { search: (q) => `https://www.linkedin.com/jobs/search/?keywords=${encodeURIComponent(q)}&f_AL=true` },
  indeed: { search: (q) => `https://br.indeed.com/jobs?q=${encodeURIComponent(q)}&l=&from=searchOnHP` },
  gupy: { search: (q) => `https://portal.gupy.io/job-search/term=${encodeURIComponent(q)}` },
  geekhunter: { search: () => `https://www.geekhunter.com/pt/vagas` },
  senior: { search: (q) => `https://www.portaldetalentos.senior.com.br/search/vacancies?jobFunction=${encodeURIComponent(q)}` },
  solides: { search: (q) => `https://vagas.solides.com.br/vagas/todos/${encodeURIComponent(q)}` },
};

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
        case "brain.answer": {
          const cfg = await getConfig();
          const resposta = await responderPergunta(cfg, msg.payload || {});
          return sendResponse({ ok: true, resposta });
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
          if (st.tabId && st.running) chrome.tabs.sendMessage(st.tabId, { type: "cs.next", platform: st.platform }).catch(() => {});
          if (sender?.tab?.id && sender.tab.id !== st.tabId) { try { await chrome.tabs.remove(sender.tab.id); } catch (_) {} }
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
          // Abre/garante a aba da busca e manda o content script começar.
          const cfg = await getConfig();
          const plat = PLATAFORMAS[msg.platform];
          const query = cfg.plataformas[msg.platform]?.query || "desenvolvedor";
          if (!plat) return sendResponse({ ok: false, erro: "Plataforma ainda não implementada nesta versão." });
          // reseta estado de fila/paginação de um run anterior
          await chrome.storage.local.remove(["oaIndeedQueue", "oaIndeedStart", "oaGupyQueue", "oaGupyPage", "oaGupyList", "oaGeekQueue", "oaSolidesQueue", "oaSolidesPage", "oaSolidesList"]);
          const tab = await chrome.tabs.create({ url: plat.search(query), active: true });
          await setState({ running: true, platform: msg.platform, tabId: tab.id, status: "abrindo busca…" });
          return sendResponse({ ok: true, tabId: tab.id });
        }
        case "run.stop": {
          await setState({ running: false, status: "parado" });
          broadcast({ action: "parado" });
          return sendResponse({ ok: true });
        }
        case "run.isRunning": {
          const st = await getState();
          return sendResponse({ ok: true, running: !!st.running, platform: st.platform, status: st.status || "" });
        }

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
  if (st.running && st.tabId === tabId) {
    chrome.tabs.sendMessage(tabId, { type: "cs.kick", platform: st.platform }).catch(() => {});
  }
});
