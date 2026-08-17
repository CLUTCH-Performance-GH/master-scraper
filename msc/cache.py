"""SQLite-backed response cache.

Every HTTP fetch, Serper query, Firecrawl scrape, and LLM extraction is cached
here. You never pay for the same request twice — this is the single biggest
cost saver in the framework (the BASF project re-scraped the same 139 product
pages across 15 fix/validate scripts; with this cache each re-run is free).
"""

import hashlib
import json
import sqlite3
import threading
import time
from typing import Any, Optional

from . import settings

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(settings.CACHE_DB, check_same_thread=False)
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_ns ON cache(namespace)")
        _conn.commit()
    return _conn


def make_key(namespace: str, *parts: Any) -> str:
    raw = json.dumps([namespace, *parts], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def get(namespace: str, *parts: Any, ttl_days: Optional[float] = None) -> Optional[Any]:
    """Return the cached JSON value, or None if missing/expired."""
    key = make_key(namespace, *parts)
    with _lock:
        row = _db().execute(
            "SELECT value, created_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return None
    value, created_at = row
    if ttl_days is not None and time.time() - created_at > ttl_days * 86400:
        return None
    return json.loads(value)


def put(namespace: str, *parts: Any, value: Any) -> None:
    key = make_key(namespace, *parts)
    with _lock:
        _db().execute(
            "INSERT OR REPLACE INTO cache (key, namespace, value, created_at) VALUES (?, ?, ?, ?)",
            (key, namespace, json.dumps(value, default=str), time.time()),
        )
        _db().commit()


def stats() -> dict:
    with _lock:
        rows = _db().execute(
            "SELECT namespace, COUNT(*) FROM cache GROUP BY namespace"
        ).fetchall()
    return dict(rows)


def clear(namespace: Optional[str] = None) -> int:
    with _lock:
        cur = (
            _db().execute("DELETE FROM cache WHERE namespace = ?", (namespace,))
            if namespace
            else _db().execute("DELETE FROM cache")
        )
        _db().commit()
    return cur.rowcount
