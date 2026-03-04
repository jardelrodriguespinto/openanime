"""
Agente de vagas — busca, recomendacao personalizada e geracao de curriculo ATS.
"""

import logging
import re
import unicodedata
import difflib

from ai.openrouter import openrouter
from data.jobs import Vaga, buscar_vagas, gerar_variantes
from graph.neo4j_client import get_neo4j
import prompts.jobs as jobs_prompt

logger = logging.getLogger(__name__)


def _sem_acento(texto: str) -> str:
    """Remove acentos para comparacao case-insensitive robusta."""
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode().lower()


def _normalizar_ruido(texto: str) -> str:
    base = _sem_acento(texto or "")
    # "curriloooo" -> "currilo"
    base = re.sub(r"(.)\1{2,}", r"\1", base)
    base = re.sub(r"[^a-z0-9\s+#/._-]", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base


def _eh_pedido_curriculo(texto: str) -> bool:
    norm = _normalizar_ruido(texto or "")
    if not norm:
        return False

    if any(kw in norm for kw in _KEYWORDS_CURRICULO_ATS):
        return True

    tokens = norm.split()
    alvos = {"curriculo", "curriculoats", "cv"}
    for tok in tokens:
        if tok in alvos:
            return True
        if any(difflib.SequenceMatcher(None, tok, alvo).ratio() >= 0.78 for alvo in ("curriculo", "curriculoats")):
            return True

    return False


_KEYWORDS_CURRICULO_ATS = [
    "curriculo", "curriculo ats", "gera curriculo", "montar curriculo",
    "customiza curriculo", "personaliza curriculo", "cria curriculo",
    "fazer curriculo", "meu curriculo", "quero curriculo",
]
_KEYWORDS_CANDIDATURAS = [
    "minhas candidaturas", "candidaturas", "onde me candidatei",
    "pipeline", "status candidatura",
]


_SENIOR_MAP = {
    "senior": ["senior", "sênior", "sr.", "sr ", " sr", "lead", "staff", "principal"],
    "pleno": ["pleno", "pl.", "pl ", " pl", "mid", "mid-level", "middle", "ii "],
    "junior": ["junior", "júnior", "jr.", "jr ", " jr", "estagio", "estágio", "entry", "trainee"],
}

# Palavras que indicam senioridade na mensagem do usuário
_SENIOR_KEYWORDS_MSG = {
    "senior": ["senior", "sênior", "sr", "lead"],
    "pleno": ["pleno", "mid", "mid-level", "middle", "pl"],
    "junior": ["junior", "júnior", "jr", "estagio", "estágio", "entry"],
}


def _detectar_senioridade_msg(msg: str) -> str:
    """Detecta senioridade explicitamente mencionada na mensagem (prioridade sobre perfil)."""
    for nivel, keywords in _SENIOR_KEYWORDS_MSG.items():
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", msg, re.IGNORECASE):
                return nivel
    return ""


def _detectar_query(mensagem: str, perfil: dict) -> tuple[str, str, str, str]:
    """Retorna (query, localizacao, modalidade, senioridade) extraidos da mensagem ou perfil."""
    msg = mensagem.lower()

    modalidade = ""
    if "remot" in msg:
        modalidade = "remoto"
    elif "hibrid" in msg:
        modalidade = "hibrido"
    elif "presencial" in msg or "escritorio" in msg:
        modalidade = "presencial"
    elif perfil.get("modalidade_preferida"):
        modalidade = perfil["modalidade_preferida"]

    # Senioridade: mensagem tem prioridade sobre perfil
    senioridade = _detectar_senioridade_msg(msg) or (perfil.get("nivel_senioridade") or "").lower()

    # Remove stopwords + termos de senioridade para extrair query de cargo/skill
    stopwords = {
        "vagas", "de", "para", "busca", "encontra", "tem", "hoje", "mim",
        "remoto", "hibrido", "presencial", "vaga", "emprego", "trabalho",
        "quero", "preciso", "procuro", "me", "meu", "minha", "meus",
        "minhas", "uma", "um", "mais", "por", "com", "sem", "novo", "nova",
        "oportunidades", "oportunidade", "recomenda", "ver",
        # ruído conversacional
        "mano", "cara", "mana", "nao", "não", "sim", "sou", "nós", "nos",
        "pois", "que", "isso", "aqui", "ali", "pra", "pro", "pros", "pras",
        "nao", "ja", "já", "só", "so", "bem", "vai", "vou", "queria",
        # senioridade (não devem ir na query de busca)
        "senior", "sênior", "pleno", "junior", "júnior", "mid", "lead",
        "estagio", "estágio", "entry", "trainee",
    }
    tokens = [t for t in re.split(r"\s+", msg) if t not in stopwords and len(t) > 2]
    query = " ".join(tokens[:5]).strip()

    # Fallback: usa cargo desejado ou skills do perfil quando query vazia ou generica
    if not query or query in {"desenvolvedor", "dev", "programador"}:
        if perfil.get("cargos_desejados"):
            query = perfil["cargos_desejados"][0]
        elif perfil.get("habilidades"):
            skills = [h.get("nome", "") for h in perfil["habilidades"][:2] if h.get("nome")]
            query = " ".join(filter(None, skills))

    localizacao = perfil.get("localizacao", "")
    if modalidade == "remoto":
        localizacao = ""

    return query or "desenvolvedor", localizacao, modalidade, senioridade


def _filtrar_por_senioridade(vagas: list, senioridade: str) -> list:
    """
    Filtra vagas pela senioridade solicitada.
    Mantém vagas sem indicação de nível (título genérico) e as do nível certo.
    Nunca retorna lista vazia — usa todas se o filtro zerasse.
    """
    if not senioridade:
        return vagas

    sinonimos_nivel = _SENIOR_MAP.get(senioridade, [])
    outros_niveis = [
        kws for nivel, kws in _SENIOR_MAP.items() if nivel != senioridade
    ]
    outros_flat = [kw for kws in outros_niveis for kw in kws]

    def _nivel_compativel(titulo: str) -> bool:
        t = titulo.lower()
        # Tem termo do nível certo → aceita
        if any(kw in t for kw in sinonimos_nivel):
            return True
        # Tem termo de outro nível → rejeita
        if any(kw in t for kw in outros_flat):
            return False
        # Sem indicação de nível → aceita (cargo genérico)
        return True

    filtradas = [v for v in vagas if _nivel_compativel(v.titulo)]
    # Se filtro muito restritivo zerou resultados, retorna tudo
    return filtradas if filtradas else vagas


def calcular_score_match(perfil: dict, vaga: Vaga) -> float:
    """Calcula score de compatibilidade entre perfil e vaga (0.0 a 1.0)."""
    score = 0.0

    # Habilidades (40%)
    skills_perfil = {h.get("nome", "").lower() for h in perfil.get("habilidades", [])}
    skills_vaga = set(vaga.requisitos)
    if skills_vaga:
        match = len(skills_perfil & skills_vaga) / len(skills_vaga)
        score += match * 0.40

    # Senioridade (25%)
    senioridade = (perfil.get("nivel_senioridade") or "").lower()
    titulo_lower = vaga.titulo.lower()
    if senioridade and senioridade in titulo_lower:
        score += 0.25
    elif senioridade:
        # Mapeamento aproximado
        senior_map = {"senior": ["senior", "sr", "lead", "staff"], "pleno": ["pleno", "pl", "mid"], "junior": ["junior", "jr", "estagio"]}
        sinonimos = senior_map.get(senioridade, [])
        if any(s in titulo_lower for s in sinonimos):
            score += 0.20

    # Modalidade (20%)
    modalidade_pref = (perfil.get("modalidade_preferida") or "").lower()
    if modalidade_pref and vaga.modalidade:
        if modalidade_pref == vaga.modalidade:
            score += 0.20
        elif vaga.modalidade == "hibrido":
            score += 0.10

    # Cargo desejado (15%)
    cargos = [c.lower() for c in perfil.get("cargos_desejados", [])]
    if cargos and any(c in titulo_lower for c in cargos):
        score += 0.15

    return round(min(score, 1.0), 2)


def jobs_node(state: dict) -> dict:
    """No LangGraph do agente de vagas."""
    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "")
    intent = state.get("intent", "")

    # Orquestrador já classificou como curriculo_ats — vai direto, sem reclassificar
    if intent == "curriculo_ats":
        return _modo_curriculo_ats(state)

    # Normaliza acentos para matching robusto ("currículo" == "curriculo")
    msg_norm = _normalizar_ruido(mensagem)

    # Modo curriculo ATS
    if _eh_pedido_curriculo(mensagem):
        return _modo_curriculo_ats(state)

    # Modo candidaturas (historico)
    if any(_sem_acento(kw) in msg_norm for kw in _KEYWORDS_CANDIDATURAS):
        return _modo_candidaturas(user_id)

    # Modo busca/recomendacao
    return _modo_busca(state)


