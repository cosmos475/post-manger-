"""
Database connection pool management.

Single asyncpg pool shared across the application, sized conservatively for
Render's free-tier 512MB RAM constraint and Neon's free-tier connection limits.
"""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def init_pool(database_url: str) -> asyncpg.Pool:
    """
    Create and store the global connection pool.

    Must be called once during application startup before any queries run.
    Safe to call only once; call `close_pool` before re-initializing.
    """
    global _pool
    if _pool is not None:
        logger.warning("init_pool called but pool already exists; reusing existing pool")
        return _pool

    _pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=1,
        max_size=5,  # single-user bot; keep small for free-tier connection limits
        command_timeout=30,
    )
    logger.info("Database connection pool initialized")
    return _pool


def get_pool() -> asyncpg.Pool:
    """
    Return the active connection pool.

    Raises:
        RuntimeError: if called before `init_pool` (indicates a startup
            ordering bug — the app must init the pool before handling
            any webhook update).
    """
    if _pool is None:
        raise RuntimeError(
            "Database pool has not been initialized. Call init_pool() during "
            "application startup before handling any requests."
        )
    return _pool


async def close_pool() -> None:
    """Gracefully close the pool. Call during application shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed")
