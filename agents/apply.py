"""
Agente de candidatura automatica — orquestra LinkedIn Easy Apply, Gupy e outros.
Sempre pede confirmacao antes de qualquer acao.
"""

import logging
import os
import tempfile

from ai.openrouter import openrouter
from automation.browser import detectar_plataforma
from graph.neo4j_client import get_neo4j
import prompts.apply as apply_prompt

logger = logging.getLogger(__name__)

APPLY_DAILY_LIMIT = int(os.getenv("APPLY_DAILY_LIMIT", "10"))

_KEYWORDS_HISTORICO = ["minhas candidaturas", "candidaturas", "pipeline", "status"]
_KEYWORDS_CANDIDATAR = ["candidata", "se candidatar", "me inscrev", "aplica para", "aplica na"]


def apply_node(state: dict) -> dict:
    """No LangGraph do agente de candidatura."""
    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "").lower()

    if any(kw in mensagem for kw in _KEYWORDS_HISTORICO):
        return _mostrar_pipeline(user_id)

    return _iniciar_candidatura(state)


def _iniciar_candidatura(state: dict) -> dict:
    """Inicia fluxo de candidatura com confirmacao."""
    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "")

    # Verifica limite diario
    try:
        neo4j = get_neo4j()
        count_hoje = neo4j.contar_candidaturas_hoje(user_id)
        if count_hoje >= APPLY_DAILY_LIMIT:
            return {"response": f"Voce ja se candidatou a {count_hoje} vagas hoje. Limite diario de {APPLY_DAILY_LIMIT} atingido para evitar spam."}
    except Exception:
        pass

    # Busca vaga alvo
    vaga = _resolver_vaga(user_id, mensagem)
    if not vaga:
        return {"response": "Nao encontrei qual vaga voce quer se candidatar. Use /vagas para buscar vagas primeiro!"}

    # Verifica candidatura duplicada
    try:
        neo4j = get_neo4j()
        if neo4j.ja_se_candidatou(user_id, vaga.get("id", "")):
            return {"response": f"Voce ja se candidatou para {vaga.get('titulo')} na {vaga.get('empresa')}!"}
    except Exception:
        pass

    # Carrega perfil
    try:
        neo4j = get_neo4j()
        perfil = neo4j.get_perfil_profissional(user_id)
    except Exception:
        perfil = {}

    plataforma = detectar_plataforma(vaga.get("url", ""))

    # Monta mensagem de confirmacao
    messages = apply_prompt.build_confirmacao_messages(vaga, perfil, plataforma)
    try:
        confirmacao_msg = openrouter.converse(messages)
    except Exception:
        confirmacao_msg = f"Vou me candidatar para:\n{vaga.get('titulo')} — {vaga.get('empresa')}\nURL: {vaga.get('url')}\n\nConfirma?"

    # Salva estado pendente no state para o handler confirmar
    return {
        "response": confirmacao_msg + "\n\nResponde com 'sim' para confirmar ou 'nao' para cancelar.",
        "candidatura_pendente": {
            "vaga": vaga,
            "perfil": perfil,
            "plataforma": plataforma,
        },
    }


