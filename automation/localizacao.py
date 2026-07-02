"""
Filtro de candidatura por MODALIDADE (remoto/híbrido/presencial) + REGIÃO do Brasil.

Regra (pedido do usuário):
- O candidato marca no dashboard quais modalidades aceita e, para presencial/híbrido,
  as REGIÕES do Brasil onde toparia trabalhar/mudar (qualquer cidade daquela região).
- Vaga REMOTA → candidata se 'remoto' está aceito (sem restrição de local).
- Vaga PRESENCIAL/HÍBRIDA → candidata só se a modalidade está aceita E a cidade da
  vaga cai numa das regiões aceitas. Ex.: Joinville (SC → Sul) presencial/híbrida →
  candidata se 'Sul' está nas regiões.

Política FAIL-OPEN (igual ao resto do projeto): na dúvida, candidata. Se nada foi
configurado, ou a modalidade/região da vaga não é identificável, NÃO bloqueia.

Módulo de LÓGICA PURA (sem Selenium/DOM) — testável isoladamente.
"""

import re

# UF → região (IBGE).
UF_REGIAO = {
    "AC": "Norte", "AP": "Norte", "AM": "Norte", "PA": "Norte", "RO": "Norte",
    "RR": "Norte", "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste",
    "PB": "Nordeste", "PE": "Nordeste", "PI": "Nordeste", "RN": "Nordeste", "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MT": "Centro-Oeste", "MS": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}

# Nome do estado por extenso → UF (locations às vezes vêm sem a sigla).
ESTADO_UF = {
    "acre": "AC", "amapá": "AP", "amapa": "AP", "amazonas": "AM", "pará": "PA", "para": "PA",
    "rondônia": "RO", "rondonia": "RO", "roraima": "RR", "tocantins": "TO",
    "alagoas": "AL", "bahia": "BA", "ceará": "CE", "ceara": "CE", "maranhão": "MA",
    "maranhao": "MA", "paraíba": "PB", "paraiba": "PB", "pernambuco": "PE", "piauí": "PI",
    "piaui": "PI", "rio grande do norte": "RN", "sergipe": "SE",
    "distrito federal": "DF", "goiás": "GO", "goias": "GO", "mato grosso do sul": "MS",
    "mato grosso": "MT",
    "espírito santo": "ES", "espirito santo": "ES", "minas gerais": "MG",
    "rio de janeiro": "RJ", "são paulo": "SP", "sao paulo": "SP",
    "paraná": "PR", "parana": "PR", "rio grande do sul": "RS", "santa catarina": "SC",
}

# Cidades comuns em vagas → UF (capitais + grandes centros e o exemplo Joinville).
# Best-effort: quando a location traz só o nome da cidade, sem a sigla do estado.
CIDADE_UF = {
    "rio branco": "AC", "macapá": "AP", "macapa": "AP", "manaus": "AM", "belém": "PA",
    "belem": "PA", "porto velho": "RO", "boa vista": "RR", "palmas": "TO",
    "maceió": "AL", "maceio": "AL", "salvador": "BA", "fortaleza": "CE", "são luís": "MA",
    "sao luis": "MA", "joão pessoa": "PB", "joao pessoa": "PB", "recife": "PE",
    "teresina": "PI", "natal": "RN", "aracaju": "SE",
    "brasília": "DF", "brasilia": "DF", "goiânia": "GO", "goiania": "GO",
    "campo grande": "MS", "cuiabá": "MT", "cuiaba": "MT",
    "vitória": "ES", "vitoria": "ES", "belo horizonte": "MG", "uberlândia": "MG",
    "uberlandia": "MG", "rio de janeiro": "RJ", "são paulo": "SP", "sao paulo": "SP",
    "campinas": "SP", "santos": "SP", "são josé dos campos": "SP", "sao jose dos campos": "SP",
    "curitiba": "PR", "londrina": "PR", "maringá": "PR", "maringa": "PR",
    "porto alegre": "RS", "caxias do sul": "RS", "florianópolis": "SC", "florianopolis": "SC",
    "joinville": "SC", "blumenau": "SC", "são josé": "SC", "sao jose": "SC",
}

