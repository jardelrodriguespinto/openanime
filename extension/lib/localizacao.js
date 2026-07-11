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
    if (/(híbrid|hibrid|hybrid|semipresencial|semi-presencial)/.test(t)) return "hibrido";
    if (/(remoto|remote|home office|home-office|100% remoto|anywhere|teletrabalho)/.test(t)) return "remoto";
    if (/(presencial|on-site|on site|onsite|no local|no escritório|no escritorio|in office|in-office)/.test(t)) return "presencial";
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
    if (!mod) return [true, "modalidade indefinida (fail-open)"];
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

  // Gate único usado por TODAS as plataformas: 1) filtro modalidade/região (config do
  // usuário — antes era ignorado na extensão), 2) match com currículo (limiar por
  // plataforma). Retorna {aplicar, motivo}. Fail-open.
  async function deveAplicar(descricao, { titulo = "", empresa = "", platform = "" } = {}) {
    const cfg = (await OA.bg({ type: "config.get" })).config || {};
    const perfil = cfg.perfil || {};
    if (descricao) {
      const [ok, motivo] = vagaAceita(descricao, perfil.modalidades_aceitas, perfil.regioes_relocacao);
      if (!ok) return { aplicar: false, motivo };
    }
    if (descricao && perfil.resumo_curriculo) {
      const limiar = (cfg.plataformas?.[platform] || {}).limiarMatch || 0;
      const m = await OA.bg({ type: "brain.match", payload: { descricao, titulo, empresa } });
      if (m?.ok && (m.aplicar === false || (limiar > 0 && typeof m.nota === "number" && m.nota < limiar))) {
        return { aplicar: false, motivo: `sem match: ${m.motivo || ""} (nota ${m.nota})`, idioma: "pt" };
      }
    }
    return { aplicar: true, motivo: "", idioma: detectarIdioma(descricao) };
  }
  OA.deveAplicar = deveAplicar;
})();
