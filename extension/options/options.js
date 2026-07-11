import { getConfig, setConfig, DEFAULT_CONFIG, getResume, setResume } from "../lib/store.js";

const $ = (id) => document.getElementById(id);
const csv = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);

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
  $("or_key").value = cfg.openrouter.apiKey || "";
  $("or_model").value = cfg.openrouter.model || "";
  $("or_model_match").value = cfg.openrouter.modelMatch || "";
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
  $("cv_atual").textContent = res ? `✅ salvo: ${res.name}` : "nenhum currículo salvo";
}

$("cv_file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  if (f.size > 8 * 1024 * 1024) { $("cv_atual").textContent = "❌ arquivo muito grande (máx 8 MB)"; return; }
  const dataUrl = await new Promise((resolve) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.readAsDataURL(f); });
  await setResume({ dataUrl, name: f.name, type: f.type });
  $("cv_atual").textContent = `✅ salvo: ${f.name}`;
});

async function salvar() {
  const cfg = await getConfig();
  cfg.openrouter.apiKey = $("or_key").value.trim();
  cfg.openrouter.model = $("or_model").value.trim() || DEFAULT_CONFIG.openrouter.model;
  cfg.openrouter.modelMatch = $("or_model_match").value.trim();
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
  for (const inp of document.querySelectorAll("#plats input")) {
    const p = inp.dataset.p, k = inp.dataset.k;
    cfg.plataformas[p] = cfg.plataformas[p] || {};
    cfg.plataformas[p][k] = inp.type === "checkbox" ? inp.checked : (inp.type === "number" ? Number(inp.value) : inp.value.trim());
  }
  await setConfig(cfg);
  $("salvo").textContent = "✅ salvo";
  setTimeout(() => ($("salvo").textContent = ""), 2000);
}

async function testar() {
  $("teste_res").textContent = "testando…";
  const key = $("or_key").value.trim();
  const model = $("or_model").value.trim() || DEFAULT_CONFIG.openrouter.model;
  if (!key) { $("teste_res").textContent = "informe a chave"; return; }
  try {
    const resp = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
      body: JSON.stringify({ model, max_tokens: 5, messages: [{ role: "user", content: "ok" }] }),
    });
    $("teste_res").textContent = resp.ok ? "✅ chave/modelo OK" : `❌ ${resp.status}`;
  } catch (e) {
    $("teste_res").textContent = "❌ " + e.message;
  }
}

$("salvar").addEventListener("click", salvar);
$("testar").addEventListener("click", testar);
load();
