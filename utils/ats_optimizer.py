"""
Otimizador de curriculo ATS local (sem LLM / sem API paga).
Usa apenas dados reais do perfil + texto da vaga.
"""

import logging
import re

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "de", "da", "do", "das", "dos", "e", "ou", "com", "para", "por", "em",
    "na", "no", "nas", "nos", "a", "o", "as", "os", "um", "uma", "vagas",
    "vaga", "experiencia", "experiencias", "conhecimento", "desejavel", "diferencial",
    "area", "areas", "atividade", "atividades", "responsavel", "responsabilidades",
}

_TECH_TERMS = [
    "python", "java", "javascript", "typescript", "node", "node.js", "react",
    "angular", "vue", "next.js", "nestjs", "django", "flask", "fastapi",
    "spring", "dotnet", ".net", "php", "go", "golang", "kotlin", "swift",
    "sql", "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform", "linux",
    "git", "github", "gitlab", "rest", "graphql", "microservices", "ci/cd",
    "jenkins", "rabbitmq", "kafka", "pandas", "numpy", "pytorch", "tensorflow",
]


def otimizar_para_vaga(
    perfil: dict,
    vaga_titulo: str,
    vaga_empresa: str,
    vaga_descricao: str,
    vaga_requisitos: list[str],
) -> dict:
    """
    Gera curriculo ATS de forma deterministica, sem usar modelo generativo.
    """
    keywords = _extrair_keywords_vaga(vaga_titulo, vaga_descricao, vaga_requisitos)
    habilidades = _priorizar_habilidades(perfil, keywords)
    experiencias = _formatar_experiencias(perfil.get("experiencias", []), keywords)

    resultado = {
        "nome": perfil.get("nome", ""),
        "email": perfil.get("email", ""),
        "telefone": perfil.get("telefone", ""),
        "linkedin": perfil.get("linkedin", ""),
        "github": perfil.get("github", ""),
        "portfolio": perfil.get("portfolio", ""),
        "localizacao": perfil.get("localizacao", ""),
        "cargo_atual": perfil.get("cargo_atual", ""),
        "objetivo": _gerar_objetivo(perfil, vaga_titulo, vaga_empresa, keywords),
        "habilidades": habilidades or _habilidades_fallback(perfil),
        "experiencias": experiencias,
        "formacao": _formatar_formacao(perfil.get("formacao", [])),
        "idiomas": perfil.get("idiomas", []),
    }

    logger.info(
        "ats_optimizer(local): vaga=%s | keywords=%d | habilidades=%d | experiencias=%d",
        vaga_titulo,
        len(keywords),
        len(resultado["habilidades"]),
        len(resultado["experiencias"]),
    )
    return resultado


def _extrair_keywords_vaga(vaga_titulo: str, vaga_descricao: str, vaga_requisitos: list[str]) -> list[str]:
    texto = " ".join([
        str(vaga_titulo or ""),
        str(vaga_descricao or ""),
        " ".join(vaga_requisitos or []),
    ]).lower()

    keywords: list[str] = []

    # Primeiro: requisitos explicitos (menos ruído)
    for req in vaga_requisitos or []:
        for chunk in re.split(r"[,;/|]", str(req)):
            term = chunk.strip().lower()
            if _termo_valido(term):
                keywords.append(term)

    # Segundo: termos tecnicos conhecidos na descricao
    for term in _TECH_TERMS:
        if re.search(r"\b" + re.escape(term) + r"\b", texto):
            keywords.append(term)

    # Terceiro: tokens relevantes do titulo
    for token in re.findall(r"[a-zA-Z0-9+#\.\-]{3,}", str(vaga_titulo or "").lower()):
        if _termo_valido(token):
            keywords.append(token)

    return _dedupe_preserve(keywords)[:20]


def _termo_valido(termo: str) -> bool:
    t = re.sub(r"\s+", " ", termo.strip().lower())
    if len(t) < 2 or len(t) > 40:
        return False
    if t in _STOPWORDS:
        return False
    if re.fullmatch(r"\d+", t):
        return False
    return True


