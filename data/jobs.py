"""
Busca de vagas em multiplas fontes — 100% gratuito, sem APIs pagas.
Fontes: Indeed (HTML), Gupy API, RemoteOK API, Trampos.co, programathor, LinkedIn.
Suporta multi-query para expandir cobertura com variantes PT/EN e skills.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urlencode

import feedparser
import httpx
from bs4 import BeautifulSoup
try:
    from scrapling import Fetcher
except Exception:
    class _FetcherResponse:
        def __init__(self, html: str):
            self.html = html

    class Fetcher:
        """Fallback minimo compatível com a API usada no projeto."""

        @staticmethod
        def get(url: str, **kwargs):
            timeout = kwargs.get("timeout", 15)
            headers = kwargs.get("headers") or _HEADERS
            resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
            resp.raise_for_status()
            return _FetcherResponse(resp.text)

        @staticmethod
        def post(url: str, data=None, **kwargs):
            timeout = kwargs.get("timeout", 15)
            headers = kwargs.get("headers") or _HEADERS
            resp = httpx.post(url, data=data, headers=headers, timeout=timeout, follow_redirects=True)
            resp.raise_for_status()
            return _FetcherResponse(resp.text)

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
            page = Fetcher.get(url, stealthy_headers=True, impersonate='chrome124', timeout=15)
            soup = BeautifulSoup(page.html, "html.parser")
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


# ─── Fonte 2b: Glassdoor (DDG dork dedicado) ──────────────────────────────────

def _buscar_glassdoor(query: str, localizacao: str = "", limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas no Glassdoor via DDG dork (site:glassdoor.com.br)."""
    vagas: list[Vaga] = []
    loc_str = f" {localizacao}" if localizacao else ""
    dork = f"site:glassdoor.com.br OR site:glassdoor.com {query}{loc_str} vaga emprego"
    try:
        ddg_url = f"https://lite.duckduckgo.com/lite/?{urlencode({'q': dork, 'kl': 'br-pt'})}"
        page = Fetcher.get(ddg_url, stealthy_headers=True, impersonate='chrome124', timeout=18)
        soup = BeautifulSoup(page.html, "html.parser")
        links = soup.select("a.result-link")

        for link_el in links[:limite * 2]:
            href = link_el.get("href", "")
            # Resolve redirecionamento DDG
            if "/l/?uddg=" in href:
                from urllib.parse import unquote, parse_qs, urlparse
                qs = parse_qs(urlparse(href).query)
                href = unquote(qs.get("uddg", [""])[0])

            if "glassdoor.com" not in href:
                continue

            titulo = link_el.get_text(strip=True)
            if not titulo or len(titulo) < 5:
                continue

            # Snippet como descricao
            snippet_el = link_el.find_next("td", class_="result-snippet")
            descricao = snippet_el.get_text(strip=True) if snippet_el else ""

            vagas.append(Vaga(
                id=f"glassdoor_{hash(href) % 1000000}",
                titulo=titulo,
                empresa="",
                url=href,
                fonte="glassdoor",
                salario="",
                localizacao=localizacao or "Brasil",
                modalidade="",
                descricao=descricao,
                requisitos=[],
            ))
            if len(vagas) >= limite:
                break

        if vagas:
            logger.info("glassdoor: %d vagas para '%s'", len(vagas), query)
    except Exception as e:
        logger.debug("glassdoor: erro: %s", e)

    return vagas


# ─── Fonte 2: Gupy API ────────────────────────────────────────────────────────

