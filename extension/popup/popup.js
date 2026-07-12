import { getConfig, getState } from "../lib/store.js";
import { t, aplicarI18n, idiomaUi } from "../lib/i18n.js";

const $ = (id) => document.getElementById(id);
const bg = (m) => new Promise((r) => chrome.runtime.sendMessage(m, (resp) => r(resp || {})));
let LANG = "pt"; // idioma da interface (cfg.ui.idioma; re-checado a cada refresh)

function setStatus(txt, running) {
  $("status").textContent = txt || t(LANG, running ? "pop_rodando" : "pop_parado");
  $("dot").classList.toggle("on", !!running);
}

async function refresh() {
  const cfg = await getConfig();
  // segue a troca de idioma feita na dashboard mesmo com o popup aberto
  const lang = idiomaUi(cfg);
  if (lang !== LANG) { LANG = lang; aplicarI18n(LANG); }
  $("warn").classList.toggle("hidden", !!cfg.openrouter.apiKey);
  const st = await getState();
  // status vindos dos content scripts (progresso da automação) chegam como texto
  // pronto (pt) — mostramos como estão; só os rótulos fixos seguem o idioma.
  setStatus(st.status, st.running);
  if (st.platform && st.counts) {
    const n = st.counts[st.platform] || 0;
    const teto = cfg.plataformas[st.platform]?.tetoDia || 0;
    $("count").textContent = `${t(LANG, "pop_hoje")} (${st.platform}): ${n}${teto ? " / " + teto : ""}`;
  }
  // Só sincroniza o seletor com a plataforma em execução — senão o refresh de 2s
  // sobrescreve a escolha do usuário com o `st.platform` de um run ANTERIOR (o bug
  // "clico em Indeed e pula pra GeekHunter").
  if (st.platform && st.running) $("plataforma").value = st.platform;
}

$("cfg").addEventListener("click", () => chrome.runtime.openOptionsPage());

$("start").addEventListener("click", async () => {
  const platform = $("plataforma").value;
  const cfg = await getConfig();
  if (!cfg.openrouter.apiKey) { $("warn").classList.remove("hidden"); return; }
  setStatus(t(LANG, "pop_iniciando"), true);
  const r = await bg({ type: "run.start", platform });
  if (!r.ok) setStatus(r.erro || t(LANG, "pop_erro_start"), false);
});

$("startAll").addEventListener("click", async () => {
  const cfg = await getConfig();
  if (!cfg.openrouter.apiKey) { $("warn").classList.remove("hidden"); return; }
  setStatus(t(LANG, "pop_abrindo"), true);
  const r = await bg({ type: "run.startAll" });
  if (!r.ok) setStatus(r.erro || t(LANG, "pop_erro_startall"), false);
  else setStatus(`${t(LANG, "pop_rodando_pfx")} ${(r.platforms || []).join(", ")}`, true);
});

$("stop").addEventListener("click", async () => {
  await bg({ type: "run.stop" });
  setStatus(t(LANG, "pop_parado"), false);
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "status.update") {
    if (msg.status) setStatus(msg.status, true);
    refresh();
  }
});

refresh();
setInterval(refresh, 2000);
