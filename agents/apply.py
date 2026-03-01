"""
Agente de candidatura automatica — proativo, le vaga completa, gera curriculo ATS
personalizado e aplica via Playwright. Pede confirmacao antes de qualquer acao.
"""

import logging
import os
import re
import tempfile

from ai.openrouter import openrouter
from automation.browser import detectar_plataforma
from graph.neo4j_client import get_neo4j
import prompts.apply as apply_prompt

logger = logging.getLogger(__name__)

APPLY_DAILY_LIMIT = int(os.getenv("APPLY_DAILY_LIMIT", "10"))

_KEYWORDS_HISTORICO = ["minhas candidaturas", "candidaturas", "pipeline", "status candidatura"]
_KEYWORDS_CANDIDATAR = [
    "candidata", "se candidatar", "me inscrev", "aplica para", "aplica na",
    "quero me candidatar", "quero aplicar", "manda curriculo", "me candidato",
]


def apply_node(state: dict) -> dict:
    """No LangGraph do agente de candidatura."""
    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "").lower()

    if any(kw in mensagem for kw in _KEYWORDS_HISTORICO):
        return _mostrar_pipeline(user_id)

    return _iniciar_candidatura(state)


def _iniciar_candidatura(state: dict) -> dict:
    """
    Fluxo proativo:
    1. Busca vaga alvo (ultima visualizada ou mencionada)
    2. Lê a pagina da vaga para extrair requisitos completos
    3. Carrega perfil do usuario
    4. Calcula score de compatibilidade
    5. Gera curriculo ATS ja personalizado
    6. Mostra confirmacao com score e o que sera feito
    """
    import asyncio

    user_id = state.get("user_id", "")
    mensagem = state.get("raw_input", "")

    # Verifica limite diario
    try:
        neo4j = get_neo4j()
        count_hoje = neo4j.contar_candidaturas_hoje(user_id)
        if count_hoje >= APPLY_DAILY_LIMIT:
            return {"response": f"Voce ja se candidatou a {count_hoje} vagas hoje (limite: {APPLY_DAILY_LIMIT}). Volte amanha!"}
    except Exception:
        pass

    # Busca vaga
    vaga = _resolver_vaga(user_id, mensagem)
    if not vaga:
        return {
            "response": (
                "Nao encontrei qual vaga voce quer se candidatar. "
                "Usa /vagas para buscar vagas primeiro, depois me diz para qual quer se candidatar!"
            )
        }

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

    if not perfil.get("nome") and not perfil.get("habilidades"):
        return {
            "response": (
                "Seu perfil profissional esta vazio. "
                "Manda seu curriculo em PDF ou me conta sobre sua experiencia antes de se candidatar!"
            )
        }

    # Enriquece vaga com dados da pagina (async em thread)
    vaga_enriquecida = vaga.copy()
    try:
        loop = asyncio.new_event_loop()
        dados_pagina = loop.run_until_complete(_ler_pagina_vaga(vaga.get("url", "")))
        loop.close()
        if dados_pagina.get("descricao"):
            vaga_enriquecida["descricao"] = dados_pagina["descricao"]
        if dados_pagina.get("requisitos"):
            vaga_enriquecida["requisitos"] = dados_pagina["requisitos"]
        if dados_pagina.get("salario") and not vaga_enriquecida.get("salario"):
            vaga_enriquecida["salario"] = dados_pagina["salario"]
        if dados_pagina.get("modalidade") and not vaga_enriquecida.get("modalidade"):
            vaga_enriquecida["modalidade"] = dados_pagina["modalidade"]
    except Exception as e:
        logger.debug("apply: nao conseguiu ler pagina da vaga: %s", e)

    plataforma = detectar_plataforma(vaga_enriquecida.get("url", ""))

    # Score de compatibilidade
    from agents.jobs import calcular_score_match
    from data.jobs import Vaga
    vaga_obj = Vaga(
        id=vaga_enriquecida.get("id", ""),
        titulo=vaga_enriquecida.get("titulo", ""),
        empresa=vaga_enriquecida.get("empresa", ""),
        url=vaga_enriquecida.get("url", ""),
        fonte=vaga_enriquecida.get("fonte", ""),
        salario=vaga_enriquecida.get("salario", ""),
        localizacao=vaga_enriquecida.get("localizacao", ""),
        modalidade=vaga_enriquecida.get("modalidade", ""),
        descricao=vaga_enriquecida.get("descricao", ""),
        requisitos=vaga_enriquecida.get("requisitos", []),
    )
    score = calcular_score_match(perfil, vaga_obj)

    # Monta mensagem de confirmacao com score e detalhes
    confirmacao = _montar_confirmacao(vaga_enriquecida, perfil, plataforma, score)

    return {
        "response": confirmacao,
        "candidatura_pendente": {
            "vaga": vaga_enriquecida,
            "perfil": perfil,
            "plataforma": plataforma,
            "score": score,
        },
    }


