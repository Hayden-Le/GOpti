from datetime import datetime, timezone

import pytest

from api.schemas import SolveEvent, SolveRequest, StartPoint


def _start_point() -> StartPoint:
    return StartPoint(lat=-33.86, lng=151.21, time=datetime(2025, 6, 11, 8, 0, tzinfo=timezone.utc))


def test_duplicate_event_ids_not_allowed():
    with pytest.raises(ValueError):
        SolveRequest(
            start=_start_point(),
            endTime=datetime(2025, 6, 11, 12, 0, tzinfo=timezone.utc),
            events=[SolveEvent(id="a"), SolveEvent(id="a")],
        )


def test_end_time_must_follow_start():
    with pytest.raises(ValueError):
        SolveRequest(
            start=_start_point(),
            endTime=datetime(2025, 6, 11, 7, 59, tzinfo=timezone.utc),
            events=[SolveEvent(id="a")],
        )