def _modo_busca(state: dict) -> dict:
    """Busca e recomenda vagas."""
    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "")

    try:
        neo4j = get_neo4j()
        perfil = neo4j.get_perfil_profissional(user_id)
    except Exception:
        perfil = {}

    query, localizacao, modalidade, senioridade = _detectar_query(mensagem, perfil)

    # Gera variantes PT/EN + skills do perfil para ampliar a busca
    top_skills = [h.get("nome", "") for h in perfil.get("habilidades", [])[:3] if h.get("nome")]
    # Inclui senioridade na query principal para resultados mais relevantes
    query_com_nivel = f"{senioridade} {query}".strip() if senioridade else query
    variantes = gerar_variantes(query_com_nivel, top_skills)
    query_principal = variantes[0]
    queries_extras = variantes[1:] if len(variantes) > 1 else []

    logger.info("jobs: query='%s' senioridade='%s' variantes=%s", query_principal, senioridade, queries_extras)

    vagas = buscar_vagas(
        query_principal,
        localizacao=localizacao,
        modalidade=modalidade,
        queries_extras=queries_extras,
    )

    # Filtra por senioridade se explicitamente solicitada
    if senioridade:
        antes = len(vagas)
        vagas = _filtrar_por_senioridade(vagas, senioridade)
        logger.info("jobs: filtro senioridade='%s' antes=%d depois=%d", senioridade, antes, len(vagas))

    # Calcula score de match para cada vaga
    for vaga in vagas:
        vaga.score_match = calcular_score_match(perfil, vaga)

    # Ordena por score
    vagas.sort(key=lambda v: v.score_match, reverse=True)

    if not vagas:
        return {"response": f"Nao encontrei vagas de '{query}' agora. Tenta com outros termos ou verifique mais tarde!"}

    # Usa recomendacao personalizada se tiver perfil, busca simples se nao tiver
    if perfil.get("habilidades"):
        messages = jobs_prompt.build_recomendacao_messages(perfil, vagas, mensagem, senioridade_filtro=senioridade)
    else:
        messages = jobs_prompt.build_busca_messages(vagas, query)

    try:
        response = openrouter.search_synthesize(messages)
    except Exception as e:
        logger.error("jobs: erro LLM: %s", e)
        linhas = [f"Vagas encontradas para '{query}':\n"]
        for v in vagas[:5]:
            linhas.append(f"- {v.titulo} — {v.empresa}")
            if v.url:
                linhas.append(f"  {v.url}")
        response = "\n".join(linhas)

    # Salva vagas no Neo4j para historico
    _salvar_vagas_neo4j(vagas[:10])

    logger.info("jobs: busca concluida user=%s query=%s vagas=%d", user_id, query, len(vagas))
    return {"response": response}


