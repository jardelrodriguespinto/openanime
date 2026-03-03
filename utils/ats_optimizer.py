"""
Otimizador de curriculo para ATS — usa LLM para personalizar para vaga especifica.
"""

import json
import logging

from ai.openrouter import openrouter

logger = logging.getLogger(__name__)

SYSTEM_ATS = """Voce e um especialista senior em recrutamento tech e curriculos ATS com 15 anos de experiencia.
Seu objetivo e criar um curriculo que:
1. Passe nos filtros automaticos ATS com pontuacao maxima
2. Impressione o recrutador humano em 6 segundos de leitura
3. Mostre impacto real e quantificavel do candidato

=== REGRAS ATS OBRIGATORIAS ===
- Layout de uma coluna — nunca use colunas paralelas
- Keywords da vaga inseridas NATURALMENTE no texto (nao em lista solta)
- Densidade de keywords: mencione cada keyword importante 2-3x no documento
- Sem abreviacoes obscuras — escreva por extenso na primeira vez
- Datas no formato MM/AAAA

=== OBJETIVO PROFISSIONAL (3-4 linhas) ===
Formato: [Nivel] [Cargo] com X anos de experiencia em [area principal].
Especializado em [2-3 tecnologias/areas mais relevantes para a vaga].
Busco [contribuicao especifica] na [empresa/setor se conhecido].
Inclua 3-4 keywords da vaga de forma natural.

=== BULLETS DE EXPERIENCIA (metodo CAR) ===
Cada bullet = Contexto + Acao + Resultado
- Sempre comece com VERBO DE ACAO FORTE no passado: Desenvolvi, Implementei, Reduzi, Aumentei, Liderei, Otimizei, Automatizei, Arquitetei, Migrei, Integrei
- Quantifique SEMPRE que possivel: %, tempo, dinheiro, usuarios, deploys/dia
- Exemplo ruim: "Trabalhei com APIs REST"
- Exemplo bom: "Desenvolvi 12 endpoints REST com FastAPI reduzindo tempo de resposta em 40%"
- Exemplo bom: "Migrei monolito legado para 8 microservicos diminuindo custo de infra em R$8k/mes"
- Minimo 3, maximo 5 bullets por experiencia
- Priorize bullets que usam keywords da vaga

=== HABILIDADES ===
- Ordene por relevancia para a vaga especifica
- Agrupe: Linguagens | Frameworks | Infraestrutura | Dados | Ferramentas
- Inclua nivel entre parenteses apenas para principais: Python (avancado), React (intermediario)
- Maximo 18 habilidades

=== NUNCA FACA (REGRAS ABSOLUTAS) ===
- NUNCA invente experiencias, empresas, projetos, tecnologias ou conquistas que o candidato nao mencionou
- NUNCA escreva bullets para experiencias sem descricao — deixe a lista de bullets vazia nesses casos
- NUNCA infira ou suponha responsabilidades com base no cargo ou nivel — use apenas o que esta no campo descricao
- NUNCA coloque porcentagens, numeros, metricas ou resultados que nao vieram do perfil
- NUNCA coloque periodos de emprego sem dados reais

Retorne APENAS JSON valido sem markdown:
{
  "objetivo": "texto corrido de 3-4 linhas com keywords da vaga",
  "habilidades": ["Python (avancado)", "FastAPI", "PostgreSQL", "Docker", "..."],
  "experiencias": [
    {
      "empresa": "Nome da Empresa",
      "cargo": "Titulo exato do cargo",
      "periodo": "MM/AAAA - MM/AAAA",
      "bullets": [
        "Desenvolvi X usando Y resultando em Z",
        "Implementei A reduzindo B em C%",
        "Liderei equipe de N pessoas para entregar X"
      ]
    }
  ],
  "keywords_usadas": ["keyword1", "keyword2", "keyword3"]
}
"""


def _parse_json_safe(raw: str) -> dict:
    raw = (raw or "").strip()
    # Remove possivel markdown code block
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except Exception:
            pass
    return {}


