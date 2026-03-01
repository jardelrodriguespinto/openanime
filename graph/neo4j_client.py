
import logging
import os
import re
import datetime

from neo4j import GraphDatabase

from data.franchise_routes import get_franchise_route

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Conexao e queries ao Neo4j."""

    def __init__(self):
        uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        logger.info("Neo4jClient conectado a %s", uri)

    def close(self):
        self.driver.close()
        logger.info("Neo4jClient desconectado")

    def setup_schema(self):
        """Cria constraints, indices e executa manutencao basica."""
        statements = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Anime) REQUIRE a.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (m:Manga) REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (u:Usuario) REQUIRE u.telegram_id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (g:Genero) REQUIRE g.nome IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Tema) REQUIRE t.nome IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Estudio) REQUIRE e.nome IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (f:Franquia) REQUIRE f.nome IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (fo:Formato) REQUIRE fo.nome IS UNIQUE",
            "CREATE INDEX IF NOT EXISTS FOR (a:Anime) ON (a.titulo)",
            "CREATE INDEX IF NOT EXISTS FOR (a:Anime) ON (a.titulo_key)",
        ]
        with self.driver.session() as session:
            for cypher in statements:
                try:
                    session.run(cypher)
                except Exception as exc:
                    logger.warning("Schema statement falhou: %s", exc)

        self._maintenance_cleanup()
        logger.info("Schema Neo4j configurado")

    @staticmethod
    def _normalize_title(text: str) -> str:
        value = (text or "").strip().lower()
        value = re.sub(r"[;:,_\-]+", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value

    @staticmethod
    def _clean_name(value: str | None) -> str:
        text = (value or "").strip()
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def _clean_list(values) -> list[str]:
        if not values:
            return []
        out = []
        seen = set()
        for item in values:
            cleaned = Neo4jClient._clean_name(str(item))
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                out.append(cleaned)
        return out

    @staticmethod
    def _to_iso(value):
        if value is None:
            return None
        try:
            return value.isoformat()
        except Exception:
            return str(value)

    def _maintenance_cleanup(self):
        with self.driver.session() as session:
            session.run(
                """
                MATCH (a:Anime)
                SET a:Obra,
                    a.titulo = coalesce(a.titulo, toString(a.id)),
                    a.titulo_key = toLower(trim(coalesce(a.titulo_key, a.titulo, toString(a.id), ''))),
                    a.id = coalesce(a.id, a.titulo_key)
                """
            )
            session.run("MATCH (g:Genero) WHERE g.nome IS NULL OR trim(g.nome) = '' DETACH DELETE g")
            session.run("MATCH (t:Tema) WHERE t.nome IS NULL OR trim(t.nome) = '' DETACH DELETE t")
            session.run("MATCH (e:Estudio) WHERE e.nome IS NULL OR trim(e.nome) = '' DETACH DELETE e")
            session.run("MATCH (fo:Formato) WHERE fo.nome IS NULL OR trim(fo.nome) = '' DETACH DELETE fo")
            session.run("MATCH (f:Franquia) WHERE f.nome IS NULL OR trim(f.nome) = '' DETACH DELETE f")

    def _merge_anime_nodes(self, session, source_nid: int, target_nid: int):
        """Move relacionamentos do source para target e remove o source."""
        if source_nid == target_nid:
            return

        session.run(
            """
            MATCH (u:Usuario)-[r:ASSISTIU]->(source:Anime) WHERE id(source) = $source_nid
            MATCH (target:Anime) WHERE id(target) = $target_nid
            MERGE (u)-[nr:ASSISTIU]->(target)
            SET nr.nota = coalesce(nr.nota, r.nota), nr.data = coalesce(nr.data, r.data)
            DELETE r
            """,
            source_nid=source_nid,
            target_nid=target_nid,
        )
        session.run(
            """
            MATCH (u:Usuario)-[r:DROPOU]->(source:Anime) WHERE id(source) = $source_nid
            MATCH (target:Anime) WHERE id(target) = $target_nid
            MERGE (u)-[nr:DROPOU]->(target)
            SET nr.episodio = coalesce(nr.episodio, r.episodio), nr.data = coalesce(nr.data, r.data)
            DELETE r
            """,
            source_nid=source_nid,
            target_nid=target_nid,
        )
        session.run(
            """
            MATCH (u:Usuario)-[r:QUER_VER]->(source:Anime) WHERE id(source) = $source_nid
            MATCH (target:Anime) WHERE id(target) = $target_nid
            MERGE (u)-[:QUER_VER]->(target)
            DELETE r
            """,
            source_nid=source_nid,
            target_nid=target_nid,
        )
        session.run(
            """
            MATCH (u:Usuario)-[r:RECOMENDADO]->(source:Anime) WHERE id(source) = $source_nid
            MATCH (target:Anime) WHERE id(target) = $target_nid
            MERGE (u)-[nr:RECOMENDADO]->(target)
            SET nr.freq = coalesce(nr.freq, 0) + coalesce(r.freq, 0),
                nr.ultima_data = coalesce(nr.ultima_data, r.ultima_data)
            DELETE r
            """,
            source_nid=source_nid,
            target_nid=target_nid,
        )
        session.run(
            """
            MATCH (u:Usuario)-[r:FEEDBACK_RECOMENDACAO]->(source:Anime) WHERE id(source) = $source_nid
            MATCH (target:Anime) WHERE id(target) = $target_nid
            MERGE (u)-[nr:FEEDBACK_RECOMENDACAO]->(target)
            SET nr.score = coalesce(nr.score, 0) + coalesce(r.score, 0),
                nr.ultima_data = coalesce(nr.ultima_data, r.ultima_data),
                nr.comentario = coalesce(nr.comentario, r.comentario)
            DELETE r
            """,
            source_nid=source_nid,
            target_nid=target_nid,
        )
        session.run(
            """
            MATCH (u:Usuario)-[r:EM_PROGRESSO]->(source:Anime) WHERE id(source) = $source_nid
            MATCH (target:Anime) WHERE id(target) = $target_nid
            MERGE (u)-[nr:EM_PROGRESSO]->(target)
            SET nr.episodio = coalesce(nr.episodio, r.episodio),
                nr.capitulo = coalesce(nr.capitulo, r.capitulo),
                nr.porcentagem = coalesce(nr.porcentagem, r.porcentagem),
                nr.formato = coalesce(nr.formato, r.formato),
                nr.atualizado_em = coalesce(nr.atualizado_em, r.atualizado_em)
            DELETE r
            """,
            source_nid=source_nid,
            target_nid=target_nid,
        )
        session.run(
            """
            MATCH (source:Anime)-[r:TEM_GENERO]->(g:Genero) WHERE id(source) = $source_nid
            MATCH (target:Anime) WHERE id(target) = $target_nid
            MERGE (target)-[:TEM_GENERO]->(g)
            DELETE r
            """,
            source_nid=source_nid,
            target_nid=target_nid,
        )
        session.run(
            """
            MATCH (source:Anime)-[r:TEM_TEMA]->(t:Tema) WHERE id(source) = $source_nid
            MATCH (target:Anime) WHERE id(target) = $target_nid
            MERGE (target)-[:TEM_TEMA]->(t)
            DELETE r
            """,
            source_nid=source_nid,
            target_nid=target_nid,
        )
        session.run(
            """
            MATCH (source:Anime)-[r:PRODUZIDO_POR]->(e:Estudio) WHERE id(source) = $source_nid
            MATCH (target:Anime) WHERE id(target) = $target_nid
            MERGE (target)-[:PRODUZIDO_POR]->(e)
            DELETE r
            """,
            source_nid=source_nid,
            target_nid=target_nid,
        )
        session.run(
            """
            MATCH (source:Anime)-[r:TEM_FORMATO]->(fo:Formato) WHERE id(source) = $source_nid
            MATCH (target:Anime) WHERE id(target) = $target_nid
            MERGE (target)-[:TEM_FORMATO]->(fo)
            DELETE r
            """,
            source_nid=source_nid,
            target_nid=target_nid,
        )
        session.run(
            """
            MATCH (source:Anime)-[r:PERTENCE_A]->(f:Franquia) WHERE id(source) = $source_nid
            MATCH (target:Anime) WHERE id(target) = $target_nid
            MERGE (target)-[:PERTENCE_A]->(f)
            DELETE r
            """,
            source_nid=source_nid,
            target_nid=target_nid,
        )
        session.run(
            """
            MATCH (source:Anime) WHERE id(source) = $source_nid
            MATCH (target:Anime) WHERE id(target) = $target_nid
            SET target:Obra,
                target.titulo = coalesce(target.titulo, source.titulo),
                target.titulo_key = coalesce(target.titulo_key, source.titulo_key),
                target.tipo = coalesce(target.tipo, source.tipo),
                target.formato_principal = coalesce(target.formato_principal, source.formato_principal),
                target.episodios = coalesce(target.episodios, source.episodios),
                target.status = coalesce(target.status, source.status),
                target.ano = coalesce(target.ano, source.ano),
                target.nota_mal = coalesce(target.nota_mal, source.nota_mal),
                target.sinopse = coalesce(target.sinopse, source.sinopse)
            DETACH DELETE source
            """,
            source_nid=source_nid,
            target_nid=target_nid,
        )

    def _ensure_anime_node(self, session, titulo: str, formato: str | None = None) -> dict:
        title = self._clean_name(titulo)
        key = self._normalize_title(title)
        if not key:
            raise ValueError("Titulo invalido para Anime")

        query = """
        MERGE (a:Anime {titulo_key: $titulo_key})
        ON CREATE SET a.id = $fallback_id, a.titulo = $titulo, a.criado_em = datetime()
        SET a:Obra,
            a.titulo = coalesce(a.titulo, $titulo),
            a.formato_principal = coalesce(a.formato_principal, $formato, "anime"),
            a.atualizado_em = datetime()
        RETURN a.id AS id, a.titulo AS titulo, a.titulo_key AS titulo_key
        """
        rec = session.run(
            query,
            titulo_key=key,
            fallback_id=key,
            titulo=title,
            formato=(formato or "").strip().lower() or None,
        ).single()
        return {
            "id": rec.get("id"),
            "titulo": rec.get("titulo"),
            "titulo_key": rec.get("titulo_key"),
        }
    # Usuario -----------------------------------------------------------------

    def get_or_create_user(self, telegram_id: str) -> dict:
        cypher = """
        MERGE (u:Usuario {telegram_id: $telegram_id})
        ON CREATE SET
          u.criado_em = datetime(),
          u.preferencia_audio = "indiferente",
          u.alerta_generos = [],
          u.alerta_estudios = [],
          u.permitir_nsfw = false
        RETURN u
        """
        with self.driver.session() as session:
            result = session.run(cypher, telegram_id=telegram_id)
            record = result.single()
            if record:
                return dict(record["u"])
        return {"telegram_id": telegram_id}

    @staticmethod
    def _week_ref() -> str:
        now = datetime.datetime.now()
        week = now.isocalendar().week
        return f"{now.year}-W{week:02d}"

    def set_mood_diario(self, telegram_id: str, mood: str):
        mood_clean = self._clean_name(mood).lower()
        if not mood_clean:
            return
        with self.driver.session() as session:
            session.run(
                """
                MERGE (u:Usuario {telegram_id: $telegram_id})
                SET u.mood_diario = $mood, u.mood_atualizado_em = datetime()
                """,
                telegram_id=telegram_id,
                mood=mood_clean,
            )

    def set_tempo_disponivel(self, telegram_id: str, minutos: int | None):
        with self.driver.session() as session:
            session.run(
                """
                MERGE (u:Usuario {telegram_id: $telegram_id})
                SET u.tempo_disponivel_min = $minutos,
                    u.tempo_atualizado_em = datetime()
                """,
                telegram_id=telegram_id,
                minutos=minutos,
            )

    def set_filtros_maturidade(
        self,
        telegram_id: str,
        permitir_nsfw: bool | None = None,
        limite_violencia: str | None = None,
        limite_ecchi: str | None = None,
    ):
        with self.driver.session() as session:
            session.run(
                """
                MERGE (u:Usuario {telegram_id: $telegram_id})
                SET
                  u.permitir_nsfw = CASE
                    WHEN $permitir_nsfw IS NULL THEN coalesce(u.permitir_nsfw, false)
                    ELSE $permitir_nsfw
                  END,
                  u.limite_violencia = CASE
                    WHEN $limite_violencia IS NULL THEN u.limite_violencia
                    ELSE $limite_violencia
                  END,
                  u.limite_ecchi = CASE
                    WHEN $limite_ecchi IS NULL THEN u.limite_ecchi
                    ELSE $limite_ecchi
                  END,
                  u.maturidade_atualizada_em = datetime()
                """,
                telegram_id=telegram_id,
                permitir_nsfw=permitir_nsfw,
                limite_violencia=(limite_violencia or "").strip().lower() or None,
                limite_ecchi=(limite_ecchi or "").strip().lower() or None,
            )

    def set_preferencia_audio(self, telegram_id: str, preferencia: str):
        pref = (preferencia or "").strip().lower()
        if pref not in {"dublado", "legendado", "indiferente"}:
            return
        with self.driver.session() as session:
            session.run(
                """
                MERGE (u:Usuario {telegram_id: $telegram_id})
                SET u.preferencia_audio = $pref, u.audio_atualizado_em = datetime()
                """,
                telegram_id=telegram_id,
                pref=pref,
            )

    def set_alertas(self, telegram_id: str, generos: list[str] | None = None, estudios: list[str] | None = None):
        generos_clean = self._clean_list(generos or [])
        estudios_clean = self._clean_list(estudios or [])
        with self.driver.session() as session:
            session.run(
                """
                MERGE (u:Usuario {telegram_id: $telegram_id})
                SET
                  u.alerta_generos = CASE
                    WHEN size($generos) = 0 THEN coalesce(u.alerta_generos, [])
                    ELSE $generos
                  END,
                  u.alerta_estudios = CASE
                    WHEN size($estudios) = 0 THEN coalesce(u.alerta_estudios, [])
                    ELSE $estudios
                  END,
                  u.alerta_atualizado_em = datetime()
                """,
                telegram_id=telegram_id,
                generos=generos_clean,
                estudios=estudios_clean,
            )

    def set_desafio_semanal(self, telegram_id: str, texto: str):
        desafio = self._clean_name(texto)
        if not desafio:
            return
        with self.driver.session() as session:
            session.run(
                """
                MERGE (u:Usuario {telegram_id: $telegram_id})
                SET u.desafio_semanal = $desafio,
                    u.desafio_semana_ref = $week_ref,
                    u.desafio_atualizado_em = datetime()
                """,
                telegram_id=telegram_id,
                desafio=desafio,
                week_ref=self._week_ref(),
            )

    def get_user_settings(self, telegram_id: str) -> dict:
        cypher = """
        MATCH (u:Usuario {telegram_id: $telegram_id})
        RETURN
          u.mood_diario AS mood_diario,
          u.tempo_disponivel_min AS tempo_disponivel_min,
          coalesce(u.permitir_nsfw, false) AS permitir_nsfw,
          u.limite_violencia AS limite_violencia,
          u.limite_ecchi AS limite_ecchi,
          coalesce(u.preferencia_audio, "indiferente") AS preferencia_audio,
          coalesce(u.alerta_generos, []) AS alerta_generos,
          coalesce(u.alerta_estudios, []) AS alerta_estudios,
          u.desafio_semanal AS desafio_semanal,
          u.desafio_semana_ref AS desafio_semana_ref
        """
        with self.driver.session() as session:
            rec = session.run(cypher, telegram_id=telegram_id).single()
            if not rec:
                return {
                    "mood_diario": None,
                    "tempo_disponivel_min": None,
                    "permitir_nsfw": False,
                    "limite_violencia": None,
                    "limite_ecchi": None,
                    "preferencia_audio": "indiferente",
                    "alerta_generos": [],
                    "alerta_estudios": [],
                    "desafio_semanal": None,
                    "desafio_semana_ref": None,
                }
            return {
                "mood_diario": rec.get("mood_diario"),
                "tempo_disponivel_min": rec.get("tempo_disponivel_min"),
                "permitir_nsfw": bool(rec.get("permitir_nsfw")),
                "limite_violencia": rec.get("limite_violencia"),
                "limite_ecchi": rec.get("limite_ecchi"),
                "preferencia_audio": rec.get("preferencia_audio") or "indiferente",
                "alerta_generos": rec.get("alerta_generos") or [],
                "alerta_estudios": rec.get("alerta_estudios") or [],
                "desafio_semanal": rec.get("desafio_semanal"),
                "desafio_semana_ref": rec.get("desafio_semana_ref"),
            }

    def registrar_recomendacoes(self, telegram_id: str, titulos: list[str]):
        limpos = self._clean_list(titulos or [])
        if not limpos:
            return
        with self.driver.session() as session:
            session.run(
                "MERGE (:Usuario {telegram_id: $telegram_id})",
                telegram_id=telegram_id,
            )
            for titulo in limpos:
                anime_ref = self._ensure_anime_node(session, titulo=titulo, formato="anime")
                session.run(
                    """
                    MATCH (u:Usuario {telegram_id: $telegram_id})
                    MATCH (a:Anime {titulo_key: $titulo_key})
                    MERGE (u)-[r:RECOMENDADO]->(a)
                    SET r.freq = coalesce(r.freq, 0) + 1,
                        r.ultima_data = datetime()
                    """,
                    telegram_id=telegram_id,
                    titulo_key=anime_ref["titulo_key"],
                )

    def get_recomendados_recentes(self, telegram_id: str, days: int = 30) -> list[str]:
        cypher = """
        MATCH (u:Usuario {telegram_id: $telegram_id})-[r:RECOMENDADO]->(a:Anime)
        WHERE r.ultima_data >= datetime() - duration({days: $days})
        RETURN a.titulo AS titulo
        ORDER BY r.ultima_data DESC
        LIMIT 40
        """
        with self.driver.session() as session:
            rows = session.run(cypher, telegram_id=telegram_id, days=max(days, 1))
            return [row.get("titulo") for row in rows if row.get("titulo")]

    def registrar_feedback_recomendacao(
        self,
        telegram_id: str,
        titulo: str,
        curti: bool,
        comentario: str | None = None,
    ):
        score_delta = 1 if curti else -1
        with self.driver.session() as session:
            anime_ref = self._ensure_anime_node(session, titulo=titulo, formato="anime")
            session.run(
                """
                MATCH (u:Usuario {telegram_id: $telegram_id})
                MATCH (a:Anime {titulo_key: $titulo_key})
                MERGE (u)-[r:FEEDBACK_RECOMENDACAO]->(a)
                SET r.score = coalesce(r.score, 0) + $delta,
                    r.ultima_data = datetime(),
                    r.comentario = coalesce($comentario, r.comentario)
                """,
                telegram_id=telegram_id,
                titulo_key=anime_ref["titulo_key"],
                delta=score_delta,
                comentario=(comentario or "").strip() or None,
            )
            session.run(
                """
                MATCH (u:Usuario {telegram_id: $telegram_id})-[f:FEEDBACK_RECOMENDACAO]->(a:Anime)
                WHERE f.score <= -1
                MATCH (a)-[:TEM_GENERO]->(g:Genero)
                MERGE (u)-[e:EVITAR_GENERO]->(g)
                SET e.freq = coalesce(e.freq, 0) + 1,
                    e.atualizado_em = datetime()
                """,
                telegram_id=telegram_id,
            )

    def get_feedback_memoria(self, telegram_id: str) -> dict:
        pos_q = """
        MATCH (u:Usuario {telegram_id: $telegram_id})-[f:FEEDBACK_RECOMENDACAO]->(a:Anime)
        WHERE f.score > 0
        RETURN a.titulo AS titulo, f.score AS score
        ORDER BY f.score DESC, f.ultima_data DESC
        LIMIT 10
        """
        neg_q = """
        MATCH (u:Usuario {telegram_id: $telegram_id})-[f:FEEDBACK_RECOMENDACAO]->(a:Anime)
        WHERE f.score < 0
        RETURN a.titulo AS titulo, f.score AS score
        ORDER BY f.score ASC, f.ultima_data DESC
        LIMIT 10
        """
        with self.driver.session() as session:
            pos = [row.get("titulo") for row in session.run(pos_q, telegram_id=telegram_id) if row.get("titulo")]
            neg = [row.get("titulo") for row in session.run(neg_q, telegram_id=telegram_id) if row.get("titulo")]
            return {"curtidos": pos, "evitar": neg}

    def get_ranking_pessoal(self, telegram_id: str, limit: int = 10) -> list[dict]:
        cypher = """
        MATCH (u:Usuario {telegram_id: $telegram_id})-[r:ASSISTIU]->(a:Anime)
        RETURN a.titulo AS titulo, r.nota AS nota, r.data AS data
        ORDER BY coalesce(toFloat(r.nota), 0.0) DESC, r.data DESC
        LIMIT $limit
        """
        with self.driver.session() as session:
            rows = session.run(cypher, telegram_id=telegram_id, limit=max(1, limit))
            ranking = []
            for row in rows:
                titulo = row.get("titulo")
                if not titulo:
                    continue
                ranking.append(
                    {
                        "titulo": titulo,
                        "nota": row.get("nota"),
                        "data": self._to_iso(row.get("data")),
                    }
                )
            return ranking

    def get_watchlist_inteligente(self, telegram_id: str, limit: int = 8) -> list[dict]:
        settings = self.get_user_settings(telegram_id)
        tempo = settings.get("tempo_disponivel_min")

        cypher = """
        MATCH (u:Usuario {telegram_id: $telegram_id})-[:QUER_VER]->(a:Anime)
        WHERE NOT (u)-[:ASSISTIU]->(a) AND NOT (u)-[:DROPOU]->(a)
        OPTIONAL MATCH (a)-[:TEM_GENERO]->(g:Genero)
        OPTIONAL MATCH (a)-[:PRODUZIDO_POR]->(e:Estudio)
        OPTIONAL MATCH (u)-[pg:PREFERE_GENERO]->(g)
        RETURN
          a.titulo AS titulo,
          a.episodios AS episodios,
          a.nota_mal AS nota_mal,
          collect(DISTINCT g.nome) AS generos,
          e.nome AS estudio,
          coalesce(sum(pg.score), 0.0) AS afinidade
        """
        with self.driver.session() as session:
            rows = session.run(cypher, telegram_id=telegram_id)
            items = []
            for row in rows:
                titulo = row.get("titulo")
                if not titulo:
                    continue
                episodios = row.get("episodios")
                nota_mal = row.get("nota_mal") or 0.0
                afinidade = float(row.get("afinidade") or 0.0)
                score = afinidade + (float(nota_mal or 0.0) * 0.35)

                if isinstance(tempo, int) and tempo > 0 and isinstance(episodios, int) and episodios > 0:
                    if tempo <= 40 and episodios <= 13:
                        score += 2.0
                    if tempo <= 25 and episodios > 24:
                        score -= 1.5

                items.append(
                    {
                        "titulo": titulo,
                        "episodios": episodios,
                        "nota_mal": nota_mal,
                        "generos": [g for g in (row.get("generos") or []) if g],
                        "estudio": row.get("estudio"),
                        "score_watchlist": round(score, 2),
                    }
                )

            items.sort(key=lambda x: x.get("score_watchlist", 0), reverse=True)
            return items[: max(1, limit)]

    def gerar_desafio_semanal(self, telegram_id: str) -> str:
        profile = self.get_user_profile(telegram_id)
        progresso = profile.get("progresso", [])
        quer_ver = profile.get("quer_ver", [])
        dropados = profile.get("dropados", [])

        if progresso:
            base = progresso[0].get("titulo")
            desafio = f"Avancar pelo menos 3 episodios/capitulos em {base} ate domingo."
        elif quer_ver:
            base = quer_ver[0].get("titulo")
            desafio = f"Comecar {base} e registrar sua primeira impressao com nota parcial."
        elif dropados:
            base = dropados[0].get("titulo")
            desafio = f"Dar segunda chance para {base} por 2 episodios antes de decidir drop final."
        else:
            desafio = "Testar 1 obra nova fora do seu genero favorito e registrar feedback."

        self.set_desafio_semanal(telegram_id, desafio)
        return desafio

    def get_franchise_timeline(self, titulo: str) -> dict | None:
        route = get_franchise_route(titulo)
        if route:
            return route

        titulo_key = self._normalize_title(titulo)
        if not titulo_key:
            return None

        cypher = """
        MATCH (a:Anime)-[:PERTENCE_A]->(f:Franquia)
        WHERE a.titulo_key CONTAINS $titulo_key OR toLower(a.titulo) CONTAINS $titulo_key
        RETURN f.nome AS franquia, f.pos_obra AS pos_obra, f.ponte_animemanga AS ponte
        LIMIT 1
        """
        with self.driver.session() as session:
            rec = session.run(cypher, titulo_key=titulo_key).single()
            if not rec:
                return None
            return {
                "franquia": rec.get("franquia"),
                "pos_obra": rec.get("pos_obra") or [],
                "ponte_animemanga": rec.get("ponte") or [],
            }

    def get_resumo_retorno(self, telegram_id: str, titulo: str | None = None) -> dict | None:
        base_where = ""
        params = {"telegram_id": telegram_id}
        if titulo:
            params["titulo_key"] = self._normalize_title(titulo)
            base_where = "AND a.titulo_key = $titulo_key"

        cypher = f"""
        MATCH (u:Usuario {{telegram_id: $telegram_id}})-[p:EM_PROGRESSO]->(a:Anime)
        WHERE 1=1 {base_where}
        OPTIONAL MATCH (a)-[:TEM_GENERO]->(g:Genero)
        RETURN
          a.titulo AS titulo,
          a.sinopse AS sinopse,
          a.episodios AS episodios_total,
          p.episodio AS episodio_atual,
          p.capitulo AS capitulo_atual,
          p.porcentagem AS porcentagem,
          collect(DISTINCT g.nome) AS generos
        ORDER BY p.atualizado_em DESC
        LIMIT 1
        """
        with self.driver.session() as session:
            rec = session.run(cypher, **params).single()
            if not rec:
                return None
            return {
                "titulo": rec.get("titulo"),
                "sinopse": rec.get("sinopse") or "",
                "episodios_total": rec.get("episodios_total"),
                "episodio_atual": rec.get("episodio_atual"),
                "capitulo_atual": rec.get("capitulo_atual"),
                "porcentagem": rec.get("porcentagem"),
                "generos": [g for g in (rec.get("generos") or []) if g],
            }

    def refresh_user_taste_links(self, telegram_id: str):
        with self.driver.session() as session:
            session.run(
                """
                MATCH (u:Usuario {telegram_id: $telegram_id})-[r:PREFERE_GENERO]->(:Genero)
                DELETE r
                """,
                telegram_id=telegram_id,
            )
            session.run(
                """
                MATCH (u:Usuario {telegram_id: $telegram_id})-[r:PREFERE_TEMA]->(:Tema)
                DELETE r
                """,
                telegram_id=telegram_id,
            )
            session.run(
                """
                MATCH (u:Usuario {telegram_id: $telegram_id})-[a:ASSISTIU]->(:Anime)-[:TEM_GENERO]->(g:Genero)
                WITH u, g, count(*) AS freq, avg(coalesce(toFloat(a.nota), 7.0)) AS score
                MERGE (u)-[r:PREFERE_GENERO]->(g)
                SET r.freq = freq, r.score = round(score * 100.0) / 100.0, r.atualizado_em = datetime()
                """,
                telegram_id=telegram_id,
            )
            session.run(
                """
                MATCH (u:Usuario {telegram_id: $telegram_id})-[a:ASSISTIU]->(:Anime)-[:TEM_TEMA]->(t:Tema)
                WITH u, t, count(*) AS freq, avg(coalesce(toFloat(a.nota), 7.0)) AS score
                MERGE (u)-[r:PREFERE_TEMA]->(t)
                SET r.freq = freq, r.score = round(score * 100.0) / 100.0, r.atualizado_em = datetime()
                """,
                telegram_id=telegram_id,
            )

    def get_user_profile(self, telegram_id: str) -> dict:
        self.refresh_user_taste_links(telegram_id)

        cypher = """
        MATCH (u:Usuario {telegram_id: $telegram_id})
        OPTIONAL MATCH (u)-[r:ASSISTIU]->(a:Anime)
        OPTIONAL MATCH (u)-[d:DROPOU]->(da:Anime)
        OPTIONAL MATCH (u)-[:QUER_VER]->(qv:Anime)
        OPTIONAL MATCH (u)-[p:EM_PROGRESSO]->(pa:Anime)
        OPTIONAL MATCH (u)-[pg:PREFERE_GENERO]->(g:Genero)
        OPTIONAL MATCH (u)-[pt:PREFERE_TEMA]->(t:Tema)
        RETURN
          collect(DISTINCT {titulo: a.titulo, nota: r.nota, data: r.data}) AS assistidos,
          collect(DISTINCT {titulo: da.titulo, episodio: d.episodio, data: d.data}) AS dropados,
          collect(DISTINCT {titulo: qv.titulo}) AS quer_ver,
          collect(
            DISTINCT {
              titulo: pa.titulo,
              episodio: p.episodio,
              capitulo: p.capitulo,
              porcentagem: p.porcentagem,
              formato: p.formato,
              atualizado_em: p.atualizado_em
            }
          ) AS progresso,
          collect(DISTINCT {genero: g.nome, score: pg.score, freq: pg.freq}) AS genero_pref,
          collect(DISTINCT {tema: t.nome, score: pt.score, freq: pt.freq}) AS tema_pref
        """
        with self.driver.session() as session:
            result = session.run(cypher, telegram_id=telegram_id)
            record = result.single()
            if not record:
                return {}

            assistidos = [
                {**item, "data": self._to_iso(item.get("data"))}
                for item in record["assistidos"]
                if item.get("titulo")
            ]
            dropados = [
                {**item, "data": self._to_iso(item.get("data"))}
                for item in record["dropados"]
                if item.get("titulo")
            ]
            quer_ver = [item for item in record["quer_ver"] if item.get("titulo")]
            progresso = [
                {**item, "atualizado_em": self._to_iso(item.get("atualizado_em"))}
                for item in record["progresso"]
                if item.get("titulo")
            ]

            generos_favoritos = [
                item["genero"]
                for item in sorted(
                    [g for g in record["genero_pref"] if g.get("genero")],
                    key=lambda g: (g.get("score") or 0, g.get("freq") or 0),
                    reverse=True,
                )[:5]
            ]
            temas_favoritos = [
                item["tema"]
                for item in sorted(
                    [t for t in record["tema_pref"] if t.get("tema")],
                    key=lambda t: (t.get("score") or 0, t.get("freq") or 0),
                    reverse=True,
                )[:5]
            ]

            drop_patterns = self.get_drop_patterns(telegram_id)
            settings = self.get_user_settings(telegram_id)
            feedback = self.get_feedback_memoria(telegram_id)
            recomendados_recentes = self.get_recomendados_recentes(telegram_id, days=21)
            mood_inferido = self._infer_mood(assistidos, dropados, progresso)
            mood_atual = settings.get("mood_diario") or mood_inferido
            queda_interesse = self._avaliar_queda_interesse(
                assistidos=assistidos,
                dropados=dropados,
                progresso=progresso,
                drop_patterns=drop_patterns,
            )

            return {
                "assistidos": assistidos,
                "dropados": dropados,
                "quer_ver": quer_ver,
                "progresso": sorted(
                    progresso,
                    key=lambda item: item.get("atualizado_em") or "",
                    reverse=True,
                ),
                "generos_favoritos": generos_favoritos,
                "temas_favoritos": temas_favoritos,
                "drop_patterns": drop_patterns,
                "feedback_memoria": feedback,
                "recomendados_recentes": recomendados_recentes,
                "preferencia_audio": settings.get("preferencia_audio"),
                "tempo_disponivel_min": settings.get("tempo_disponivel_min"),
                "filtros_maturidade": {
                    "permitir_nsfw": settings.get("permitir_nsfw"),
                    "limite_violencia": settings.get("limite_violencia"),
                    "limite_ecchi": settings.get("limite_ecchi"),
                },
                "alerta_generos": settings.get("alerta_generos", []),
                "alerta_estudios": settings.get("alerta_estudios", []),
                "desafio_semanal": settings.get("desafio_semanal"),
                "mood_diario": settings.get("mood_diario"),
                "mood_atual": mood_atual,
                "queda_interesse": queda_interesse,
            }

    def _infer_mood(self, assistidos: list[dict], dropados: list[dict], progresso: list[dict]) -> str:
        notas = [item.get("nota") for item in assistidos if isinstance(item.get("nota"), (int, float))]
        media = (sum(notas) / len(notas)) if notas else None

        drops_recentes = 0
        for item in dropados[:5]:
            if item.get("data"):
                drops_recentes += 1

        em_andamento = len(progresso)
        if media is not None and media >= 8.3 and drops_recentes == 0:
            return "empolgado"
        if media is not None and media <= 6.5:
            return "frustrado"
        if drops_recentes >= 3:
            return "desanimado"
        if em_andamento >= 4:
            return "saturado"
        return "neutro"

    def _avaliar_queda_interesse(
        self,
        assistidos: list[dict],
        dropados: list[dict],
        progresso: list[dict],
        drop_patterns: dict,
    ) -> dict:
        total_assistidos = len(assistidos)
        total_dropados = len(dropados)
        em_andamento = len(progresso)
        ratio = 0.0
        if (total_assistidos + total_dropados) > 0:
            ratio = total_dropados / (total_assistidos + total_dropados)

        nivel = "baixo"
        if ratio >= 0.45 or drop_patterns.get("risk_level") == "alto":
            nivel = "alto"
        elif ratio >= 0.3 or drop_patterns.get("risk_level") == "medio":
            nivel = "medio"

        sinais = []
        if total_dropados >= 3:
            sinais.append("muitos drops recentes")
        if em_andamento >= 5:
            sinais.append("backlog alto em progresso")
        if ratio >= 0.35:
            sinais.append("taxa de drop acima do ideal")

        sugestao = "Mantenha o ritmo atual."
        if nivel == "medio":
            sugestao = "Priorize obras curtas e de generos que voce ja gosta."
        elif nivel == "alto":
            sugestao = "Pause obras longas e foque em 1 titulo curto para recuperar ritmo."

        return {
            "nivel": nivel,
            "ratio_drop": round(ratio, 2),
            "sinais": sinais,
            "sugestao": sugestao,
        }
    # Registro de interacoes ---------------------------------------------------

    def registrar_assistido(self, telegram_id: str, titulo: str, nota: float | None = None):
        with self.driver.session() as session:
            anime_ref = self._ensure_anime_node(session, titulo=titulo, formato="anime")
            cypher = """
            MATCH (u:Usuario {telegram_id: $telegram_id})
            MATCH (a:Anime {titulo_key: $titulo_key})
            MERGE (u)-[r:ASSISTIU]->(a)
            SET r.nota = $nota, r.data = datetime()
            WITH u, a
            OPTIONAL MATCH (u)-[p:EM_PROGRESSO]->(a)
            DELETE p
            """
            session.run(
                cypher,
                telegram_id=telegram_id,
                titulo_key=anime_ref["titulo_key"],
                nota=nota,
            )
        self.refresh_user_taste_links(telegram_id)
        logger.info("Registrado assistido: user=%s titulo=%s nota=%s", telegram_id, titulo, nota)

    def registrar_drop(self, telegram_id: str, titulo: str, episodio: int | None = None):
        ep_final = episodio
        with self.driver.session() as session:
            anime_ref = self._ensure_anime_node(session, titulo=titulo, formato="anime")
            if ep_final is None:
                ep_query = """
                MATCH (u:Usuario {telegram_id: $telegram_id})-[p:EM_PROGRESSO]->(a:Anime {titulo_key: $titulo_key})
                RETURN p.episodio AS episodio
                """
                rec = session.run(
                    ep_query,
                    telegram_id=telegram_id,
                    titulo_key=anime_ref["titulo_key"],
                ).single()
                if rec:
                    ep_final = rec.get("episodio")

            cypher = """
            MATCH (u:Usuario {telegram_id: $telegram_id})
            MATCH (a:Anime {titulo_key: $titulo_key})
            MERGE (u)-[r:DROPOU]->(a)
            SET r.episodio = $episodio, r.data = datetime()
            WITH u, a
            OPTIONAL MATCH (u)-[p:EM_PROGRESSO]->(a)
            DELETE p
            """
            session.run(
                cypher,
                telegram_id=telegram_id,
                titulo_key=anime_ref["titulo_key"],
                episodio=ep_final,
            )
        logger.info("Registrado drop: user=%s titulo=%s ep=%s", telegram_id, titulo, ep_final)

    def registrar_quer_ver(self, telegram_id: str, titulo: str):
        with self.driver.session() as session:
            anime_ref = self._ensure_anime_node(session, titulo=titulo, formato="anime")
            cypher = """
            MATCH (u:Usuario {telegram_id: $telegram_id})
            MATCH (a:Anime {titulo_key: $titulo_key})
            MERGE (u)-[:QUER_VER]->(a)
            """
            session.run(cypher, telegram_id=telegram_id, titulo_key=anime_ref["titulo_key"])
        logger.info("Registrado quer ver: user=%s titulo=%s", telegram_id, titulo)

    def registrar_progresso(
        self,
        telegram_id: str,
        titulo: str,
        episodio: int | None = None,
        capitulo: int | None = None,
        porcentagem: float | None = None,
        formato: str | None = None,
    ):
        with self.driver.session() as session:
            anime_ref = self._ensure_anime_node(session, titulo=titulo, formato=formato or "anime")
            cypher = """
            MATCH (u:Usuario {telegram_id: $telegram_id})
            MATCH (a:Anime {titulo_key: $titulo_key})
            MERGE (u)-[r:EM_PROGRESSO]->(a)
            SET
              r.formato = coalesce($formato, r.formato, "anime"),
              r.episodio = CASE WHEN $episodio IS NULL THEN r.episodio ELSE $episodio END,
              r.capitulo = CASE WHEN $capitulo IS NULL THEN r.capitulo ELSE $capitulo END,
              r.porcentagem = CASE WHEN $porcentagem IS NULL THEN r.porcentagem ELSE $porcentagem END,
              r.atualizado_em = datetime()
            """
            session.run(
                cypher,
                telegram_id=telegram_id,
                titulo_key=anime_ref["titulo_key"],
                episodio=episodio,
                capitulo=capitulo,
                porcentagem=porcentagem,
                formato=(formato or "").strip().lower() or None,
            )
        logger.info(
            "Registrado progresso: user=%s titulo=%s ep=%s cap=%s pct=%s formato=%s",
            telegram_id,
            titulo,
            episodio,
            capitulo,
            porcentagem,
            formato,
        )

    def atualizar_nota(self, telegram_id: str, titulo: str, nota: float) -> bool:
        titulo_key = self._normalize_title(titulo)
        cypher = """
        MATCH (u:Usuario {telegram_id: $telegram_id})-[r:ASSISTIU]->(a:Anime {titulo_key: $titulo_key})
        SET r.nota = $nota, r.atualizado_em = datetime()
        RETURN r
        """
        with self.driver.session() as session:
            result = session.run(
                cypher,
                telegram_id=telegram_id,
                titulo_key=titulo_key,
                nota=nota,
            )
            ok = result.single() is not None
        if ok:
            self.refresh_user_taste_links(telegram_id)
        return ok
    def get_user_progress(self, telegram_id: str) -> list[dict]:
        cypher = """
        MATCH (u:Usuario {telegram_id: $telegram_id})-[r:EM_PROGRESSO]->(a:Anime)
        RETURN
          a.titulo AS titulo,
          r.formato AS formato,
          r.episodio AS episodio,
          r.capitulo AS capitulo,
          r.porcentagem AS porcentagem,
          r.atualizado_em AS atualizado_em
        ORDER BY r.atualizado_em DESC
        """
        with self.driver.session() as session:
            rows = session.run(cypher, telegram_id=telegram_id)
            return [
                {
                    "titulo": row.get("titulo"),
                    "formato": row.get("formato"),
                    "episodio": row.get("episodio"),
                    "capitulo": row.get("capitulo"),
                    "porcentagem": row.get("porcentagem"),
                    "atualizado_em": self._to_iso(row.get("atualizado_em")),
                }
                for row in rows
                if row.get("titulo")
            ]

    def get_drop_patterns(self, telegram_id: str) -> dict:
        totals_query = """
        MATCH (u:Usuario {telegram_id: $telegram_id})
        OPTIONAL MATCH (u)-[d:DROPOU]->(:Anime)
        WITH u, count(d) AS total_drops, avg(toFloat(d.episodio)) AS avg_drop_episode
        OPTIONAL MATCH (u)-[a:ASSISTIU]->(:Anime)
        RETURN total_drops, coalesce(avg_drop_episode, 0.0) AS avg_drop_episode, count(a) AS total_assistidos
        """
        genres_query = """
        MATCH (u:Usuario {telegram_id: $telegram_id})-[:DROPOU]->(:Anime)-[:TEM_GENERO]->(g:Genero)
        RETURN g.nome AS genero, count(*) AS qtd
        ORDER BY qtd DESC
        LIMIT 4
        """
        recent_query = """
        MATCH (u:Usuario {telegram_id: $telegram_id})-[d:DROPOU]->(a:Anime)
        RETURN a.titulo AS titulo, d.episodio AS episodio, d.data AS data
        ORDER BY d.data DESC
        LIMIT 5
        """
        with self.driver.session() as session:
            totals = session.run(totals_query, telegram_id=telegram_id).single()
            if not totals:
                return {
                    "total_drops": 0,
                    "drop_ratio": 0.0,
                    "avg_drop_episode": 0.0,
                    "top_drop_genres": [],
                    "recent_drops": [],
                    "risk_level": "baixo",
                }

            total_drops = int(totals.get("total_drops") or 0)
            total_assistidos = int(totals.get("total_assistidos") or 0)
            avg_drop_episode = float(totals.get("avg_drop_episode") or 0.0)

            denom = max(total_assistidos + total_drops, 1)
            drop_ratio = total_drops / denom

            top_drop_genres = [
                {"genero": row.get("genero"), "qtd": int(row.get("qtd") or 0)}
                for row in session.run(genres_query, telegram_id=telegram_id)
                if row.get("genero")
            ]
            recent_drops = [
                {
                    "titulo": row.get("titulo"),
                    "episodio": row.get("episodio"),
                    "data": self._to_iso(row.get("data")),
                }
                for row in session.run(recent_query, telegram_id=telegram_id)
                if row.get("titulo")
            ]

            risk_level = "baixo"
            if total_drops >= 8 or drop_ratio >= 0.5 or (total_drops >= 3 and avg_drop_episode <= 5):
                risk_level = "alto"
            elif total_drops >= 4 or drop_ratio >= 0.3:
                risk_level = "medio"

            return {
                "total_drops": total_drops,
                "drop_ratio": round(drop_ratio, 2),
                "avg_drop_episode": round(avg_drop_episode, 2),
                "top_drop_genres": top_drop_genres,
                "recent_drops": recent_drops,
                "risk_level": risk_level,
            }

    # Anime data ---------------------------------------------------------------

    def upsert_anime(self, anime: dict):
        anime_id = str(anime.get("id", "")).strip()
        titulo = self._clean_name(anime.get("titulo", ""))
        titulo_key = self._normalize_title(titulo or anime_id)
        if not anime_id or not titulo_key:
            return

        props = {
            "titulo": titulo or anime_id,
            "titulo_key": titulo_key,
            "tipo": anime.get("tipo", "") or "anime",
            "formato_principal": self._clean_name(anime.get("tipo", "anime")).lower() or "anime",
            "episodios": anime.get("episodios"),
            "status": anime.get("status", ""),
            "ano": anime.get("ano"),
            "nota_mal": anime.get("nota_mal"),
            "sinopse": anime.get("synopsis", ""),
        }

        generos = self._clean_list(anime.get("generos", []))
        temas = self._clean_list(anime.get("temas", []))
        estudio = self._clean_name(anime.get("estudio", "Desconhecido")) or "Desconhecido"
        formato_nome = props["formato_principal"]

        with self.driver.session() as session:
            by_id = session.run("MATCH (a:Anime {id: $id}) RETURN id(a) AS nid", id=anime_id).single()
            by_key = session.run(
                "MATCH (a:Anime {titulo_key: $titulo_key}) RETURN id(a) AS nid, a.id AS existing_id",
                titulo_key=titulo_key,
            ).single()

            if by_key and (not by_id):
                # Reaproveita node por titulo_key para evitar duplicado "id numerico x id texto".
                session.run(
                    "MATCH (a:Anime) WHERE id(a) = $nid SET a.id = $id",
                    nid=by_key["nid"],
                    id=anime_id,
                )
            elif by_id and by_key and by_id["nid"] != by_key["nid"]:
                # Se os dois existem, consolida tudo no node por ID real.
                self._merge_anime_nodes(session, source_nid=by_key["nid"], target_nid=by_id["nid"])

            session.run(
                """
                MERGE (a:Anime {id: $id})
                SET a += $props,
                    a:Obra,
                    a.atualizado_em = datetime()
                """,
                id=anime_id,
                props=props,
            )
            session.run(
                """
                MATCH (a:Anime {id: $id})
                MERGE (fo:Formato {nome: $formato_nome})
                MERGE (a)-[:TEM_FORMATO]->(fo)
                """,
                id=anime_id,
                formato_nome=formato_nome,
            )
            for genero in generos:
                session.run(
                    """
                    MATCH (a:Anime {id: $id})
                    MERGE (g:Genero {nome: $genero})
                    MERGE (a)-[:TEM_GENERO]->(g)
                    """,
                    id=anime_id,
                    genero=genero,
                )

            for tema in temas:
                session.run(
                    """
                    MATCH (a:Anime {id: $id})
                    MERGE (t:Tema {nome: $tema})
                    MERGE (a)-[:TEM_TEMA]->(t)
                    """,
                    id=anime_id,
                    tema=tema,
                )

            session.run(
                """
                MATCH (a:Anime {id: $id})
                MERGE (e:Estudio {nome: $estudio})
                MERGE (a)-[:PRODUZIDO_POR]->(e)
                """,
                id=anime_id,
                estudio=estudio,
            )

            route = get_franchise_route(titulo or anime_id)
            if route:
                session.run(
                    """
                    MATCH (a:Anime {id: $id})
                    MERGE (f:Franquia {nome: $franquia})
                    SET f.pos_obra = $pos_obra,
                        f.ponte_animemanga = $ponte
                    MERGE (a)-[:PERTENCE_A]->(f)
                    """,
                    id=anime_id,
                    franquia=route["franquia"],
                    pos_obra=route.get("pos_obra", []),
                    ponte=route.get("ponte_animemanga", []),
                )

    def get_all_user_ids(self) -> list[str]:
        cypher = "MATCH (u:Usuario) RETURN u.telegram_id AS tid"
        with self.driver.session() as session:
            result = session.run(cypher)
            return [row["tid"] for row in result if row["tid"]]

    def get_historico(self, telegram_id: str) -> dict:
        cypher = """
        MATCH (u:Usuario {telegram_id: $telegram_id})
        OPTIONAL MATCH (u)-[r:ASSISTIU]->(a:Anime)
        OPTIONAL MATCH (u)-[d:DROPOU]->(da:Anime)
        OPTIONAL MATCH (u)-[p:EM_PROGRESSO]->(pa:Anime)
        RETURN
          collect(DISTINCT {titulo: a.titulo, nota: r.nota, data: r.data}) AS assistidos,
          collect(DISTINCT {titulo: da.titulo, episodio: d.episodio, data: d.data}) AS dropados,
          collect(
            DISTINCT {
              titulo: pa.titulo,
              episodio: p.episodio,
              capitulo: p.capitulo,
              porcentagem: p.porcentagem,
              formato: p.formato
            }
          ) AS progresso
        """
        with self.driver.session() as session:
            result = session.run(cypher, telegram_id=telegram_id)
            record = result.single()
            if not record:
                return {"assistidos": [], "dropados": [], "progresso": []}
            return {
                "assistidos": [
                    {**item, "data": self._to_iso(item.get("data"))}
                    for item in record["assistidos"]
                    if item.get("titulo")
                ],
                "dropados": [
                    {**item, "data": self._to_iso(item.get("data"))}
                    for item in record["dropados"]
                    if item.get("titulo")
                ],
                "progresso": [item for item in record["progresso"] if item.get("titulo")],
            }


_client: Neo4jClient | None = None


def get_neo4j() -> Neo4jClient:
    global _client
    if _client is None:
        _client = Neo4jClient()
        _client.setup_schema()
    return _client
