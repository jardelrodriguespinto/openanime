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


# ─── Fonte 6: WeWorkRemotely RSS ─────────────────────────────────────────────

def _buscar_weworkremotely(query: str, limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas remotas via WeWorkRemotely RSS (EN)."""
    vagas: list[Vaga] = []
    feeds = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
        "https://weworkremotely.com/remote-jobs.rss",
    ]
    query_en = query.lower()
    for pt, en in _PT_EN.items():
        query_en = query_en.replace(pt, en)
    query_terms = [t for t in query_en.split() if len(t) > 2]

    try:
        for feed_url in feeds:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                titulo = entry.get("title", "").strip()
                texto = f"{titulo} {entry.get('summary', '')}".lower()
                if query_terms and not any(t in texto for t in query_terms):
                    continue
                empresa = ""
                if ":" in titulo:
                    partes = titulo.split(":", 1)
                    empresa, titulo = partes[0].strip(), partes[1].strip()
                link = entry.get("link", "")
                vagas.append(Vaga(
                    id=f"wwr_{hash(link or titulo) % 1000000}",
                    titulo=titulo, empresa=empresa,
                    localizacao="Remoto", modalidade="remoto",
                    salario="A combinar",
                    descricao=entry.get("summary", "")[:500],
                    requisitos=_extrair_requisitos(entry.get("summary", "")),
                    url=link, fonte="WeWorkRemotely",
                    data_publicacao=entry.get("published", ""),
                ))
                if len(vagas) >= limite:
                    break
            if len(vagas) >= limite:
                break
        if vagas:
            logger.info("weworkremotely: %d vagas para '%s'", len(vagas), query)
    except Exception as e:
        logger.debug("weworkremotely: erro: %s", e)
    return vagas


# ─── Fonte 7: WorkingNomads API (JSON publica) ────────────────────────────────

def _buscar_workingnomads(query: str, limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas remotas via WorkingNomads API publica."""
    vagas: list[Vaga] = []
    try:
        query_en = query.lower()
        for pt, en in _PT_EN.items():
            query_en = query_en.replace(pt, en)
        slug = query_en.split()[0] if query_en else "developer"
        url = f"https://www.workingnomads.com/api/exposed_jobs/?category={quote_plus(slug)}&limit={limite}"
        with httpx.Client(timeout=15, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                # fallback: busca geral
                resp = client.get(f"https://www.workingnomads.com/api/exposed_jobs/?limit={limite}")
            if resp.status_code != 200:
                return []
            data = resp.json()
            jobs = data if isinstance(data, list) else data.get("results", [])
            query_terms = [t for t in query_en.split() if len(t) > 2]
            for item in jobs[:limite * 2]:
                titulo = (item.get("title") or "").strip()
                empresa = (item.get("company_name") or "").strip()
                descricao = (item.get("description") or "").strip()
                texto = f"{titulo} {descricao}".lower()
                if query_terms and not any(t in texto for t in query_terms):
                    continue
                link = item.get("url") or item.get("apply_url") or ""
                vagas.append(Vaga(
                    id=f"workingnomads_{hash(link or titulo) % 1000000}",
                    titulo=titulo, empresa=empresa,
                    localizacao="Remoto", modalidade="remoto",
                    salario="A combinar",
                    descricao=descricao[:500],
                    requisitos=_extrair_requisitos(descricao),
                    url=link, fonte="WorkingNomads",
                    data_publicacao=item.get("pub_date", ""),
                ))
                if len(vagas) >= limite:
                    break
        if vagas:
            logger.info("workingnomads: %d vagas para '%s'", len(vagas), query)
    except Exception as e:
        logger.debug("workingnomads: erro: %s", e)
    return vagas


# ─── Fonte 8: Programathor (tech Brasil) ─────────────────────────────────────

def _buscar_programathor(query: str, limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas via Programathor (tech jobs Brasil, scraping)."""
    vagas: list[Vaga] = []
    try:
        url = f"https://programathor.com.br/jobs?search={quote_plus(query)}"
        with httpx.Client(timeout=15, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.find_all(class_=re.compile(r"job-opportunity|job-card|cell-list-developer"))
            for card in cards[:limite]:
                titulo_el = card.find(["h2", "h3", "h4"])
                empresa_el = card.find(class_=re.compile(r"company|empresa"))
                local_el = card.find(class_=re.compile(r"location|city|cidade"))
                link_el = card.find("a", href=True)
                titulo = titulo_el.get_text(strip=True) if titulo_el else ""
                empresa = empresa_el.get_text(strip=True) if empresa_el else ""
                local = local_el.get_text(strip=True) if local_el else "Brasil"
                href = link_el.get("href", "") if link_el else ""
                link = f"https://programathor.com.br{href}" if href.startswith("/") else href
                if not titulo:
                    continue
                vagas.append(Vaga(
                    id=f"programathor_{hash(link or titulo) % 1000000}",
                    titulo=titulo, empresa=empresa,
                    localizacao=local,
                    modalidade=_normalizar_modalidade(local),
                    salario="A combinar", descricao="",
                    url=link, fonte="Programathor",
                ))
        if vagas:
            logger.info("programathor: %d vagas para '%s'", len(vagas), query)
    except Exception as e:
        logger.debug("programathor: erro: %s", e)
    return vagas


# ─── Fonte 9: Revelo (Brasil, tech) ──────────────────────────────────────────

def _buscar_revelo(query: str, limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas via Revelo (scraping pagina publica)."""
    vagas: list[Vaga] = []
    try:
        url = f"https://www.revelo.com.br/vagas?q={quote_plus(query)}&remote=true"
        with httpx.Client(timeout=15, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.find_all(class_=re.compile(r"job-card|opportunity|position"))
            for card in cards[:limite]:
                titulo_el = card.find(["h2", "h3"])
                empresa_el = card.find(class_=re.compile(r"company"))
                link_el = card.find("a", href=True)
                titulo = titulo_el.get_text(strip=True) if titulo_el else ""
                empresa = empresa_el.get_text(strip=True) if empresa_el else ""
                href = link_el.get("href", "") if link_el else ""
                link = f"https://www.revelo.com.br{href}" if href.startswith("/") else href
                if not titulo:
                    continue
                vagas.append(Vaga(
                    id=f"revelo_{hash(link or titulo) % 1000000}",
                    titulo=titulo, empresa=empresa,
                    localizacao="Brasil", modalidade="remoto",
                    salario="A combinar", descricao="",
                    url=link, fonte="Revelo",
                ))
        if vagas:
            logger.info("revelo: %d vagas para '%s'", len(vagas), query)
    except Exception as e:
        logger.debug("revelo: erro: %s", e)
    return vagas


# ─── Fonte 10: Google Dorks (DuckDuckGo como proxy gratuito) ─────────────────

_DORK_SITES = [
    "revelo.com.br", "trampos.co", "programathor.com.br", "remotar.com.br",
    "weworkremotely.com", "remoteok.com", "gupy.io",
]


def _buscar_google_dork(query: str, localizacao: str = "", limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """
    Usa DuckDuckGo Lite como motor de busca com Google Dork para encontrar
    vagas em multiplos sites ao mesmo tempo.
    site:gupy.io OR site:trampos.co <query> <localizacao> vaga emprego
    """
    vagas: list[Vaga] = []
    try:
        sites_dork = " OR ".join(f"site:{s}" for s in _DORK_SITES)
        loc_str = f" {localizacao}" if localizacao and localizacao.lower() != "remoto" else ""
        dork = f"({sites_dork}) {query}{loc_str} vaga emprego"

        with httpx.Client(timeout=20, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": dork},
            )
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            links = soup.find_all("a", class_=re.compile(r"result-link|link"), href=True)
            if not links:
                links = soup.select("a.result-link") or soup.select("a[href*='http']")

            vistas_dork: set[str] = set()
            for link_el in links[:limite * 2]:
                href = link_el.get("href", "")
                if not href or href.startswith("/") or "duckduckgo" in href:
                    continue
                # Filtra apenas dos sites alvo
                if not any(s in href for s in _DORK_SITES):
                    continue
                titulo = link_el.get_text(strip=True) or href
                if href in vistas_dork:
                    continue
                vistas_dork.add(href)
                # Detecta fonte pelo dominio
                fonte = next((s.split(".")[0].capitalize() for s in _DORK_SITES if s in href), "Dork")
                vagas.append(Vaga(
                    id=f"dork_{hash(href) % 1000000}",
                    titulo=titulo, empresa="",
                    localizacao=localizacao or "Brasil",
                    modalidade=_normalizar_modalidade(localizacao),
                    salario="A combinar", descricao="",
                    url=href, fonte=fonte,
                ))
                if len(vagas) >= limite:
                    break

        if vagas:
            logger.info("dork: %d vagas para '%s'", len(vagas), query)
    except Exception as e:
        logger.debug("dork: erro: %s", e)
    return vagas


# ─── Fonte 11: Inhire (tech Brasil) ──────────────────────────────────────────

def _buscar_inhire(query: str, limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas via Inhire (plataforma tech Brasil, scraping)."""
    vagas: list[Vaga] = []
    try:
        url = f"https://inhire.com.br/vagas?q={quote_plus(query)}"
        with httpx.Client(timeout=15, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.find_all(class_=re.compile(r"job|vaga|opportunity|card"))
            for card in cards[:limite]:
                titulo_el = card.find(["h2", "h3", "h4"])
                empresa_el = card.find(class_=re.compile(r"company|empresa"))
                local_el = card.find(class_=re.compile(r"location|local"))
                link_el = card.find("a", href=True)
                titulo = titulo_el.get_text(strip=True) if titulo_el else ""
                empresa = empresa_el.get_text(strip=True) if empresa_el else ""
                local = local_el.get_text(strip=True) if local_el else "Brasil"
                href = link_el.get("href", "") if link_el else ""
                link = f"https://inhire.com.br{href}" if href.startswith("/") else href
                if not titulo:
                    continue
                vagas.append(Vaga(
                    id=f"inhire_{hash(link or titulo) % 1000000}",
                    titulo=titulo, empresa=empresa,
                    localizacao=local,
                    modalidade=_normalizar_modalidade(local),
                    salario="A combinar", descricao="",
                    url=link, fonte="Inhire",
                ))
        if vagas:
            logger.info("inhire: %d vagas para '%s'", len(vagas), query)
    except Exception as e:
        logger.debug("inhire: erro: %s", e)
    return vagas


# ─── Filtro de data ───────────────────────────────────────────────────────────

import datetime as _dt
from email.utils import parsedate_to_datetime as _parsedate


def _dentro_prazo(data_str: str, max_dias: int = 15) -> bool:
    """Retorna True se a vaga foi publicada nos ultimos max_dias dias."""
    if not data_str:
        return True  # sem data: assume valida (nao descarta por falta de info)
    try:
        # Tenta RFC 2822 (RSS)
        try:
            pub = _parsedate(data_str).replace(tzinfo=None)
        except Exception:
            # Tenta ISO 8601 / varios formatos
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    pub = _dt.datetime.strptime(data_str[:19], fmt)
                    break
                except Exception:
                    continue
            else:
                return True  # formato desconhecido: nao descarta
        delta = _dt.datetime.utcnow() - pub
        return delta.days <= max_dias
    except Exception:
        return True


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

    # Monta tarefas por fonte e query
    tarefas = []
    for q in queries_unicas:
        tarefas.append((_buscar_indeed, (q, localizacao, limite_por)))
        tarefas.append((_buscar_gupy, (q, localizacao, limite_por)))
        tarefas.append((_buscar_trampos, (q, limite_por)))
        tarefas.append((_buscar_programathor, (q, limite_por)))
        tarefas.append((_buscar_revelo, (q, limite_por)))
        tarefas.append((_buscar_inhire, (q, limite_por)))
        if not localizacao or modalidade == "remoto":
            tarefas.append((_buscar_remoteok, (q, limite_por)))
            tarefas.append((_buscar_weworkremotely, (q, limite_por)))
            tarefas.append((_buscar_workingnomads, (q, limite_por)))

    # Google Dork (uma chamada por query principal — evita flood)
    for q in queries_unicas[:2]:
        tarefas.append((_buscar_google_dork, (q, localizacao, limite_por)))

    # Execucao paralela (max 12 workers)
    with ThreadPoolExecutor(max_workers=min(len(tarefas), 12)) as executor:
        futures = {executor.submit(fn, *args): fn.__name__ for fn, args in tarefas}
        for future in as_completed(futures, timeout=30):
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

    # Filtra vagas com mais de 15 dias (mantém as sem data)
    todas = [v for v in todas if _dentro_prazo(v.data_publicacao, max_dias=15)]

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
        "jobs: queries=%d total_bruto=%d pos_filtro_data=%d unicas=%d",
        len(queries_unicas), len(todas), len([v for v in todas if v.titulo]), len(unicas),
    )
    return unicas[:limite]
