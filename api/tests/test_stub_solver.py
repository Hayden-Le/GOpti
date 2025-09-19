from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

import pytest

from api import db
from api.schemas import SolveEvent, SolveRequest, StartPoint
from api.solver import stub_solver


class DummyConn:
    def __enter__(self):  # noqa: D401
        return self

    def __exit__(self, *args):  # noqa: D401
        return False


@pytest.fixture
def fake_rows() -> List[Dict[str, object]]:
    base = datetime(2025, 6, 11, 8, 0, tzinfo=timezone.utc)
    rows: List[Dict[str, object]] = []
    for idx, mins in enumerate((0, 60)):
        start = base + timedelta(minutes=mins + 30)
        end = start + timedelta(minutes=60)
        rows.append(
            {
                "event_id": f"evt_{idx}",
                "min_dwell_min": 20,
                "venue_name": f"Venue {idx}",
                "address": "Somewhere",
                "lat": -33.86 + idx * 0.001,
                "lng": 151.21 + idx * 0.001,
                "start_ts": start,
                "end_ts": end,
            }
        )
    return rows


@pytest.fixture(autouse=True)
def patch_db(monkeypatch, fake_rows):
    monkeypatch.setattr(db, "get_conn", lambda dsn: DummyConn())
    monkeypatch.setattr(db, "fetch_candidates", lambda conn, event_ids, target_date: list(fake_rows))
    yield


def _request(compress: bool = False) -> SolveRequest:
    return SolveRequest(
        start=StartPoint(lat=-33.86, lng=151.21, time=datetime(2025, 6, 11, 8, 0, tzinfo=timezone.utc)),
        endTime=datetime(2025, 6, 11, 12, 0, tzinfo=timezone.utc),
        events=[SolveEvent(id="evt_0"), SolveEvent(id="evt_1", dwell_min=45)],
        compressDwellToMin=compress,
    )


def test_stub_solver_returns_polyline_and_sources():
    response = stub_solver.solve_stub(_request(), dsn="postgresql://test")
    assert response.metrics.visited == 2
    assert response.route[0].polyline
    assert response.route[0].source["directions"]["provider"]


def test_compress_dwell_uses_event_min(fake_rows):
    fake_rows[1]["min_dwell_min"] = 15
    response = stub_solver.solve_stub(_request(compress=True), dsn="postgresql://test")
    dwell_minutes = response.route[1].dwellSec // 60
    assert dwell_minutes == 15
