"""Greedy fallback solver."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Dict, List, Sequence

from .. import db
from ..schemas import DroppedReason, SolveRequest, SolveResponse, StopOut
from .directions import DirectionsProvider, StraightLineDirectionsProvider
from .travel import StraightLineTravel, TravelTimeProvider, TravelTimeWrapper
from .util import earliest_departure, summarise_drop_reason


def _make_travel_provider(req: SolveRequest, provider: TravelTimeProvider | None) -> TravelTimeProvider:
    base = provider if provider is not None else StraightLineTravel(walking_speed=req.walkingSpeed)
    return TravelTimeWrapper(base, walking_speed=req.walkingSpeed)


def _make_directions_provider(provider: DirectionsProvider | None) -> DirectionsProvider:
    return provider or StraightLineDirectionsProvider()


def solve_stub(
    req: SolveRequest,
    *,
    dsn: str,
    provider: TravelTimeProvider | None = None,
    directions: DirectionsProvider | None = None,
) -> SolveResponse:
    start_dt, end_dt = req.start.time, req.endTime
    target_date = start_dt.date()
    dwell_map = db.build_dwell_map(req.events)
    travel = _make_travel_provider(req, provider)
    directions_provider = _make_directions_provider(directions)

    with db.get_conn(dsn) as conn:
        event_ids = [e.id for e in req.events]
        rows = db.fetch_candidates(conn, event_ids, target_date)

    if not rows:
        metrics = {"visited": 0, "dropped": len(req.events), "totalWalkSec": 0, "solver": "stub"}
        dropped = [DroppedReason(eventId=e.id, reason="no_sessions_on_date") for e in req.events]
        return SolveResponse(route=[], dropped=dropped, metrics=metrics)

    sessions_by_event: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    venue_by_event: Dict[str, Dict[str, object]] = {}
    event_min_map: Dict[str, int] = {}
    for row in rows:
        sessions_by_event[row["event_id"]].append(row)
        event_min_map[row["event_id"]] = int(row.get("min_dwell_min") or 15)
        venue_by_event[row["event_id"]] = {
            "name": row["venue_name"],
            "address": row["address"],
            "lat": float(row["lat"]),
            "lng": float(row["lng"]),
        }
    for sess in sessions_by_event.values():
        sess.sort(key=lambda r: r["start_ts"])

    curr_time = start_dt
    curr_pos: Sequence[float] = (req.start.lat, req.start.lng)
    total_walk = 0
    route: List[StopOut] = []
    dropped: List[DroppedReason] = []

    for ev in req.events:
        attempts: List[Dict[str, object]] = []
        picked = None
        requested = dwell_map.get(ev.id)
        event_min = event_min_map.get(ev.id, 15)
        if requested is None:
            dwell_minutes = event_min
        else:
            dwell_minutes = max(requested, event_min)
        if req.compressDwellToMin:
            dwell_minutes = event_min
        dwell_sec = dwell_minutes * 60
        for sess in sessions_by_event.get(ev.id, []):
            venue_pos = (float(sess["lat"]), float(sess["lng"]))
            walk_sec, travel_meta = travel.travel_seconds(curr_pos, venue_pos, departure=curr_time)
            arrival = max(curr_time + timedelta(seconds=walk_sec), sess["start_ts"])
            depart = earliest_departure(arrival, dwell_sec)
            attempts.append(
                {
                    "sessionStart": sess["start_ts"].isoformat(),
                    "sessionEnd": sess["end_ts"].isoformat(),
                    "walkSec": walk_sec,
                    "arrival": arrival.isoformat(),
                    "depart": depart.isoformat(),
                }
            )
            if depart <= sess["end_ts"] and depart <= end_dt:
                picked = (sess, walk_sec, arrival, depart, travel_meta, venue_pos)
                break
        if picked:
            sess, walk_sec, arrival, depart, travel_meta, venue_pos = picked
            total_walk += walk_sec
            directions_meta = directions_provider.get_directions(curr_pos, venue_pos, departure=curr_time)
            route.append(
                StopOut(
                    eventId=ev.id,
                    sessionStart=sess["start_ts"],
                    sessionEnd=sess["end_ts"],
                    arrive=arrival,
                    depart=depart,
                    dwellSec=dwell_sec,
                    travelSecFromPrev=walk_sec,
                    venue=venue_by_event.get(
                        ev.id,
                        {"lat": venue_pos[0], "lng": venue_pos[1], "name": "", "address": ""},
                    ),
                    polyline=directions_meta.get("polyline"),
                    source={
                        "travel": travel_meta,
                        "directions": directions_meta,
                    },
                )
            )
            curr_time = depart
            curr_pos = venue_pos
        else:
            reason = "no_feasible_session_within_windows"
            if not attempts:
                reason = "no_sessions_for_event"
            dropped.append(
                DroppedReason(
                    eventId=ev.id,
                    reason=reason,
                    sessionsConsidered=len(attempts),
                    detail={"attempts": attempts},
                )
            )

    metrics = {
        "visited": len(route),
        "dropped": len(dropped),
        "totalWalkSec": total_walk,
        "solver": "stub",
    }
    return SolveResponse(route=route, dropped=dropped, metrics=metrics)
