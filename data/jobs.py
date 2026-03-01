"""
Busca de vagas em multiplas fontes — 100% gratuito, sem APIs pagas.
Fontes: Indeed (HTML), Gupy API, RemoteOK API, Trampos.co, programathor, LinkedIn.
Suporta multi-query para expandir cobertura com variantes PT/EN e skills.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import quote_plus

import feedparser
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

JOBS_LIMITE_POR_FONTE = int(__import__("os").getenv("JOBS_LIMITE_POR_FONTE", "10"))
JOBS_LIMITE_TOTAL = int(__import__("os").getenv("JOBS_LIMITE_TOTAL", "30"))

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

# Mapa de equivalências PT→EN para buscas internacionais
_PT_EN = {
    "desenvolvedor": "developer",
    "engenheiro de software": "software engineer",
    "engenheiro": "engineer",
    "analista": "analyst",
    "programador": "programmer",
    "cientista de dados": "data scientist",
    "dados": "data",
    "frontend": "frontend",
    "front-end": "frontend",
    "backend": "backend",
    "back-end": "backend",
    "fullstack": "fullstack",
    "full stack": "fullstack",
    "infraestrutura": "infrastructure",
    "segurança": "security",
    "devops": "devops",
    "mobile": "mobile",
    "embarcado": "embedded",
    "machine learning": "machine learning",
    "inteligencia artificial": "artificial intelligence",
}


@dataclass
class Vaga:
    id: str
    titulo: str
    empresa: str
    localizacao: str
    modalidade: str  # remoto | hibrido | presencial | ""
    salario: str
    descricao: str
    requisitos: list[str] = field(default_factory=list)
    url: str = ""
    fonte: str = ""
    data_publicacao: str = ""
    score_match: float = 0.0


def _normalizar_modalidade(texto: str) -> str:
    t = (texto or "").lower()
    if "remot" in t or "remote" in t:
        return "remoto"
    if "hibrid" in t or "hybrid" in t:
        return "hibrido"
    if "presencial" in t or "on-site" in t or "onsite" in t:
        return "presencial"
    return ""


def _extrair_requisitos(descricao: str) -> list[str]:
    techs = re.findall(
        r"\b(Python|Java(?:Script)?|TypeScript|React|Node\.js|Vue|Angular|"
        r"Django|FastAPI|Flask|Spring|AWS|Azure|GCP|Docker|Kubernetes|SQL|NoSQL|"
        r"PostgreSQL|MySQL|MongoDB|Redis|Git|Linux|REST|GraphQL|CI/CD|"
        r"Machine Learning|Data Science|TensorFlow|PyTorch|Pandas|Spark|"
        r"PHP|Laravel|Go|Golang|Rust|Swift|Kotlin|\.NET|C#|C\+\+|"
        r"n8n|Airflow|Terraform|Ansible|Kafka|RabbitMQ)\b",
        descricao,
        re.IGNORECASE,
    )
    return list(dict.fromkeys(t.lower() for t in techs))[:15]


def gerar_variantes(query: str, skills_extras: list[str] | None = None) -> list[str]:
    """
    Gera variantes de busca:
    - termo original
    - versao EN (se tiver mapeamento PT→EN)
    - combinacoes com top skills
    """
    q = query.strip()
    variantes = [q]

    q_lower = q.lower()
    for pt, en in _PT_EN.items():
        if pt in q_lower and en not in q_lower:
            variante_en = re.sub(pt, en, q_lower, flags=re.IGNORECASE).strip()
            if variante_en and variante_en != q_lower:
                variantes.append(variante_en)
            break  # uma substituicao por vez

    # Adiciona variantes com skills do perfil
    for skill in (skills_extras or [])[:2]:
        skill = skill.strip()
        if skill and skill.lower() not in q_lower:
            variantes.append(f"{q} {skill}")

    # Remove duplicatas mantendo ordem
    seen: set[str] = set()
    result: list[str] = []
    for v in variantes:
        vl = v.lower()
        if vl not in seen:
            seen.add(vl)
            result.append(v)

    return result[:4]


# ─── Fonte 1: Indeed (scraping HTML) ─────────────────────────────────────────

def _buscar_indeed(query: str, localizacao: str = "", limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas via scraping da pagina publica do Indeed Brasil."""
    vagas: list[Vaga] = []
    urls_tentar = [
        f"https://br.indeed.com/empregos?q={quote_plus(query)}&l={quote_plus(localizacao)}&sort=date",
        f"https://www.indeed.com/jobs?q={quote_plus(query)}+jobs&l=Brazil&sort=date",
    ]
    for url in urls_tentar:
        try:
            with httpx.Client(timeout=15, headers=_HEADERS, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    logger.debug("indeed: status %d para %s", resp.status_code, url)
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("div", class_=re.compile(r"job_seen_beacon|resultContent|tapItem"))
                if not cards:
                    cards = soup.find_all("div", attrs={"data-testid": re.compile(r"slider_item|job")})

                for card in cards[:limite]:
                    titulo_el = card.find(["h2", "h3"], class_=re.compile(r"jobTitle|title"))
                    empresa_el = card.find(class_=re.compile(r"companyName|company"))
                    local_el = card.find(class_=re.compile(r"companyLocation|location"))
                    link_el = card.find("a", href=True)

                    titulo = titulo_el.get_text(strip=True) if titulo_el else ""
                    empresa = empresa_el.get_text(strip=True) if empresa_el else ""
                    local = local_el.get_text(strip=True) if local_el else localizacao
                    link = ""
                    if link_el:
                        href = link_el.get("href", "")
                        if href.startswith("/"):
                            link = f"https://br.indeed.com{href.split('?')[0]}"
                        elif href.startswith("http"):
                            link = href.split("?")[0]

                    if not titulo:
                        continue

                    vagas.append(Vaga(
                        id=f"indeed_{hash(link or titulo) % 1000000}",
                        titulo=titulo,
                        empresa=empresa,
                        localizacao=local,
                        modalidade=_normalizar_modalidade(local),
                        salario="A combinar",
                        descricao="",
                        url=link,
                        fonte="Indeed",
                    ))

            if vagas:
                logger.info("indeed: %d vagas para '%s'", len(vagas), query)
                return vagas

        except Exception as e:
            logger.debug("indeed: erro com %s: %s", url, e)

    # Fallback: RSS
    try:
        rss_url = f"https://br.indeed.com/rss?q={quote_plus(query)}&l={quote_plus(localizacao)}&sort=date"
        feed = feedparser.parse(rss_url)
        for entry in feed.entries[:limite]:
            titulo = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            resumo = entry.get("summary", "").strip()
            if resumo:
                try:
                    resumo = BeautifulSoup(resumo, "html.parser").get_text(separator=" ", strip=True)[:500]
                except Exception:
                    pass
            empresa = ""
            if " - " in titulo:
                partes = titulo.rsplit(" - ", 1)
                titulo, empresa = partes[0].strip(), partes[1].strip()
            if titulo:
                vagas.append(Vaga(
                    id=f"indeed_rss_{hash(link) % 1000000}",
                    titulo=titulo, empresa=empresa,
                    localizacao=localizacao or "Brasil",
                    modalidade=_normalizar_modalidade(resumo),
                    salario="A combinar", descricao=resumo[:500],
                    requisitos=_extrair_requisitos(resumo),
                    url=link, fonte="Indeed",
                    data_publicacao=entry.get("published", ""),
                ))
        if vagas:
            logger.info("indeed rss: %d vagas para '%s'", len(vagas), query)
    except Exception as e:
        logger.debug("indeed rss: erro: %s", e)

    return vagas


# ─── Fonte 2: Gupy API ────────────────────────────────────────────────────────

def _buscar_gupy(query: str, localizacao: str = "", limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas via API publica da Gupy (varios endpoints em cascata)."""
    vagas: list[Vaga] = []

    endpoints = [
        ("GET", f"https://portal.api.gupy.io/api/job?jobName={quote_plus(query)}&limit={limite}&offset=0"),
        ("GET", f"https://portal.api.gupy.io/api/v1/jobs?name={quote_plus(query)}&limit={limite}"),
        ("GET", f"https://portal.api.gupy.io/api/v2/jobs?name={quote_plus(query)}&limit={limite}"),
    ]
    if localizacao:
        endpoints = [(m, u + f"&cityName={quote_plus(localizacao)}") for m, u in endpoints]

    headers_gupy = {**_HEADERS, "Accept": "application/json"}

    for _, url in endpoints:
        try:
            with httpx.Client(timeout=15, headers=headers_gupy, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code not in (200, 201):
                    logger.debug("gupy: status %d para %s", resp.status_code, url)
                    continue

                data = resp.json()
                jobs = data.get("data", data.get("jobs", data if isinstance(data, list) else []))
                if not isinstance(jobs, list):
                    continue

                for item in jobs[:limite]:
                    titulo = (item.get("name") or item.get("title") or "").strip()
                    empresa = (item.get("careerPageName") or item.get("company") or "").strip()
                    cidade = (item.get("city") or "").strip()
                    estado = (item.get("state") or "").strip()
                    loc = f"{cidade}, {estado}".strip(", ") or localizacao or "Brasil"
                    tipo = (item.get("workplaceType") or item.get("type") or "").strip()
                    job_id = item.get("id", "")
                    slug = empresa.lower().replace(" ", "-").replace("/", "")
                    job_url = item.get("jobUrl") or item.get("url") or f"https://{slug}.gupy.io/jobs/{job_id}"

                    if not titulo:
                        continue

                    vagas.append(Vaga(
                        id=f"gupy_{job_id or hash(titulo) % 1000000}",
                        titulo=titulo, empresa=empresa,
                        localizacao=loc,
                        modalidade=_normalizar_modalidade(tipo),
                        salario="A combinar",
                        descricao=(item.get("description") or "")[:500],
                        url=job_url, fonte="Gupy",
                        data_publicacao=item.get("publishedDate") or item.get("createdAt") or "",
                    ))

                if vagas:
                    logger.info("gupy: %d vagas para '%s' via %s", len(vagas), query, url)
                    return vagas

        except Exception as e:
            logger.debug("gupy: erro com %s: %s", url, e)

    return vagas


# ─── Fonte 3: RemoteOK API (publica, EN) ──────────────────────────────────────

def _buscar_remoteok(query: str, limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas remotas via RemoteOK API publica (termos em EN)."""
    vagas: list[Vaga] = []
    try:
        with httpx.Client(timeout=15, headers={"User-Agent": "anime-bot/1.0"}) as client:
            resp = client.get("https://remoteok.com/api")
            if resp.status_code != 200:
                return []

            data = resp.json()
            # Normaliza query para EN para melhor matching
            query_en = query.lower()
            for pt, en in _PT_EN.items():
                query_en = query_en.replace(pt, en)
            query_terms = [t for t in query_en.split() if len(t) > 2]

            for item in data[1:]:
                if not isinstance(item, dict):
                    continue
                titulo = (item.get("position") or "").strip()
                empresa = (item.get("company") or "").strip()
                descricao = (item.get("description") or "").strip()
                tags = item.get("tags", []) or []
                texto_busca = f"{titulo} {descricao} {' '.join(tags)}".lower()

                if not any(t in texto_busca for t in query_terms):
                    continue

                url = item.get("url") or f"https://remoteok.com/jobs/{item.get('id', '')}"
                vagas.append(Vaga(
                    id=f"remoteok_{item.get('id', hash(titulo) % 1000000)}",
                    titulo=titulo, empresa=empresa,
                    localizacao="Remoto", modalidade="remoto",
                    salario=_formatar_salario_remoteok(item),
                    descricao=descricao[:500],
                    requisitos=[t.lower() for t in tags[:10]],
                    url=url, fonte="RemoteOK",
                    data_publicacao=item.get("date", ""),
                ))
                if len(vagas) >= limite:
                    break

    except Exception as e:
        logger.debug("remoteok: erro: %s", e)
    return vagas


def _formatar_salario_remoteok(item: dict) -> str:
    salary_min = item.get("salary_min")
    salary_max = item.get("salary_max")
    if salary_min and salary_max:
        return f"USD {salary_min:,} - {salary_max:,}/ano"
    if salary_min:
        return f"USD {salary_min:,}+/ano"
    return "A combinar"


# ─── Fonte 4: Trampos.co (tech jobs Brasil) ───────────────────────────────────

def _buscar_trampos(query: str, limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas de tech no Trampos.co via scraping."""
    vagas: list[Vaga] = []
    try:
        url = f"https://trampos.co/oportunidades?t={quote_plus(query)}"
        with httpx.Client(timeout=15, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.find_all(class_=re.compile(r"opportunity|job|listing"))

            for card in cards[:limite]:
                titulo_el = card.find(["h2", "h3", "h4", "a"])
                empresa_el = card.find(class_=re.compile(r"company|empresa"))
                local_el = card.find(class_=re.compile(r"location|local|cidade"))
                link_el = card.find("a", href=True)

                titulo = titulo_el.get_text(strip=True) if titulo_el else ""
                empresa = empresa_el.get_text(strip=True) if empresa_el else ""
                local = local_el.get_text(strip=True) if local_el else "Brasil"
                link = ""
                if link_el:
                    href = link_el.get("href", "")
                    link = f"https://trampos.co{href}" if href.startswith("/") else href

                if not titulo:
                    continue

                vagas.append(Vaga(
                    id=f"trampos_{hash(link or titulo) % 1000000}",
                    titulo=titulo, empresa=empresa,
                    localizacao=local,
                    modalidade=_normalizar_modalidade(local),
                    salario="A combinar", descricao="",
                    url=link, fonte="Trampos.co",
                ))

        if vagas:
            logger.info("trampos: %d vagas para '%s'", len(vagas), query)
    except Exception as e:
        logger.debug("trampos: erro: %s", e)
    return vagas


# ─── Fonte 5: LinkedIn Jobs (pagina publica) ──────────────────────────────────

def _buscar_linkedin(query: str, localizacao: str = "", limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Scraping das vagas publicas do LinkedIn (sem login)."""
    vagas: list[Vaga] = []
    try:
        q = quote_plus(query)
        loc = quote_plus(localizacao or "Brazil")
        url = (
            f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={q}&location={loc}&start=0"
        )
        with httpx.Client(timeout=20, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            for card in soup.find_all("li")[:limite]:
                titulo_el = card.find("h3")
                empresa_el = card.find("h4")
                local_el = card.find("span", class_=re.compile("location"))
                link_el = card.find("a", href=True)

                titulo = titulo_el.get_text(strip=True) if titulo_el else ""
                empresa = empresa_el.get_text(strip=True) if empresa_el else ""
                local = local_el.get_text(strip=True) if local_el else localizacao
                link = link_el["href"].split("?")[0] if link_el else ""

                if not titulo:
                    continue

                vagas.append(Vaga(
                    id=f"linkedin_{hash(link or titulo) % 1000000}",
                    titulo=titulo, empresa=empresa,
                    localizacao=local,
                    modalidade=_normalizar_modalidade(local),
                    salario="A combinar", descricao="",
                    url=link, fonte="LinkedIn",
                ))

        if vagas:
            logger.info("linkedin: %d vagas para '%s'", len(vagas), query)
    except Exception as e:
        logger.debug("linkedin: erro: %s", e)
    return vagas


# ─── Agregador principal ──────────────────────────────────────────────────────

def buscar_vagas(
    query: str,
    localizacao: str = "",
    modalidade: str = "",
    limite: int = JOBS_LIMITE_TOTAL,
    queries_extras: list[str] | None = None,
) -> list[Vaga]:
    """
    Busca vagas em todas as fontes com suporte a multi-query.
    Execucao paralela por ThreadPoolExecutor para minimizar latencia.
    """
    todas_queries = [query] + (queries_extras or [])
    # Remove duplicatas mantendo ordem
    seen_q: set[str] = set()
    queries_unicas: list[str] = []
    for q in todas_queries:
        ql = q.lower().strip()
        if ql and ql not in seen_q:
            seen_q.add(ql)
            queries_unicas.append(q)

    todas: list[Vaga] = []
    vistas: set[str] = set()
    limite_por = max(JOBS_LIMITE_POR_FONTE, limite // max(len(queries_unicas), 1))

    # Monta tarefas: (funcao, args)
    tarefas = []
    for q in queries_unicas:
        tarefas.append((_buscar_indeed, (q, localizacao, limite_por)))
        tarefas.append((_buscar_gupy, (q, localizacao, limite_por)))
        if not localizacao or modalidade == "remoto":
            tarefas.append((_buscar_remoteok, (q, limite_por)))
        tarefas.append((_buscar_trampos, (q, limite_por)))

    # Execucao paralela
    with ThreadPoolExecutor(max_workers=min(len(tarefas), 8)) as executor:
        futures = {executor.submit(fn, *args): fn.__name__ for fn, args in tarefas}
        for future in as_completed(futures, timeout=25):
            try:
                resultado = future.result()
                todas.extend(resultado)
            except Exception as e:
                logger.debug("jobs: tarefa %s falhou: %s", futures[future], e)

    # LinkedIn como fallback se poucos resultados
    if len(todas) < 5:
        for q in queries_unicas[:2]:
            try:
                vagas_li = _buscar_linkedin(q, localizacao, limite_por)
                todas.extend(vagas_li)
            except Exception:
                pass

    # Deduplica por titulo+empresa
    unicas: list[Vaga] = []
    for vaga in todas:
        chave = f"{vaga.titulo.lower().strip()}|{vaga.empresa.lower().strip()}"
        if chave not in vistas and vaga.titulo:
            vistas.add(chave)
            unicas.append(vaga)

    # Filtra por modalidade se especificado
    if modalidade:
        filtradas = [v for v in unicas if v.modalidade == modalidade or not v.modalidade]
        unicas = filtradas if filtradas else unicas

    logger.info(
        "jobs: queries=%d total=%d unicas=%d",
        len(queries_unicas), len(todas), len(unicas),
    )
    return unicas[:limite]