def _gupy_parse_jobs(jobs: list, localizacao: str, query: str, fonte_tag: str) -> list[Vaga]:
    """Converte lista de dicts Gupy em objetos Vaga."""
    vagas = []
    for item in jobs:
        titulo = (item.get("name") or item.get("title") or "").strip()
        if not titulo:
            continue
        empresa = (item.get("careerPageName") or item.get("company") or "").strip()
        cidade = (item.get("city") or "").strip()
        estado = (item.get("state") or "").strip()
        loc = f"{cidade}, {estado}".strip(", ") or localizacao or "Brasil"
        tipo = (item.get("workplaceType") or item.get("type") or "").strip()
        job_id = item.get("id", "")
        slug = empresa.lower().replace(" ", "-").replace("/", "").replace(".", "") if empresa else "empresa"
        job_url = (item.get("jobUrl") or item.get("url") or
                   f"https://{slug}.gupy.io/jobs/{job_id}" if job_id else "")
        vagas.append(Vaga(
            id=f"gupy_{job_id or hash(titulo + empresa) % 1000000}",
            titulo=titulo, empresa=empresa,
            localizacao=loc,
            modalidade=_normalizar_modalidade(tipo),
            salario="A combinar",
            descricao=(item.get("description") or "")[:500],
            url=job_url, fonte=fonte_tag,
            data_publicacao=item.get("publishedDate") or item.get("createdAt") or "",
        ))
    return vagas


