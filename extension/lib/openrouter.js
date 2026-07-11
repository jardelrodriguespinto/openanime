// O "cérebro" — chama a OpenRouter direto do service worker (tem host_permission,
// então sem CORS). Faz match vaga↔currículo e gera respostas de formulário.
// Porta a lógica do form_filler.py / _avaliar_match_vaga do backend Python.

const ENDPOINT = "https://openrouter.ai/api/v1/chat/completions";

async function chat(cfg, messages, { json = false, maxTokens = 400, temperature = 0.3 } = {}) {
  const apiKey = cfg?.openrouter?.apiKey;
  if (!apiKey) throw new Error("OpenRouter API key não configurada (abra a dashboard da extensão).");
  const model = (json && cfg.openrouter.modelMatch) || cfg.openrouter.model;
  const body = {
    model,
    messages,
    max_tokens: maxTokens,
    temperature,
  };
  if (json) body.response_format = { type: "json_object" };

  const resp = await fetch(ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
      "HTTP-Referer": "https://autoapply.extension",
      "X-Title": "AutoApply Extension",
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    throw new Error(`OpenRouter ${resp.status}: ${t.slice(0, 200)}`);
  }
  const data = await resp.json();
  return data?.choices?.[0]?.message?.content?.trim() || "";
}

// ── Match vaga ↔ currículo (fail-open: erro/sem-CV → aplica) ──────────────────
export async function avaliarMatch(cfg, { descricao, titulo = "", empresa = "" }) {
  const cv = cfg?.perfil?.resumo_curriculo || "";
  const limiar = 0; // o limiar por-plataforma é aplicado por quem chama
  if (!cv || !descricao) return { aplicar: true, nota: 100, motivo: "sem CV/descrição — fail-open" };
  try {
    const sys =
      "Você avalia se um candidato deve se candidatar a uma vaga, comparando o " +
      "currículo com a descrição. Responda SOMENTE JSON: " +
      '{"nota": <0-100>, "aplicar": <true|false>, "motivo": "<curto>"}. ' +
      "nota = aderência do currículo à vaga. Seja permissivo (fail-open): na dúvida, aplicar=true.";
    const usr =
      `VAGA: ${titulo} @ ${empresa}\n\nDESCRIÇÃO:\n${descricao.slice(0, 4000)}\n\n` +
      `CURRÍCULO:\n${cv.slice(0, 4000)}`;
    const out = await chat(cfg, [
      { role: "system", content: sys },
      { role: "user", content: usr },
    ], { json: true, maxTokens: 200 });
    const j = JSON.parse(out);
    return {
      nota: typeof j.nota === "number" ? j.nota : 100,
      aplicar: j.aplicar !== false,
      motivo: j.motivo || "",
    };
  } catch (e) {
    return { aplicar: true, nota: 100, motivo: "erro no match (fail-open): " + e.message };
  }
}

import { transcreverRecaptcha } from "../lib/assemblyia.js";

// ── Transcrição de áudio (assinatura/estilo igual a avaliarMatch) ──────────────
// Delega AssemblyAI para assemblyia.js, retornando objeto estruturado em vez de propagar throw.

export async function transcreverAudio(cfg, payload = {}) {
  const apiKey = cfg?.assemblyia?.apiKey;
  if (!apiKey) return { erro: "AssemblyAI API key não configurada." };
  try {
    const texto = await transcreverRecaptcha(payload.driver || cfg.driver, apiKey);
    return { texto };
  } catch (e) {
    return { erro: e.message };
  }
}

// ── Respostas de formulário ───────────────────────────────────────────────────
// Suporta tipos: TEXT, NUMERO, SELECT/RADIO (com opções). Salário NUNCA vem da IA.

const RE_SALARIO = /(sal[aá]ri|remunera|pretens|compensation|expected (pay|salary))/i;

function respostaSalario(perfil, pergunta) {
  const p = (pergunta || "").toLowerCase();
  let v;
  if (/(d[oó]lar|usd|dollar)/.test(p)) v = perfil.remuneracao_dolar;
  else if (/(pj|pessoa jur|cnpj|jur[ií]dica)/.test(p)) v = perfil.remuneracao_pj;
  else if (/clt/.test(p)) v = perfil.remuneracao_clt;
  else v = perfil.remuneracao_clt || perfil.pretensao_salarial;
  return String(v || perfil.pretensao_salarial || "").trim();
}

function respostaSeguraLocal(tipo, opcoes) {
  // Fallback determinístico se a IA falhar — nunca deixa vazio (campo obrigatório).
  if (tipo === "SELECT" || tipo === "RADIO") {
    const ops = opcoes || [];
    // prefere "Sim"/"Yes" quando existir; senão a 1ª opção não-placeholder
    const sim = ops.find((o) => /^(sim|yes)$/i.test(o.trim()));
    return sim || ops.find((o) => o && !/selecione|select|choose|--/i.test(o)) || ops[0] || "";
  }
  if (tipo === "NUMERO") return "0";
  return "Tenho interesse e disponibilidade para a vaga.";
}

