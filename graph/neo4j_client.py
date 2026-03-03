
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
            "CREATE INDEX IF NOT EXISTS FOR (a:Anime) ON (a.tipo)",
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
          collect(DISTINCT {titulo: a.titulo, nota: r.nota, data: r.data, opiniao: r.opiniao}) AS assistidos,
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

    def registrar_assistido(
        self,
        telegram_id: str,
        titulo: str,
        nota: float | None = None,
        opiniao: str | None = None,
    ):
        opiniao_clean = (opiniao or "").strip()[:500] or None
        with self.driver.session() as session:
            anime_ref = self._ensure_anime_node(session, titulo=titulo, formato="anime")
            cypher = """
            MATCH (u:Usuario {telegram_id: $telegram_id})
            MATCH (a:Anime {titulo_key: $titulo_key})
            MERGE (u)-[r:ASSISTIU]->(a)
            SET r.nota = $nota, r.data = datetime(),
                r.opiniao = CASE WHEN $opiniao IS NOT NULL THEN $opiniao ELSE r.opiniao END
            WITH u, a
            OPTIONAL MATCH (u)-[p:EM_PROGRESSO]->(a)
            DELETE p
            """
            session.run(
                cypher,
                telegram_id=telegram_id,
                titulo_key=anime_ref["titulo_key"],
                nota=nota,
                opiniao=opiniao_clean,
            )
        self.refresh_user_taste_links(telegram_id)
        logger.info("Registrado assistido: user=%s titulo=%s nota=%s", telegram_id, titulo, nota)

    def get_opinioes_usuario(self, telegram_id: str, limit: int = 10) -> list[dict]:
        """Retorna opinioes textuais do usuario sobre obras que assistiu/leu."""
        cypher = """
        MATCH (u:Usuario {telegram_id: $telegram_id})-[r:ASSISTIU]->(a:Anime)
        WHERE r.opiniao IS NOT NULL AND r.opiniao <> ''
        RETURN a.titulo AS titulo, r.nota AS nota, r.opiniao AS opiniao
        ORDER BY r.data DESC
        LIMIT $limit
        """
        with self.driver.session() as session:
            result = session.run(cypher, telegram_id=telegram_id, limit=limit)
            return [
                {"titulo": row["titulo"], "nota": row["nota"], "opiniao": row["opiniao"]}
                for row in result
                if row["titulo"] and row["opiniao"]
            ]

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

    def upsert_midia(self, payload: dict):
        """Insere ou atualiza qualquer midia (filme, serie, dorama, anime) no Neo4j."""
        self.upsert_anime(payload)

    def get_stats_pessoais(self, telegram_id: str) -> dict:
        """Retorna estatisticas agregadas do usuario para o comando /stats."""
        cypher = """
        MATCH (u:Usuario {telegram_id: $telegram_id})
        OPTIONAL MATCH (u)-[r:ASSISTIU]->(a:Anime)
        OPTIONAL MATCH (u)-[d:DROPOU]->(da:Anime)
        OPTIONAL MATCH (u)-[p:EM_PROGRESSO]->(pa:Anime)
        RETURN
            count(DISTINCT a)  AS total_assistidos,
            count(DISTINCT da) AS total_dropados,
            count(DISTINCT pa) AS total_progresso,
            avg(CASE WHEN r.nota IS NOT NULL THEN toFloat(r.nota) ELSE null END) AS media_notas
        """
        by_tipo_cypher = """
        MATCH (u:Usuario {telegram_id: $telegram_id})-[:ASSISTIU]->(a:Anime)
        WHERE a.tipo IS NOT NULL
        RETURN a.tipo AS tipo, count(*) AS qtd
        ORDER BY qtd DESC
        """
        with self.driver.session() as session:
            rec = session.run(cypher, telegram_id=telegram_id).single()
            if not rec:
                return {}
            by_tipo = {
                row["tipo"]: int(row["qtd"])
                for row in session.run(by_tipo_cypher, telegram_id=telegram_id)
                if row.get("tipo")
            }

        total_assistidos = rec.get("total_assistidos") or 0
        total_dropados = rec.get("total_dropados") or 0
        total_progresso = rec.get("total_progresso") or 0
        media_notas = rec.get("media_notas")
        total = total_assistidos + total_dropados
        drop_rate = round((total_dropados / total) * 100) if total > 0 else 0

        top_generos = self._top_items_por_relacao(telegram_id, "ASSISTIU", "Genero", "TEM_GENERO", limit=3)
        top_estudios = self._top_items_por_relacao(telegram_id, "ASSISTIU", "Estudio", "PRODUZIDO_POR", limit=3)

        return {
            "total_assistidos": total_assistidos,
            "total_dropados": total_dropados,
            "total_progresso": total_progresso,
            "media_notas": round(media_notas, 1) if media_notas is not None else None,
            "drop_rate": drop_rate,
            "top_generos": top_generos,
            "top_estudios": top_estudios,
            "por_tipo": by_tipo,
        }

    def _top_items_por_relacao(
        self, telegram_id: str, user_rel: str, node_label: str, anime_rel: str, limit: int = 3
    ) -> list[str]:
        cypher = f"""
        MATCH (u:Usuario {{telegram_id: $telegram_id}})-[:{user_rel}]->(a:Anime)-[:{anime_rel}]->(n:{node_label})
        WHERE n.nome IS NOT NULL
        RETURN n.nome AS nome, count(*) AS freq
        ORDER BY freq DESC
        LIMIT $limit
        """
        with self.driver.session() as session:
            rows = session.run(cypher, telegram_id=telegram_id, limit=limit)
            return [row["nome"] for row in rows if row.get("nome")]

    def get_progresso_ativo(self, telegram_id: str) -> list[str]:
        """Retorna titulos das series que o usuario tem EM_PROGRESSO."""
        cypher = """
        MATCH (u:Usuario {telegram_id: $telegram_id})-[p:EM_PROGRESSO]->(a:Anime)
        RETURN a.titulo AS titulo
        ORDER BY p.atualizado_em DESC
        """
        with self.driver.session() as session:
            rows = session.run(cypher, telegram_id=telegram_id)
            return [row["titulo"] for row in rows if row.get("titulo")]

    def get_all_user_ids(self) -> list[str]:
        cypher = "MATCH (u:Usuario) RETURN u.telegram_id AS tid"
        with self.driver.session() as session:
            result = session.run(cypher)
            return [row["tid"] for row in result if row["tid"]]

    # ── Artistas e Autores favoritos (para notificações) ────────────────────

    def adicionar_artista_favorito(self, telegram_id: str, artista: str) -> None:
        """Adiciona artista à lista de favoritos do usuario (para notificacoes)."""
        artista_clean = (artista or "").strip()
        if not artista_clean:
            return
        with self.driver.session() as session:
            session.run(
                """
                MERGE (u:Usuario {telegram_id: $telegram_id})
                SET u.artistas_favoritos = CASE
                    WHEN $artista IN coalesce(u.artistas_favoritos, [])
                    THEN coalesce(u.artistas_favoritos, [])
                    ELSE coalesce(u.artistas_favoritos, []) + [$artista]
                END
                """,
                telegram_id=telegram_id,
                artista=artista_clean,
            )

    def adicionar_autor_favorito(self, telegram_id: str, autor: str) -> None:
        """Adiciona autor à lista de favoritos do usuario (para notificacoes)."""
        autor_clean = (autor or "").strip()
        if not autor_clean:
            return
        with self.driver.session() as session:
            session.run(
                """
                MERGE (u:Usuario {telegram_id: $telegram_id})
                SET u.autores_favoritos = CASE
                    WHEN $autor IN coalesce(u.autores_favoritos, [])
                    THEN coalesce(u.autores_favoritos, [])
                    ELSE coalesce(u.autores_favoritos, []) + [$autor]
                END
                """,
                telegram_id=telegram_id,
                autor=autor_clean,
            )

    def get_artistas_favoritos(self, telegram_id: str) -> list[str]:
        """Retorna artistas favoritos do usuario."""
        with self.driver.session() as session:
            result = session.run(
                "MATCH (u:Usuario {telegram_id: $telegram_id}) "
                "RETURN coalesce(u.artistas_favoritos, []) AS artistas",
                telegram_id=telegram_id,
            )
            record = result.single()
            return list(record["artistas"]) if record else []

    def get_autores_favoritos(self, telegram_id: str) -> list[str]:
        """Retorna autores favoritos do usuario."""
        with self.driver.session() as session:
            result = session.run(
                "MATCH (u:Usuario {telegram_id: $telegram_id}) "
                "RETURN coalesce(u.autores_favoritos, []) AS autores",
                telegram_id=telegram_id,
            )
            record = result.single()
            return list(record["autores"]) if record else []

    def get_usuarios_com_preferencias_culturais(self) -> list[dict]:
        """Retorna usuarios que tem artistas ou autores favoritos cadastrados."""
        cypher = """
        MATCH (u:Usuario)
        WHERE size(coalesce(u.artistas_favoritos, [])) > 0
           OR size(coalesce(u.autores_favoritos, [])) > 0
        RETURN
            u.telegram_id AS telegram_id,
            coalesce(u.artistas_favoritos, []) AS artistas_favoritos,
            coalesce(u.autores_favoritos, []) AS autores_favoritos
        """
        with self.driver.session() as session:
            result = session.run(cypher)
            return [
                {
                    "telegram_id": row["telegram_id"],
                    "artistas_favoritos": list(row["artistas_favoritos"]),
                    "autores_favoritos": list(row["autores_favoritos"]),
                }
                for row in result
                if row["telegram_id"]
            ]

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


    # ─── Noticias — interesses do usuario ────────────────────────────────────

    def get_interesses_noticias(self, telegram_id: str) -> list[str]:
        cypher = "MATCH (u:Usuario {telegram_id: $tid}) RETURN coalesce(u.interesses_noticias, []) AS interesses"
        with self.driver.session() as session:
            result = session.run(cypher, tid=telegram_id)
            record = result.single()
            if not record:
                return []
            return list(record["interesses"] or [])

    def salvar_interesses_noticias(self, telegram_id: str, categorias: list[str]) -> None:
        cypher = """
        MERGE (u:Usuario {telegram_id: $tid})
        SET u.interesses_noticias = $categorias
        """
        with self.driver.session() as session:
            session.run(cypher, tid=telegram_id, categorias=categorias)

    # ─── Preferencias de notificacao ─────────────────────────────────────────

    def get_preferencias_notificacao(self, telegram_id: str) -> dict:
        """
        Retorna preferencias de notificacao do usuario.
        Defaults: digest ativo 8h, episodios ativo 20h, vagas/noticias desativados.
        """
        cypher = """
        MATCH (u:Usuario {telegram_id: $tid})
        RETURN
            coalesce(u.notif_digest_ativo, true)           AS digest_ativo,
            coalesce(u.notif_digest_hora, 8)               AS digest_hora,
            coalesce(u.notif_episodios_ativo, true)        AS episodios_ativo,
            coalesce(u.notif_episodios_hora, 20)           AS episodios_hora,
            coalesce(u.notif_vagas_ativo, false)           AS vagas_ativo,
            coalesce(u.notif_vagas_hora, 9)                AS vagas_hora,
            coalesce(u.notif_noticias_ativo, false)        AS noticias_ativo,
            coalesce(u.notif_noticias_hora, 8)             AS noticias_hora,
            coalesce(u.notif_noticias_minuto, 0)           AS noticias_minuto
        """
        with self.driver.session() as session:
            result = session.run(cypher, tid=telegram_id)
            record = result.single()
            if not record:
                return {
                    "digest_ativo": True, "digest_hora": 8,
                    "episodios_ativo": True, "episodios_hora": 20,
                    "vagas_ativo": False, "vagas_hora": 9,
                    "noticias_ativo": False, "noticias_hora": 8, "noticias_minuto": 0,
                }
            return dict(record)

    def salvar_preferencias_notificacao(self, telegram_id: str, prefs: dict) -> None:
        """Salva preferencias de notificacao do usuario."""
        cypher = """
        MERGE (u:Usuario {telegram_id: $tid})
        SET
            u.notif_digest_ativo     = $digest_ativo,
            u.notif_digest_hora      = $digest_hora,
            u.notif_episodios_ativo  = $episodios_ativo,
            u.notif_episodios_hora   = $episodios_hora,
            u.notif_vagas_ativo      = $vagas_ativo,
            u.notif_vagas_hora       = $vagas_hora,
            u.notif_noticias_ativo   = $noticias_ativo,
            u.notif_noticias_hora    = $noticias_hora,
            u.notif_noticias_minuto  = $noticias_minuto
        """
        with self.driver.session() as session:
            session.run(
                cypher,
                tid=telegram_id,
                digest_ativo=prefs.get("digest_ativo", True),
                digest_hora=int(prefs.get("digest_hora", 8)),
                episodios_ativo=prefs.get("episodios_ativo", True),
                episodios_hora=int(prefs.get("episodios_hora", 20)),
                vagas_ativo=prefs.get("vagas_ativo", False),
                vagas_hora=int(prefs.get("vagas_hora", 9)),
                noticias_ativo=prefs.get("noticias_ativo", False),
                noticias_hora=int(prefs.get("noticias_hora", 8)),
                noticias_minuto=int(prefs.get("noticias_minuto", 0)),
            )

    def get_usuarios_por_hora_notificacao(self, hora: int, tipo: str) -> list[str]:
        """
        Retorna user_ids que devem receber notificacao do tipo/hora especificado.
        tipo: 'digest' | 'episodios' | 'vagas' | 'noticias'
        """
        campo_ativo = f"notif_{tipo}_ativo"
        campo_hora = f"notif_{tipo}_hora"
        cypher = f"""
        MATCH (u:Usuario)
        WHERE coalesce(u.{campo_ativo}, $default_ativo) = true
          AND coalesce(u.{campo_hora}, $default_hora) = $hora
        RETURN u.telegram_id AS tid
        """
        defaults_ativo = {"digest": True, "episodios": True, "vagas": False, "noticias": False}
        defaults_hora = {"digest": 8, "episodios": 20, "vagas": 9, "noticias": 8}
        with self.driver.session() as session:
            result = session.run(
                cypher,
                hora=hora,
                default_ativo=defaults_ativo.get(tipo, False),
                default_hora=defaults_hora.get(tipo, 8),
            )
            return [r["tid"] for r in result if r["tid"]]

    def get_usuarios_noticias_agendadas(self, hora: int, minuto: int) -> list[str]:
        """
        Retorna user_ids com noticias agendadas para hora:minuto exatos.
        Usado pelo coordinator que roda a cada minuto.
        """
        cypher = """
        MATCH (u:Usuario)
        WHERE coalesce(u.notif_noticias_ativo, false) = true
          AND coalesce(u.notif_noticias_hora, 8) = $hora
          AND coalesce(u.notif_noticias_minuto, 0) = $minuto
        RETURN u.telegram_id AS tid
        """
        with self.driver.session() as session:
            result = session.run(cypher, hora=hora, minuto=minuto)
            return [r["tid"] for r in result if r["tid"]]

    # ─── Documentos ──────────────────────────────────────────────────────────

    def registrar_documento(self, telegram_id: str, doc_id: str, nome: str, tipo: str) -> None:
        cypher = """
        MERGE (u:Usuario {telegram_id: $tid})
        MERGE (d:Documento {id: $doc_id})
        SET d.nome = $nome, d.tipo = $tipo, d.data_upload = datetime()
        MERGE (u)-[:ENVIOU]->(d)
        """
        with self.driver.session() as session:
            session.run(cypher, tid=telegram_id, doc_id=doc_id, nome=nome, tipo=tipo)

    # ─── Perfil profissional ──────────────────────────────────────────────────

    def salvar_perfil_profissional(self, telegram_id: str, dados: dict) -> None:
        """Salva perfil completo extraido de curriculo."""
        with self.driver.session() as session:
            # Campos diretos no usuario
            campos = {k: v for k, v in {
                "nome_real": dados.get("nome"),
                "email": dados.get("email"),
                "telefone": dados.get("telefone"),
                "linkedin_url": dados.get("linkedin"),
                "github_url": dados.get("github"),
                "portfolio_url": dados.get("portfolio"),
                "cargo_atual": dados.get("cargo_atual"),
                "nivel_senioridade": dados.get("nivel_senioridade"),
                "localizacao": dados.get("localizacao"),
                "pretensao_salarial": dados.get("pretensao_salarial"),
                "modalidade_preferida": dados.get("modalidade_preferida"),
                "objetivo_profissional": dados.get("objetivo"),
            }.items() if v}

            if campos:
                set_clause = ", ".join(f"u.{k} = ${k}" for k in campos)
                session.run(
                    f"MERGE (u:Usuario {{telegram_id: $tid}}) SET {set_clause}",
                    tid=telegram_id, **campos,
                )

            # Habilidades
            for hab in (dados.get("habilidades") or []):
                if not hab or not hab.get("nome"):
                    continue
                self.upsert_habilidade(
                    telegram_id, hab["nome"],
                    hab.get("nivel", 3), hab.get("anos_exp", 0)
                )

            # Experiencias
            for exp in (dados.get("experiencias") or []):
                if not exp or not exp.get("empresa"):
                    continue
                session.run("""
                    MERGE (u:Usuario {telegram_id: $tid})
                    MERGE (e:Empresa {nome: $empresa})
                    MERGE (u)-[r:TRABALHOU_EM {cargo: $cargo}]->(e)
                    SET r.inicio = $inicio, r.fim = $fim, r.descricao = $desc
                """, tid=telegram_id, empresa=exp.get("empresa", ""),
                    cargo=exp.get("cargo", ""), inicio=exp.get("inicio", ""),
                    fim=exp.get("fim", "atual"), desc=exp.get("descricao", ""))

            # Formacao
            for form in (dados.get("formacao") or []):
                if not form or not form.get("curso"):
                    continue
                session.run("""
                    MERGE (u:Usuario {telegram_id: $tid})
                    MERGE (f:Formacao {curso: $curso, instituicao: $inst})
                    SET f.nivel = $nivel, f.ano = $ano
                    MERGE (u)-[:CURSOU]->(f)
                """, tid=telegram_id, curso=form.get("curso", ""),
                    inst=form.get("instituicao", ""), nivel=form.get("nivel", ""),
                    ano=form.get("ano", ""))

            # Idiomas
            for idioma in (dados.get("idiomas") or []):
                if idioma and idioma.get("idioma"):
                    session.run("""
                        MERGE (u:Usuario {telegram_id: $tid})
                        SET u.idiomas = coalesce(u.idiomas, []) + [$idioma_nivel]
                    """, tid=telegram_id,
                        idioma_nivel=f"{idioma['idioma']}:{idioma.get('nivel', '')}")

        logger.info("Neo4j: perfil profissional salvo user=%s", telegram_id)

    def upsert_habilidade(self, telegram_id: str, nome: str, nivel: int = 3, anos_exp: int = 0) -> None:
        cypher = """
        MERGE (u:Usuario {telegram_id: $tid})
        MERGE (h:Habilidade {nome: $nome})
        MERGE (u)-[r:TEM_HABILIDADE]->(h)
        SET r.nivel = $nivel, r.anos_exp = $anos_exp
        """
        with self.driver.session() as session:
            session.run(cypher, tid=telegram_id, nome=nome.lower(), nivel=nivel, anos_exp=anos_exp)

    def salvar_preferencias_emprego(self, telegram_id: str, prefs: dict) -> None:
        campos = {k: v for k, v in prefs.items() if v}
        if not campos:
            return
        set_clause = ", ".join(f"u.{k} = ${k}" for k in campos)
        cypher = f"MERGE (u:Usuario {{telegram_id: $tid}}) SET {set_clause}"
        with self.driver.session() as session:
            session.run(cypher, tid=telegram_id, **campos)

    def adicionar_cargo_desejado(self, telegram_id: str, cargo: str) -> None:
        cypher = """
        MERGE (u:Usuario {telegram_id: $tid})
        MERGE (c:Cargo {titulo: $cargo})
        MERGE (u)-[:QUER_CARGO]->(c)
        """
        with self.driver.session() as session:
            session.run(cypher, tid=telegram_id, cargo=cargo)

    def get_perfil_profissional(self, telegram_id: str) -> dict:
        cypher = """
        MATCH (u:Usuario {telegram_id: $tid})
        OPTIONAL MATCH (u)-[th:TEM_HABILIDADE]->(h:Habilidade)
        OPTIONAL MATCH (u)-[tr:TRABALHOU_EM]->(e:Empresa)
        OPTIONAL MATCH (u)-[:CURSOU]->(f:Formacao)
        OPTIONAL MATCH (u)-[:QUER_CARGO]->(c:Cargo)
        RETURN u,
               collect(DISTINCT {nome: h.nome, nivel: th.nivel, anos_exp: th.anos_exp}) AS habilidades,
               collect(DISTINCT {empresa: e.nome, cargo: tr.cargo, inicio: tr.inicio, fim: tr.fim, descricao: tr.descricao}) AS experiencias,
               collect(DISTINCT {curso: f.curso, instituicao: f.instituicao, nivel: f.nivel, ano: f.ano}) AS formacao,
               collect(DISTINCT c.titulo) AS cargos_desejados
        """
        with self.driver.session() as session:
            result = session.run(cypher, tid=telegram_id)
            record = result.single()
            if not record:
                return {}
            u = dict(record["u"])
            return {
                "nome": u.get("nome_real", ""),
                "email": u.get("email", ""),
                "telefone": u.get("telefone", ""),
                "linkedin": u.get("linkedin_url", ""),
                "github": u.get("github_url", ""),
                "portfolio": u.get("portfolio_url", ""),
                "cargo_atual": u.get("cargo_atual", ""),
                "nivel_senioridade": u.get("nivel_senioridade", ""),
                "localizacao": u.get("localizacao", ""),
                "pretensao_salarial": u.get("pretensao_salarial", ""),
                "modalidade_preferida": u.get("modalidade_preferida", ""),
                "objetivo": u.get("objetivo_profissional", ""),
                "habilidades": [h for h in record["habilidades"] if h.get("nome")],
                "experiencias": [e for e in record["experiencias"] if e.get("empresa")],
                "formacao": [f for f in record["formacao"] if f.get("curso")],
                "cargos_desejados": [c for c in record["cargos_desejados"] if c],
                "idiomas": self._parse_idiomas(u.get("idiomas", [])),
            }

    def _parse_idiomas(self, idiomas_raw: list) -> list[dict]:
        result = []
        for item in (idiomas_raw or []):
            if ":" in str(item):
                parts = str(item).split(":", 1)
                result.append({"idioma": parts[0], "nivel": parts[1]})
        return result

    def get_score_completude_perfil(self, telegram_id: str) -> int:
        """Retorna score de completude do perfil profissional (0-100)."""
        perfil = self.get_perfil_profissional(telegram_id)
        score = 0
        if len(perfil.get("habilidades", [])) >= 3:
            score += 20
        if perfil.get("experiencias"):
            score += 20
        if perfil.get("formacao"):
            score += 10
        if perfil.get("cargos_desejados"):
            score += 15
        if perfil.get("pretensao_salarial"):
            score += 10
        if perfil.get("modalidade_preferida"):
            score += 10
        if perfil.get("localizacao"):
            score += 10
        if perfil.get("nivel_senioridade"):
            score += 5
        return score

    # ─── Vagas ───────────────────────────────────────────────────────────────

    def upsert_vaga(self, dados: dict) -> None:
        cypher = """
        MERGE (v:Vaga {id: $id})
        SET v.titulo = $titulo,
            v.empresa = $empresa,
            v.url = $url,
            v.fonte = $fonte,
            v.salario = $salario,
            v.modalidade = $modalidade,
            v.descricao = $descricao,
            v.status = 'aberta',
            v.data_indexacao = datetime()
        """
        with self.driver.session() as session:
            session.run(cypher,
                id=dados.get("id", ""),
                titulo=dados.get("titulo", ""),
                empresa=dados.get("empresa", ""),
                url=dados.get("url", ""),
                fonte=dados.get("fonte", ""),
                salario=dados.get("salario", ""),
                modalidade=dados.get("modalidade", ""),
                descricao=dados.get("descricao", "")[:500],
            )

    def get_ultima_vaga_visualizada(self, telegram_id: str) -> dict | None:
        cypher = """
        MATCH (u:Usuario {telegram_id: $tid})-[r:VISUALIZOU|FAVORITOU]->(v:Vaga)
        RETURN v ORDER BY r.data DESC LIMIT 1
        """
        with self.driver.session() as session:
            result = session.run(cypher, tid=telegram_id)
            record = result.single()
            if not record:
                return None
            return dict(record["v"])

    def registrar_candidatura(self, user_id: str, vaga_id: str, plataforma: str, status: str) -> None:
        cypher = """
        MERGE (u:Usuario {telegram_id: $uid})
        MERGE (v:Vaga {id: $vaga_id})
        MERGE (u)-[r:SE_CANDIDATOU {vaga_id: $vaga_id}]->(v)
        SET r.data = datetime(), r.plataforma = $plataforma, r.status = $status,
            r.data_ultima_atualizacao = datetime()
        """
        with self.driver.session() as session:
            session.run(cypher, uid=user_id, vaga_id=vaga_id,
                        plataforma=plataforma, status=status)

    def get_candidaturas(self, telegram_id: str) -> list[dict]:
        cypher = """
        MATCH (u:Usuario {telegram_id: $tid})-[r:SE_CANDIDATOU]->(v:Vaga)
        RETURN v.titulo AS titulo, v.empresa AS empresa, v.url AS url,
               r.status AS status, r.plataforma AS plataforma,
               toString(r.data) AS data
        ORDER BY r.data DESC LIMIT 20
        """
        with self.driver.session() as session:
            result = session.run(cypher, tid=telegram_id)
            return [dict(r) for r in result]

    def ja_se_candidatou(self, telegram_id: str, vaga_id: str) -> bool:
        cypher = """
        MATCH (u:Usuario {telegram_id: $tid})-[r:SE_CANDIDATOU {vaga_id: $vaga_id}]->(v:Vaga)
        RETURN count(r) > 0 AS existe
        """
        with self.driver.session() as session:
            result = session.run(cypher, tid=telegram_id, vaga_id=vaga_id)
            record = result.single()
            return record["existe"] if record else False

    def contar_candidaturas_hoje(self, telegram_id: str) -> int:
        cypher = """
        MATCH (u:Usuario {telegram_id: $tid})-[r:SE_CANDIDATOU]->(v:Vaga)
        WHERE r.data >= datetime({year: date().year, month: date().month, day: date().day})
        RETURN count(r) AS total
        """
        with self.driver.session() as session:
            result = session.run(cypher, tid=telegram_id)
            record = result.single()
            return record["total"] if record else 0


_client: Neo4jClient | None = None


def get_neo4j() -> Neo4jClient:
    global _client
    if _client is None:
        _client = Neo4jClient()
        _client.setup_schema()
    return _client