async def executar_candidatura(user_id: str, vaga: dict, perfil: dict, plataforma: str) -> dict:
    """
    Executa a candidatura apos confirmacao do usuario.
    """
    vaga_url = vaga.get("url", "")
    if not vaga_url:
        return {"sucesso": False, "mensagem": "URL da vaga nao encontrada."}

    # Gera curriculo ATS temporario
    curriculo_path = ""
    try:
        from utils.ats_optimizer import otimizar_para_vaga
        from utils.pdf_writer import gerar_pdf_curriculo

        dados = otimizar_para_vaga(
            perfil=perfil,
            vaga_titulo=vaga.get("titulo", ""),
            vaga_empresa=vaga.get("empresa", ""),
            vaga_descricao=vaga.get("descricao", ""),
            vaga_requisitos=vaga.get("requisitos", []),
        )
        pdf_bytes = gerar_pdf_curriculo(dados)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            curriculo_path = f.name
    except Exception as e:
        logger.warning("apply: nao foi possivel gerar curriculo ATS: %s", e)

    # Tenta candidatura conforme plataforma
    resultado = None
    if plataforma == "linkedin":
        from automation.linkedin_apply import aplicar
        resultado = await aplicar(vaga_url, perfil, curriculo_path)
    elif plataforma == "gupy":
        from automation.gupy_apply import aplicar
        resultado = await aplicar(vaga_url, perfil, curriculo_path)
    else:
        # Para plataformas desconhecidas: abre link
        resultado = {
            "sucesso": False,
            "motivo_falha": "plataforma_desconhecida",
            "mensagem": f"Plataforma nao suportada para automacao. Acesse manualmente: {vaga_url}",
        }

    # Limpa arquivo temporario
    if curriculo_path:
        try:
            os.remove(curriculo_path)
        except Exception:
            pass

    # Registra tentativa no Neo4j
    try:
        neo4j = get_neo4j()
        status = "candidatado" if resultado.get("sucesso") else "tentativa_falhou"
        neo4j.registrar_candidatura(
            user_id=user_id,
            vaga_id=vaga.get("id", ""),
            plataforma=plataforma,
            status=status,
        )
    except Exception as e:
        logger.debug("apply: erro ao registrar candidatura: %s", e)

    # Handle perguntas customizadas
    if resultado.get("motivo_falha") == "perguntas_customizadas":
        perguntas = resultado.get("perguntas_customizadas", [])
        messages = apply_prompt.build_perguntas_messages(perguntas, perfil, vaga)
        try:
            sugestoes = openrouter.converse(messages)
        except Exception:
            sugestoes = "\n".join(f"{i+1}. {p}" for i, p in enumerate(perguntas))
        resultado["mensagem"] = f"Preciso das suas respostas para estas perguntas:\n\n{sugestoes}"

    return resultado


def _resolver_vaga(user_id: str, mensagem: str) -> dict | None:
    """Tenta resolver a vaga mencionada na mensagem."""
    try:
        neo4j = get_neo4j()
        # Busca ultima vaga visualizada ou busca por nome
        vaga = neo4j.get_ultima_vaga_visualizada(user_id)
        return vaga
    except Exception:
        return None


def _mostrar_pipeline(user_id: str) -> dict:
    """Mostra pipeline de candidaturas."""
    try:
        neo4j = get_neo4j()
        candidaturas = neo4j.get_candidaturas(user_id)
    except Exception:
        return {"response": "Nao consegui carregar suas candidaturas agora."}

    if not candidaturas:
        return {"response": "Nenhuma candidatura ainda. Use /vagas para buscar vagas!"}

    em_andamento = [c for c in candidaturas if c.get("status") in ("candidatado", "visualizado", "entrevista")]
    finalizadas = [c for c in candidaturas if c.get("status") in ("oferta", "recusado")]

    linhas = ["<b>Suas candidaturas:</b>\n"]
    status_emoji = {"candidatado": "🟡", "visualizado": "🔵", "entrevista": "🟢", "oferta": "✅", "recusado": "❌"}

    if em_andamento:
        linhas.append("<b>Em andamento:</b>")
        for c in em_andamento[:8]:
            emoji = status_emoji.get(c.get("status", ""), "⚪")
            linhas.append(f"{emoji} {c.get('titulo', '?')} — {c.get('empresa', '?')} ({c.get('data', '?')})")

    if finalizadas:
        linhas.append("\n<b>Finalizadas:</b>")
        for c in finalizadas[:5]:
            emoji = status_emoji.get(c.get("status", ""), "⚪")
            linhas.append(f"{emoji} {c.get('titulo', '?')} — {c.get('empresa', '?')}")

    return {"response": "\n".join(linhas)}
