"""
Agente de vagas — busca, recomendacao personalizada e geracao de curriculo ATS.
"""

import asyncio
import logging
import re

from ai.openrouter import openrouter
from data.jobs import Vaga, buscar_vagas, gerar_variantes
from graph.neo4j_client import get_neo4j
import prompts.jobs as jobs_prompt

logger = logging.getLogger(__name__)

_KEYWORDS_CURRICULO_ATS = [
    "curriculo", "gera curriculo", "montar curriculo", "curriculo ats",
    "customiza curriculo", "personaliza curriculo",
]
_KEYWORDS_CANDIDATURAS = [
    "minhas candidaturas", "candidaturas", "onde me candidatei",
    "pipeline", "status candidatura",
]


def _detectar_query(mensagem: str, perfil: dict) -> tuple[str, str, str]:
    """Retorna (query, localizacao, modalidade) extraidos da mensagem ou perfil."""
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

    # Remove stopwords para extrair query principal
    stopwords = {"vagas", "de", "para", "busca", "encontra", "tem", "hoje",
                 "remoto", "hibrido", "presencial", "vaga", "emprego", "trabalho"}
    tokens = [t for t in re.split(r"\s+", msg) if t not in stopwords and len(t) > 2]
    query = " ".join(tokens[:5]).strip()

    if not query and perfil.get("cargos_desejados"):
        query = perfil["cargos_desejados"][0]
    elif not query and perfil.get("habilidades"):
        query = " ".join(h.get("nome", "") for h in perfil["habilidades"][:2])

    localizacao = perfil.get("localizacao", "")
    if modalidade == "remoto":
        localizacao = ""

    return query or "desenvolvedor", localizacao, modalidade


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
    msg_lower = mensagem.lower()

    # Modo curriculo ATS
    if any(kw in msg_lower for kw in _KEYWORDS_CURRICULO_ATS):
        return _modo_curriculo_ats(state)

    # Modo candidaturas (historico)
    if any(kw in msg_lower for kw in _KEYWORDS_CANDIDATURAS):
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

    query, localizacao, modalidade = _detectar_query(mensagem, perfil)

    # Gera variantes PT/EN + skills do perfil para ampliar a busca
    top_skills = [h.get("nome", "") for h in perfil.get("habilidades", [])[:3] if h.get("nome")]
    variantes = gerar_variantes(query, top_skills)
    query_principal = variantes[0]
    queries_extras = variantes[1:] if len(variantes) > 1 else []

    logger.info("jobs: query='%s' variantes=%s", query_principal, queries_extras)

    vagas = buscar_vagas(
        query_principal,
        localizacao=localizacao,
        modalidade=modalidade,
        queries_extras=queries_extras,
    )

    # Calcula score de match para cada vaga
    for vaga in vagas:
        vaga.score_match = calcular_score_match(perfil, vaga)

    # Ordena por score
    vagas.sort(key=lambda v: v.score_match, reverse=True)

    if not vagas:
        return {"response": f"Nao encontrei vagas de '{query}' agora. Tenta com outros termos ou verifique mais tarde!"}

    # Usa recomendacao personalizada se tiver perfil, busca simples se nao tiver
    if perfil.get("habilidades"):
        messages = jobs_prompt.build_recomendacao_messages(perfil, vagas, mensagem)
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

    # Tenta detectar vaga especifica na mensagem ou usa generico
    vaga_titulo = "desenvolvedor"
    vaga_empresa = ""
    vaga_descricao = ""
    vaga_requisitos = []

    # Tenta buscar ultima vaga visualizada
    try:
        neo4j = get_neo4j()
        ultima_vaga = neo4j.get_ultima_vaga_visualizada(user_id)
        if ultima_vaga:
            vaga_titulo = ultima_vaga.get("titulo", vaga_titulo)
            vaga_empresa = ultima_vaga.get("empresa", "")
            vaga_descricao = ultima_vaga.get("descricao", "")
            vaga_requisitos = ultima_vaga.get("requisitos", [])
    except Exception:
        pass

    try:
        from utils.ats_optimizer import otimizar_para_vaga
        from utils.pdf_writer import gerar_pdf_curriculo

        dados_curriculo = otimizar_para_vaga(
            perfil=perfil,
            vaga_titulo=vaga_titulo,
            vaga_empresa=vaga_empresa,
            vaga_descricao=vaga_descricao,
            vaga_requisitos=vaga_requisitos,
        )

        pdf_bytes = gerar_pdf_curriculo(dados_curriculo)

        # Salva referencia no state para o handler enviar o PDF
        return {
            "response": f"Curriculo ATS gerado para vaga de {vaga_titulo}! Otimizado com keywords da vaga e pronto para passar nos filtros automaticos.",
            "pdf_bytes": pdf_bytes,
            "pdf_filename": f"curriculo_ats_{user_id}.pdf",
        }
    except Exception as e:
        logger.error("jobs: erro ao gerar curriculo ATS: %s", e)
        return {"response": "Nao consegui gerar o PDF agora. Verifique se weasyprint esta instalado (pip install weasyprint)."}


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

    linhas = ["<b>Suas candidaturas:</b>\n"]

    if em_andamento:
        linhas.append("<b>Em andamento:</b>")
        status_emoji = {"candidatado": "🟡", "visualizado": "🔵", "entrevista": "🟢"}
        for c in em_andamento[:8]:
            emoji = status_emoji.get(c.get("status", ""), "⚪")
            linhas.append(f"{emoji} {c.get('titulo', '?')} — {c.get('empresa', '?')} ({c.get('data', '?')})")

    if finalizadas:
        linhas.append("\n<b>Finalizadas:</b>")
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
