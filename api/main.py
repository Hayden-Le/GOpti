"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Dict, List

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query

from . import db
from .schemas import DebugSolveResponse, SolveRequest, SolveResponse
from .providers import build_directions_provider, build_travel_provider
from .solver import stub_solver
try:  # pragma: no cover - optional dependency
    from .solver import ortools_solver
    HAS_ORTOOLS = True
except RuntimeError:
    HAS_ORTOOLS = False
except ImportError:
    HAS_ORTOOLS = False


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv()  # so .env works

MAPBOX_TOKEN = os.getenv("MAPBOX_ACCESS_TOKEN", "")
DSN = os.environ.get("DATABASE_URL", "postgresql://gopti:gopti@127.0.0.1:5433/gopti")
USE_ORTOOLS = os.getenv("USE_ORTOOLS", "0") == "1"

app = FastAPI(title="GOpti API")


def get_dsn() -> str:
    return DSN


def list_events(conn: psycopg.Connection, day: date) -> List[Dict[str, object]]:
    sql = """
    SELECT e.id, e.event_name, e.event_type, e.url, e.short_description,
           e.artist, e.require_booking, e.booking_detail,
           v.name AS venue_name, v.address, v.lat, v.lng,
           s.start_ts, s.end_ts
    FROM events e
    JOIN venues v ON v.id = e.venue_id
    JOIN event_sessions s ON s.event_id = e.id
    WHERE s.start_ts >= %s::date
      AND s.start_ts <  (%s::date + INTERVAL '1 day')
    ORDER BY s.start_ts
    """
    with conn.cursor() as cur:
        cur.execute(sql, (str(day), str(day)))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/events")
def events(date_: date = Query(..., alias="date"), dsn: str = Depends(get_dsn)) -> List[Dict[str, object]]:
    with psycopg.connect(dsn) as conn:
        return list_events(conn, date_)


def _run_solver(req: SolveRequest, dsn: str, debug: bool = False) -> SolveResponse:
    provider = "mapbox" if os.getenv("MATRIX_PROVIDER", "straight") == "mapbox" and MAPBOX_TOKEN else "straight"
    directions_provider = "mapbox" if os.getenv("DIRECTIONS_PROVIDER", "none") == "mapbox" and MAPBOX_TOKEN else "none"
    if USE_ORTOOLS and HAS_ORTOOLS:
        try:
            return ortools_solver.solve_ortools(req, dsn=dsn, provider=provider, directions=directions_provider)
        except ortools_solver.ORToolsUnavailable:  # type: ignore[attr-defined]
            logger.warning("ORTools unavailable at runtime; using stub")
        except Exception:
            logger.exception("ORTools solver failure; falling back to stub")
    return stub_solver.solve_stub(req, dsn=dsn, provider=provider, directions=directions_provider)


@app.post("/solve", response_model=SolveResponse)
def solve(req: SolveRequest, dsn: str = Depends(get_dsn)) -> SolveResponse:
    try:
        return _run_solver(req, dsn)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/debug/solve", response_model=DebugSolveResponse)
def debug_solve(req: SolveRequest, dsn: str = Depends(get_dsn)) -> DebugSolveResponse:
    result = _run_solver(req, dsn, debug=True)
    nodes = []
    matrix_meta = {}
    if USE_ORTOOLS and HAS_ORTOOLS:
        try:
            debug = ortools_solver.inspect_last_run()  # type: ignore[attr-defined]
            nodes = debug.get("nodes", [])
            matrix_meta = debug.get("matrix", {})
        except AttributeError:
            nodes = []
    return DebugSolveResponse(**result.dict(), nodes=nodes, matrixMeta=matrix_meta)
