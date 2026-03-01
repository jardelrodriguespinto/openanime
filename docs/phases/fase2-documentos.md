# Fase 2 — Agente de Documentos (PDF)

## Objetivo
Receber PDFs pelo Telegram, processar com LLM (resumo, Q&A, extração
de dados), armazenar no Weaviate para busca futura, e gerar PDFs
(relatórios, templates, currículos base). Esta fase é a fundação para
a Fase 4 (currículos ATS).

---

## Novos Arquivos

```
data/pdf_reader.py        → extração de texto de PDFs com pdfplumber
utils/pdf_writer.py       → geração de PDFs via HTML+CSS → weasyprint
utils/templates/          → templates HTML para PDFs gerados
  base.html               → layout base
  report.html             → relatório de análise de PDF
  resume_base.html        → currículo base (usado na Fase 4)
agents/documents.py       → agente LangGraph de documentos
prompts/documents.py      → prompts do agente
```

### Arquivos modificados

```
agents/orchestrator.py    → adicionar intent "documento"
bot/handlers.py           → handler para DocumentMessage (PDF recebido)
graph/weaviate_client.py  → novo método: upsert_documento
graph/neo4j_client.py     → registrar documentos do usuário
.env.example              → MODEL_DOCUMENTS
```

---

## Fluxo Completo — Receber PDF

```
Usuário envia PDF pelo Telegram
    ↓
handlers.py captura (document handler, mime_type: application/pdf)
    ↓
bot baixa o arquivo para /tmp/
    ↓
data/pdf_reader.py extrai texto página por página
    ↓
LangGraph state: intent = "documento", pdf_content = texto extraído
    ↓
agents/documents.py processa
    ↓
┌─ resumo automático (sempre)
├─ extração de entidades (nomes, datas, valores, empresas)
└─ armazena no Weaviate (embedding do texto)
    ↓
bot responde: resumo + pergunta "o que quer saber sobre este documento?"
    ↓
usuário faz perguntas → Q&A via Weaviate semantic search + LLM
```

---

## Fluxo Completo — Gerar PDF

```
Usuário pede "gera um PDF com o resumo daquele documento"
    ↓
agents/documents.py monta conteúdo via LLM
    ↓
utils/pdf_writer.py: conteúdo + template HTML → weasyprint → PDF bytes
    ↓
bot envia o PDF como documento no Telegram
```

---

## data/pdf_reader.py

### Interface pública

```python
async def extrair_texto(caminho: str) -> dict:
    """
    Extrai texto de PDF página por página.
    Retorna:
    {
        "paginas": int,
        "texto_completo": str,
        "texto_por_pagina": list[str],
        "metadados": {titulo, autor, data_criacao},
        "tamanho_chars": int
    }
    """

async def extrair_tabelas(caminho: str) -> list[dict]:
    """
    Extrai tabelas estruturadas do PDF.
    Retorna lista de tabelas como list[list[str]].
    """

def truncar_para_contexto(texto: str, max_chars: int = 12000) -> str:
    """
    Se o PDF for muito longo, pega início + fim + partes relevantes.
    Evita estourar contexto do LLM.
    """
```

### Limitações tratadas

```
PDF com imagens apenas (scan):    → avisa usuário, sugere OCR futuro
PDF protegido com senha:          → pede senha ou avisa impossibilidade
PDF muito grande (>50 páginas):   → processa em chunks, resumo por seção
Encoding quebrado:                → fallback para extração básica
```

---

## utils/pdf_writer.py

### Interface pública

```python
async def gerar_pdf(
    template: str,           # "report" | "resume_base" | "custom"
    conteudo: dict,          # dados para preencher o template
    nome_arquivo: str        # nome do arquivo de saída
) -> bytes:
    """
    Gera PDF a partir de template HTML + dados.
    Retorna bytes do PDF para enviar pelo Telegram.
    """
```

### Por que weasyprint?

- Escreve HTML+CSS normal → vira PDF profissional
- Templates fáceis de customizar sem código Python
- Suporta fontes, cores, margens, headers/footers
- ATS-friendly: gera PDF com texto selecionável (não imagem)

### Estrutura dos templates

```
utils/templates/
  base.html          → CSS base, fontes, margens padrão
  report.html        → seções: título, resumo, pontos-chave, entidades
  resume_base.html   → currículo: cabeçalho, experiência, formação, skills
                       (layout de coluna única — ATS-friendly)
```

---

## agents/documents.py

### Responsabilidades

1. **Análise automática** ao receber PDF:
   - Detecta tipo do documento (contrato, currículo, artigo, relatório, nota fiscal)
   - Gera resumo executivo
   - Extrai entidades relevantes conforme o tipo
   - Armazena no Weaviate + registra no Neo4j

2. **Q&A sobre documento armazenado:**
   - Usuário pergunta sobre documento anterior
   - Busca semântica no Weaviate pelo conteúdo
   - LLM responde com base no trecho relevante

