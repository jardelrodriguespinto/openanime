import logging
import os

import weaviate

logger = logging.getLogger(__name__)

ANIME_CLASS = "Anime"
REVIEW_CLASS = "Review"
DOCUMENT_CLASS = "Document"

DOCUMENT_SCHEMA = {
    "class": DOCUMENT_CLASS,
    "description": "Documentos PDF enviados pelos usuarios",
    "vectorizer": "text2vec-openai",
    "moduleConfig": {
        "text2vec-openai": {
            "vectorizeClassName": False,
            "model": "text-embedding-3-small",
            "type": "text",
        }
    },
    "properties": [
        {"name": "user_id", "dataType": ["text"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "doc_id", "dataType": ["text"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "nome", "dataType": ["text"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "tipo", "dataType": ["text"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "conteudo", "dataType": ["text"]},
        {"name": "resumo", "dataType": ["text"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "data_upload", "dataType": ["text"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
    ],
}

ANIME_SCHEMA = {
    "class": ANIME_CLASS,
    "description": "Anime com embedding da sinopse para busca semantica",
    "vectorizer": "text2vec-openai",
    "moduleConfig": {
        "text2vec-openai": {
            "vectorizeClassName": False,
            "model": "text-embedding-3-small",
            "type": "text",
        }
    },
    "properties": [
        {"name": "titulo", "dataType": ["text"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "synopsis", "dataType": ["text"]},
        {"name": "temas", "dataType": ["text[]"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "generos", "dataType": ["text[]"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "estudio", "dataType": ["text"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "ano", "dataType": ["int"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "nota", "dataType": ["number"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "anime_id", "dataType": ["text"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
    ],
}

REVIEW_SCHEMA = {
    "class": REVIEW_CLASS,
    "description": "Reviews e discussoes de anime",
    "vectorizer": "text2vec-openai",
    "moduleConfig": {
        "text2vec-openai": {
            "vectorizeClassName": False,
            "model": "text-embedding-3-small",
            "type": "text",
        }
    },
    "properties": [
        {"name": "anime_id", "dataType": ["text"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "texto", "dataType": ["text"]},
        {"name": "fonte", "dataType": ["text"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
        {"name": "sentimento", "dataType": ["text"], "moduleConfig": {"text2vec-openai": {"skip": True}}},
    ],
}


class WeaviateClient:
    """Conexao e busca semantica no Weaviate."""

    def __init__(self):
        url = os.getenv("WEAVIATE_URL", "http://weaviate:8080")
        self.client = weaviate.Client(url)
        logger.info("WeaviateClient conectado a %s", url)

    @staticmethod
    def _log_weaviate_error(context: str, exc: Exception):
        logger.error("Weaviate erro em %s: %s", context, exc)

    def setup_schema(self):
        """Cria schema se nao existir."""
        try:
            existing = {c["class"] for c in self.client.schema.get().get("classes", [])}
        except Exception as exc:
            self._log_weaviate_error("schema.get", exc)
            return

        for schema in [ANIME_SCHEMA, REVIEW_SCHEMA, DOCUMENT_SCHEMA]:
            if schema["class"] in existing:
                logger.debug("Weaviate: classe %s ja existe", schema["class"])
                continue
            try:
                self.client.schema.create_class(schema)
                logger.info("Weaviate: classe %s criada", schema["class"])
            except Exception as exc:
                self._log_weaviate_error(f"schema.create_class:{schema['class']}", exc)

    def upsert_anime(self, anime: dict):
        """Insere ou atualiza anime no Weaviate."""
        anime_id = str(anime.get("id", anime.get("titulo", "")))
        data = {
            "anime_id": anime_id,
            "titulo": anime.get("titulo", ""),
            "synopsis": anime.get("synopsis", ""),
            "temas": anime.get("temas", []),
            "generos": anime.get("generos", []),
            "estudio": anime.get("estudio", ""),
            "ano": anime.get("ano", 0),
            "nota": anime.get("nota_mal", 0.0),
        }

        try:
            result = (
                self.client.query
                .get(ANIME_CLASS, ["anime_id"])
                .with_where({"path": ["anime_id"], "operator": "Equal", "valueText": anime_id})
                .with_additional(["id"])
                .with_limit(1)
                .do()
            )
            existing = result.get("data", {}).get("Get", {}).get(ANIME_CLASS, [])

            if existing:
                uuid = existing[0].get("_additional", {}).get("id")
                if uuid:
                    self.client.data_object.update(data, ANIME_CLASS, uuid)
                    logger.debug("Weaviate: anime atualizado id=%s", anime_id)
                    return
                logger.warning("Weaviate: anime existe mas sem uuid, recriando id=%s", anime_id)

            self.client.data_object.create(data, ANIME_CLASS)
            logger.debug("Weaviate: anime inserido titulo=%s", data["titulo"])
        except Exception as exc:
            self._log_weaviate_error("upsert_anime", exc)

    def upsert_midia(self, payload: dict):
        """Insere ou atualiza qualquer midia (filme, serie, dorama) no Weaviate."""
        self.upsert_anime(payload)

    def busca_semantica(
        self,
        query: str,
        limit: int = 5,
        generos_preferidos: list[str] | None = None,
    ) -> list[dict]:
        """Busca animes por similaridade semantica, com boost opcional por generos favoritos."""
        logger.info("Weaviate busca semantica: query='%s' limit=%d", query, limit)

        concepts = [query]
        if generos_preferidos:
            concepts.append(" ".join(generos_preferidos[:3]))

        try:
            result = (
                self.client.query
                .get(ANIME_CLASS, ["titulo", "synopsis", "generos", "temas", "ano", "nota"])
                .with_near_text({"concepts": concepts})
                .with_limit(limit)
                .with_additional(["certainty"])
                .do()
            )
            items = result.get("data", {}).get("Get", {}).get(ANIME_CLASS, []) or []
            logger.info("Weaviate: %d resultados para '%s'", len(items), query)
            return items
        except Exception as exc:
            self._log_weaviate_error("busca_semantica", exc)
            return []

    def inserir_review(self, anime_id: str, texto: str, fonte: str, sentimento: str = ""):
        """Insere review de anime."""
        data = {
            "anime_id": anime_id,
            "texto": texto,
            "fonte": fonte,
            "sentimento": sentimento,
        }
        try:
            self.client.data_object.create(data, REVIEW_CLASS)
            logger.debug("Weaviate: review inserida anime_id=%s fonte=%s", anime_id, fonte)
        except Exception as exc:
            self._log_weaviate_error("inserir_review", exc)

    def busca_reviews(self, query: str, limit: int = 5) -> list[dict]:
        """Busca reviews por similaridade."""
        try:
            result = (
                self.client.query
                .get(REVIEW_CLASS, ["anime_id", "texto", "fonte", "sentimento"])
                .with_near_text({"concepts": [query]})
                .with_limit(limit)
                .do()
            )
            return result.get("data", {}).get("Get", {}).get(REVIEW_CLASS, [])
        except Exception as exc:
            self._log_weaviate_error("busca_reviews", exc)
            return []

    def upsert_documento(self, doc: dict) -> None:
        """Insere ou atualiza documento do usuario no Weaviate."""
        from datetime import datetime

        doc_id = str(doc.get("doc_id", ""))
        data = {
            "user_id": str(doc.get("user_id", "")),
            "doc_id": doc_id,
            "nome": doc.get("nome", ""),
            "tipo": doc.get("tipo", "generico"),
            "conteudo": doc.get("conteudo", "")[:8000],
            "resumo": doc.get("resumo", "")[:500],
            "data_upload": datetime.utcnow().isoformat(),
        }

        try:
            result = (
                self.client.query
                .get(DOCUMENT_CLASS, ["doc_id"])
                .with_where({"path": ["doc_id"], "operator": "Equal", "valueText": doc_id})
                .with_additional(["id"])
                .with_limit(1)
                .do()
            )
            existing = result.get("data", {}).get("Get", {}).get(DOCUMENT_CLASS, [])

            if existing:
                uuid = existing[0].get("_additional", {}).get("id")
                if uuid:
                    self.client.data_object.update(data, DOCUMENT_CLASS, uuid)
                    return

            self.client.data_object.create(data, DOCUMENT_CLASS)
            logger.debug("Weaviate: documento inserido doc_id=%s", doc_id)
        except Exception as exc:
            self._log_weaviate_error("upsert_documento", exc)

    def busca_documento(self, user_id: str, query: str, limit: int = 3) -> list[dict]:
        """Busca documentos do usuario por similaridade semantica."""
        try:
            result = (
                self.client.query
                .get(DOCUMENT_CLASS, ["user_id", "doc_id", "nome", "tipo", "conteudo", "resumo"])
                .with_near_text({"concepts": [query]})
                .with_where({"path": ["user_id"], "operator": "Equal", "valueText": str(user_id)})
                .with_limit(limit)
                .do()
            )
            return result.get("data", {}).get("Get", {}).get(DOCUMENT_CLASS, []) or []
        except Exception as exc:
            self._log_weaviate_error("busca_documento", exc)
            return []

    def total_animes(self) -> int:
        try:
            result = (
                self.client.query
                .aggregate(ANIME_CLASS)
                .with_meta_count()
                .do()
            )
            count = (
                result.get("data", {})
                .get("Aggregate", {})
                .get(ANIME_CLASS, [{}])[0]
                .get("meta", {})
                .get("count", 0)
            )
            return count
        except Exception as exc:
            self._log_weaviate_error("total_animes", exc)
            return 0


# Singleton
_client: WeaviateClient | None = None


def get_weaviate() -> WeaviateClient:
    global _client
    if _client is None:
        _client = WeaviateClient()
        _client.setup_schema()
    return _client