def _buscar_gupy(query: str, localizacao: str = "", limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas via API publica da Gupy (varios endpoints em cascata)."""
    vagas: list[Vaga] = []

    # Headers que simulam o portal Gupy
    headers_gupy = {
        **_HEADERS,
        "Accept": "application/json",
        "Origin": "https://portal.gupy.io",
        "Referer": "https://portal.gupy.io/",
    }

    endpoints = [
        f"https://portal.api.gupy.io/api/job?jobName={quote_plus(query)}&limit={limite}&offset=0",
        f"https://portal.api.gupy.io/api/v1/jobs?name={quote_plus(query)}&limit={limite}",
        f"https://portal.api.gupy.io/api/v2/jobs?name={quote_plus(query)}&limit={limite}",
    ]
    if localizacao:
        endpoints = [u + f"&cityName={quote_plus(localizacao)}" for u in endpoints]

    for url in endpoints:
        try:
            with httpx.Client(timeout=15, headers=headers_gupy, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code not in (200, 201):
                    logger.debug("gupy central: status %d", resp.status_code)
                    continue
                data = resp.json()
                logger.debug("gupy central: resposta keys=%s", list(data.keys()) if isinstance(data, dict) else type(data))
                jobs = data.get("data", data.get("jobs", data if isinstance(data, list) else []))
                if not isinstance(jobs, list) or not jobs:
                    continue
                vagas = _gupy_parse_jobs(jobs[:limite], localizacao, query, "Gupy")
                if vagas:
                    logger.info("gupy central: %d vagas para '%s'", len(vagas), query)
                    return vagas
        except Exception as e:
            logger.debug("gupy central: erro %s: %s", url[:60], e)

    return vagas


# Principais empresas tech BR com portais no Gupy
_GUPY_EMPRESAS_BR = [
    "nubank", "ifood", "stone", "totvs", "ci-t", "stefanini", "neon",
    "picpay", "rappi", "loggi", "olist", "contabilizei", "creditas",
    "dock", "pismo", "vtex", "hotmart", "madeiramadeira", "loft",
    "cloudwalk", "zup", "conductor", "mercadolivre", "b2w", "americanas",
    "raia-drogasil", "grupozap", "quinto-andar", "quintoandar",
    "conta-azul", "contaazul", "gympass", "uol", "terra",
    "movidesk", "solfacil", "warren", "nuvemshop", "linx",
]


def _buscar_gupy_portais(query: str, limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """
    Busca vagas diretamente nas portais Gupy das principais empresas BR.
    Cada portal expoe a mesma API REST: /{empresa}.gupy.io/api/job
    """
    query_terms = [t.lower() for t in query.split() if len(t) > 2]

    def _portal(empresa: str) -> list[Vaga]:
        try:
            url = f"https://{empresa}.gupy.io/api/job?jobName={quote_plus(query)}&limit=5&offset=0"
            headers = {"Accept": "application/json", "User-Agent": _HEADERS["User-Agent"]}
            with httpx.Client(timeout=10, headers=headers, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    return []
                data = resp.json()
                jobs = data.get("data", data if isinstance(data, list) else [])
                if not isinstance(jobs, list):
                    return []
                result = _gupy_parse_jobs(jobs[:3], "", query, "Gupy")
                # Filtra por relevância do título
                if query_terms:
                    result = [v for v in result if any(t in v.titulo.lower() for t in query_terms)]
                return result
        except Exception:
            return []

    todas: list[Vaga] = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(_portal, e) for e in _GUPY_EMPRESAS_BR]
        for f in as_completed(futs, timeout=18):
            try:
                todas.extend(f.result())
            except Exception:
                pass

    if todas:
        logger.info("gupy_portais: %d vagas para '%s'", len(todas), query)
    return todas[:limite]


# ─── Fonte 2b: Vagas.com.br ───────────────────────────────────────────────────

def _buscar_vagas_com_br(query: str, localizacao: str = "", limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas via vagas.com.br (maior job board BR)."""
    vagas: list[Vaga] = []
    try:
        slug = quote_plus(query.replace(" ", "-").lower())
        urls = [
            f"https://www.vagas.com.br/vagas-de-{slug}?ordenar_por=mais_recentes",
            f"https://www.vagas.com.br/empregos?q={quote_plus(query)}&ordenar_por=mais_recentes",
        ]
        if localizacao:
            urls = [u + f"&cidade={quote_plus(localizacao)}" for u in urls]

        for url in urls:
            try:
                page = Fetcher.get(url, stealthy_headers=True, impersonate='chrome124', timeout=15)
                soup = BeautifulSoup(page.html, "html.parser")
                # vagas.com.br usa <article class="job-shortcut--list" data-job-id="...">
                cards = soup.find_all("article", class_=re.compile(r"job-shortcut|job-item|vaga"))
                if not cards:
                    cards = soup.find_all(attrs={"data-job-id": True})
                if not cards:
                    # Fallback: qualquer li/div com link de vaga
                    cards = soup.find_all("li", class_=re.compile(r"job|vaga|opportunity"))

                for card in cards[:limite]:
                    titulo_el = (card.find("a", class_=re.compile(r"job-shortcut__title|title"))
                                 or card.find(["h2", "h3", "a"]))
                    empresa_el = card.find(class_=re.compile(r"company|empresa|recruiter"))
                    local_el = card.find(class_=re.compile(r"location|local|city|cidade"))
                    link_el = card.find("a", href=True)

                    titulo = titulo_el.get_text(strip=True) if titulo_el else ""
                    empresa = empresa_el.get_text(strip=True) if empresa_el else ""
                    local = local_el.get_text(strip=True) if local_el else localizacao or "Brasil"
                    href = link_el.get("href", "") if link_el else ""
                    link = f"https://www.vagas.com.br{href}" if href.startswith("/") else href

                    if not titulo:
                        continue
                    vagas.append(Vaga(
                        id=f"vagas_br_{hash(link or titulo) % 1000000}",
                        titulo=titulo, empresa=empresa,
                        localizacao=local,
                        modalidade=_normalizar_modalidade(local),
                        salario="A combinar", descricao="",
                        url=link, fonte="Vagas.com.br",
                    ))

                if vagas:
                    logger.info("vagas.com.br: %d vagas para '%s'", len(vagas), query)
                    return vagas
            except Exception as e:
                logger.debug("vagas.com.br: erro com %s: %s", url[:60], e)
    except Exception as e:
        logger.debug("vagas.com.br: erro: %s", e)
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
        page = Fetcher.get(url, stealthy_headers=True, impersonate='chrome124', timeout=15)
        soup = BeautifulSoup(page.html, "html.parser")
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
        page = Fetcher.get(url, stealthy_headers=True, impersonate='chrome124', timeout=20)
        soup = BeautifulSoup(page.html, "html.parser")
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
        page = Fetcher.get(url, stealthy_headers=True, impersonate='chrome124', timeout=15)
        soup = BeautifulSoup(page.html, "html.parser")
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
        page = Fetcher.get(url, stealthy_headers=True, impersonate='chrome124', timeout=15)
        soup = BeautifulSoup(page.html, "html.parser")
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

# Sites brasileiros de vagas tech
_DORK_SITES_BR = [
    "revelo.com.br", "trampos.co", "programathor.com.br", "remotar.com.br",
    "inhire.com.br", "vagas.com.br", "99jobs.com", "getmanifest.com.br",
    "kenoby.com", "solides.com.br", "catho.com.br", "infojobs.net",
]
# Sites internacionais / plataformas de ATS com pages publicas
_DORK_SITES_INTL = [
    "gupy.io", "weworkremotely.com", "remoteok.com",
    "lever.co", "greenhouse.io", "jobs.lever.co", "boards.greenhouse.io",
    "wellfound.com", "angel.co",
    "glassdoor.com.br", "glassdoor.com",
    "br.indeed.com", "indeed.com",
]
# Lista completa para detectar fonte pelo domínio
_DORK_SITES_ALL = list(dict.fromkeys(_DORK_SITES_BR + _DORK_SITES_INTL))


def _ddg_uma_dork(dork: str, sites_alvo: list[str], limite: int, localizacao: str = "") -> list[Vaga]:
    """Executa uma unica query DDG Lite e retorna vagas dos sites alvo."""
    vagas: list[Vaga] = []
    try:
        ddg_url = f"https://lite.duckduckgo.com/lite/?{urlencode({'q': dork, 'kl': 'br-pt'})}"
        page = Fetcher.get(ddg_url, stealthy_headers=True, impersonate='chrome124', timeout=22)
        soup = BeautifulSoup(page.html, "html.parser")

        # DDG Lite: links em <a class="result-link"> + snippets em <td class="result-snippet">
        links = soup.find_all("a", class_="result-link", href=True)
        if not links:
            # Fallback: qualquer <a> com href externo
            links = [a for a in soup.find_all("a", href=True)
                     if a.get("href", "").startswith("http")]

        snippets_els = soup.find_all("td", class_="result-snippet")
        snippets = [el.get_text(" ", strip=True) for el in snippets_els]

        vistas: set[str] = set()
        for i, link_el in enumerate(links[:limite * 4]):
            href = link_el.get("href", "")

            # DDG às vezes usa redirect /l/?uddg=URL_ENCODED
            if "duckduckgo.com/l/" in href or href.startswith("/l/"):
                m = re.search(r"uddg=([^&]+)", href)
                if m:
                    from urllib.parse import unquote
                    href = unquote(m.group(1))
                else:
                    continue

            if not href or "duckduckgo" in href or href in vistas:
                continue
            if not any(s in href for s in sites_alvo):
                continue

            vistas.add(href)
            titulo = link_el.get_text(strip=True)
            if not titulo or len(titulo) < 4:
                titulo = href.split("/")[-1].replace("-", " ").replace("_", " ").strip() or href

            snippet = snippets[i] if i < len(snippets) else ""
            fonte = next((s.split(".")[0].capitalize() for s in sites_alvo if s in href), "Dork")

            vagas.append(Vaga(
                id=f"dork_{hash(href) % 10000000}",
                titulo=titulo, empresa="",
                localizacao=localizacao or "Brasil",
                modalidade=_normalizar_modalidade(snippet or localizacao),
                salario="A combinar",
                descricao=snippet[:400],
                requisitos=_extrair_requisitos(snippet),
                url=href, fonte=fonte,
            ))
            if len(vagas) >= limite:
                break

    except Exception as e:
        logger.debug("dork query erro [%s]: %s", dork[:60], e)
    return vagas


def _buscar_google_dork(
    query: str,
    localizacao: str = "",
    limite: int = JOBS_LIMITE_POR_FONTE,
    queries_extras: list[str] | None = None,
) -> list[Vaga]:
    """
    Busca agressiva via DuckDuckGo Dorks.
    Gera múltiplas combinações de sites × queries × sufixos e roda em paralelo.
    """
    loc_str = (
        f" {localizacao}"
        if localizacao and localizacao.lower() not in ("remoto", "brasil", "brazil")
        else ""
    )

    # Strings de site-groups (DDG suporta uns 4-5 site: por query)
    br_str   = " OR ".join(f"site:{s}" for s in _DORK_SITES_BR[:5])
    br2_str  = " OR ".join(f"site:{s}" for s in _DORK_SITES_BR[5:])
    intl_str = " OR ".join(f"site:{s}" for s in _DORK_SITES_INTL[:5])
    intl2_str = " OR ".join(f"site:{s}" for s in _DORK_SITES_INTL[5:])

    # Queries a tentar (principal + extras)
    todas_q = [query] + (queries_extras or [])[:2]

    # Gera lista de (dork_string, sites_alvo)
    dorks: list[tuple[str, list[str]]] = []
    for q in todas_q:
        q = q.strip()
        # Grupo BR — termos PT
        dorks.append((f"({br_str}) {q}{loc_str} vaga emprego", _DORK_SITES_BR[:5]))
        dorks.append((f"({br_str}) {q}{loc_str} desenvolvedor programador", _DORK_SITES_BR[:5]))
        # Grupo BR2 — outros sites brasileiros
        if br2_str:
            dorks.append((f"({br2_str}) {q}{loc_str} vaga", _DORK_SITES_BR[5:]))
        # Grupo INTL — termos EN
        dorks.append((f"({intl_str}) {q} job remote", _DORK_SITES_INTL[:5]))
        # Grupo INTL2
        if intl2_str:
            dorks.append((f"({intl2_str}) {q} software engineer remote", _DORK_SITES_INTL[5:]))

    # Limita a 8 dorks para não estourar timeout
    dorks = dorks[:8]
    limite_por = max(5, limite // 3)

    todas: list[Vaga] = []
    with ThreadPoolExecutor(max_workers=min(len(dorks), 6)) as ex:
        futs = [ex.submit(_ddg_uma_dork, d, sites, limite_por, localizacao) for d, sites in dorks]
        for f in as_completed(futs, timeout=28):
            try:
                todas.extend(f.result())
            except Exception:
                pass

    # Deduplica por URL
    vistas: set[str] = set()
    unicas: list[Vaga] = []
    for v in todas:
        if v.url and v.url not in vistas and v.titulo:
            vistas.add(v.url)
            unicas.append(v)

    if unicas:
        logger.info("dork: %d vagas para '%s' (%d dorks)", len(unicas), query, len(dorks))
    return unicas[:limite]


# ─── Fonte 11: Inhire (tech Brasil) ──────────────────────────────────────────

def _buscar_inhire(query: str, limite: int = JOBS_LIMITE_POR_FONTE) -> list[Vaga]:
    """Busca vagas via Inhire (plataforma tech Brasil, scraping)."""
    vagas: list[Vaga] = []
    try:
        url = f"https://inhire.com.br/vagas?q={quote_plus(query)}"
        page = Fetcher.get(url, stealthy_headers=True, impersonate='chrome124', timeout=15)
        soup = BeautifulSoup(page.html, "html.parser")
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
        tarefas.append((_buscar_vagas_com_br, (q, localizacao, limite_por)))
        tarefas.append((_buscar_linkedin, (q, localizacao, limite_por)))
        tarefas.append((_buscar_glassdoor, (q, localizacao, limite_por)))
        if not localizacao or modalidade == "remoto":
            tarefas.append((_buscar_remoteok, (q, limite_por)))
            tarefas.append((_buscar_weworkremotely, (q, limite_por)))
            tarefas.append((_buscar_workingnomads, (q, limite_por)))

    # Gupy portais das empresas BR (uma tarefa paralela por query, vai internamente usar ThreadPool)
    for q in queries_unicas[:2]:
        tarefas.append((_buscar_gupy_portais, (q, limite_por)))

    # Google Dork — expande em múltiplas queries × site-groups internamente
    tarefas.append((_buscar_google_dork, (queries_unicas[0], localizacao, limite_por * 2, queries_unicas[1:])))

    # Execucao paralela (max 20 workers)
    with ThreadPoolExecutor(max_workers=min(len(tarefas), 20)) as executor:
        futures = {executor.submit(fn, *args): fn.__name__ for fn, args in tarefas}
        concluidas: set = set()
        try:
            for future in as_completed(futures, timeout=45):
                concluidas.add(future)
                try:
                    resultado = future.result()
                    todas.extend(resultado)
                except Exception as e:
                    logger.debug("jobs: tarefa %s falhou: %s", futures[future], e)
        except FuturesTimeoutError:
            pendentes = [f for f in futures if f not in concluidas]
            logger.warning(
                "jobs: timeout parcial na coleta (%d/%d tarefas pendentes), retornando resultados parciais",
                len(pendentes),
                len(futures),
            )
            for future in pendentes:
                if not future.done():
                    future.cancel()
                    continue
                try:
                    resultado = future.result()
                    todas.extend(resultado)
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