def otimizar_para_vaga(perfil: dict, vaga_titulo: str, vaga_empresa: str,
                       vaga_descricao: str, vaga_requisitos: list[str]) -> dict:
    """
    Usa LLM para personalizar o curriculo para a vaga especifica.
    Retorna dict pronto para o template HTML.
    """
    # Envia perfil completo para o LLM ter contexto rico
    perfil_resumido = {
        "nivel_senioridade": perfil.get("nivel_senioridade", ""),
        "cargo_atual": perfil.get("cargo_atual", ""),
        "habilidades": [
            {"nome": h.get("nome", ""), "nivel": h.get("nivel", 0), "anos": h.get("anos_exp", 0)}
            for h in perfil.get("habilidades", [])[:20]
        ],
        "experiencias": perfil.get("experiencias", [])[:5],
        "formacao": perfil.get("formacao", []),
        "idiomas": perfil.get("idiomas", []),
        "pretensao_salarial": perfil.get("pretensao_salarial", ""),
    }

    prompt_user = f"""PERFIL DO CANDIDATO:
{json.dumps(perfil_resumido, ensure_ascii=False, indent=2)}

VAGA ALVO:
Titulo: {vaga_titulo}
Empresa: {vaga_empresa or 'nao informada'}
Requisitos: {', '.join(vaga_requisitos[:20]) if vaga_requisitos else 'nao informados'}
Descricao: {vaga_descricao[:3000] if vaga_descricao else 'nao informada'}

INSTRUCAO: Crie um curriculo ATS otimizado para esta vaga especifica.
Se a descricao da vaga for escassa, use o titulo e requisitos para identificar keywords relevantes.
CRITICO: Use APENAS os dados que existem no perfil. Para bullets de cada experiencia, reescreva o conteudo do campo "descricao" com formato CAR — nao invente informacoes que nao estejam la.
Se uma experiencia nao tiver campo "descricao" preenchido, coloque apenas cargo + empresa + periodo com lista de bullets VAZIA.
NUNCA crie bullets, projetos, numeros ou conquistas que o candidato nao mencionou."""

    messages = [
        {"role": "system", "content": SYSTEM_ATS},
        {"role": "user", "content": prompt_user},
    ]

    try:
        raw = openrouter.converse(messages)
        otimizado = _parse_json_safe(raw)
    except Exception as e:
        logger.error("ats_optimizer: erro LLM: %s", e)
        otimizado = {}

    # Monta contexto completo para o template
    resultado = {
        "nome": perfil.get("nome", ""),
        "email": perfil.get("email", ""),
        "telefone": perfil.get("telefone", ""),
        "linkedin": perfil.get("linkedin", ""),
        "github": perfil.get("github", ""),
        "portfolio": perfil.get("portfolio", ""),
        "localizacao": perfil.get("localizacao", ""),
        "cargo_atual": perfil.get("cargo_atual", ""),
        "objetivo": otimizado.get("objetivo") or _gerar_objetivo_fallback(perfil, vaga_titulo),
        "habilidades": otimizado.get("habilidades") or _habilidades_fallback(perfil),
        "experiencias": _formatar_experiencias(otimizado.get("experiencias", []), perfil),
        "formacao": _formatar_formacao(perfil.get("formacao", [])),
        "idiomas": perfil.get("idiomas", []),
    }

    logger.info("ats_optimizer: curriculo otimizado para vaga=%s keywords=%d",
                vaga_titulo, len(otimizado.get("keywords_usadas", [])))
    return resultado


def _gerar_objetivo_fallback(perfil: dict, vaga_titulo: str) -> str:
    nivel = perfil.get("nivel_senioridade", "").capitalize()
    cargo = perfil.get("cargo_atual", "") or vaga_titulo
    skills = [h.get("nome", "") for h in perfil.get("habilidades", [])[:3] if h.get("nome")]
    skills_str = ", ".join(skills) if skills else "desenvolvimento de software"
    return f"{nivel} {cargo} com experiencia em {skills_str}. Busco contribuir com solucoes de alto impacto alinhadas a vaga de {vaga_titulo}.".strip()


def _habilidades_fallback(perfil: dict) -> list:
    return [h.get("nome", "") for h in perfil.get("habilidades", [])[:15] if h.get("nome")]


def _formatar_experiencias(experiencias_otimizadas: list, perfil: dict) -> list:
    """Usa experiencias otimizadas pelo LLM ou fallback para as do perfil."""
    if experiencias_otimizadas:
        return experiencias_otimizadas

    # Fallback: converte experiencias brutas do perfil
    resultado = []
    for exp in perfil.get("experiencias", [])[:4]:
        descricao = exp.get("descricao", "")
        bullets = []
        if descricao:
            partes = [p.strip() for p in descricao.replace(";", ".").split(".") if len(p.strip()) > 20]
            bullets = partes[:4] if partes else [descricao]
        resultado.append({
            "empresa": exp.get("empresa", ""),
            "cargo": exp.get("cargo", ""),
            "periodo": f"{exp.get('inicio', '')} - {exp.get('fim', 'Atual')}",
            "bullets": bullets,
        })
    return resultado


def _formatar_formacao(formacao_list: list) -> list:
    resultado = []
    for f in formacao_list[:3]:
        resultado.append({
            "curso": f.get("curso", ""),
            "instituicao": f.get("instituicao", ""),
            "nivel": f.get("nivel", ""),
            "ano": f.get("ano", ""),
        })
    return resultado
