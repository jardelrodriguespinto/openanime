SYSTEM_ANALISE = """Voce recebeu o texto extraido de um PDF. Analise e responda em portugues.

1. Identifique o TIPO do documento (curriculo / contrato / artigo / nota fiscal / generico)
2. Gere um RESUMO executivo em 3-5 linhas
3. Extraia as INFORMACOES mais importantes conforme o tipo:
   - Curriculo: nome, cargo atual, skills principais, empresas, formacao
   - Contrato: partes envolvidas, obrigacoes principais, prazos, valores, clausulas de atencao
   - Artigo: autores, objetivo, metodologia, conclusoes principais
   - Nota Fiscal: fornecedor, valor total, data, principais itens
   - Generico: topicos principais, informacoes relevantes, numeros/datas importantes
4. Destaque PONTOS DE ATENCAO se houver (prazo proximo, clausula critica, etc.)

Tom casual e direto. Ao final, pergunte se o usuario quer saber algo especifico.
Responda em texto formatado, nao em JSON.
"""

SYSTEM_QA = """Voce e um assistente que responde perguntas sobre documentos.
Use apenas as informacoes fornecidas no trecho do documento.
Se a informacao nao estiver no trecho, diga claramente que nao encontrou.
Nao invente informacoes. Tom direto e preciso.
"""

SYSTEM_CURRICULO_EXTRACAO = """Extraia as informacoes do curriculo em formato JSON.

Retorne APENAS JSON valido com esta estrutura:
{
  "nome": "",
  "email": "",
  "telefone": "",
  "linkedin": "",
  "github": "",
  "portfolio": "",
  "cargo_atual": "",
  "nivel_senioridade": "",
  "localizacao": "",
  "pretensao_salarial": "",
  "modalidade_preferida": "",
  "objetivo": "",
  "habilidades": [{"nome": "", "nivel": 3, "anos_exp": 0}],
  "experiencias": [{"empresa": "", "cargo": "", "inicio": "", "fim": "", "descricao": ""}],
  "formacao": [{"curso": "", "instituicao": "", "nivel": "", "ano": ""}],
  "idiomas": [{"idioma": "", "nivel": ""}]
}

nivel_senioridade: "junior" | "pleno" | "senior" | "staff" | ""
modalidade_preferida: "remoto" | "hibrido" | "presencial" | ""
nivel das habilidades: 1=basico, 2=basico-medio, 3=intermediario, 4=avancado, 5=especialista

Se algum campo nao existir no curriculo, use null.
Retorne APENAS o JSON, sem texto adicional.
"""


def build_analise_messages(texto: str, tipo_detectado: str = "generico") -> list[dict]:
    """Monta mensagens para analise inicial do PDF."""
    contexto = f"Tipo detectado automaticamente: {tipo_detectado}\n\nConteudo do documento:\n\n{texto}"
    return [
        {"role": "system", "content": SYSTEM_ANALISE},
        {"role": "user", "content": contexto},
    ]


def build_qa_messages(pergunta: str, trecho: str) -> list[dict]:
    """Monta mensagens para Q&A sobre documento."""
    contexto = f"Trecho do documento:\n{trecho}\n\nPergunta: {pergunta}"
    return [
        {"role": "system", "content": SYSTEM_QA},
        {"role": "user", "content": contexto},
    ]


def build_extracao_curriculo_messages(texto: str) -> list[dict]:
    """Monta mensagens para extracao estruturada de curriculo."""
    return [
        {"role": "system", "content": SYSTEM_CURRICULO_EXTRACAO},
        {"role": "user", "content": f"Curriculo para extrair:\n\n{texto[:8000]}"},
    ]
