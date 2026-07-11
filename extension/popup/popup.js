import { getConfig, getState } from "../lib/store.js";

const $ = (id) => document.getElementById(id);
const bg = (m) => new Promise((r) => chrome.runtime.sendMessage(m, (resp) => r(resp || {})));

function setStatus(txt, running) {
  $("status").textContent = txt || (running ? "rodando" : "parado");
  $("dot").classList.toggle("on", !!running);
}

async function refresh() {
  const cfg = await getConfig();
  $("warn").classList.toggle("hidden", !!cfg.openrouter.apiKey);
  const st = await getState();
  setStatus(st.status, st.running);
  if (st.platform && st.counts) {
    const n = st.counts[st.platform] || 0;
    const teto = cfg.plataformas[st.platform]?.tetoDia || 0;
    $("count").textContent = `Hoje (${st.platform}): ${n}${teto ? " / " + teto : ""}`;
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
  setStatus("iniciando…", true);
  const r = await bg({ type: "run.start", platform });
  if (!r.ok) setStatus(r.erro || "erro ao iniciar", false);
});

$("stop").addEventListener("click", async () => {
  await bg({ type: "run.stop" });
  setStatus("parado", false);
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "status.update") {
    if (msg.status) setStatus(msg.status, true);
    refresh();
  }
});

refresh();
setInterval(refresh, 2000);
