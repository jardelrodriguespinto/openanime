import os
import logging
import weaviate

logger = logging.getLogger(__name__)

ANIME_CLASS = "Anime"
REVIEW_CLASS = "Review"

ANIME_SCHEMA = {
    "class": ANIME_CLASS,
    "description": "Anime com embedding da sinopse para busca semântica",
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
    "description": "Reviews e discussões de anime",
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
    """Conexão e busca semântica no Weaviate."""

    def __init__(self):
        url = os.getenv("WEAVIATE_URL", "http://weaviate:8080")
        self.client = weaviate.Client(url)
        logger.info("WeaviateClient conectado a %s", url)

    def setup_schema(self):
        """Cria schema se não existir."""
        existing = {c["class"] for c in self.client.schema.get().get("classes", [])}
        for schema in [ANIME_SCHEMA, REVIEW_SCHEMA]:
            if schema["class"] not in existing:
                self.client.schema.create_class(schema)
                logger.info("Weaviate: classe %s criada", schema["class"])
            else:
                logger.debug("Weaviate: classe %s já existe", schema["class"])

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

        # Verifica se já existe — precisa pedir _additional para ter o uuid
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
            else:
                logger.warning("Weaviate: anime existe mas sem uuid, recriando id=%s", anime_id)
                self.client.data_object.create(data, ANIME_CLASS)
        else:
            self.client.data_object.create(data, ANIME_CLASS)
            logger.debug("Weaviate: anime inserido titulo=%s", data["titulo"])

    def busca_semantica(
        self,
        query: str,
        limit: int = 5,
        generos_preferidos: list[str] | None = None,
    ) -> list[dict]:
        """Busca animes por similaridade semântica, com boost opcional por gêneros favoritos."""
        logger.info("Weaviate busca semântica: query='%s' limit=%d", query, limit)

        # Se tiver gêneros preferidos, combina query + gêneros para melhor resultado
        concepts = [query]
        if generos_preferidos:
            concepts.append(" ".join(generos_preferidos[:3]))

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

    def inserir_review(self, anime_id: str, texto: str, fonte: str, sentimento: str = ""):
        """Insere review de anime."""
        data = {
            "anime_id": anime_id,
            "texto": texto,
            "fonte": fonte,
            "sentimento": sentimento,
        }
        self.client.data_object.create(data, REVIEW_CLASS)
        logger.debug("Weaviate: review inserida anime_id=%s fonte=%s", anime_id, fonte)

    def busca_reviews(self, query: str, limit: int = 5) -> list[dict]:
        """Busca reviews por similaridade."""
        result = (
            self.client.query
            .get(REVIEW_CLASS, ["anime_id", "texto", "fonte", "sentimento"])
            .with_near_text({"concepts": [query]})
            .with_limit(limit)
            .do()
        )
        return result.get("data", {}).get("Get", {}).get(REVIEW_CLASS, [])

    def total_animes(self) -> int:
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


# Singleton
_client: WeaviateClient | None = None


def get_weaviate() -> WeaviateClient:
    global _client
    if _client is None:
        _client = WeaviateClient()
        _client.setup_schema()
    return _client
