"""Caching utilities for travel-time and directions lookups."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import psycopg


@dataclass(frozen=True)
class MatrixCacheKey:
    provider: str
    mode: str
    origin: Tuple[float, float]
    destination: Tuple[float, float]
    bucket: Optional[str] = None

    def serialise(self) -> str:
        lat1 = round(self.origin[0], 4)
        lng1 = round(self.origin[1], 4)
        lat2 = round(self.destination[0], 4)
        lng2 = round(self.destination[1], 4)
        bucket = f":{self.bucket}" if self.bucket else ""
        return f"{self.provider}:{self.mode}:{lat1},{lng1}->{lat2},{lng2}{bucket}"


@dataclass(frozen=True)
class DirectionsCacheKey:
    provider: str
    mode: str
    origin: Tuple[float, float]
    destination: Tuple[float, float]

    def serialise(self) -> str:
        lat1 = round(self.origin[0], 5)
        lng1 = round(self.origin[1], 5)
        lat2 = round(self.destination[0], 5)
        lng2 = round(self.destination[1], 5)
        return f"{self.provider}:{self.mode}:{lat1},{lng1}->{lat2},{lng2}"


class _BaseCacheRepository:
    dsn: str

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._ensure_table()

    def _ensure_table(self) -> None:  # pragma: no cover - table creation
        raise NotImplementedError


class MatrixCacheRepository(_BaseCacheRepository):
    def __init__(self, dsn: str, *, ttl_minutes: int = 1440) -> None:
        self.ttl_minutes = ttl_minutes
        super().__init__(dsn)

    def _ensure_table(self) -> None:  # pragma: no cover - DDL
        sql = """
        CREATE TABLE IF NOT EXISTS matrix_cache (
            cache_key TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            mode TEXT NOT NULL,
            duration_sec INTEGER NOT NULL,
            distance_m INTEGER,
            meta JSONB,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()

    def get(self, key: MatrixCacheKey) -> Optional[Dict[str, object]]:
        sql = """
        SELECT duration_sec, distance_m, meta
        FROM matrix_cache
        WHERE cache_key = %s
          AND expires_at > now()
        """
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, (key.serialise(),))
            row = cur.fetchone()
        if not row:
            return None
        duration, distance, meta = row
        return {
            "duration_sec": int(duration),
            "distance_m": int(distance) if distance is not None else None,
            "meta": meta or {},
        }

    def store(self, key: MatrixCacheKey, duration_sec: int, distance_m: Optional[int], meta: Dict[str, object]) -> None:
        expiry = datetime.now(timezone.utc) + timedelta(minutes=self.ttl_minutes)
        sql = """
        INSERT INTO matrix_cache (cache_key, provider, mode, duration_sec, distance_m, meta, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (cache_key)
        DO UPDATE SET duration_sec = EXCLUDED.duration_sec,
                      distance_m = EXCLUDED.distance_m,
                      meta = EXCLUDED.meta,
                      expires_at = EXCLUDED.expires_at,
                      provider = EXCLUDED.provider,
                      mode = EXCLUDED.mode
        """
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    key.serialise(),
                    key.provider,
                    key.mode,
                    int(duration_sec),
                    int(distance_m) if distance_m is not None else None,
                    meta,
                    expiry,
                ),
            )
            conn.commit()


class DirectionsCacheRepository(_BaseCacheRepository):
    def __init__(self, dsn: str, *, ttl_minutes: int = 10080) -> None:
        self.ttl_minutes = ttl_minutes
        super().__init__(dsn)

    def _ensure_table(self) -> None:  # pragma: no cover - DDL
        sql = """
        CREATE TABLE IF NOT EXISTS directions_cache (
            cache_key TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            mode TEXT NOT NULL,
            polyline TEXT,
            duration_sec INTEGER,
            distance_m INTEGER,
            meta JSONB,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()

    def get(self, key: DirectionsCacheKey) -> Optional[Dict[str, object]]:
        sql = """
        SELECT polyline, duration_sec, distance_m, meta
        FROM directions_cache
        WHERE cache_key = %s
          AND expires_at > now()
        """
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, (key.serialise(),))
            row = cur.fetchone()
        if not row:
            return None
        polyline, duration, distance, meta = row
        return {
            "polyline": polyline,
            "duration_sec": int(duration) if duration is not None else None,
            "distance_m": int(distance) if distance is not None else None,
            "meta": meta or {},
        }

    def store(
        self,
        key: DirectionsCacheKey,
        polyline: Optional[str],
        duration_sec: Optional[int],
        distance_m: Optional[int],
        meta: Dict[str, object],
    ) -> None:
        expiry = datetime.now(timezone.utc) + timedelta(minutes=self.ttl_minutes)
        sql = """
        INSERT INTO directions_cache (cache_key, provider, mode, polyline, duration_sec, distance_m, meta, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (cache_key)
        DO UPDATE SET polyline = EXCLUDED.polyline,
                      duration_sec = EXCLUDED.duration_sec,
                      distance_m = EXCLUDED.distance_m,
                      meta = EXCLUDED.meta,
                      expires_at = EXCLUDED.expires_at,
                      provider = EXCLUDED.provider,
                      mode = EXCLUDED.mode
        """
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    key.serialise(),
                    key.provider,
                    key.mode,
                    polyline,
                    int(duration_sec) if duration_sec is not None else None,
                    int(distance_m) if distance_m is not None else None,
                    meta,
                    expiry,
                ),
            )
            conn.commit()