async def _ler_pagina_vaga(url: str) -> dict:
    """
    Lê a pagina da vaga para extrair descricao completa, requisitos, salario e modalidade.
    Usa httpx puro (sem Playwright) para nao sobrecarregar.
    """
    if not url:
        return {}

    resultado = {"descricao": "", "requisitos": [], "salario": "", "modalidade": ""}

    try:
        import httpx
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "pt-BR,pt;q=0.9",
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                return resultado
            html = r.text
    except Exception as e:
        logger.debug("apply: erro ao buscar pagina vaga: %s", e)
        return resultado

    # Extrai texto puro
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Remove scripts e estilos
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        texto = soup.get_text(separator=" ", strip=True)
        resultado["descricao"] = texto[:4000]
    except Exception:
        resultado["descricao"] = re.sub(r"<[^>]+>", " ", html)[:4000]

    # Extrai requisitos (linhas com keywords tecnicas)
    requisitos = _extrair_requisitos_texto(resultado["descricao"])
    resultado["requisitos"] = requisitos

    # Detecta salario
    match_sal = re.search(r"R\$\s*[\d.,]+(?:\s*[-–]\s*R?\$?\s*[\d.,]+)?", resultado["descricao"])
    if match_sal:
        resultado["salario"] = match_sal.group(0).strip()

    # Detecta modalidade
    texto_lower = resultado["descricao"].lower()
    if "100% remoto" in texto_lower or "home office" in texto_lower:
        resultado["modalidade"] = "remoto"
    elif "hibrido" in texto_lower or "híbrido" in texto_lower:
        resultado["modalidade"] = "hibrido"
    elif "presencial" in texto_lower:
        resultado["modalidade"] = "presencial"

    return resultado


def _extrair_requisitos_texto(texto: str) -> list:
    """Extrai termos tecnicos do texto da vaga como lista de requisitos."""
    # Termos tecnicos comuns
    patterns = [
        r"\b(Python|Java|JavaScript|TypeScript|Node\.?js|React|Vue|Angular|Go|Golang|Rust|C\+\+|C#|\.NET|PHP|Ruby|Swift|Kotlin)\b",
        r"\b(FastAPI|Django|Flask|Spring|Laravel|Rails|Express|NestJS|NextJS|Nuxt)\b",
        r"\b(PostgreSQL|MySQL|MongoDB|Redis|Elasticsearch|DynamoDB|Cassandra|SQLite)\b",
        r"\b(Docker|Kubernetes|K8s|AWS|Azure|GCP|Terraform|Ansible|Jenkins|CI/CD)\b",
        r"\b(REST|GraphQL|gRPC|microservices|microsservicos|kafka|RabbitMQ)\b",
        r"\b(Machine Learning|ML|Deep Learning|NLP|LLM|PyTorch|TensorFlow|Pandas|NumPy)\b",
        r"\b(Git|GitHub|GitLab|Bitbucket|Jira|Scrum|Agile|Kanban)\b",
    ]
    requisitos = set()
    for pattern in patterns:
        matches = re.findall(pattern, texto, re.IGNORECASE)
        requisitos.update(m.strip() for m in matches if m.strip())
    return list(requisitos)[:20]


