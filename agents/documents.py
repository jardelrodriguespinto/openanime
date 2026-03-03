"""
Agente de documentos — processa PDFs recebidos e gera PDFs.
"""

import asyncio
import logging
import os

from ai.openrouter import openrouter
from data.pdf_reader import detectar_tipo_documento, extrair_texto, truncar_para_contexto
from graph.neo4j_client import get_neo4j
from graph.weaviate_client import get_weaviate
import prompts.documents as doc_prompt
from utils.curriculo_parser import extrair_perfil_curriculo_local

logger = logging.getLogger(__name__)

def documents_node(state: dict) -> dict:
    """No LangGraph do agente de documentos."""
    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "")
    pdf_path = state.get("pdf_path", "")
    pdf_doc_id = state.get("pdf_doc_id", "")

    # Modo Q&A: usuario pergunta sobre documento ja armazenado
    if not pdf_path and not pdf_doc_id:
        return _modo_qa(state)

    # Modo analise: novo PDF recebido
    if pdf_path:
        return _modo_analise(state, pdf_path)

    return {"response": "Manda o PDF que voce quer que eu analise!"}


def _modo_analise(state: dict, pdf_path: str) -> dict:
    """Analisa PDF recebido."""
    user_id = state.get("user_id", "")

    dados = extrair_texto(pdf_path)
    if dados.get("erro"):
        return {"response": f"Nao consegui ler esse PDF: {dados['erro']}. Tenta outro arquivo!"}

    texto = dados["texto_completo"]
    if not texto.strip():
        return {"response": "Esse PDF parece estar vazio ou so tem imagens. Nao consigo extrair texto de PDFs escaneados ainda."}

    tipo = detectar_tipo_documento(texto)
    texto_llm = truncar_para_contexto(texto, max_chars=10000)
    if tipo == "curriculo":
        perfil_local = extrair_perfil_curriculo_local(texto_llm)
        analise = _resumo_curriculo_local(perfil_local, dados.get("paginas", 0))
    else:
        messages = doc_prompt.build_analise_messages(texto_llm, tipo)
        try:
            analise = openrouter.converse(messages)
        except Exception as e:
            logger.error("documents: erro LLM analise: %s", e)
            analise = f"Documento de {dados['paginas']} pagina(s) recebido. Tipo detectado: {tipo}. Nao consegui gerar analise detalhada agora."

    # Armazena no Weaviate para buscas futuras
    nome_arquivo = os.path.basename(pdf_path).replace(".pdf", "")
    doc_id = _armazenar_documento(user_id, nome_arquivo, tipo, texto, analise)

    # Se for curriculo, extrai e salva perfil profissional
    if tipo == "curriculo":
        asyncio.create_task(_extrair_curriculo_background(user_id, texto))
        analise += "\n\nDetectei que isso e um curriculo! Estou extraindo suas informacoes profissionais para melhorar suas recomendacoes de vagas."

    # Limpa arquivo temporario
    try:
        os.remove(pdf_path)
    except Exception:
        pass

    return {"response": analise, "pdf_doc_id": doc_id}


def _resumo_curriculo_local(perfil: dict, paginas: int) -> str:
    """Resumo local de curriculo sem LLM."""
    nome = perfil.get("nome") or "Nome nao identificado"
    cargo = perfil.get("cargo_atual") or "Cargo nao identificado"
    habs = [h.get("nome", "") for h in perfil.get("habilidades", []) if h.get("nome")]
    exps = perfil.get("experiencias", []) or []
    forms = perfil.get("formacao", []) or []

    linhas = [
        f"Curriculo recebido ({paginas} pagina(s)).",
        f"Nome: {nome}",
        f"Cargo atual: {cargo}",
        f"Habilidades detectadas: {', '.join(habs[:8]) if habs else 'nenhuma'}",
        f"Experiencias detectadas: {len(exps)}",
        f"Formacoes detectadas: {len(forms)}",
    ]
    return "\n".join(linhas)


def _modo_qa(state: dict) -> dict:
    """Responde perguntas sobre documentos ja armazenados."""
    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "")

    # Busca contexto relevante no Weaviate
    try:
        weaviate = get_weaviate()
        resultados = weaviate.busca_documento(user_id, mensagem, limit=3)
        if resultados:
            trechos = "\n\n---\n\n".join(
                r.get("conteudo", "")[:2000] for r in resultados if r.get("conteudo")
            )
            messages = doc_prompt.build_qa_messages(mensagem, trechos)
            response = openrouter.converse(messages)
        else:
            response = "Nao encontrei nenhum documento seu relacionado a essa pergunta. Manda o PDF que quero analisar!"
    except Exception as e:
        logger.error("documents: erro Q&A: %s", e)
        response = "Nao consegui buscar nos seus documentos agora. Tenta de novo!"

    return {"response": response}


def _armazenar_documento(user_id: str, nome: str, tipo: str, texto: str, resumo: str) -> str:
    """Armazena documento no Weaviate e Neo4j. Retorna doc_id."""
    doc_id = f"doc_{user_id}_{hash(texto[:100]) % 100000}"
    try:
        weaviate = get_weaviate()
        weaviate.upsert_documento({
            "user_id": user_id,
            "doc_id": doc_id,
            "nome": nome,
            "tipo": tipo,
            "conteudo": texto[:5000],
            "resumo": resumo[:1000],
        })
    except Exception as e:
        logger.warning("documents: erro ao armazenar Weaviate: %s", e)

    try:
        neo4j = get_neo4j()
        neo4j.registrar_documento(user_id, doc_id, nome, tipo)
    except Exception as e:
        logger.warning("documents: erro ao registrar Neo4j: %s", e)

    return doc_id


async def _extrair_curriculo_background(user_id: str, texto: str) -> None:
    """Extrai perfil profissional do curriculo em background."""
    try:
        dados = await asyncio.to_thread(extrair_perfil_curriculo_local, texto)
        if dados and (
            dados.get("nome")
            or dados.get("email")
            or dados.get("habilidades")
            or dados.get("experiencias")
            or dados.get("formacao")
        ):
            neo4j = get_neo4j()
            neo4j.salvar_perfil_profissional(user_id, dados)
            logger.info(
                "documents: perfil profissional extraido localmente user=%s habs=%d exp=%d",
                user_id,
                len(dados.get("habilidades", [])),
                len(dados.get("experiencias", [])),
            )
        else:
            logger.info("documents: extracao local de curriculo sem dados relevantes user=%s", user_id)
    except Exception as e:
        logger.debug("documents: extracao curriculo background erro: %s", e)
