"""OR-Tools based optimal solver."""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2  # type: ignore
except ImportError as exc:  # pragma: no cover - depends on optional extra
    pywrapcp = None  # type: ignore[assignment]
    routing_enums_pb2 = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

from .. import db
from ..schemas import DroppedReason, SolveRequest, SolveResponse, StopOut
from .directions import DirectionsProvider, StraightLineDirectionsProvider
from .travel import StraightLineTravel, TravelTimeProvider, TravelTimeWrapper
from .util import (
    SessionNode,
    clamp_time_window,
    earliest_departure,
    summarise_drop_reason,
)


@dataclass
class SolverConfig:
    time_limit_sec: float = 2.5
    drop_penalty: Optional[int] = None
    slack_max: int = 900  # seconds


class ORToolsUnavailable(RuntimeError):
    """Raised when OR-Tools is not installed."""


_LAST_DEBUG: Dict[str, object] = {}


def inspect_last_run() -> Dict[str, object]:
    """Return debug information for the last OR-Tools solve."""
    return _LAST_DEBUG



def _make_travel_provider(req: SolveRequest, provider: TravelTimeProvider | None) -> TravelTimeProvider:
    base = provider if provider is not None else StraightLineTravel(walking_speed=req.walkingSpeed)
    return TravelTimeWrapper(base, walking_speed=req.walkingSpeed)


def _make_directions_provider(provider: DirectionsProvider | None) -> DirectionsProvider:
    return provider or StraightLineDirectionsProvider()


def _departure_hint(start_dt: datetime, node: SessionNode) -> timedelta:
    if node.session_start:
        return node.session_start - start_dt
    if node.tw_start:
        return timedelta(seconds=max(0, node.tw_start))
    return timedelta(seconds=0)


