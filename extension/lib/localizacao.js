// Filtro por MODALIDADE (remoto/híbrido/presencial) + REGIÃO — porta de
// automation/localizacao.py. Lógica pura. Exposto em OA.vagaAceita.
// FAIL-OPEN: sem config ou sinal indefinido → candidata (não bloqueia).
(function () {
  const OA = window.OA;
  if (!OA || OA.vagaAceita) return;

  const UF_REGIAO = {
    AC: "norte", AP: "norte", AM: "norte", PA: "norte", RO: "norte", RR: "norte", TO: "norte",
    AL: "nordeste", BA: "nordeste", CE: "nordeste", MA: "nordeste", PB: "nordeste", PE: "nordeste", PI: "nordeste", RN: "nordeste", SE: "nordeste",
    DF: "centro-oeste", GO: "centro-oeste", MT: "centro-oeste", MS: "centro-oeste",
    ES: "sudeste", MG: "sudeste", RJ: "sudeste", SP: "sudeste",
    PR: "sul", RS: "sul", SC: "sul",
  };
  const ESTADO_UF = {
    "acre": "AC", "amapá": "AP", "amapa": "AP", "amazonas": "AM", "pará": "PA", "para": "PA", "rondônia": "RO", "rondonia": "RO", "roraima": "RR", "tocantins": "TO",
    "alagoas": "AL", "bahia": "BA", "ceará": "CE", "ceara": "CE", "maranhão": "MA", "maranhao": "MA", "paraíba": "PB", "paraiba": "PB", "pernambuco": "PE", "piauí": "PI", "piaui": "PI", "rio grande do norte": "RN", "sergipe": "SE",
    "distrito federal": "DF", "goiás": "GO", "goias": "GO", "mato grosso do sul": "MS", "mato grosso": "MT",
    "espírito santo": "ES", "espirito santo": "ES", "minas gerais": "MG", "rio de janeiro": "RJ", "são paulo": "SP", "sao paulo": "SP",
    "paraná": "PR", "parana": "PR", "rio grande do sul": "RS", "santa catarina": "SC",
  };
  const CIDADE_UF = {
    "rio branco": "AC", "manaus": "AM", "belém": "PA", "belem": "PA", "porto velho": "RO", "palmas": "TO",
    "maceió": "AL", "maceio": "AL", "salvador": "BA", "fortaleza": "CE", "são luís": "MA", "sao luis": "MA", "joão pessoa": "PB", "joao pessoa": "PB", "recife": "PE", "teresina": "PI", "natal": "RN", "aracaju": "SE",
    "brasília": "DF", "brasilia": "DF", "goiânia": "GO", "goiania": "GO", "campo grande": "MS", "cuiabá": "MT", "cuiaba": "MT",
    "vitória": "ES", "vitoria": "ES", "belo horizonte": "MG", "uberlândia": "MG", "uberlandia": "MG", "rio de janeiro": "RJ", "são paulo": "SP", "sao paulo": "SP", "campinas": "SP", "santos": "SP",
    "curitiba": "PR", "londrina": "PR", "maringá": "PR", "maringa": "PR", "porto alegre": "RS", "caxias do sul": "RS", "florianópolis": "SC", "florianopolis": "SC", "joinville": "SC", "blumenau": "SC",
  };
  const norm = (s) => (s || "").toLowerCase().replace(/\s+/g, " ").trim();

  function modalidadeDoTexto(texto) {
    const t = norm(texto);
    // híbrido ANTES de presencial ("semipresencial" contém "presencial")
    if (/(híbrid|hibrid|hybrid|semipresencial|semi-presencial|modelo\s+híbrido|modelo\s+hibrido)/.test(t)) return "hibrido";
    if (/(remoto|remote|home[- ]office|anywhere|teletrabalho|trabalho (100%\s*)?remoto|vaga remota|100%\s*remot)/.test(t)) return "remoto";
    if (/(presencial|on[- ]?site|no local|no escritório|no escritorio|in office|trabalho presencial|vaga presencial|100%\s*presencial)/.test(t)) return "presencial";
    return "";
  }

  // Nível do texto (TÍTULO da vaga OU senioridade do perfil) → junior|pleno|senior|gestao.
  // GESTÃO primeiro: "Gerente de Desenvolvimento" contém palavra de dev mas é cargo de
  // CHEFIA — um candidato técnico NÃO deve aplicar. Depois sênior: "Líder/Especialista"
  // não pode ser mascarado por um "júnior" no meio do texto; \b evita casar "jr" em palavra.
  function nivelDoTexto(t) {
    const s = norm(t);
    if (!s) return "";
    if (/(\bgerente\b|\bmanager\b|coordenador|\bhead of\b|\bdiretor|supervisor)/.test(s)) return "gestao";
    if (/(\bs[eê]nior\b|\bsenior\b|\bsr\b\.?|especialista|staff|principal|\blead\b|\bl[íi]der\b|\blider\b|arquitet)/.test(s)) return "senior";
    if (/(\bpleno\b|\bmid\b|mid-level|\bpl\b\.?)/.test(s)) return "pleno";
    if (/(est[aá]gio|trainee|aprendiz|\bj[uú]nior\b|\bjunior\b|\bjr\b\.?)/.test(s)) return "junior";
    return "";
  }
  function extrairUF(texto) {
    if (!texto) return "";
    const t = norm(texto);
    for (const uf of Object.keys(UF_REGIAO)) if (new RegExp(`(?:^|[\\s,\\-/(])${uf}(?:[\\s,\\-/).]|$)`, "i").test(texto)) return uf;
    for (const nome of Object.keys(ESTADO_UF).sort((a, b) => b.length - a.length)) if (t.includes(nome)) return ESTADO_UF[nome];
    for (const cid of Object.keys(CIDADE_UF).sort((a, b) => b.length - a.length)) if (t.includes(cid)) return CIDADE_UF[cid];
    return "";
  }
  function regiaoDoTexto(texto) {
    const uf = extrairUF(texto);
    if (uf) return UF_REGIAO[uf] || "";
    const t = norm(texto);
    for (const r of ["centro-oeste", "nordeste", "sudeste", "norte", "sul"]) if (t.includes(r)) return r;
    return "";
  }

  // (aceita, motivo)
  function vagaAceita(textoVaga, modalidadesAceitas, regioes) {
    const mods = new Set((modalidadesAceitas || []).map(norm).filter(Boolean));
    if (!mods.size) return [true, "sem filtro de modalidade"];
    const mod = modalidadeDoTexto(textoVaga);
    // Modalidade EXPLÍCITA e fora do filtro → descarta na hora (estrito). NÃO
    // identificada → NÃO descarta aqui: muitas páginas não trazem "Remoto" em lugar
    // nenhum e vagas compatíveis estavam sendo jogadas fora ("ignorava vaga que o perfil
    // atende"). Quem fecha esse caso é o MATCH IA, que recebe as modalidades aceitas.
    if (!mod) return [true, "modalidade indefinida — decide a IA"];
    if (!mods.has(mod)) return [false, `modalidade '${mod}' não aceita`];
    if (mod === "remoto") return [true, "remoto aceito"];
    const regs = new Set((regioes || []).map((r) => norm(r).replace("centro oeste", "centro-oeste")).filter(Boolean));
    if (!regs.size) return [true, `${mod} aceito (sem região, fail-open)`];
    const reg = regiaoDoTexto(textoVaga);
    if (!reg) return [true, `${mod}: região indefinida (fail-open)`];
    return regs.has(reg) ? [true, `${mod} na região ${reg}`] : [false, `${mod} fora das regiões (vaga em ${reg})`];
  }

  OA.vagaAceita = vagaAceita;

  // Detecta idioma da vaga (pt|en) por contagem de stopwords — p/ responder no idioma
  // certo (o Selenium fazia via detectar_idioma_texto). Default pt.
  function detectarIdioma(texto) {
    const t = (texto || "").toLowerCase();
    const en = (t.match(/\b(the|and|you|for|with|your|are|will|our|we|is|to|of|in|on|as|experience|requirements|responsibilities|skills|team|work|company|about)\b/g) || []).length;
    const pt = (t.match(/\b(e|de|da|do|para|com|você|voce|sua|são|sao|nossa|nós|nos|é|em|como|experiência|experiencia|requisitos|responsabilidades|habilidades|equipe|trabalho|empresa|vaga|conhecimento|atuar)\b/g) || []).length;
    return en > pt * 1.3 ? "en" : "pt";
  }
  OA.detectarIdioma = detectarIdioma;

  // Gate único usado por TODAS as plataformas:
  //   1) MODALIDADE/REGIÃO (config do usuário) — sinal AMPLIADO: título + descrição +
  //      texto da página. ESTRICTO: modalidade não identificada DESCARTA a vaga
  //      ("se for pleno remoto, apenas vagas pleno e remoto").
  //   2) SENIORIDADE LOCAL (determinística, funciona MESMO se a IA cair) — ESTRICTA:
  //      o nível do título tem que ser IGUAL ao do perfil (pleno↔sênior bloqueia).
  //   3) MATCH IA (limiar por plataforma) — com fallback de modelo (openrouter.js).
  // Retorna {aplicar, motivo}.
  const ESCADA = { junior: 0, pleno: 1, senior: 2, gestao: 3 };
  async function deveAplicar(descricao, { titulo = "", empresa = "", platform = "", pagina = "" } = {}) {
    const cfg = (await OA.bg({ type: "config.get" })).config || {};
    const perfil = cfg.perfil || {};
    const idioma = detectarIdioma([titulo, descricao].filter(Boolean).join("\n"));
    // 1) modalidade/região
    const textoMod = [titulo, descricao, pagina].filter(Boolean).join("\n");
    if (textoMod) {
      const [ok, motivo] = vagaAceita(textoMod, perfil.modalidades_aceitas, perfil.regioes_relocacao);
      if (!ok) return _logGate(platform, titulo, false, motivo, idioma);
    }
    // 2) senioridade local (título da vaga × senioridade declarada no perfil) — ESTRICTA:
    // só passa se o nível bater EXATAMENTE ("pleno remoto" ⇒ só vaga pleno). Título sem
    // nível detectável não bloqueia aqui (o match com IA decide). Nível "gestao" (gerente/
    // coordenador/diretor) ≠ qualquer nível técnico → dev não aplica em vaga de gerente.
    const nvUser = nivelDoTexto(perfil.nivel_senioridade);
    const nvVaga = nivelDoTexto(titulo);
    if (nvUser && nvVaga && ESCADA[nvUser] !== ESCADA[nvVaga]) {
      return _logGate(platform, titulo, false, `senioridade/função: candidato ${nvUser}, vaga ${nvVaga}`, idioma);
    }
    // 3) match IA (currículo × descrição) — roda MESMO sem descrição raspada: usa
    // título + texto da página. Antes, descrição vazia = SEM match nenhum → vaga de
    // gerente passava direto ("aplicava para vaga de gerente sendo desenvolvedor").
    if ((descricao || titulo) && perfil.resumo_curriculo) {
      const limiar = (cfg.plataformas?.[platform] || {}).limiarMatch || 0;
      const descIA = descricao || [titulo, pagina].filter(Boolean).join("\n").slice(0, 2500);
      const m = await OA.bg({ type: "brain.match", payload: { descricao: descIA, titulo, empresa } });
      // IA INDISPONÍVEL não pode passar em silêncio (parecia "não chama a IA"): avisa no
      // console da aba com o motivo real (chave, créditos, timeout…).
      if (m?.ok && /indispon|fail-open|erro no match/i.test(m.motivo || "")) {
        try { console.warn(`[OA-IA] match indisponível p/ "${(titulo || "").slice(0, 50)}": ${m.motivo}`); } catch (_) {}
      }
      if (m?.ok && (m.aplicar === false || (limiar > 0 && typeof m.nota === "number" && m.nota < limiar))) {
        return _logGate(platform, titulo, false, `sem match: ${m.motivo || ""} (nota ${m.nota})`, idioma);
      }
    }
    return _logGate(platform, titulo, true, "", idioma);
  }
  // Log de TODOS os vereditos do gate no console da página — facilita ver POR QUE uma
  // vaga foi aplicada ou pulada (F12 → Console → filtro "OA-gate").
  function _logGate(platform, titulo, ok, motivo, idioma) {
    try { console.log(`[OA-gate] ${platform || "?"} | ${(titulo || "(sem título)").slice(0, 60)} → ${ok ? "APLICA" : "PULA"}${motivo ? " | " + motivo : ""}`); } catch (_) {}
    return { aplicar: ok, motivo, idioma };
  }
  OA.deveAplicar = deveAplicar;

  // ── Pré-gate por TÍTULO (IA) na COLETA da lista ─────────────────────────────
  // Antes de enfileirar os cards, cada TÍTULO vai pra IA (brain.title): faz sentido
  // com o perfil (área/senioridade)? É a "consulta antes de aplicar" — sem ela as
  // plataformas abriam e preenchiam QUALQUER vaga. Cache por sessão + pool de 3
  // chamadas em paralelo (25 cards × ~2s em série = lento demais).
  const _tituloCache = new Map();
  async function tituloOk(titulo, opts = {}) {
    const key = norm(titulo);
    if (!key) return true; // sem título → fail-open
    if (_tituloCache.has(key)) return _tituloCache.get(key);
    let ok = true;
    try {
      const r = await OA.bg({ type: "brain.title", payload: { titulo: key, empresa: opts.empresa || "" } });
      ok = !r || r.aplicar !== false; // SW falhou → fail-open
    } catch (_) { /* fail-open */ }
    _tituloCache.set(key, ok);
    return ok;
  }
  async function filtrarTitulos(itens, onProgresso) {
    // itens: [{ titulo, empresa?, ref }] → devolve os aprovados (ordem preservada)
    const aprovados = [];
    if (!itens.length) return aprovados;
    let i = 0;
    const worker = async () => {
      while (i < itens.length) {
        const it = itens[i++];
        if (await tituloOk(it.titulo, it)) aprovados.push(it);
        if (onProgresso && i % 5 === 0) onProgresso(i, itens.length);
      }
    };
    await Promise.all(Array.from({ length: Math.min(3, itens.length) }, worker));
    return aprovados;
  }
  OA.tituloOk = tituloOk;
  OA.filtrarTitulos = filtrarTitulos;
})();
