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
    "desenvolvedor", "developer", "engenheiro", "software", "analista",
    "senior", "pleno", "junior", "sr", "jr", "mid", "nivel",
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
    preferencias: dict | None = None,
    instrucoes_usuario: str = "",
) -> dict:
    """
    Gera curriculo ATS de forma deterministica, sem usar modelo generativo.
    """
    contexto_vaga_especifica = _tem_contexto_vaga_especifica(
        vaga_titulo=vaga_titulo,
        vaga_empresa=vaga_empresa,
        vaga_descricao=vaga_descricao,
        vaga_requisitos=vaga_requisitos,
    )
    keywords = _extrair_keywords_vaga(vaga_titulo, vaga_descricao, vaga_requisitos)
    keywords_aderentes = _filtrar_keywords_aderentes_ao_perfil(perfil, keywords)
    habilidades = _priorizar_habilidades(perfil, keywords)
    # Sanitiza vaga_titulo — rejeita mensagens cruas do usuario (ex: "mim mano")
    vaga_titulo_limpo = _sanitizar_titulo_vaga(vaga_titulo, perfil)

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
        "objetivo": _gerar_objetivo(
            perfil=perfil,
            vaga_titulo=vaga_titulo_limpo,
            vaga_empresa=vaga_empresa,
            keywords=keywords_aderentes,
            contexto_vaga_especifica=contexto_vaga_especifica,
        ),
        "habilidades": habilidades or _habilidades_fallback(perfil),
        "experiencias": experiencias,
        "formacao": _formatar_formacao(_dedup_formacao(perfil.get("formacao", []))),
        "idiomas": _dedup_idiomas(perfil.get("idiomas", [])),
    }
    _aplicar_preferencias_curriculo(resultado, preferencias or {})
    _aplicar_instrucoes_livres(resultado, instrucoes_usuario)

    logger.info(
        "ats_optimizer(local): vaga=%s | keywords=%d | habilidades=%d | experiencias=%d",
        vaga_titulo,
        len(keywords_aderentes),
        len(resultado["habilidades"]),
        len(resultado["experiencias"]),
    )
    return resultado


def _aplicar_preferencias_curriculo(resultado: dict, preferencias: dict) -> None:
    if not preferencias:
        return

    # Atalhos de secoes.
    if preferencias.get("somente_habilidades"):
        resultado["experiencias"] = []
        resultado["formacao"] = []
        resultado["idiomas"] = []

    if preferencias.get("somente_experiencias"):
        resultado["habilidades"] = []
        resultado["formacao"] = []
        resultado["idiomas"] = []

    if preferencias.get("incluir_objetivo") is False:
        resultado["objetivo"] = ""

    if preferencias.get("incluir_habilidades") is False:
        resultado["habilidades"] = []

    if preferencias.get("incluir_experiencias") is False:
        resultado["experiencias"] = []

    if preferencias.get("incluir_formacao") is False:
        resultado["formacao"] = []

    if preferencias.get("incluir_idiomas") is False:
        resultado["idiomas"] = []

    if preferencias.get("incluir_linkedin") is False:
        resultado["linkedin"] = ""
    if preferencias.get("incluir_github") is False:
        resultado["github"] = ""
    if preferencias.get("incluir_portfolio") is False:
        resultado["portfolio"] = ""
    if preferencias.get("incluir_telefone") is False:
        resultado["telefone"] = ""
    if preferencias.get("incluir_email") is False:
        resultado["email"] = ""

    foco_palavras = preferencias.get("foco_palavras") or []
    if foco_palavras:
        resultado["habilidades"] = _ordenar_habilidades_por_foco(
            resultado.get("habilidades", []),
            foco_palavras,
        )
        _ordenar_bullets_experiencia_por_foco(
            resultado.get("experiencias", []),
            foco_palavras,
        )

    max_habilidades = preferencias.get("max_habilidades")
    if isinstance(max_habilidades, int) and max_habilidades > 0:
        resultado["habilidades"] = (resultado.get("habilidades") or [])[:max_habilidades]

    max_experiencias = preferencias.get("max_experiencias")
    if isinstance(max_experiencias, int) and max_experiencias > 0:
        resultado["experiencias"] = (resultado.get("experiencias") or [])[:max_experiencias]

    max_bullets = preferencias.get("max_bullets_por_experiencia")
    if isinstance(max_bullets, int) and max_bullets > 0:
        for exp in resultado.get("experiencias", []) or []:
            exp["bullets"] = (exp.get("bullets") or [])[:max_bullets]

    max_formacao = preferencias.get("max_formacao")
    if isinstance(max_formacao, int) and max_formacao >= 0:
        resultado["formacao"] = (resultado.get("formacao") or [])[:max_formacao]

    max_idiomas = preferencias.get("max_idiomas")
    if isinstance(max_idiomas, int) and max_idiomas >= 0:
        resultado["idiomas"] = (resultado.get("idiomas") or [])[:max_idiomas]

    if preferencias.get("experiencia_primeiro"):
        resultado["experiencia_primeiro"] = True

    if preferencias.get("objetivo_curto") and resultado.get("objetivo"):
        resultado["objetivo"] = _encurtar_texto(resultado["objetivo"], max_chars=180)