def _dedupe_preserve(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def _gerar_objetivo(perfil: dict, vaga_titulo: str, vaga_empresa: str, keywords: list[str]) -> str:
    nivel = str(perfil.get("nivel_senioridade", "") or "").strip().capitalize()
    cargo_base = str(perfil.get("cargo_atual", "") or "").strip() or (vaga_titulo or "Desenvolvedor")

    skills = [h.get("nome", "") for h in perfil.get("habilidades", []) if h.get("nome")]
    skills_top = ", ".join(skills[:3]) if skills else "desenvolvimento de software"
    kw_top = ", ".join(keywords[:4]) if keywords else ""

    parte_nivel = f"{nivel} " if nivel else ""
    parte_empresa = f" na {vaga_empresa}" if vaga_empresa else ""
    parte_kw = f" com foco em {kw_top}" if kw_top else ""

    return (
        f"{parte_nivel}{cargo_base} com experiencia em {skills_top}."
        f" Busco atuar na vaga de {vaga_titulo or cargo_base}{parte_empresa}{parte_kw}."
    ).strip()


def _priorizar_habilidades(perfil: dict, keywords: list[str]) -> list[str]:
    skills = perfil.get("habilidades", []) or []
    if not skills:
        return []

    keywords_lower = [k.lower() for k in keywords]

    ranked = []
    for idx, item in enumerate(skills):
        nome = str(item.get("nome", "") or "").strip()
        if not nome:
            continue
        nome_lower = nome.lower()
        nivel = int(item.get("nivel", 0) or 0)
        score = nivel / 10.0

        for kw in keywords_lower:
            if not kw:
                continue
            if kw == nome_lower:
                score += 4.0
            elif kw in nome_lower or nome_lower in kw:
                score += 2.0

        ranked.append((score, idx, nome, nivel))

    ranked.sort(key=lambda x: (-x[0], x[1]))

    resultado = []
    for pos, (_, _, nome, nivel) in enumerate(ranked[:18], 1):
        # Coloca nivel so nas primeiras skills principais.
        if pos <= 5 and nivel > 0:
            nivel_txt = {
                1: "basico",
                2: "basico-medio",
                3: "intermediario",
                4: "avancado",
                5: "especialista",
            }.get(nivel, "")
            resultado.append(f"{nome} ({nivel_txt})" if nivel_txt else nome)
        else:
            resultado.append(nome)
    return resultado


def _formatar_experiencias(experiencias: list[dict], keywords: list[str]) -> list[dict]:
    resultado = []
    for exp in experiencias[:5]:
        empresa = str(exp.get("empresa", "") or "").strip()
        cargo = str(exp.get("cargo", "") or "").strip()
        inicio = str(exp.get("inicio", "") or "").strip()
        fim = str(exp.get("fim", "") or "").strip()
        descricao = str(exp.get("descricao", "") or "").strip()

        if not empresa and not cargo and not descricao:
            continue

        bullets = _bullets_from_descricao(descricao, keywords) if descricao else []
        resultado.append({
            "empresa": empresa,
            "cargo": cargo,
            "periodo": _formatar_periodo(inicio, fim),
            "bullets": bullets,
        })
    return resultado


def _formatar_periodo(inicio: str, fim: str) -> str:
    ini = inicio.strip()
    end = fim.strip()
    if ini and end:
        return f"{ini} - {end}"
    if ini:
        return f"{ini} - Atual"
    return end


def _bullets_from_descricao(descricao: str, keywords: list[str]) -> list[str]:
    partes = re.split(r"[\n\r;]+|\.\s+", descricao)
    sentencas = [p.strip(" .\t-•") for p in partes if len(p.strip()) >= 20]
    if not sentencas:
        return []

    kws = [k.lower() for k in keywords]

    def _score(frase: str) -> int:
        fl = frase.lower()
        return sum(1 for kw in kws if kw and kw in fl)

    # Reordena priorizando frases que batem com keywords da vaga, sem reescrever texto.
    ordenadas = sorted(enumerate(sentencas), key=lambda x: (-_score(x[1]), x[0]))
    selecionadas = [s for _, s in ordenadas[:5]]
    return selecionadas


def _habilidades_fallback(perfil: dict) -> list[str]:
    skills = []
    for h in perfil.get("habilidades", [])[:15]:
        nome = str(h.get("nome", "") or "").strip()
        if nome:
            skills.append(nome)
    return skills


def _formatar_formacao(formacao_list: list[dict]) -> list[dict]:
    resultado = []
    for f in (formacao_list or [])[:3]:
        resultado.append({
            "curso": f.get("curso", ""),
            "instituicao": f.get("instituicao", ""),
            "nivel": f.get("nivel", ""),
            "ano": f.get("ano", ""),
        })
    return resultado