export async function responderPergunta(cfg, { pergunta, tipo = "TEXT", opcoes = [], vagaTitulo = "", vagaEmpresa = "", idioma = "pt" }) {
  const perfil = cfg?.perfil || {};

  // 1) Salário → config, nunca IA.
  if (RE_SALARIO.test(pergunta)) {
    const v = respostaSalario(perfil, pergunta);
    if (v) return v;
  }

  // 2) IA (OpenRouter).
  try {
    const idiomaLabel = idioma === "en" ? "English" : "português";
    const sys =
      `Você preenche formulários de candidatura em nome do candidato. Responda no idioma: ${idiomaLabel}. ` +
      "Seja direto e curto. NUNCA invente salário. " +
      (tipo === "SELECT" || tipo === "RADIO"
        ? "A resposta DEVE ser EXATAMENTE uma das opções dadas (copie o texto da opção)."
        : tipo === "NUMERO"
        ? "Responda APENAS um número inteiro (anos de experiência, quantidade etc.). Se não souber, 0."
        : "Responda em 1-2 frases.");
    const ctx =
      `CANDIDATO (currículo):\n${(perfil.resumo_curriculo || "").slice(0, 2500)}\n\n` +
      `Cargo atual: ${perfil.cargo_atual || "-"} | Senioridade: ${perfil.nivel_senioridade || "-"}\n` +
      `VAGA: ${vagaTitulo} @ ${vagaEmpresa}\n\n` +
      `PERGUNTA (${tipo}): ${pergunta}` +
      (opcoes && opcoes.length ? `\nOPÇÕES: ${opcoes.join(" | ")}` : "");
    const out = await chat(cfg, [
      { role: "system", content: sys },
      { role: "user", content: ctx },
    ], { maxTokens: 160, temperature: 0.3 });
    let ans = (out || "").trim();
    if ((tipo === "SELECT" || tipo === "RADIO") && opcoes?.length) {
      // casa a resposta com a opção mais próxima
      const exact = opcoes.find((o) => o.trim().toLowerCase() === ans.toLowerCase());
      if (exact) return exact;
      const contains = opcoes.find((o) => ans.toLowerCase().includes(o.trim().toLowerCase()) || o.toLowerCase().includes(ans.toLowerCase()));
      if (contains) return contains;
      return respostaSeguraLocal(tipo, opcoes);
    }
    if (tipo === "NUMERO") {
      const m = ans.match(/-?\d+/);
      return m ? m[0] : "0";
    }
    return ans || respostaSeguraLocal(tipo, opcoes);
  } catch (e) {
    return respostaSeguraLocal(tipo, opcoes);
  }
}

// ── Habilidades eliminatórias (Solides) — nível por skill, UMA chamada ─────────
// Recebe a lista de skills + as opções (ex.: Nenhum/Básico/Intermediário/Avançado) e
// devolve { índice_da_skill: "Nível" }, avaliando SÓ pelo currículo (não inventa —
// skills eliminatórias vão pro empregador; o usuário revisa antes de enviar). 1 call.
export async function avaliarHabilidades(cfg, { skills = [], opcoes = [], vagaTitulo = "", idioma = "pt" }) {
  const perfil = cfg?.perfil || {};
  const niveis = (opcoes && opcoes.length ? opcoes : ["Nenhum", "Básico", "Intermediário", "Avançado"]).map((s) => String(s).trim()).filter(Boolean);
  if (!skills.length) return {};
  try {
    const sys =
      "Você avalia, com honestidade e SOMENTE com base no currículo do candidato, o nível " +
      "dele em cada habilidade listada. Para CADA habilidade, responda UMA linha no formato " +
      `"N|Nível", onde N é o número da habilidade e Nível é EXATAMENTE um de: ${niveis.join(", ")}. ` +
      "Se o currículo não evidenciar a habilidade, escolha o menor nível plausível. NÃO invente. " +
      "Responda apenas as linhas, nada mais.";
    const lista = skills.map((s, i) => `${i + 1}. ${s}`).join("\n");
    const ctx =
      `CURRÍCULO:\n${(perfil.resumo_curriculo || "").slice(0, 3000)}\n\n` +
      `VAGA: ${vagaTitulo}\n\nHABILIDADES:\n${lista}`;
    const out = await chat(cfg, [
      { role: "system", content: sys },
      { role: "user", content: ctx },
    ], { maxTokens: 500, temperature: 0.2 });
    const map = {};
    for (const line of (out || "").split("\n")) {
      const m = line.match(/(\d+)\s*[|)\-.:]+\s*(.+)/);
      if (!m) continue;
      const idx = parseInt(m[1], 10) - 1;
      const nivel = niveis.find((n) => m[2].toLowerCase().includes(n.toLowerCase()));
      if (idx >= 0 && nivel) map[idx] = nivel;
    }
    return map; // { 0: "Avançado", 1: "Básico", ... }
  } catch (e) {
    return {};
  }
}
