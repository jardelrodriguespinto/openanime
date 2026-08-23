// Config + estado persistidos em chrome.storage.local.
// Substitui o .env: tudo que a automação precisa é configurado aqui (na dashboard
// da extensão). Credenciais das plataformas NÃO ficam aqui — o usuário já está
// logado no próprio navegador (é a grande vantagem da extensão vs. Selenium).

export const CONFIG_KEY = "autoapply.config";
export const STATE_KEY = "autoapply.state";
export const RESUME_KEY = "autoapply.resume";

// Modelo padrão — TEM que existir no OpenRouter. O antigo default "x-ai/grok-4.20"
// NÃO existe (o id correto nunca foi esse): toda chamada do cérebro voltava erro e
// os campos caíam na resposta genérica "Tenho interesse e disponibilidade…" (a IA
// nunca respondia). gpt-4o-mini: barato, rápido e bom em pt-BR p/ perguntas de form.
export const MODEL_PADRAO = "openai/gpt-4o-mini";
// Modelos obsoletos que já chegaram a ser default → migrados silenciosamente no load.
const MODELS_QUEBRADOS = new Set(["x-ai/grok-4.20"]);

// Estrutura padrão da config. Espelha o .env, menos EMAIL/PASSWORD das plataformas.
export const DEFAULT_CONFIG = {
  openrouter: {
    apiKey: "",
    // Um modelo resolve match + respostas. Barato/rápido é suficiente.
    model: MODEL_PADRAO,
    // Modelo opcional separado p/ classificar/《match》 (vazio = usa o `model`).
    modelMatch: "",
  },
  assemblyia: {
    apiKey: ""
  },
  // Perfil usado para preencher formulários e gerar respostas.
  perfil: {
    nome: "",
    email: "",
    telefone: "",
    linkedin: "",
    localizacao: "",
    cargo_atual: "",
    nivel_senioridade: "",
    // Texto do currículo (cole aqui) — base das respostas e do match.
    resumo_curriculo: "",
    // Remuneração — NUNCA gerada por IA; vem daqui.
    remuneracao_clt: "",
    remuneracao_pj: "",
    remuneracao_dolar: "",
    pretensao_salarial: "",
    // Filtros
    modalidades_aceitas: ["remoto", "híbrido", "presencial"],
    regioes_relocacao: [],
  },
  // Por plataforma: liga/desliga, palavra-chave, nota mínima de match (0-100),
  // teto de candidaturas por dia.
  plataformas: {
    linkedin: { enabled: true, query: "desenvolvedor", limiarMatch: 20, tetoDia: 28 },
    indeed: { enabled: false, query: "desenvolvedor", limiarMatch: 20, tetoDia: 25 },
    gupy: { enabled: false, query: "desenvolvedor", limiarMatch: 0, tetoDia: 50 },
    geekhunter: { enabled: false, query: "desenvolvedor", limiarMatch: 0, tetoDia: 50 },
    senior: { enabled: false, query: "desenvolvedor", limiarMatch: 0, tetoDia: 50 },
    solides: { enabled: false, query: "desenvolvedor", limiarMatch: 0, tetoDia: 50 },
    // Modo REDE: conecta com recrutadores na busca de pessoas do LinkedIn (sem IA,
    // só pra aumentar a rede). "query" = termo da busca (ex.: "tech recruiter").
    // tetoDia 0 = sem limite próprio (convites não contam como candidatura).
    rede: { enabled: false, query: "tech recruiter", limiarMatch: 0, tetoDia: 0 },
  },
  // Pausa antes de enviar (revisão/CAPTCHA) — o humano confirma. Recomendado true.
  pausarAntesEnvio: true,
  // Interface (dashboard/popup). idioma: "pt" | "en" | "es" | "" (auto = navegador).
  ui: { idioma: "" },
};

function deepMerge(base, extra) {
  if (Array.isArray(base)) return Array.isArray(extra) ? extra : base;
  if (base && typeof base === "object") {
    const out = { ...base };
    for (const k of Object.keys(base)) {
      if (extra && k in extra) out[k] = deepMerge(base[k], extra[k]);
    }
    // preserva chaves extras que o usuário possa ter salvo
    if (extra) for (const k of Object.keys(extra)) if (!(k in out)) out[k] = extra[k];
    return out;
  }
  return extra === undefined ? base : extra;
}

export async function getConfig() {
  const raw = await chrome.storage.local.get(CONFIG_KEY);
  const cfg = deepMerge(DEFAULT_CONFIG, raw[CONFIG_KEY] || {});
  // Migração: config salva com um modelo que não existe no OpenRouter → troca pelo
  // padrão (sem isso o usuário antigo continuaria SEM IA mesmo após o update).
  if (MODELS_QUEBRADOS.has(cfg.openrouter?.model)) cfg.openrouter.model = MODEL_PADRAO;
  return cfg;
}

export async function setConfig(cfg) {
  await chrome.storage.local.set({ [CONFIG_KEY]: cfg });
}

// ── Estado / contadores / dedupe ─────────────────────────────────────────────
// state = { running: bool, platform, tabId, status, day, counts:{plat:n}, applied:{plat:{jobId:1}} }

function today() {
  return new Date().toISOString().slice(0, 10);
}

export async function getState() {
  const raw = await chrome.storage.local.get(STATE_KEY);
  const st = raw[STATE_KEY] || {};
  // reset diário dos contadores
  if (st.day !== today()) {
    st.day = today();
    st.counts = {};
  }
  st.counts = st.counts || {};
  st.applied = st.applied || {};
  return st;
}

// Mutações de estado SERIALIZADAS numa fila única. Com várias plataformas rodando
// ("Iniciar tudo"), dois handlers concorrentes fazem read-modify-write e o segundo
// sobrescreve o primeiro — um status.push que leu o estado ANTES do run.start gravar
// apagava running/platforms recém-escritos e a automação "parava" sozinha.
let _fila = Promise.resolve();
function serial(fn) {
  const p = _fila.then(fn, fn);
  _fila = p.then(() => {}, () => {});
  return p;
}

export function setState(patch) {
  return serial(async () => {
    const st = await getState();
    const next = { ...st, ...patch };
    await chrome.storage.local.set({ [STATE_KEY]: next });
    return next;
  });
}

export async function contador(platform) {
  const st = await getState();
  return st.counts[platform] || 0;
}

export function registrarCandidatura(platform, jobId) {
  return serial(async () => {
    const st = await getState();
    st.counts[platform] = (st.counts[platform] || 0) + 1;
    st.applied[platform] = st.applied[platform] || {};
    if (jobId) st.applied[platform][jobId] = 1;
    await chrome.storage.local.set({ [STATE_KEY]: st });
    return st.counts[platform];
  });
}

export async function jaAplicou(platform, jobId) {
  if (!jobId) return false;
  const st = await getState();
  return !!(st.applied[platform] && st.applied[platform][jobId]);
}

// ── Currículo (upload de arquivo) ─────────────────────────────────────────────
// Guardado como data URL (base64) + nome + tipo. Content scripts pegam via SW e
// injetam no <input type=file> com DataTransfer.
export async function getResume() {
  const raw = await chrome.storage.local.get(RESUME_KEY);
  return raw[RESUME_KEY] || null; // { dataUrl, name, type } | null
}
export async function setResume(resume) {
  if (!resume) return chrome.storage.local.remove(RESUME_KEY);
  await chrome.storage.local.set({ [RESUME_KEY]: resume });
}