def _modo_curriculo_ats(state: dict) -> dict:
    """Gera curriculo ATS para vaga especifica."""
    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "")

    try:
        neo4j = get_neo4j()
        perfil = neo4j.get_perfil_profissional(user_id)
    except Exception as e:
        return {"response": "Nao encontrei seu perfil profissional. Manda seu curriculo em PDF primeiro ou me conta sobre sua experiencia!"}

    if not perfil.get("nome") and not perfil.get("habilidades"):
        return {"response": "Seu perfil esta vazio ainda. Manda seu curriculo em PDF ou me conta sobre sua experiencia para eu gerar um curriculo personalizado!"}

    preferencias_curriculo = _extrair_preferencias_curriculo(mensagem)
    if _pediu_formato_sem_detalhar(mensagem, preferencias_curriculo) or _pediu_ajuste_generico_sem_detalhar(mensagem, preferencias_curriculo):
        return {
            "response": (
                "Posso gerar no formato que voce quiser. Me diga como quer, por exemplo: "
                "\"sem resumo\", \"mais compacto (1 pagina)\", \"foco em projetos\", "
                "\"remover idiomas\" ou \"priorizar experiencia antes de habilidades\"."
            )
        }

    # Tenta detectar vaga especifica na mensagem antes de usar historico.
    vaga_titulo_extraido = _extrair_titulo_vaga_mensagem(mensagem)
    vaga_titulo = vaga_titulo_extraido or ""
    vaga_empresa = ""
    vaga_descricao = ""
    vaga_requisitos = []

    # So usa ultima vaga quando a mensagem realmente se refere a ela.
    try:
        neo4j = get_neo4j()
        ultima_vaga = neo4j.get_ultima_vaga_visualizada(user_id)
        if ultima_vaga and _deve_usar_ultima_vaga(mensagem, vaga_titulo_extraido):
            if not vaga_titulo:
                vaga_titulo = ultima_vaga.get("titulo", vaga_titulo)
            vaga_empresa = ultima_vaga.get("empresa", "")
            vaga_descricao = ultima_vaga.get("descricao", "")
            vaga_requisitos = ultima_vaga.get("requisitos", [])
    except Exception:
        pass

    if not vaga_titulo:
        vaga_titulo = (
            (perfil.get("cargos_desejados") or [""])[0]
            or perfil.get("cargo_atual")
            or "Desenvolvedor de Software"
        )

    contexto_vaga = bool(
        vaga_empresa.strip()
        or (vaga_descricao or "").strip()
        or vaga_requisitos
        or vaga_titulo_extraido
    )

    try:
        from utils.ats_optimizer import otimizar_para_vaga
        from utils.pdf_writer import gerar_pdf_curriculo

        dados_curriculo = otimizar_para_vaga(
            perfil=perfil,
            vaga_titulo=vaga_titulo,
            vaga_empresa=vaga_empresa,
            vaga_descricao=vaga_descricao,
            vaga_requisitos=vaga_requisitos,
            preferencias=preferencias_curriculo,
        )

        pdf_bytes = gerar_pdf_curriculo(dados_curriculo)

        resposta = _mensagem_curriculo_gerado(
            vaga_titulo=vaga_titulo,
            vaga_empresa=vaga_empresa,
            contexto_vaga=contexto_vaga,
        )
        resumo_prefs = _resumo_preferencias(preferencias_curriculo)
        if resumo_prefs:
            resposta = f"{resposta}\n\nAjustes aplicados: {resumo_prefs}."

        # Salva referencia no state para o handler enviar o PDF
        return {
            "response": resposta,
            "pdf_bytes": pdf_bytes,
            "pdf_filename": f"curriculo_ats_{user_id}.pdf",
        }
    except Exception as e:
        logger.error("jobs: erro ao gerar curriculo ATS: %s", e)
        return {"response": "Nao consegui gerar o PDF agora. Verifique se weasyprint esta instalado (pip install weasyprint)."}