def solve_ortools(
    req: SolveRequest,
    *,
    dsn: str,
    provider: TravelTimeProvider | None = None,
    directions: DirectionsProvider | None = None,
    config: SolverConfig | None = None,
) -> SolveResponse:
    if _IMPORT_ERROR is not None or pywrapcp is None or routing_enums_pb2 is None:
        raise ORToolsUnavailable("ortools is not installed; pip install ortools==9.*")

    cfg = config or SolverConfig()
    travel = _make_travel_provider(req, provider)
    directions_provider = _make_directions_provider(directions)

    start_dt, end_dt = req.start.time, req.endTime
    target_date = start_dt.date()
    dwell_overrides = db.build_dwell_map(req.events)

    with db.get_conn(dsn) as conn:
        event_ids = [ev.id for ev in req.events]
        rows = db.fetch_candidates(conn, event_ids, target_date)

    if not rows:
        dropped = [DroppedReason(eventId=e.id, reason="no_sessions_on_date") for e in req.events]
        metrics = {"visited": 0, "dropped": len(dropped), "totalWalkSec": 0, "solver": "ortools-none", "solveMs": 0}
        return SolveResponse(route=[], dropped=dropped, metrics=metrics)

    nodes: List[SessionNode] = []
    depot = SessionNode(
        event_id=None,
        session_start=None,
        session_end=None,
        lat=req.start.lat,
        lng=req.start.lng,
        service_sec=0,
        tw_start=0,
        tw_end=0,
        venue={"name": "start", "lat": req.start.lat, "lng": req.start.lng},
    )
    nodes.append(depot)

    horizon = max(0, int((end_dt - start_dt).total_seconds()))
    depot.tw_end = horizon

    node_per_event: Dict[str, List[SessionNode]] = defaultdict(list)
    for row in rows:
        event_default = int(row.get("min_dwell_min") or 15)
        requested = dwell_overrides.get(row["event_id"])
        if requested is None:
            dwell_minutes = event_default
        else:
            dwell_minutes = max(requested, event_default)
        if req.compressDwellToMin:
            dwell_minutes = event_default
        dwell_sec = dwell_minutes * 60
        s_start = row["start_ts"]
        s_end = row["end_ts"]
        start_offset = int((s_start - start_dt).total_seconds())
        end_offset = int((s_end - start_dt).total_seconds())
        latest_start = min(horizon, end_offset - dwell_sec)
        tw = clamp_time_window(start_offset, latest_start)
        node = SessionNode(
            event_id=row["event_id"],
            session_start=s_start,
            session_end=s_end,
            lat=float(row["lat"]),
            lng=float(row["lng"]),
            service_sec=dwell_sec,
            tw_start=tw["start"],
            tw_end=tw["end"],
            venue={
                "name": row["venue_name"],
                "address": row["address"],
                "lat": float(row["lat"]),
                "lng": float(row["lng"]),
            },
        )
        nodes.append(node)
        node_per_event[row["event_id"]].append(node)

    sink = SessionNode(
        event_id=None,
        session_start=None,
        session_end=None,
        lat=None,
        lng=None,
        service_sec=0,
        tw_start=0,
        tw_end=horizon,
        venue={"name": "end"},
    )
    nodes.append(sink)

    for idx, node in enumerate(nodes):
        node.index = idx

    if cfg.drop_penalty is None:
        cfg.drop_penalty = max(3000, horizon * 4)

    num_nodes = len(nodes)
    travel_matrix: List[List[int]] = [[0 for _ in range(num_nodes)] for _ in range(num_nodes)]
    source_matrix: List[List[Dict[str, object]]] = [[{} for _ in range(num_nodes)] for _ in range(num_nodes)]

    for i, src in enumerate(nodes):
        origin = (src.lat, src.lng) if src.lat is not None and src.lng is not None else None
        departure_dt = start_dt + _departure_hint(start_dt, src)
        for j, dst in enumerate(nodes):
            if i == j or dst.lat is None or dst.lng is None or origin is None:
                travel_matrix[i][j] = 0
                source_matrix[i][j] = {"provider": "none"}
                continue
            seconds, meta = travel.travel_seconds(origin, (dst.lat, dst.lng), departure=departure_dt)
            travel_matrix[i][j] = max(0, seconds)
            meta_copy = dict(meta) if isinstance(meta, dict) else {"value": meta}
            meta_copy.update({"from": src.event_id, "to": dst.event_id})
            source_matrix[i][j] = meta_copy

    global _LAST_DEBUG
    _LAST_DEBUG = {
        "nodes": [node.to_debug_dict() for node in nodes],
        "matrix": {"travel": travel_matrix, "sources": source_matrix},
        "params": {
            "dropPenalty": cfg.drop_penalty,
            "horizonSec": horizon,
            "eventCount": len(node_per_event),
            "compressDwell": req.compressDwellToMin,
        },
    }

    manager = pywrapcp.RoutingIndexManager(num_nodes, 1, [depot.index], [sink.index])
    routing = pywrapcp.RoutingModel(manager)

    def transit_cb(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        service = nodes[i].service_sec
        travel_time = travel_matrix[i][j]
        return service + travel_time

    transit_idx = routing.RegisterTransitCallback(transit_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    routing.AddDimension(transit_idx, cfg.slack_max, horizon, True, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    for node in nodes:
        idx = manager.NodeToIndex(node.index)
        start = max(0, node.tw_start)
        end = max(start, node.tw_end)
        time_dim.CumulVar(idx).SetRange(start, end)

    for event_id, event_nodes in node_per_event.items():
        disjunction = [manager.NodeToIndex(n.index) for n in event_nodes]
        routing.AddDisjunction(disjunction, cfg.drop_penalty, 1)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(max(1, int(cfg.time_limit_sec)))

    start_ms = time.perf_counter()
    solution = routing.SolveWithParameters(params)
    duration_ms = int((time.perf_counter() - start_ms) * 1000)

    if not solution:
        dropped = [DroppedReason(eventId=ev.id, reason="infeasible") for ev in req.events]
        metrics = {
            "visited": 0,
            "dropped": len(dropped),
            "totalWalkSec": 0,
            "solver": "ortools-timeout",
            "solveMs": duration_ms,
        }
        return SolveResponse(route=[], dropped=dropped, metrics=metrics)

    route: List[StopOut] = []
    visited_events: Dict[str, bool] = {}
    total_walk = 0

    index = routing.Start(0)
    prev_node_index = manager.IndexToNode(index)
    while not routing.IsEnd(index):
        node_index = manager.IndexToNode(index)
        next_index = solution.Value(routing.NextVar(index))
        if 0 < node_index < sink.index:
            node = nodes[node_index]
            arrive_sec = solution.Value(time_dim.CumulVar(index))
            arrive_dt = start_dt + timedelta(seconds=arrive_sec)
            depart_dt = earliest_departure(arrive_dt, node.service_sec)
            walk_sec = travel_matrix[prev_node_index][node_index]
            total_walk += walk_sec
            origin_node = nodes[prev_node_index]
            origin_pos = (origin_node.lat, origin_node.lng)
            dest_pos = (node.lat, node.lng)
            directions_meta = {}
            if origin_pos[0] is not None and origin_pos[1] is not None and dest_pos[0] is not None and dest_pos[1] is not None:
                directions_meta = directions_provider.get_directions(
                    (origin_pos[0], origin_pos[1]),
                    (dest_pos[0], dest_pos[1]),
                    departure=arrive_dt,
                )
            route.append(
                StopOut(
                    eventId=node.event_id or "",
                    sessionStart=node.session_start,
                    sessionEnd=node.session_end,
                    arrive=arrive_dt,
                    depart=depart_dt,
                    dwellSec=node.service_sec,
                    travelSecFromPrev=walk_sec,
                    venue=node.venue,
                    polyline=directions_meta.get("polyline") if directions_meta else None,
                    source={
                        "travel": source_matrix[prev_node_index][node_index],
                        "directions": directions_meta,
                    },
                )
            )
            if node.event_id:
                visited_events[node.event_id] = True
        prev_node_index = node_index
        index = next_index

    dropped: List[DroppedReason] = []
    for event in req.events:
        if visited_events.get(event.id):
            continue
        tws = [{"start": n.tw_start, "end": n.tw_end} for n in node_per_event.get(event.id, [])]
        dropped.append(DroppedReason(**summarise_drop_reason(event.id, tws, horizon)))

    metrics = {
        "visited": len(route),
        "dropped": len(dropped),
        "totalWalkSec": total_walk,
        "solver": "ortools-tsp-tw",
        "solveMs": duration_ms,
    }
    return SolveResponse(route=route, dropped=dropped, metrics=metrics)