def _aplicar_instrucoes_livres(resultado: dict, instrucoes_usuario: str) -> None:
    """
    Pequenos ajustes adicionais quando o usuario descreve preferencia em linguagem livre.
    Mantem o comportamento deterministico e sem inventar dados.
    """
    txt = (instrucoes_usuario or "").lower()
    if not txt:
        return

    if ("habilidades primeiro" in txt) or ("skills primeiro" in txt):
        resultado["experiencia_primeiro"] = False

    if ("experiencia primeiro" in txt) or ("experiencias primeiro" in txt):
        resultado["experiencia_primeiro"] = True

    if ("objetivo curto" in txt or "resumo curto" in txt) and resultado.get("objetivo"):
        resultado["objetivo"] = _encurtar_texto(resultado["objetivo"], max_chars=180)


def _encurtar_texto(texto: str, max_chars: int = 180) -> str:
    t = (texto or "").strip()
    if len(t) <= max(40, max_chars):
        return t
    corte = t[:max_chars].rsplit(" ", 1)[0].strip()
    return (corte or t[:max_chars]).rstrip(".,;:") + "."


def _normalizar_foco(txt: str) -> str:
    base = str(txt or "").lower().strip()
    base = re.sub(r"[^a-z0-9+#/._\-\s]", " ", base)
    return re.sub(r"\s+", " ", base).strip()


def _score_foco(texto: str, foco_palavras: list[str]) -> int:
    alvo = _normalizar_foco(texto)
    if not alvo:
        return 0
    score = 0
    for foco in foco_palavras:
        f = _normalizar_foco(foco)
        if not f:
            continue
        if f == alvo:
            score += 3
        elif f in alvo or alvo in f:
            score += 2
        elif any(tok == f for tok in alvo.split()):
            score += 1
    return score


def _ordenar_habilidades_por_foco(habilidades: list[str], foco_palavras: list[str]) -> list[str]:
    if not habilidades or not foco_palavras:
        return habilidades
    ranked = []
    for idx, skill in enumerate(habilidades):
        ranked.append((_score_foco(skill, foco_palavras), idx, skill))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [item[2] for item in ranked]


def _ordenar_bullets_experiencia_por_foco(experiencias: list[dict], foco_palavras: list[str]) -> None:
    if not experiencias or not foco_palavras:
        return
    for exp in experiencias:
        bullets = exp.get("bullets") or []
        ranked = []
        for idx, b in enumerate(bullets):
            ranked.append((_score_foco(b, foco_palavras), idx, b))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        exp["bullets"] = [item[2] for item in ranked]


def _tem_contexto_vaga_especifica(
    vaga_titulo: str,
    vaga_empresa: str,
    vaga_descricao: str,
    vaga_requisitos: list[str],
) -> bool:
    titulo = (vaga_titulo or "").strip().lower()
    titulos_genericos = {
        "desenvolvedor",
        "developer",
        "programador",
        "engenheiro de software",
        "software engineer",
    }
    if vaga_empresa and vaga_empresa.strip():
        return True
    if vaga_descricao and len(vaga_descricao.strip()) >= 80:
        return True
    if vaga_requisitos:
        return True
    if titulo and titulo not in titulos_genericos:
        return True
    return False


