import os
import threading

import psycopg2
import psycopg2.extras
from psycopg2 import extensions
from psycopg2.pool import ThreadedConnectionPool

_pool = None
_pool_lock = threading.Lock()


def _build_pool():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL não configurado.")
    minconn = max(1, int(os.getenv("DB_POOL_MIN", "1")))
    maxconn = max(minconn, int(os.getenv("DB_POOL_MAX", "6")))
    statement_timeout = max(0, int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000")))
    idle_timeout = max(0, int(os.getenv("DB_IDLE_TX_TIMEOUT_MS", "30000")))
    options = f"-c statement_timeout={statement_timeout} -c idle_in_transaction_session_timeout={idle_timeout}"
    return ThreadedConnectionPool(
        minconn,
        maxconn,
        database_url,
        sslmode=os.getenv("DB_SSLMODE", "require"),
        cursor_factory=psycopg2.extras.RealDictCursor,
        connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
        application_name=os.getenv("DB_APPLICATION_NAME", "sigeu-web"),
        options=options,
        keepalives=1,
        keepalives_idle=int(os.getenv("DB_KEEPALIVES_IDLE", "30")),
        keepalives_interval=int(os.getenv("DB_KEEPALIVES_INTERVAL", "10")),
        keepalives_count=int(os.getenv("DB_KEEPALIVES_COUNT", "5")),
    )


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _build_pool()
    return _pool


class PooledConnection:
    """Proxy compatível com psycopg2; close() devolve a conexão ao pool."""

    def __init__(self, pool, conn):
        self._pool = pool
        self._conn = conn
        self._returned = False

    def close(self):
        if self._returned:
            return
        try:
            if not self._conn.closed:
                status = self._conn.get_transaction_status()
                if status != extensions.TRANSACTION_STATUS_IDLE:
                    self._conn.rollback()
        except Exception:
            try:
                self._conn.reset()
            except Exception:
                pass
        finally:
            self._pool.putconn(self._conn, close=bool(self._conn.closed))
            self._returned = True

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __del__(self):
        # Rede de segurança para rotas legadas que eventualmente esqueçam close().
        # O fechamento explícito continua sendo o caminho normal.
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            try:
                self._conn.rollback()
            except Exception:
                pass
        self.close()
        return False


def _connection_is_healthy(conn):
    """Valida também conexões SSL que parecem abertas localmente mas já morreram no servidor."""
    if conn is None or conn.closed:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        # SELECT inicia uma transação no psycopg2; devolvemos a conexão limpa ao chamador.
        conn.rollback()
        return True
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return False


def get_db_connection():
    pool = _get_pool()
    ultimo_erro = None
    for _ in range(3):
        conn = pool.getconn()
        if _connection_is_healthy(conn):
            return PooledConnection(pool, conn)
        try:
            pool.putconn(conn, close=True)
        except Exception as exc:
            ultimo_erro = exc
    raise psycopg2.OperationalError(
        "Não foi possível obter uma conexão PostgreSQL saudável do pool."
        + (f" Detalhe: {ultimo_erro}" if ultimo_erro else "")
    )


def pool_stats():
    pool = _pool
    if pool is None:
        return {"initialized": False}
    # psycopg2 não expõe contadores públicos; estes campos são apenas diagnóstico.
    return {
        "initialized": True,
        "minconn": getattr(pool, "minconn", None),
        "maxconn": getattr(pool, "maxconn", None),
        "used": len(getattr(pool, "_used", {}) or {}),
        "idle": len(getattr(pool, "_pool", []) or []),
    }


def close_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
