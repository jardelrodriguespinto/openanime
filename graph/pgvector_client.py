import logging
import os

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

ANIME_TABLE = "animes"
REVIEW_TABLE = "reviews"
DOCUMENT_TABLE = "documentos"


class PgVectorClient:
    """Cliente PostgreSQL com pgvector para busca semantica."""

    def __init__(self):
        self.conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "postgres"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "anime"),
            user=os.getenv("POSTGRES_USER", "anime"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
        )
        logger.info("PgVectorClient conectado")

    def close(self):
        self.conn.close()
        logger.info("PgVectorClient desconectado")

    def setup_schema(self):
        with self.conn.cursor() as cur:
            cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {ANIME_TABLE} (
                id SERIAL PRIMARY KEY,
                anime_id TEXT UNIQUE,
                titulo TEXT,
                synopsis TEXT,
                temas TEXT[],
                generos TEXT[],
                estudio TEXT,
                ano INTEGER,
                nota REAL,
                embedding VECTOR(1536),
                created_at TIMESTAMP DEFAULT NOW()
            )
            """)
            cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {REVIEW_TABLE} (
                id SERIAL PRIMARY KEY,
                anime_id TEXT,
                texto TEXT,
                fonte TEXT,
                sentimento TEXT,
                embedding VECTOR(1536),
                created_at TIMESTAMP DEFAULT NOW()
            )
            """)
            cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {DOCUMENT_TABLE} (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                doc_id TEXT,
                nome TEXT,
                tipo TEXT,
                conteudo TEXT,
                resumo TEXT,
                embedding VECTOR(1536),
                data_upload TIMESTAMP DEFAULT NOW()
            )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS animes_embedding_idx ON animes USING ivfflat (embedding vector_l2_ops)")
            self.conn.commit()
        logger.info("Schema PostgreSQL configurado")

    def upsert_anime(self, anime: dict):
        from datetime import datetime
        import numpy as np

        anime_id = str(anime.get("id", anime.get("titulo", "")))
        data = {
            "anime_id": anime_id,
            "titulo": anime.get("titulo", ""),
            "synopsis": anime.get("synopsis", ""),
            "temas": anime.get("temas", []),
            "generos": anime.get("generos", []),
            "estudio": anime.get("estudio", ""),
            "ano": anime.get("ano", 0),
            "nota": float(anime.get("nota_mal", 0.0)),
        }

        embedding = anime.get("embedding")
        if embedding and isinstance(embedding, (list, np.ndarray)):
            data["embedding"] = embedding

        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {ANIME_TABLE} (anime_id, titulo, synopsis, temas, generos, estudio, ano, nota, embedding)
                VALUES (%(anime_id)s, %(titulo)s, %(synopsis)s, %(temas)s, %(generos)s, %(estudio)s, %(ano)s, %(nota)s, %(embedding)s)
                ON CONFLICT (anime_id) DO UPDATE SET
                    titulo = EXCLUDED.titulo,
                    synopsis = EXCLUDED.synopsis,
                    temas = EXCLUDED.temas,
                    generos = EXCLUDED.generos,
                    estudio = EXCLUDED.estudio,
                    ano = EXCLUDED.ano,
                    nota = EXCLUDED.nota,
                    embedding = COALESCE(EXCLUDED.embedding, {ANIME_TABLE}.embedding)
                """,
                data,
            )
            self.conn.commit()
        logger.debug("PgVector: anime inserido titulo=%s", data["titulo"])

    def upsert_midia(self, payload: dict):
        self.upsert_anime(payload)

    def busca_semantica(
        self,
        query: str,
        limit: int = 5,
        generos_preferidos: list[str] | None = None,
    ) -> list[dict]:
        logger.info("PgVector busca semantica: query='%s' limit=%d", query, limit)

        embedding = self._get_embedding(query)
        if not embedding:
            return []

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT titulo, synopsis, generos, temas, ano, nota,
                       1 - (embedding <=> %s::vector) AS certainty
                FROM {ANIME_TABLE}
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (embedding, embedding, limit),
            )
            items = cur.fetchall()
        logger.info("PgVector: %d resultados para '%s'", len(items), query)
        return items

    def _get_embedding(self, text: str) -> list[float] | None:
        try:
            from ai.openrouter import openrouter
            emb = openrouter.embedding(text)
            return emb if emb else None
        except Exception as e:
            logger.debug("PgVector: falha embedding: %s", e)
            return None

    def inserir_review(self, anime_id: str, texto: str, fonte: str, sentimento: str = ""):
        embedding = self._get_embedding(texto)
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {REVIEW_TABLE} (anime_id, texto, fonte, sentimento, embedding)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (anime_id, texto, fonte, sentimento, embedding or None),
            )
            self.conn.commit()
        logger.debug("PgVector: review inserida anime_id=%s", anime_id)

    def busca_reviews(self, query: str, limit: int = 5) -> list[dict]:
        embedding = self._get_embedding(query)
        if not embedding:
            return []

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT anime_id, texto, fonte, sentimento
                FROM {REVIEW_TABLE}
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (embedding, limit),
            )
            return cur.fetchall()

    def upsert_documento(self, doc: dict) -> None:
        from datetime import datetime

        doc_id = str(doc.get("doc_id", ""))
        user_id = str(doc.get("user_id", ""))

        embedding = self._get_embedding(f"{doc.get('conteudo', '')} {doc.get('resumo', '')}")

        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {DOCUMENT_TABLE} (user_id, doc_id, nome, tipo, conteudo, resumo, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (doc_id) DO UPDATE SET
                    nome = EXCLUDED.nome,
                    tipo = EXCLUDED.tipo,
                    conteudo = EXCLUDED.conteudo,
                    resumo = EXCLUDED.resumo,
                    embedding = COALESCE(EXCLUDED.embedding, {DOCUMENT_TABLE}.embedding)
                """,
                (user_id, doc_id, doc.get("nome", ""), doc.get("tipo", "generico"),
                 doc.get("conteudo", "")[:8000], doc.get("resumo", "")[:500], embedding or None),
            )
            self.conn.commit()

    def busca_documento(self, user_id: str, query: str, limit: int = 3) -> list[dict]:
        embedding = self._get_embedding(query)
        if not embedding:
            return []

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT user_id, doc_id, nome, tipo, conteudo, resumo
                FROM {DOCUMENT_TABLE}
                WHERE user_id = %s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (str(user_id), embedding, limit),
            )
            return cur.fetchall()

    def total_animes(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {ANIME_TABLE}")
            return cur.fetchone()[0] or 0


_client: PgVectorClient | None = None


def get_pgvector() -> PgVectorClient:
    global _client
    if _client is None:
        _client = PgVectorClient()
        _client.setup_schema()
    return _client


def get_weaviate():
    return get_pgvector()