def _montar_confirmacao(vaga: dict, perfil: dict, plataforma: str, score: float) -> str:
    """Monta mensagem de confirmacao rica com score e o que sera feito."""
    score_pct = int(score * 100)
    score_emoji = "🟢" if score_pct >= 70 else "🟡" if score_pct >= 40 else "🔴"

    # Compara skills
    skills_perfil = {h.get("nome", "").lower() for h in perfil.get("habilidades", [])}
    requisitos = [r.lower() for r in vaga.get("requisitos", [])]
    matches = [r for r in requisitos if any(r in s or s in r for s in skills_perfil)]
    faltam = [r for r in requisitos if r not in matches][:5]

    plataforma_info = {
        "linkedin": "LinkedIn Easy Apply (automatico)",
        "gupy": "Gupy (automatico com login)",
        "greenhouse": "Greenhouse (automatico)",
        "lever": "Lever (automatico)",
        "desconhecido": "link externo (manual)",
    }.get(plataforma, plataforma)

    linhas = [
        f"<b>Candidatura para:</b>",
        f"<b>{vaga.get('titulo', '?')}</b> — {vaga.get('empresa', '?')}",
        f"📍 {vaga.get('localizacao', '?')} | {vaga.get('modalidade', '?')}",
    ]

    if vaga.get("salario") and vaga["salario"] != "A combinar":
        linhas.append(f"💰 {vaga['salario']}")

    linhas += [
        "",
        f"{score_emoji} <b>Compatibilidade: {score_pct}%</b>",
    ]

    if matches:
        linhas.append(f"✅ Skills que batem: {', '.join(matches[:6])}")
    if faltam:
        linhas.append(f"⚠️ Skills que faltam: {', '.join(faltam[:4])}")

    linhas += [
        "",
        f"🤖 <b>O que vou fazer:</b>",
        f"• Gerar curriculo ATS personalizado para esta vaga",
        f"• Aplicar via <b>{plataforma_info}</b>",
        f"• Responder perguntas do formulario com base no seu perfil",
        "",
        "Confirma com <b>sim</b> ou cancela com <b>nao</b>.",
    ]

    return "\n".join(linhas)


async def executar_candidatura(user_id: str, vaga: dict, perfil: dict, plataforma: str) -> dict:
    """
    Executa candidatura apos confirmacao do usuario.
    1. Gera curriculo ATS especifico para a vaga
    2. Aplica na plataforma com Playwright
    3. Registra no Neo4j
    """
    vaga_url = vaga.get("url", "")
    if not vaga_url:
        return {"sucesso": False, "mensagem": "URL da vaga nao encontrada."}

    # Gera curriculo ATS personalizado para esta vaga
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

        logger.info("apply: curriculo ATS gerado para vaga=%s (%d bytes)", vaga.get("titulo"), len(pdf_bytes))
    except Exception as e:
        logger.warning("apply: falha ao gerar curriculo ATS: %s — usando sem curriculo", e)

    # Aplica na plataforma
    resultado = None
    try:
        if plataforma == "linkedin":
            from automation.linkedin_apply import aplicar
            resultado = await aplicar(vaga_url, perfil, curriculo_path)
        elif plataforma == "gupy":
            from automation.gupy_apply import aplicar
            resultado = await aplicar(vaga_url, perfil, curriculo_path)
        elif plataforma in ("greenhouse", "lever"):
            resultado = await _aplicar_generico(vaga_url, perfil, curriculo_path, plataforma)
        else:
            resultado = {
                "sucesso": False,
                "motivo_falha": "plataforma_nao_suportada",
                "mensagem": (
                    f"Plataforma '{plataforma}' nao tem automacao ainda. "
                    f"Acesse manualmente: {vaga_url}\n\n"
                    f"Seu curriculo ATS foi gerado e ja esta otimizado para esta vaga!"
                ),
                "pdf_curriculo_disponivel": bool(curriculo_path),
            }
    except Exception as e:
        logger.error("apply: erro na automacao %s: %s", plataforma, e)
        resultado = {
            "sucesso": False,
            "motivo_falha": "erro_automacao",
            "mensagem": f"Erro tecnico na automacao. Candidate-se manualmente: {vaga_url}",
        }

    # Limpa arquivo temporario
    if curriculo_path:
        try:
            os.remove(curriculo_path)
        except Exception:
            pass

    # Registra no Neo4j
    status = "candidatado" if resultado.get("sucesso") else "tentativa_falhou"
    try:
        neo4j = get_neo4j()
        neo4j.registrar_candidatura(
            user_id=user_id,
            vaga_id=vaga.get("id", ""),
            plataforma=plataforma,
            status=status,
        )
    except Exception as e:
        logger.debug("apply: erro ao registrar candidatura: %s", e)

    logger.info("apply: candidatura user=%s vaga=%s plataforma=%s sucesso=%s",
                user_id, vaga.get("titulo"), plataforma, resultado.get("sucesso"))
    return resultado


