"""Database helpers for the GOpti API."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Dict, Iterable, Iterator, List

import psycopg

from .schemas import SolveEvent


@contextmanager
def get_conn(dsn: str) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(dsn)
    try:
        yield conn
    finally:
        conn.close()


def fetch_candidates(conn: psycopg.Connection, event_ids: Iterable[str], target_date: date) -> List[Dict[str, object]]:
    sql = """
    SELECT e.id AS event_id,
           e.event_name,
           e.min_dwell_min,
           v.id AS venue_id,
           v.name AS venue_name,
           v.address,
           v.lat,
           v.lng,
           s.start_ts,
           s.end_ts
    FROM events e
    JOIN venues v ON v.id = e.venue_id
    JOIN event_sessions s ON s.event_id = e.id
    WHERE e.id = ANY(%s)
      AND s.start_ts >= %s::date
      AND s.start_ts <  (%s::date + INTERVAL '1 day')
    ORDER BY s.start_ts
    """
    with conn.cursor() as cur:
        ev_ids = list(event_ids)
        cur.execute(sql, (ev_ids, str(target_date), str(target_date)))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_dwell_map(events: Iterable[SolveEvent], default_min: int = 15) -> Dict[str, int]:
    dwell_map: Dict[str, int] = {}
    for ev in events:
        dwell_map[ev.id] = int(ev.dwell_min or default_min)
    return dwell_map
