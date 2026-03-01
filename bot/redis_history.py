import json
import logging
import os

import redis

logger = logging.getLogger(__name__)

HISTORY_TTL = 60 * 60 * 24 * 7  # 7 dias


class RedisHistoryClient:
    def __init__(self):
        self._client = redis.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            username=os.getenv("REDIS_USER", "open-anime"),
            password=os.getenv("REDIS_PASSWORD", "open-anime"),
            db=int(os.getenv("REDIS_DB", 0)),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        logger.info("RedisHistoryClient conectado a %s:%s", os.getenv("REDIS_HOST", "redis"), os.getenv("REDIS_PORT", 6379))

    def get(self, user_id: str) -> list:
        try:
            raw = self._client.get(f"conv:{user_id}")
            return json.loads(raw) if raw else []
        except Exception as e:
            logger.warning("Redis get erro user=%s: %s", user_id, e)
            return []

    def set(self, user_id: str, history: list, max_items: int = 20) -> None:
        try:
            trimmed = history[-max_items:]
            self._client.setex(f"conv:{user_id}", HISTORY_TTL, json.dumps(trimmed))
        except Exception as e:
            logger.warning("Redis set erro user=%s: %s", user_id, e)

    def delete(self, user_id: str) -> None:
        try:
            self._client.delete(f"conv:{user_id}")
        except Exception as e:
            logger.warning("Redis delete erro user=%s: %s", user_id, e)


_client: RedisHistoryClient | None = None


def get_redis_history() -> RedisHistoryClient:
    global _client
    if _client is None:
        _client = RedisHistoryClient()
    return _client
