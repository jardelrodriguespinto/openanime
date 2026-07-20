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

  // TIMEOUT obrigatório: um fetch pendurado deixava o canal de resposta do SW aberto
  // pra sempre → o OA.bg() do content script nunca resolvia → a automação "ficava
  // parada" na aba (o guard _fluxo não solta e nem o cs.kick reentra). Com o abort,
  // o erro propaga e os callers são fail-open (match aplica, resposta cai no fallback).
  // 40s (não 60s): o responderPergunta faz 1 retry → 2×40=80s cabem no watchdog de 90s
  // do OA.bg (dom.js). Os content scripts NÃO fazem mais race próprio (era menor que o
  // abort e só descartava resposta VÁLIDA lenta → caía no genérico "tenho disponibilidade").
  const resp = await fetch(ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
      "HTTP-Referer": "https://autoapply.extension",
      "X-Title": "AutoApply Extension",
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(40000),
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
  const nivel = cfg?.perfil?.nivel_senioridade || "";
  const cargo = cfg?.perfil?.cargo_atual || "";
  const limiar = 0; // o limiar por-plataforma é aplicado por quem chama
  if (!cv || !descricao) return { aplicar: true, nota: 100, motivo: "sem CV/descrição — fail-open" };
  try {
    const sys =
      "Você avalia se um candidato deve se candidatar a uma vaga, comparando o " +
      "currículo E A SENIORIDADE do candidato com a descrição. Responda SOMENTE JSON: " +
      '{"nota": <0-100>, "aplicar": <true|false>, "motivo": "<curto>"}. ' +
      "nota = aderência do currículo à vaga. Seja permissivo (fail-open): na dúvida, aplicar=true. " +
      // pedido explícito do usuário: NÃO candidatar sênior a vaga júnior (nem o inverso claro).
      "PORÉM respeite a SENIORIDADE: se houver incompatibilidade CLARA de nível — candidato " +
      "sênior/pleno para vaga júnior/estágio/trainee, ou candidato júnior para vaga " +
      "sênior/especialista/staff/lead/principal — então nota BAIXA (<40) e aplicar=false, " +
      "citando o nível no motivo. Só bloqueie por senioridade quando a incompatibilidade for " +
      "evidente pelo título/descrição; na dúvida sobre o nível, aplicar=true.";
    const usr =
      `CANDIDATO — senioridade: ${nivel || "não informada"} | cargo atual: ${cargo || "não informado"}\n\n` +
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

// ── Transcrição de áudio (assinatura/estilo igual a avaliarMatch) ──────────────
// Implementação standalone para o browser. NÃO importa assemblyia.js Node/Selenium.

const ASSEMBLY_ENDPOINT = "https://api.assemblyai.com/v2";

// base64 → Uint8Array (o content script baixa o áudio same-origem e manda os bytes em b64)
function b64ParaBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export async function transcreverAudio(cfg, { audioUrl, audioB64 } = {}) {
  const apiKey = cfg?.assemblyia?.apiKey;
  if (!apiKey) return { erro: "AssemblyAI API key não configurada." };
  if (!audioUrl && !audioB64) return { erro: "Áudio não fornecido." };
  try {
    // Se veio o áudio em bytes (baixado no frame do reCAPTCHA), faz UPLOAD na AssemblyAI e
    // usa a upload_url resultante — igual ao assemblyia.js do Selenium. A URL enterprise/
    // payload nem sempre é buscável remotamente pela AssemblyAI; o upload é o caminho robusto.
    let fonteAudio = audioUrl;
    if (audioB64) {
      const up = await fetch(`${ASSEMBLY_ENDPOINT}/upload`, {
        method: "POST",
        headers: { Authorization: apiKey, "Content-Type": "application/octet-stream" },
        body: b64ParaBytes(audioB64),
      });
      if (!up.ok) throw new Error(`Erro no upload do áudio: ${await up.text()}`);
      const upJson = await up.json();
      if (!upJson.upload_url) throw new Error("AssemblyAI não retornou upload_url");
      fonteAudio = upJson.upload_url;
    }
    const response = await fetch(`${ASSEMBLY_ENDPOINT}/transcript`, {
      method: "POST",
      headers: {
        Authorization: apiKey,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        audio_url: fonteAudio,
        language_code: "pt",
        punctuate: true,
        format_text: true,
      }),
    });
    if (!response.ok) {
      const erro = await response.text();
      throw new Error(`Erro ao enviar áudio: ${erro}`);
    }
    const data = await response.json();
    if (!data.id) throw new Error("AssemblyAI não retornou ID da transcrição");
    const id = data.id;
    let tentativas = 0;
    const limiteTentativas = 100;
    while (tentativas < limiteTentativas) {
      const result = await fetch(
        `${ASSEMBLY_ENDPOINT}/transcript/${encodeURIComponent(id)}`,
        { headers: { Authorization: apiKey } }
      );
      if (!result.ok) {
        const erro = await result.text();
        throw new Error(`Erro consultando transcrição: ${erro}`);
      }
      const json = await result.json();
      if (json.status === "completed") {
        return { texto: json.text };
      }
      if (json.status === "error") {
        throw new Error(`Erro AssemblyAI: ${json.error}`);
      }
      tentativas++;
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
    throw new Error("Tempo limite excedido aguardando transcrição");
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

// Limpa o texto que a IA às vezes envolve em rótulo/markdown ("**Resposta:** ...", "R:",
// aspas, ``) — esse literal ia direto pro campo do formulário. Tira SÓ o invólucro,
// preservando o conteúdo. Não toca em número/salário (tratados antes).
function limparResposta(s) {
  let t = (s || "").trim();
  // rótulo inicial em qualquer combinação de markdown: **Resposta:**, _Resp:_, "Answer -"
  t = t.replace(/^\s*[*_`>#\s]*\b(resposta|minha resposta|answer|resp)\b[*_`\s]*\s*[:\-–]\s*/i, "");
  // ênfase/código remanescente e aspas externas
  t = t.replace(/\*\*|__|`/g, "").replace(/^["'“”\s]+|["'“”\s]+$/g, "");
  return t.trim();
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
  const idiomaLabel = idioma === "en" ? "English" : "português";
  const sys =
    `Você preenche formulários de candidatura em nome do candidato. Responda no idioma: ${idiomaLabel}. ` +
    "Seja direto e curto. NUNCA invente salário. " +
    // a IA às vezes devolvia "**Resposta:** …" e o rótulo ia pro campo → proíbe explicitamente.
    "Responda APENAS o conteúdo final — SEM rótulos (nada de 'Resposta:', 'R:', 'Answer:') e SEM markdown (nada de **, _, #, aspas ou listas). " +
    (tipo === "SELECT" || tipo === "RADIO"
      ? "A resposta DEVE ser EXATAMENTE uma das opções dadas (copie o texto da opção, sem nada a mais)."
      : tipo === "NUMERO"
      ? "Responda APENAS um número inteiro (anos de experiência, quantidade etc.). Se não souber, 0."
      // reforça que precisa RESPONDER O QUE FOI PERGUNTADO (não um texto genérico de interesse).
      : "Leia a pergunta com atenção e responda EXATAMENTE o que ela pede, em 1-2 frases.");
  const ctx =
    `CANDIDATO (currículo):\n${(perfil.resumo_curriculo || "").slice(0, 2500)}\n\n` +
    `Cargo atual: ${perfil.cargo_atual || "-"} | Senioridade: ${perfil.nivel_senioridade || "-"}\n` +
    `VAGA: ${vagaTitulo} @ ${vagaEmpresa}\n\n` +
    `PERGUNTA (${tipo}): ${pergunta}` +
    (opcoes && opcoes.length ? `\nOPÇÕES: ${opcoes.join(" | ")}` : "");

  // 1 RETRY: uma falha transitória da OpenRouter (timeout/rede) NÃO deve virar a resposta
  // genérica ("Tenho disponibilidade…") — dá 2ª chance antes do fallback. Os content scripts
  // não fazem mais race próprio, então o retry mora aqui (2×40s < watchdog de 90s do OA.bg).
  let ans = "";
  for (let tent = 0; tent < 2; tent++) {
    try {
      const out = await chat(cfg, [
        { role: "system", content: sys },
        { role: "user", content: ctx },
      ], { maxTokens: 160, temperature: 0.3 });
      ans = limparResposta(out);
      if (ans) break;
    } catch (e) { ans = ""; }
  }

  if ((tipo === "SELECT" || tipo === "RADIO") && opcoes?.length) {
    // casa a resposta com a opção mais próxima (só se a IA respondeu — senão o
    // `o.includes("")` casaria a 1ª opção por engano; sem resposta vai pro fallback seguro)
    const exact = ans && opcoes.find((o) => o.trim().toLowerCase() === ans.toLowerCase());
    if (exact) return exact;
    const contains = ans && opcoes.find((o) => ans.toLowerCase().includes(o.trim().toLowerCase()) || o.toLowerCase().includes(ans.toLowerCase()));
    if (contains) return contains;
    return respostaSeguraLocal(tipo, opcoes);
  }
  if (tipo === "NUMERO") {
    const m = ans.match(/-?\d+/);
    return m ? m[0] : "0";
  }
  return ans || respostaSeguraLocal(tipo, opcoes);
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
