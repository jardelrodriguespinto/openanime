"""Contador PERSISTENTE de candidaturas aplicadas (via Redis) + teto via .env.

O bot respeita LINKEDIN_TETO_APLICACOES (flag no .env) como TETO TOTAL de
candidaturas — não um número por execução. A contagem fica no Redis, então
persiste entre runs: se o run for reiniciado, ele continua de onde parou e
não ultrapassa o teto.

.env:
  LINKEDIN_TETO_APLICACOES=50   # 0 ou ausente = sem teto (usa max_vagas por run)

Reset (começar um novo lote): apague a chave no Redis ou chame reset_count().
"""
import os

_KEY = "linkedin:aplicacoes:{user_id}"


def _redis():
    try:
        import redis
        return redis.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            username=os.getenv("REDIS_USER", "open-anime"),
            password=os.getenv("REDIS_PASSWORD", "open-anime"),
            db=int(os.getenv("REDIS_DB", 0)),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    except Exception:
        return None


def get_teto() -> int:
    """Teto total de candidaturas lido do .env. 0 = sem teto."""
    try:
        return int(os.getenv("LINKEDIN_TETO_APLICACOES", "0"))
    except (TypeError, ValueError):
        return 0


def get_count(user_id: str = "admin") -> int:
    """Quantas candidaturas já foram aplicadas (contagem persistente no Redis)."""
    r = _redis()
    if not r:
        return 0
    try:
        v = r.get(_KEY.format(user_id=user_id))
        return int(v) if v else 0
    except Exception:
        return 0


def incr_count(user_id: str = "admin", n: int = 1) -> int:
    """Soma +n ao contador e retorna o novo total."""
    r = _redis()
    if not r:
        return 0
    try:
        return int(r.incrby(_KEY.format(user_id=user_id), n))
    except Exception:
        return 0


def reset_count(user_id: str = "admin") -> None:
    """Zera o contador (novo lote)."""
    r = _redis()
    if r:
        try:
            r.delete(_KEY.format(user_id=user_id))
        except Exception:
            pass


def teto_atingido(user_id: str = "admin") -> bool:
    """True se o teto do .env já foi atingido (e há teto definido)."""
    teto = get_teto()
    if teto <= 0:
        return False
    return get_count(user_id) >= teto