_MODALIDADES_VALIDAS = ("remoto", "hibrido", "presencial")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def modalidade_do_texto(texto: str) -> str:
    """Detecta a modalidade da vaga a partir do texto (modalidade/local/descrição).
    Ordem importa: 'híbrido' costuma citar remoto+presencial, então vem primeiro.
    Retorna 'remoto'|'hibrido'|'presencial'|'' (desconhecida)."""
    t = _norm(texto)
    if any(k in t for k in ("híbrid", "hibrid", "hybrid", "semipresencial", "semi-presencial")):
        return "hibrido"
    if any(k in t for k in ("remoto", "remote", "home office", "home-office", "trabalho remoto",
                            "100% remoto", "anywhere", "teletrabalho")):
        return "remoto"
    if any(k in t for k in ("presencial", "on-site", "on site", "onsite", "no local",
                            "no escritório", "no escritorio", "in office", "in-office")):
        return "presencial"
    return ""


def extrair_uf(texto: str) -> str:
    """Extrai a UF (sigla) do texto de localização. Tenta: sigla isolada (', SC' /
    '- SC' / '/SC' / '(SC)'), nome do estado por extenso, e por fim a cidade conhecida.
    Retorna '' se não achar."""
    if not texto:
        return ""
    bruto = texto
    t = _norm(texto)

    # 1) Sigla de UF isolada, cercada por vírgula/hífen/barra/parênteses/espaço/fim.
    for uf in UF_REGIAO:
        if re.search(rf"(?:^|[\s,\-/(]){uf}(?:[\s,\-/)\.]|$)", bruto, flags=re.IGNORECASE):
            return uf

    # 2) Nome do estado por extenso (casa o mais longo primeiro p/ não pegar
    #    'mato grosso' dentro de 'mato grosso do sul').
    for nome in sorted(ESTADO_UF, key=len, reverse=True):
        if nome in t:
            return ESTADO_UF[nome]

    # 3) Cidade conhecida (mesma ordenação por tamanho).
    for cidade in sorted(CIDADE_UF, key=len, reverse=True):
        if cidade in t:
            return CIDADE_UF[cidade]
    return ""


def regiao_do_texto(texto: str) -> str:
    """Região do Brasil da vaga (via UF) OU a própria região citada no texto.
    Retorna '' quando indeterminada."""
    uf = extrair_uf(texto)
    if uf:
        return UF_REGIAO.get(uf, "")
    # A vaga pode citar a região diretamente ('vaga na região Sul').
    t = _norm(texto)
    for reg in ("centro-oeste", "nordeste", "sudeste", "norte", "sul"):
        if reg in t:
            return reg.title() if reg != "centro-oeste" else "Centro-Oeste"
    return ""


def _norm_regioes(regioes) -> set:
    return {_norm(r).replace("centro oeste", "centro-oeste") for r in (regioes or []) if _norm(r)}


def vaga_aceita(texto_vaga: str, modalidades_aceitas, regioes) -> tuple:
    """Decide se deve candidatar-se, dado o texto da vaga e as prefs do candidato.

    Retorna (aceita: bool, motivo: str). FAIL-OPEN: sem config ou sem sinal claro,
    candidata (True)."""
    mods = {_norm(m) for m in (modalidades_aceitas or []) if _norm(m)}
    if not mods:
        return True, "sem filtro de modalidade configurado"

    mod = modalidade_do_texto(texto_vaga)
    if not mod:
        return True, "modalidade da vaga indefinida (fail-open)"
    if mod not in mods:
        return False, f"modalidade '{mod}' não aceita"
    if mod == "remoto":
        return True, "remoto aceito"

    # presencial/híbrido → depende da região da vaga.
    regs = _norm_regioes(regioes)
    if not regs:
        return True, f"{mod} aceito (sem região configurada, fail-open)"
    reg = regiao_do_texto(texto_vaga)
    if not reg:
        return True, f"{mod}: região da vaga indefinida (fail-open)"
    if _norm(reg) in regs:
        return True, f"{mod} na região {reg} (aceita)"
    return False, f"{mod} fora das regiões aceitas (vaga em {reg})"
