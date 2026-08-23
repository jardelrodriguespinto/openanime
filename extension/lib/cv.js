// lib/cv.js — calcula ANOS DE EXPERIÊNCIA POR TECNOLOGIA a partir das DATAS do CV
// (perfil.resumo_curriculo da dashboard). Usado nos NumberInputs "Quantos anos de
// experiência você tem com X?" (GeekHunter etc.) ANTES de consultar a IA: o número é
// CALCULADO dos períodos do currículo, não chutado pelo modelo. window.OACV.
(function () {
  const norm = (s) => (s || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");

  const MESES = {
    jan: 0, fev: 1, mar: 2, abr: 3, mai: 4, jun: 5, jul: 6, ago: 7, set: 8, sep: 8, out: 9, nov: 10, dez: 11,
    janeiro: 0, fevereiro: 1, marco: 2, abril: 3, maio: 4, junho: 5, julho: 6, agosto: 7, setembro: 8, outubro: 9, novembro: 10, dezembro: 11,
  };

  // Ordem importa: "javascript" antes de "java", "spring boot" antes de "spring" etc.
  // (sort por tamanho desc. evita casar o prefixo errado na pergunta).
  const TECNOLOGIAS = [
    "spring boot", "javascript", "typescript", "postgresql", "postgres", "kubernetes",
    "mongodb", "laravel", "fastapi", "django", "flutter", "angular", "python", "react",
    "spring", "node", "mysql", "redis", "docker", "azure", "html", "css", "java",
    "php", "sql", "aws", "gcp", "git", "n8n", "c#", "c++", ".net", "vue",
  ].sort((a, b) => b.length - a.length);

  // "julho 2026" | "jul/2026" | "03/2021" | "2021" → Date | null
  function parseData(txt) {
    const t = norm(txt);
    let m = t.match(/([a-z]+)\D{0,8}(\d{4})/);
    if (m && MESES[m[1]] != null) return new Date(+m[2], MESES[m[1]], 1);
    m = t.match(/(\d{1,2})\/(\d{4})/);
    if (m && +m[1] >= 1 && +m[1] <= 12) return new Date(+m[2], +m[1] - 1, 1);
    m = t.match(/(\d{4})/);
    return m ? new Date(+m[1], 0, 1) : null;
  }

  const AGORA = /(atual(?:mente)?|actual(?:mente)?|presente|hoje|now|hoy|current|date em curso|o momento)/i;

  // Segmentos de experiência: uma linha com data(s) (+ "até"/traço/"atualmente") abre um
  // bloco que se estende até a próxima linha-de-período. As tecnologias citadas NO TEXTO
  // do bloco herdam a duração dele ("Clamed – Julho 2026 até Atualmente / atuo com Java…").
  function segmentos(cv) {
    const segs = [];
    let atual = null;
    for (const ln of String(cv || "").split(/\n+/)) {
      const datas = ln.match(/[a-zç]{3,}\.?\s*(?:de\s*)?\d{4}|\d{2}\/\d{4}/gi) || [];
      if ((datas.length || AGORA.test(ln)) && /\d{4}|atual/i.test(ln)) {
        const ini = datas.length ? parseData(datas[0]) : parseData(ln);
        const fim = AGORA.test(ln) ? new Date() : (datas.length > 1 ? parseData(datas[datas.length - 1]) : new Date());
        if (ini && fim && fim >= ini) { atual = { ini, fim, texto: "" }; segs.push(atual); continue; }
      }
      if (atual) atual.texto += "\n" + ln;
    }
    return segs;
  }

  const mesesEntre = (a, b) => Math.max(0, (b.getFullYear() - a.getFullYear()) * 12 + (b.getMonth() - a.getMonth()));

  // Anos de experiência p/ a pergunta (procura a tecnologia NELA; sem tech identificada →
  // tempo total de carreira). Tech citada na pergunta mas AUSENTE do CV → 0 (nunca inventa).
  // Retorna null se o CV não tem nenhum período datado (aí quem responde é a IA).
  function calcular(cv, perguntaOuTech) {
    const segs = segmentos(cv);
    if (!segs.length) return null;
    const alvo = norm(perguntaOuTech);
    const tech = TECNOLOGIAS.find((t) => alvo.includes(t));
    let meses = 0;
    for (const s of segs) {
      if (!tech || norm(s.texto).includes(tech)) meses += mesesEntre(s.ini, s.fim);
    }
    if (tech && meses === 0) return 0;
    return Math.max(0, Math.floor(meses / 12));
  }

  window.OACV = { calcular, segmentos };
})();