def _normalizar_termo(termo: str) -> str:
    txt = (termo or "").strip().lower()
    txt = re.sub(r"[^\w+#.\-/ ]+", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def _filtrar_keywords_aderentes_ao_perfil(perfil: dict, keywords: list[str]) -> list[str]:
    skills = [str(h.get("nome", "") or "") for h in perfil.get("habilidades", [])]
    skills_norm = [_normalizar_termo(s) for s in skills if s and _normalizar_termo(s)]
    if not skills_norm:
        return []

    aderentes: list[str] = []
    for kw in keywords:
        kn = _normalizar_termo(kw)
        if not kn:
            continue
        if any(kn == s or kn in s or s in kn for s in skills_norm):
            aderentes.append(kw)

    return _dedupe_preserve(aderentes)[:10]


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


def _gerar_objetivo(
    perfil: dict,
    vaga_titulo: str,
    vaga_empresa: str,
    keywords: list[str],
    contexto_vaga_especifica: bool,
) -> str:
    nivel = str(perfil.get("nivel_senioridade", "") or "").strip().capitalize()
    cargo_base = str(perfil.get("cargo_atual", "") or "").strip() or (vaga_titulo or "Desenvolvedor")
    objetivo_base = str(perfil.get("objetivo", "") or "").strip()

    skills = [h.get("nome", "") for h in perfil.get("habilidades", []) if h.get("nome")]
    skills_top = ", ".join(skills[:3]) if skills else "desenvolvimento de software"
    kw_top = ", ".join(keywords[:4]) if keywords else ""

    parte_nivel = f"{nivel} " if nivel else ""
    parte_empresa = f" na empresa {vaga_empresa}" if vaga_empresa else ""
    parte_kw = f" com foco em {kw_top}" if kw_top else ""

    if not contexto_vaga_especifica:
        if objetivo_base:
            return objetivo_base[:320]
        return (
            f"{parte_nivel}{cargo_base} com experiencia em {skills_top}. "
            "Busco contribuir em projetos de tecnologia com foco em impacto e qualidade."
        ).strip()

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
    vistos: set[str] = set()
    for exp in experiencias[:8]:  # itera mais para ter margem de dedup
        empresa = str(exp.get("empresa", "") or "").strip()
        cargo = str(exp.get("cargo", "") or "").strip()
        inicio = str(exp.get("inicio", "") or "").strip()
        fim = str(exp.get("fim", "") or "").strip()
        descricao = str(exp.get("descricao", "") or "").strip()

        if not empresa and not cargo and not descricao:
            continue

        # Dedup por empresa + cargo + inicio
        chave = f"{empresa.lower()}|{cargo.lower()}|{inicio}"
        if chave in vistos:
            continue
        vistos.add(chave)

        bullets = _bullets_from_descricao(descricao, keywords) if descricao else []
        resultado.append({
            "empresa": empresa,
            "cargo": cargo,
            "periodo": _formatar_periodo(inicio, fim),
            "bullets": bullets,
        })
        if len(resultado) >= 5:
            break
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


def _sanitizar_titulo_vaga(vaga_titulo: str, perfil: dict) -> str:
    """
    Rejeita titulos que sao mensagens cruas do usuario (ex: 'mim mano', 'pdf', 'curriculo').
    Retorna cargo_atual do perfil ou string vazia se o titulo parecer invalido.
    """
    titulo = (vaga_titulo or "").strip()
    if not titulo:
        return ""
    # Considerado invalido se: muito curto, contem palavras coloquiais, ou nao tem termo tecnico
    palavras_invalidas = {
        "mano", "cara", "mim", "me", "eu", "meu", "minha", "vc", "voce",
        "pdf", "curriculo", "cv", "sim", "nao", "ok", "please", "pls",
    }
    palavras = set(titulo.lower().split())
    if palavras & palavras_invalidas:
        return str(perfil.get("cargo_atual", "") or "")
    # Titulo muito curto e sem termos tecnicos tambem e suspeito
    if len(titulo) < 6:
        return str(perfil.get("cargo_atual", "") or "")
    return titulo


def _dedup_idiomas(idiomas: list) -> list:
    """Remove idiomas duplicados — mantém o de nivel mais alto."""
    vistos: dict[str, dict] = {}
    _nivel_ord = {"nativo": 5, "fluente": 4, "avancado": 4, "avançado": 4,
                  "intermediario": 3, "intermediário": 3, "basico": 2, "básico": 2}
    for item in idiomas:
        idioma = str(item.get("idioma", "") or "").strip().lower()
        nivel = str(item.get("nivel", "") or "").strip()
        if not idioma:
            continue
        if idioma not in vistos:
            vistos[idioma] = {"idioma": item.get("idioma", "").strip(), "nivel": nivel}
        else:
            # Mantém nivel mais alto
            atual = vistos[idioma]["nivel"].lower()
            novo = nivel.lower()
            if _nivel_ord.get(novo, 0) > _nivel_ord.get(atual, 0):
                vistos[idioma]["nivel"] = nivel
    return list(vistos.values())


def _dedup_formacao(formacao: list) -> list:
    """Remove formacoes duplicadas — mesmo curso + instituicao."""
    vistos: set[str] = set()
    resultado = []
    for f in formacao:
        curso = str(f.get("curso", "") or "").strip().lower()
        inst = str(f.get("instituicao", "") or "").strip().lower()
        chave = f"{curso}|{inst}"
        if chave in vistos or (not curso and not inst):
            continue
        vistos.add(chave)
        resultado.append(f)
    return resultado


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