async def _aplicar_generico(vaga_url: str, perfil: dict, curriculo_path: str, plataforma: str) -> dict:
    """Tentativa generica de candidatura para Greenhouse, Lever e similares."""
    from automation.browser import nova_pagina, clicar_qualquer, esperar_navegacao
    from automation.form_filler import responder_pergunta

    _BTN_APPLY = [
        'a:has-text("Apply")', 'a:has-text("Apply now")', 'button:has-text("Apply")',
        'a:has-text("Candidatar")', 'a[class*="apply"]',
    ]
    _BTN_SUBMIT = [
        'button[type="submit"]', 'input[type="submit"]',
        'button:has-text("Submit")', 'button:has-text("Send")',
    ]

    page = None
    try:
        page = await nova_pagina(stealth=True)
        await page.goto(vaga_url)
        await esperar_navegacao(page)

        # Clica em Apply
        await clicar_qualquer(page, _BTN_APPLY, timeout=10000)
        await esperar_navegacao(page)

        # Preenche nome/email basicos
        campos = {
            'input[name="first_name"], input[name="firstName"]': (perfil.get("nome") or "").split()[0] if perfil.get("nome") else "",
            'input[name="last_name"], input[name="lastName"]': " ".join((perfil.get("nome") or "").split()[1:]),
            'input[type="email"]': perfil.get("email", ""),
            'input[type="tel"]': perfil.get("telefone", ""),
        }
        for sel, val in campos.items():
            if val:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.fill(val)
                except Exception:
                    pass

        # Upload curriculo
        if curriculo_path:
            try:
                fi = await page.query_selector('input[type="file"]')
                if fi:
                    await fi.set_input_files(curriculo_path)
            except Exception:
                pass

        # Submit
        if await clicar_qualquer(page, _BTN_SUBMIT, timeout=5000):
            await esperar_navegacao(page)
            await page.close()
            return {"sucesso": True, "mensagem": f"Candidatura enviada via {plataforma}!"}

        await page.close()
        return {
            "sucesso": False,
            "motivo_falha": "submit_nao_encontrado",
            "mensagem": f"Nao consegui completar candidatura em {plataforma}. Acesse manualmente: {vaga_url}",
        }
    except Exception as e:
        if page:
            try:
                await page.close()
            except Exception:
                pass
        return {
            "sucesso": False,
            "motivo_falha": "erro_tecnico",
            "mensagem": f"Erro tecnico em {plataforma}. Acesse manualmente: {vaga_url}",
        }


def _resolver_vaga(user_id: str, mensagem: str) -> dict | None:
    """
    Resolve vaga alvo: busca URL na mensagem, ultima visualizada ou ultima buscada.
    """
    # URL direto na mensagem
    url_match = re.search(r"https?://\S+", mensagem)
    if url_match:
        url = url_match.group(0)
        return {
            "id": url,
            "titulo": "Vaga",
            "empresa": "",
            "url": url,
            "descricao": "",
            "requisitos": [],
        }

    try:
        neo4j = get_neo4j()
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
    finalizadas = [c for c in candidaturas if c.get("status") in ("oferta", "recusado", "tentativa_falhou")]

    linhas = ["<b>Suas candidaturas:</b>\n"]
    status_emoji = {
        "candidatado": "🟡", "visualizado": "🔵", "entrevista": "🟢",
        "oferta": "✅", "recusado": "❌", "tentativa_falhou": "⚠️",
    }

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
