"""
Extracao local de perfil profissional a partir de texto de curriculo.
Sem LLM e sem API paga.
"""

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

_SEC_HABS = ("habilidades", "skills", "tecnologias", "competencias")
_SEC_EXP = ("experiencia", "experiencias", "experiencia profissional")
_SEC_FORM = ("formacao", "educacao", "academica")
_SEC_IDIOMAS = ("idiomas", "languages")
_SEC_OBJ = ("objetivo", "resumo", "perfil profissional", "sobre")

_KNOWN_SKILLS = [
    "python", "java", "javascript", "typescript", "node", "node.js", "react",
    "angular", "vue", "next.js", "nestjs", "django", "flask", "fastapi",
    "spring", ".net", "dotnet", "php", "golang", "go", "kotlin", "swift",
    "sql", "postgresql", "mysql", "mongodb", "redis", "docker", "kubernetes",
    "aws", "azure", "gcp", "linux", "git", "github", "gitlab", "rest",
    "graphql", "microservices", "microservicos", "ci/cd", "jenkins",
]


def extrair_perfil_curriculo_local(texto: str) -> dict:
    lines = _linhas_limpa(texto)
    text = "\n".join(lines)
    text_lower = text.lower()

    email = _first_regex(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    telefone = _first_regex(
        r"(?:\+?\d{1,3}\s*)?(?:\(?\d{2}\)?\s*)?\d{4,5}[-\s]?\d{4}",
        text,
    )
    linkedin = _first_regex(r"https?://(?:www\.)?linkedin\.com/[^\s]+", text)
    github = _first_regex(r"https?://(?:www\.)?github\.com/[^\s]+", text)
    portfolio = _first_regex(
        r"https?://(?!(?:www\.)?(?:linkedin|github)\.com)[^\s]+",
        text,
    )

    nome = _extrair_nome(lines, email, telefone)
    localizacao = _extrair_localizacao(lines)
    nivel_senioridade = _detectar_senioridade(text_lower)
    modalidade_preferida = _detectar_modalidade(text_lower)
    pretensao_salarial = _first_regex(
        r"R\$\s*\d[\d\.\,]*(?:\s*[-a]\s*R?\$?\s*\d[\d\.\,]*)?",
        text,
        flags=re.IGNORECASE,
    )
    cargo_atual = _extrair_cargo_atual(lines)
    objetivo = _extrair_objetivo(lines)
    habilidades = _extrair_habilidades(lines, text_lower)
    experiencias = _extrair_experiencias(lines)
    formacao = _extrair_formacao(lines)
    idiomas = _extrair_idiomas(lines)

    return {
        "nome": nome,
        "email": email,
        "telefone": telefone,
        "linkedin": linkedin,
        "github": github,
        "portfolio": portfolio,
        "cargo_atual": cargo_atual,
        "nivel_senioridade": nivel_senioridade,
        "localizacao": localizacao,
        "pretensao_salarial": pretensao_salarial,
        "modalidade_preferida": modalidade_preferida,
        "objetivo": objetivo,
        "habilidades": habilidades,
        "experiencias": experiencias,
        "formacao": formacao,
        "idiomas": idiomas,
    }


def _linhas_limpa(texto: str) -> list[str]:
    out = []
    for raw in (texto or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            out.append(line)
    return out


def _first_regex(pattern: str, text: str, flags: int = 0) -> str:
    m = re.search(pattern, text or "", flags)
    return m.group(0).strip() if m else ""


def _extrair_nome(lines: list[str], email: str, telefone: str) -> str:
    blockers = {"curriculo", "curriculum", "vitae", "resumo", "perfil"}
    for line in lines[:12]:
        low = line.lower()
        if email and email in line:
            continue
        if telefone and telefone in line:
            continue
        if "http" in low or "www." in low or "@" in low:
            continue
        if any(b in low for b in blockers):
            continue
        if len(line) < 5 or len(line) > 60:
            continue
        if len(re.findall(r"\d", line)) > 0:
            continue
        tokens = line.split()
        if 2 <= len(tokens) <= 5:
            return line
    return ""


def _extrair_localizacao(lines: list[str]) -> str:
    for line in lines[:20]:
        if re.search(r"\b[A-Za-z]+(?:\s+[A-Za-z]+)*\s*[-/]\s*[A-Z]{2}\b", line):
            return line
        if re.search(r"\b(sao paulo|rio de janeiro|curitiba|belo horizonte|porto alegre|brasilia)\b", line.lower()):
            return line
    return ""


def _detectar_senioridade(text_lower: str) -> str:
    if re.search(r"\b(staff|principal|lead|senior|seniora)\b", text_lower):
        return "senior"
    if re.search(r"\b(pleno|mid|middle)\b", text_lower):
        return "pleno"
    if re.search(r"\b(junior|juniora|jr|estagio|trainee)\b", text_lower):
        return "junior"
    return ""


def _detectar_modalidade(text_lower: str) -> str:
    if "remoto" in text_lower or "home office" in text_lower:
        return "remoto"
    if "hibrido" in text_lower or "hibrida" in text_lower:
        return "hibrido"
    if "presencial" in text_lower:
        return "presencial"
    return ""


def _extrair_cargo_atual(lines: list[str]) -> str:
    for line in lines[:25]:
        low = line.lower()
        if any(
            k in low
            for k in ["desenvolvedor", "engenheiro", "analista", "software", "programador", "devops", "data"]
        ):
            if len(line) <= 90:
                return line
    return ""


def _find_section(lines: list[str], keys: tuple[str, ...], max_take: int = 25) -> list[str]:
    idx = -1
    for i, line in enumerate(lines):
        low = _norm(line)
        if any(re.search(r"\b" + re.escape(k) + r"\b", low) for k in keys):
            idx = i
            break
    if idx < 0:
        return []

    section = []
    for line in lines[idx + 1 : idx + 1 + max_take]:
        low = _norm(line)
        # Para ao encontrar outro cabecalho claro.
        if _is_section_header(low):
            break
        section.append(line)
    return section


def _is_section_header(line_lower: str) -> bool:
    headers = _SEC_HABS + _SEC_EXP + _SEC_FORM + _SEC_IDIOMAS + _SEC_OBJ
    normalized = _norm(line_lower)
    if len(normalized) > 40:
        return False
    for h in headers:
        if re.match(r"^\s*" + re.escape(h) + r"(?:\s|$)", normalized):
            return True
    return False


def _extrair_objetivo(lines: list[str]) -> str:
    sec = _find_section(lines, _SEC_OBJ, max_take=6)
    if sec:
        return " ".join(sec)[:500]
    return ""


def _extrair_habilidades(lines: list[str], text_lower: str) -> list[dict]:
    raw_terms: list[str] = []
    sec = _find_section(lines, _SEC_HABS, max_take=20)
    for line in sec:
        for part in re.split(r"[,;|/•\-]", line):
            t = part.strip()
            if _skill_valida(t):
                raw_terms.append(t)

    # Fallback: detecta termos tecnicos conhecidos no texto.
    if not raw_terms:
        for skill in _KNOWN_SKILLS:
            if re.search(r"\b" + re.escape(skill) + r"\b", text_lower):
                raw_terms.append(skill)

    skills = []
    for s in _dedupe(raw_terms)[:20]:
        skills.append({"nome": s, "nivel": 3, "anos_exp": 0})
    return skills


def _skill_valida(term: str) -> bool:
    if not term or len(term) < 2 or len(term) > 40:
        return False
    if re.fullmatch(r"\d+", term):
        return False
    return True


def _extrair_experiencias(lines: list[str]) -> list[dict]:
    sec = _find_section(lines, _SEC_EXP, max_take=60)
    if not sec:
        return []

    experiencias = []
    atual = None

    for line in sec:
        if _is_experience_title(line):
            if atual and (atual.get("empresa") or atual.get("cargo")):
                experiencias.append(atual)
            cargo, empresa = _split_cargo_empresa(line)
            atual = {
                "empresa": empresa,
                "cargo": cargo,
                "inicio": "",
                "fim": "",
                "descricao": "",
            }
            continue

        if not atual:
            continue

        inicio, fim = _extract_period(line)
        if inicio or fim:
            atual["inicio"] = atual["inicio"] or inicio
            atual["fim"] = atual["fim"] or fim
            continue

        if len(line) >= 12:
            atual["descricao"] = (atual["descricao"] + " " + line).strip()[:1200]

    if atual and (atual.get("empresa") or atual.get("cargo")):
        experiencias.append(atual)

    # Remove entradas muito vazias
    cleaned = []
    for exp in experiencias[:6]:
        if exp.get("empresa") or exp.get("cargo"):
            cleaned.append(exp)
    return cleaned


def _is_experience_title(line: str) -> bool:
    if len(line) < 6 or len(line) > 120:
        return False
    norm_line = _norm(line)
    if re.fullmatch(
        r"(?:\d{2}/)?\d{4}\s*(?:-|ate|a)\s*(?:\d{2}/)?\d{4}|(?:\d{2}/)?\d{4}\s*(?:-|ate|a)\s*(?:atual|presente)",
        norm_line,
    ):
        return False
    # Ex: "Software Engineer - Empresa X"
    if any(sep in line for sep in [" - ", " | ", " @ ", " — "]):
        return True
    return False


def _split_cargo_empresa(line: str) -> tuple[str, str]:
    for sep in [" - ", " | ", " @ ", " — "]:
        if sep in line:
            left, right = [p.strip() for p in line.split(sep, 1)]
            if _parece_empresa(right):
                return left, right
            if _parece_empresa(left):
                return right, left
            return left, right
    return line.strip(), ""


def _parece_empresa(text: str) -> bool:
    low = text.lower()
    markers = ["ltda", "s/a", "sa", "inc", "corp", "empresa", "tecnologia", "solutions", "studio"]
    if any(m in low for m in markers):
        return True
    return bool(re.search(r"[A-Z][a-z]+", text))


def _extract_period(line: str) -> tuple[str, str]:
    m = re.search(
        r"((?:\d{2}/)?\d{4})\s*(?:-|ate|até|a)\s*((?:\d{2}/)?\d{4}|atual|presente)",
        line.lower(),
    )
    if not m:
        return "", ""
    inicio = m.group(1).strip()
    fim = m.group(2).strip()
    if fim in {"atual", "presente"}:
        fim = "Atual"
    return inicio, fim


def _extrair_formacao(lines: list[str]) -> list[dict]:
    sec = _find_section(lines, _SEC_FORM, max_take=25)
    out = []
    for line in sec[:8]:
        curso = line
        instituicao = ""
        ano = _first_regex(r"\b(19|20)\d{2}\b", line)
        if " - " in line:
            p1, p2 = [p.strip() for p in line.split(" - ", 1)]
            if len(p1) > 2 and len(p2) > 2:
                curso, instituicao = p1, p2
        out.append({
            "curso": curso[:120],
            "instituicao": instituicao[:120],
            "nivel": "",
            "ano": ano,
        })
    return out[:4]


def _extrair_idiomas(lines: list[str]) -> list[dict]:
    sec = _find_section(lines, _SEC_IDIOMAS, max_take=12)
    out = []
    for line in sec:
        idioma = line
        nivel = ""
        if ":" in line:
            idioma, nivel = [p.strip() for p in line.split(":", 1)]
        elif " - " in line:
            idioma, nivel = [p.strip() for p in line.split(" - ", 1)]
        out.append({"idioma": idioma[:50], "nivel": nivel[:50]})
    return out[:6]


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        key = item.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def _norm(text: str) -> str:
    base = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(ch for ch in base if unicodedata.category(ch) != "Mn")