3. **Geração de PDF:**
   - Recebe instrução para gerar documento
   - Monta conteúdo via LLM
   - Chama `pdf_writer.py` e envia arquivo

### Tipos de documento detectados e comportamento

```
CURRÍCULO:
  → extrai: nome, skills, experiências, formação, contato
  → salva no Neo4j como perfil profissional base
  → pergunta: "posso usar este currículo como seu perfil profissional?"
  → se sim: alimenta Fase 3 automaticamente

CONTRATO/DOCUMENTO JURÍDICO:
  → extrai: partes, obrigações, prazos, valores, cláusulas críticas
  → destaca pontos de atenção

ARTIGO/PAPER:
  → extrai: título, autores, abstract, conclusões, metodologia
  → gera resumo técnico

NOTA FISCAL/FINANCEIRO:
  → extrai: fornecedor, valor, data, itens, impostos
  → formato estruturado

GENÉRICO:
  → resumo geral + tópicos principais
```

---

## Expansão Weaviate — Classe Document

```python
# nova classe no schema Weaviate
{
    "class": "Document",
    "properties": [
        {"name": "user_id",      "dataType": ["text"]},   # skip vectorize
        {"name": "nome",         "dataType": ["text"]},   # skip vectorize
        {"name": "tipo",         "dataType": ["text"]},   # skip vectorize
        {"name": "conteudo",     "dataType": ["text"]},   # ← embedding aqui
        {"name": "resumo",       "dataType": ["text"]},   # skip vectorize
        {"name": "data_upload",  "dataType": ["text"]},   # skip vectorize
    ]
}
```

---

## Expansão Neo4j — Documentos do Usuário

```cypher
// novo nó
(:Documento {
    id,
    nome,
    tipo,
    data_upload,
    weaviate_id    // referência para busca semântica
})

// relação
(Usuario)-[:ENVIOU]->(Documento)
```

---

## handlers.py — Handler de PDF

```python
# lógica a adicionar
async def handle_document(update, context):
    doc = update.message.document
    if doc.mime_type != "application/pdf":
        await update.message.reply_text("Por enquanto só aceito PDFs.")
        return

    # baixar arquivo
    file = await doc.get_file()
    caminho = f"/tmp/{doc.file_unique_id}.pdf"
    await file.download_to_drive(caminho)

    # processar via LangGraph com intent = "documento"
    await processar_mensagem(update, context, override_intent="documento",
                              pdf_path=caminho)
```

---

## prompts/documents.py

### Prompt de análise

```
Você recebeu um documento PDF. Analise o conteúdo e:

1. Identifique o tipo do documento
2. Gere um resumo executivo em 3-5 linhas
3. Extraia as informações mais importantes conforme o tipo
4. Destaque qualquer ponto que mereça atenção especial

Seja direto e útil. Tom casual mas preciso.
Ao final, pergunte se o usuário quer saber algo específico sobre o documento.
```

### Prompt de Q&A

```
Com base no trecho do documento abaixo, responda a pergunta do usuário.
Se a informação não estiver no trecho, diga claramente que não encontrou.
Não invente informações.

Trecho: {contexto_weaviate}
Pergunta: {pergunta_usuario}
```

---

## Variáveis de Ambiente

```env
# Modelo para documentos (análise densa — use modelo bom)
MODEL_DOCUMENTS=anthropic/claude-sonnet-4-6

# Tamanho máximo de PDF aceito em MB
PDF_MAX_SIZE_MB=20

# Máximo de páginas para processar completo (acima disso: chunking)
PDF_MAX_PAGES_FULL=30
```

---

## Dependências Novas

```
pdfplumber==0.11.0     → extração de texto e tabelas de PDF
weasyprint==62.3       → geração de PDF via HTML+CSS
```

---

## Ordem de Implementação Interna

```
1. data/pdf_reader.py          → extração (testável isoladamente)
2. utils/pdf_writer.py         → geração + templates HTML base
3. prompts/documents.py        → prompts
4. agents/documents.py         → agente completo
5. graph/weaviate_client.py    → upsert_documento
6. graph/neo4j_client.py       → registrar documento
7. agents/orchestrator.py      → intent "documento"
8. bot/handlers.py             → handler de PDF
9. .env.example                → novas variáveis
```

---

## Critérios de Conclusão

- [ ] Enviar PDF pelo Telegram retorna resumo automático
- [ ] Q&A sobre documento enviado funciona sem novo envio
- [ ] Currículo PDF enviado popula perfil profissional no Neo4j
- [ ] `"gera um PDF do resumo"` retorna arquivo baixável
- [ ] Documentos grandes (>30 páginas) são processados sem travar
- [ ] Arquivo com senha retorna mensagem clara de erro
- [ ] Weaviate armazena documento com embedding para busca futura