def _extrair_titulo_vaga_mensagem(mensagem: str) -> str:
    """
    Extrai um possivel titulo de vaga da mensagem do usuario sem LLM.
    Ex: "gera curriculo ats para backend python pleno" -> "backend python pleno"
    """
    if not mensagem:
        return ""

    txt = _sem_acento(mensagem.lower())
    txt = re.sub(r"[^\w\s+#/.-]", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()

    patterns = [
        r"(?:para|pra)\s+(?:vaga\s+de\s+|vaga\s+)?(.+)$",
        r"(?:vaga\s+de|cargo\s+de|posicao\s+de)\s+(.+)$",
    ]

    extraido = ""
    for pat in patterns:
        m = re.search(pat, txt)
        if m:
            extraido = m.group(1).strip()
            break

    if not extraido:
        return ""

    stop = {
        "curriculo", "curriculoats", "ats", "gera", "gerar", "monta", "montar",
        "fazer", "faz", "meu", "me", "por", "favor", "hoje", "agora", "um", "uma",
    }
    tokens = [t for t in extraido.split() if t not in stop and len(t) > 1]
    titulo = " ".join(tokens[:8]).strip()

    if len(titulo) < 3:
        return ""
    return titulo


def _deve_usar_ultima_vaga(mensagem: str, vaga_titulo_extraido: str) -> bool:
    """
    Evita "alucinacao de contexto": nao reaproveita vaga antiga quando o usuario
    so pediu um curriculo generico.
    """
    if vaga_titulo_extraido:
        return False

    txt = _sem_acento((mensagem or "").lower())
    gatilhos = [
        "essa vaga",
        "essa oportunidade",
        "aquela vaga",
        "aquela oportunidade",
        "ultima vaga",
        "vaga que voce mostrou",
        "vaga que me mostrou",
        "essa posicao",
        "essa posicao ai",
    ]
    return any(g in txt for g in gatilhos)


def _mensagem_curriculo_gerado(vaga_titulo: str, vaga_empresa: str, contexto_vaga: bool) -> str:
    if contexto_vaga:
        alvo = f"{vaga_titulo} na {vaga_empresa}" if vaga_empresa else vaga_titulo
        return (
            f"Curriculo ATS gerado para {alvo}. "
            "Usei apenas dados reais do seu perfil e ajustei a linguagem para os requisitos da vaga."
        )

    return (
        "Curriculo ATS base gerado com os dados reais do seu perfil profissional. "
        "Se quiser versao mais precisa para uma vaga especifica, me envie o link ou a descricao da vaga."
    )


def _extrair_preferencias_curriculo(mensagem: str) -> dict:
    """Extrai preferencias de formato do curriculo a partir da mensagem do usuario."""
    txt = _normalizar_ruido((mensagem or "").lower())
    prefs: dict = {}

    if any(k in txt for k in ["sem resumo", "sem objetivo", "sem perfil profissional"]):
        prefs["incluir_objetivo"] = False
    elif any(k in txt for k in ["com resumo", "com objetivo", "inclui resumo"]):
        prefs["incluir_objetivo"] = True

    if any(k in txt for k in ["sem idiomas", "remover idiomas", "tira idiomas"]):
        prefs["incluir_idiomas"] = False

    if any(k in txt for k in ["sem formacao", "remover formacao", "tira formacao"]):
        prefs["incluir_formacao"] = False

    if any(k in txt for k in ["sem habilidades", "remover habilidades", "tira habilidades"]):
        prefs["incluir_habilidades"] = False

    if any(k in txt for k in ["sem experiencia", "sem experiencias", "remover experiencias", "tira experiencias"]):
        prefs["incluir_experiencias"] = False

    if any(k in txt for k in ["somente habilidades", "so habilidades", "apenas habilidades"]):
        prefs["somente_habilidades"] = True

    if any(k in txt for k in ["somente experiencias", "so experiencias", "apenas experiencias"]):
        prefs["somente_experiencias"] = True

    if any(k in txt for k in ["experiencia primeiro", "prioriza experiencia"]):
        prefs["experiencia_primeiro"] = True

    if any(k in txt for k in ["foco em projetos", "foco em projeto", "foco em experiencia", "foco em experiencias"]):
        prefs["experiencia_primeiro"] = True
        prefs.setdefault("max_habilidades", 10)
        prefs.setdefault("max_experiencias", 5)

    if any(k in txt for k in ["compacto", "1 pagina", "uma pagina", "enxuto", "resumido"]):
        prefs["max_habilidades"] = 10
        prefs["max_experiencias"] = 3
        prefs["max_bullets_por_experiencia"] = 3
        prefs["max_formacao"] = 2
        prefs["max_idiomas"] = 2

    if any(k in txt for k in ["detalhado", "completo", "2 paginas", "duas paginas"]):
        prefs.setdefault("max_habilidades", 18)
        prefs.setdefault("max_experiencias", 5)
        prefs.setdefault("max_bullets_por_experiencia", 5)

    m_pag = re.search(r"\b([12])\s+paginas?\b", txt)
    if m_pag:
        paginas = int(m_pag.group(1))
        if paginas <= 1:
            prefs["max_habilidades"] = 10
            prefs["max_experiencias"] = 3
            prefs["max_bullets_por_experiencia"] = 3
            prefs["max_formacao"] = 2
            prefs["max_idiomas"] = 2
        else:
            prefs.setdefault("max_habilidades", 18)
            prefs.setdefault("max_experiencias", 5)
            prefs.setdefault("max_bullets_por_experiencia", 5)

    m_hab = re.search(r"(?:max(?:imo)?\s+)?(\d{1,2})\s+habilidades", txt)
    if m_hab:
        prefs["max_habilidades"] = max(4, min(int(m_hab.group(1)), 30))

    m_exp = re.search(r"(?:max(?:imo)?\s+)?(\d{1,2})\s+experiencias?", txt)
    if m_exp:
        prefs["max_experiencias"] = max(1, min(int(m_exp.group(1)), 10))

    m_bul = re.search(r"(?:max(?:imo)?\s+)?(\d{1,2})\s+(?:bullets|itens)", txt)
    if m_bul:
        prefs["max_bullets_por_experiencia"] = max(1, min(int(m_bul.group(1)), 8))

    m_form = re.search(r"(?:max(?:imo)?\s+)?(\d{1,2})\s+formac", txt)
    if m_form:
        prefs["max_formacao"] = max(0, min(int(m_form.group(1)), 5))

    m_idi = re.search(r"(?:max(?:imo)?\s+)?(\d{1,2})\s+idiomas", txt)
    if m_idi:
        prefs["max_idiomas"] = max(0, min(int(m_idi.group(1)), 6))

    focos = _extrair_foco_palavras(txt)
    if focos:
        prefs["foco_palavras"] = focos

    return prefs


def _pediu_formato_sem_detalhar(mensagem: str, prefs: dict) -> bool:
    txt = _normalizar_ruido((mensagem or "").lower())
    gatilhos = [
        "do meu jeito",
        "da minha forma",
        "como eu sugerir",
        "no formato que eu quiser",
        "quero escolher o formato",
    ]
    return any(g in txt for g in gatilhos) and not prefs


def _pediu_ajuste_generico_sem_detalhar(mensagem: str, prefs: dict) -> bool:
    txt = _normalizar_ruido((mensagem or "").lower())
    gatilhos = [
        "ajusta isso",
        "ajuste isso",
        "nao ajusta",
        "nao ficou bom",
        "deixa melhor",
        "faz melhor",
        "quero diferente",
        "do jeito que eu quero",
    ]
    return any(g in txt for g in gatilhos) and not prefs


def _extrair_foco_palavras(txt: str) -> list[str]:
    focos: list[str] = []
    stop = {
        "foco", "em", "de", "da", "do", "para", "pra", "mais", "menos",
        "curriculo", "curriculoats", "vaga", "vagas", "experiencia", "experiencias",
        "max", "min", "minimo", "maximo", "pagina", "paginas",
        "idioma", "idiomas", "habilidade", "habilidades", "bullets", "itens",
        "formacao", "formacoes",
    }

    for m in re.finditer(r"foco em ([a-z0-9+#/._\-\s]{2,80})", txt):
        trecho = m.group(1)
        trecho = re.split(r"[,;:.]|\b(?:com|sem|mas|porem|max|min|minimo|maximo)\b", trecho)[0]
        tokens = [t for t in trecho.split() if len(t) > 2 and t not in stop]
        for t in tokens[:6]:
            if re.fullmatch(r"\d+", t):
                continue
            if t not in focos:
                focos.append(t)

    return focos[:8]


def _resumo_preferencias(prefs: dict) -> str:
    if not prefs:
        return ""

    itens: list[str] = []
    if prefs.get("incluir_objetivo") is False:
        itens.append("sem resumo")
    if prefs.get("incluir_formacao") is False:
        itens.append("sem formacao")
    if prefs.get("incluir_idiomas") is False:
        itens.append("sem idiomas")
    if prefs.get("incluir_habilidades") is False:
        itens.append("sem habilidades")
    if prefs.get("incluir_experiencias") is False:
        itens.append("sem experiencias")
    if prefs.get("somente_habilidades"):
        itens.append("somente habilidades")
    if prefs.get("somente_experiencias"):
        itens.append("somente experiencias")
    if prefs.get("experiencia_primeiro"):
        itens.append("experiencia antes de habilidades")

    if isinstance(prefs.get("max_habilidades"), int):
        itens.append(f"max {prefs['max_habilidades']} habilidades")
    if isinstance(prefs.get("max_experiencias"), int):
        itens.append(f"max {prefs['max_experiencias']} experiencias")
    if isinstance(prefs.get("max_bullets_por_experiencia"), int):
        itens.append(f"max {prefs['max_bullets_por_experiencia']} bullets por experiencia")
    if isinstance(prefs.get("max_formacao"), int):
        itens.append(f"max {prefs['max_formacao']} formacoes")
    if isinstance(prefs.get("max_idiomas"), int):
        itens.append(f"max {prefs['max_idiomas']} idiomas")

    focos = prefs.get("foco_palavras") or []
    if focos:
        itens.append(f"foco em {', '.join(focos[:4])}")

    return ", ".join(itens[:8])


def _modo_candidaturas(user_id: str) -> dict:
    """Mostra pipeline de candidaturas."""
    try:
        neo4j = get_neo4j()
        candidaturas = neo4j.get_candidaturas(user_id)
    except Exception as e:
        logger.error("jobs: erro ao buscar candidaturas: %s", e)
        return {"response": "Nao consegui carregar suas candidaturas agora."}

    if not candidaturas:
        return {"response": "Voce ainda nao se candidatou a nenhuma vaga por aqui. Use /vagas para buscar vagas!"}

    em_andamento = [c for c in candidaturas if c.get("status") in ("candidatado", "visualizado", "entrevista")]
    finalizadas = [c for c in candidaturas if c.get("status") in ("oferta", "recusado")]

    linhas = ["Suas candidaturas:\n"]

    if em_andamento:
        linhas.append("Em andamento:")
        status_emoji = {"candidatado": "🟡", "visualizado": "🔵", "entrevista": "🟢"}
        for c in em_andamento[:8]:
            emoji = status_emoji.get(c.get("status", ""), "⚪")
            linhas.append(f"{emoji} {c.get('titulo', '?')} — {c.get('empresa', '?')} ({c.get('data', '?')})")

    if finalizadas:
        linhas.append("\nFinalizadas:")
        for c in finalizadas[:5]:
            emoji = "✅" if c.get("status") == "oferta" else "❌"
            linhas.append(f"{emoji} {c.get('titulo', '?')} — {c.get('empresa', '?')}")

    return {"response": "\n".join(linhas)}


def _salvar_vagas_neo4j(vagas: list[Vaga]) -> None:
    """Salva vagas no Neo4j para referencia futura."""
    try:
        neo4j = get_neo4j()
        for vaga in vagas:
            neo4j.upsert_vaga({
                "id": vaga.id,
                "titulo": vaga.titulo,
                "empresa": vaga.empresa,
                "url": vaga.url,
                "fonte": vaga.fonte,
                "salario": vaga.salario,
                "modalidade": vaga.modalidade,
                "descricao": vaga.descricao[:500],
                "requisitos": vaga.requisitos,
            })
    except Exception as e:
        logger.debug("jobs: erro ao salvar vagas: %s", e)
