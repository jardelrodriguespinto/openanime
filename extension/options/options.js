import { getConfig, setConfig, DEFAULT_CONFIG, getResume, setResume } from "../lib/store.js";
import { t, aplicarI18n, idiomaUi } from "../lib/i18n.js";

const $ = (id) => document.getElementById(id);
const csv = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);
let LANG = "pt"; // idioma efetivo da interface (setado no load / no seletor)

const PLAT_LABEL = {
  linkedin: "LinkedIn",
  indeed: "Indeed",
  gupy: "Gupy",
  geekhunter: "GeekHunter",
  senior: "Senior",
  solides: "Solides",
};

function renderPlats(cfg) {
  const tb = $("plats");
  tb.innerHTML = "";
  for (const key of Object.keys(DEFAULT_CONFIG.plataformas)) {
    const p = cfg.plataformas[key] || DEFAULT_CONFIG.plataformas[key];
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="narrow"><input type="checkbox" data-p="${key}" data-k="enabled" ${p.enabled ? "checked" : ""} /></td>
      <td>${PLAT_LABEL[key]}</td>
      <td><input type="text" data-p="${key}" data-k="query" value="${p.query || ""}" /></td>
      <td class="narrow"><input type="number" data-p="${key}" data-k="limiarMatch" value="${p.limiarMatch ?? 0}" min="0" max="100" /></td>
      <td class="narrow"><input type="number" data-p="${key}" data-k="tetoDia" value="${p.tetoDia ?? 0}" min="0" /></td>`;
    tb.appendChild(tr);
  }
}

async function load() {
  const cfg = await getConfig();
  LANG = idiomaUi(cfg);
  $("ui_idioma").value = LANG;
  aplicarI18n(LANG);
  $("or_key").value = cfg.openrouter.apiKey || "";
  $("or_model").value = cfg.openrouter.model || "";
  $("or_model_match").value = cfg.openrouter.modelMatch || "";
  $("ai_key").value = cfg.assemblyia?.apiKey || "";
  const p = cfg.perfil;
  $("p_nome").value = p.nome || "";
  $("p_email").value = p.email || "";
  $("p_telefone").value = p.telefone || "";
  $("p_linkedin").value = p.linkedin || "";
  $("p_localizacao").value = p.localizacao || "";
  $("p_cargo").value = p.cargo_atual || "";
  $("p_senioridade").value = p.nivel_senioridade || "";
  $("p_cv").value = p.resumo_curriculo || "";
  $("p_clt").value = p.remuneracao_clt || "";
  $("p_pj").value = p.remuneracao_pj || "";
  $("p_usd").value = p.remuneracao_dolar || "";
  $("p_pretensao").value = p.pretensao_salarial || "";
  $("p_modalidades").value = (p.modalidades_aceitas || []).join(", ");
  $("p_regioes").value = (p.regioes_relocacao || []).join(", ");
  $("pausar").checked = cfg.pausarAntesEnvio !== false;
  renderPlats(cfg);
  const res = await getResume();
  $("cv_atual").textContent = res ? `${t(LANG, "cv_salvo")} ${res.name}` : t(LANG, "cv_none");
}

// Troca de idioma: salva NA HORA (independe do botão Salvar) e re-traduz a página,
// inclusive as strings dinâmicas (currículo salvo + diagnóstico vazio).
$("ui_idioma").addEventListener("change", async () => {
  LANG = $("ui_idioma").value;
  const cfg = await getConfig();
  cfg.ui = cfg.ui || {};
  cfg.ui.idioma = LANG;
  await setConfig(cfg);
  aplicarI18n(LANG);
  const res = await getResume();
  $("cv_atual").textContent = res ? `${t(LANG, "cv_salvo")} ${res.name}` : t(LANG, "cv_none");
  carregarDiag();
});

$("cv_file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  if (f.size > 8 * 1024 * 1024) { $("cv_atual").textContent = t(LANG, "cv_grande"); return; }
  const dataUrl = await new Promise((resolve) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.readAsDataURL(f); });
  await setResume({ dataUrl, name: f.name, type: f.type });
  $("cv_atual").textContent = `${t(LANG, "cv_salvo")} ${f.name}`;
});

async function salvar() {
  const cfg = await getConfig();
  cfg.openrouter.apiKey = $("or_key").value.trim();
  cfg.openrouter.model = $("or_model").value.trim() || DEFAULT_CONFIG.openrouter.model;
  cfg.openrouter.modelMatch = $("or_model_match").value.trim();
  cfg.assemblyia = cfg.assemblyia || {};
  cfg.assemblyia.apiKey = $("ai_key").value.trim();
  Object.assign(cfg.perfil, {
    nome: $("p_nome").value.trim(),
    email: $("p_email").value.trim(),
    telefone: $("p_telefone").value.trim(),
    linkedin: $("p_linkedin").value.trim(),
    localizacao: $("p_localizacao").value.trim(),
    cargo_atual: $("p_cargo").value.trim(),
    nivel_senioridade: $("p_senioridade").value.trim(),
    resumo_curriculo: $("p_cv").value.trim(),
    remuneracao_clt: $("p_clt").value.trim(),
    remuneracao_pj: $("p_pj").value.trim(),
    remuneracao_dolar: $("p_usd").value.trim(),
    pretensao_salarial: $("p_pretensao").value.trim(),
    modalidades_aceitas: csv($("p_modalidades").value),
    regioes_relocacao: csv($("p_regioes").value),
  });
  cfg.pausarAntesEnvio = $("pausar").checked;
  cfg.ui = cfg.ui || {};
  cfg.ui.idioma = $("ui_idioma").value;
  for (const inp of document.querySelectorAll("#plats input")) {
    const p = inp.dataset.p, k = inp.dataset.k;
    cfg.plataformas[p] = cfg.plataformas[p] || {};
    cfg.plataformas[p][k] = inp.type === "checkbox" ? inp.checked : (inp.type === "number" ? Number(inp.value) : inp.value.trim());
  }
  await setConfig(cfg);
  $("salvo").textContent = t(LANG, "salvo_ok");
  setTimeout(() => ($("salvo").textContent = ""), 2000);
}

async function testar() {
  $("teste_res").textContent = t(LANG, "testando");
  const key = $("or_key").value.trim();
  const model = $("or_model").value.trim() || DEFAULT_CONFIG.openrouter.model;
  if (!key) { $("teste_res").textContent = t(LANG, "informe_chave"); return; }
  try {
    const resp = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
      body: JSON.stringify({ model, max_tokens: 5, messages: [{ role: "user", content: "ok" }] }),
    });
    $("teste_res").textContent = resp.ok ? t(LANG, "chave_ok") : `❌ ${resp.status}`;
  } catch (e) {
    $("teste_res").textContent = "❌ " + e.message;
  }
}

// ── Diagnóstico (log baixável — a extensão não tem console p/ o usuário copiar) ──
function fmtDiag(log) {
  // o CONTEÚDO do diagnóstico fica em pt (é lido pelo desenvolvedor); só a
  // mensagem de vazio segue o idioma da interface.
  if (!log || !log.length) return t(LANG, "diag_vazio");
  return log.map((e) => {
    const d = e.data || {};
    const campos = (d.campos || []).map((c) => `    ${c.req ? "*" : " "} ${c.tag}/${c.type} name=${c.name || "-"} label="${c.label}" val=[${c.val}]`).join("\n");
    const botoes = (d.botoes || []).map((b) => `    ${b.off ? "[OFF]" : "[ on]"} "${b.txt}" ${b.name ? "name=" + b.name : ""}${b.id ? " id=" + b.id : ""}`).join("\n");
    return `===== ${e.t} | ${e.platform} | ${e.tag} =====\nURL: ${d.url}\nTitulo: ${d.title}\nFALTA PREENCHER: ${(d.vazios || []).join(" · ") || "(nada apontado)"}\nBOTOES:\n${botoes}\nCAMPOS:\n${campos}`;
  }).join("\n\n");
}
async function carregarDiag() {
  const r = await chrome.runtime.sendMessage({ type: "debug.get" });
  $("diagOut").value = fmtDiag(r?.log);
}
$("diagAtualizar").addEventListener("click", carregarDiag);
$("diagCopiar").addEventListener("click", () => { $("diagOut").select(); try { document.execCommand("copy"); } catch (_) {} });
$("diagBaixar").addEventListener("click", () => {
  const blob = new Blob([$("diagOut").value || ""], { type: "text/plain" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "autoapply-diagnostico.txt"; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
});
$("diagLimpar").addEventListener("click", async () => { await chrome.runtime.sendMessage({ type: "debug.clear" }); carregarDiag(); });

$("salvar").addEventListener("click", salvar);
$("testar").addEventListener("click", testar);
load();
carregarDiag();
