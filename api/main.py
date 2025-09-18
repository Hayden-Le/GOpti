# api/main.py
import os, logging, math
from typing import List, Dict, Any, Tuple, Optional
from datetime import date, datetime, timedelta

import psycopg
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel, Field

# ---- Config ----
DSN = os.environ.get("DATABASE_URL", "postgresql://gopti:gopti@127.0.0.1:5433/gopti")
USE_ORTOOLS = os.getenv("USE_ORTOOLS", "0") == "1"
logging.basicConfig(level=logging.INFO)

# Optional OR-Tools import
try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    HAS_ORTOOLS = True
except Exception:
    HAS_ORTOOLS = False

app = FastAPI(title="GOpti API")

# ---- Models ----
class StartPoint(BaseModel):
    lat: float
    lng: float
    time: datetime

class SolveEvent(BaseModel):
    id: str
    dwell_min: Optional[int] = None

class SolveRequest(BaseModel):
    start: StartPoint
    endTime: datetime
    events: List[SolveEvent] = Field(default_factory=list)
    walkingSpeed: float = 1.35  # m/s

class StopOut(BaseModel):
    eventId: str
    sessionStart: datetime
    sessionEnd: datetime
    arrive: datetime
    depart: datetime
    travelSecFromPrev: int
    venue: Dict[str, Any]

class SolveResponse(BaseModel):
    route: List[StopOut]
    dropped: List[Dict[str, Any]]
    metrics: Dict[str, Any]

# ---- Helpers ----
def fetch_candidates(conn, event_ids: List[str], target_date: date):
    sql = """
    SELECT e.id AS event_id, e.min_dwell_min,
           v.name AS venue_name, v.address, v.lat, v.lng,
           s.start_ts, s.end_ts
    FROM events e
    JOIN venues v ON v.id = e.venue_id
    JOIN event_sessions s ON s.event_id = e.id
    WHERE e.id = ANY(%s)
      AND s.start_ts >= %s::date
      AND s.start_ts <  (%s::date + INTERVAL '1 day')
    ORDER BY s.start_ts
    """
    with conn.cursor() as cur:
        cur.execute(sql, (event_ids, str(target_date), str(target_date)))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

def haversine_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    (lat1, lon1), (lat2, lon2) = a, b
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    h = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlmb/2)**2
    return 2 * R * math.asin(math.sqrt(h))

# ---- Endpoints: health + events ----
@app.get("/health")
def health(): return {"ok": True}

@app.get("/events")
def events(date_: date = Query(..., alias="date")):
    sql = """
    SELECT e.id, e.event_name, e.event_type, e.url, e.short_description,
           e.artist, e.require_booking, e.booking_detail,
           v.name AS venue_name, v.address, v.lat, v.lng,
           s.start_ts, s.end_ts
    FROM events e
    JOIN venues v ON v.id=e.venue_id
    JOIN event_sessions s ON s.event_id=e.id
    WHERE s.start_ts >= %s::date
      AND s.start_ts <  (%s::date + INTERVAL '1 day')
    ORDER BY s.start_ts
    """
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(sql, (str(date_), str(date_)))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

# ---- Greedy fallback (always works) ----
def solve_stub(req: SolveRequest) -> SolveResponse:
    start_dt, end_dt = req.start.time, req.endTime
    target_date = start_dt.date()
    with psycopg.connect(DSN) as conn:
        event_ids = [e.id for e in req.events]
        rows = fetch_candidates(conn, event_ids, target_date)
    if not rows:
        return SolveResponse(route=[], dropped=[{"reason":"no_sessions_on_date","date":str(target_date)}],
                             metrics={"visited":0,"dropped":len(req.events),"totalWalkSec":0,"solver":"stub"})

    from collections import defaultdict
    sessions_by_event = defaultdict(list)
    venue_by_event = {}
    for r in rows:
        sessions_by_event[r["event_id"]].append(r)
        venue_by_event[r["event_id"]] = {"name": r["venue_name"], "address": r["address"],
                                          "lat": float(r["lat"]), "lng": float(r["lng"])}
    for k in sessions_by_event:
        sessions_by_event[k].sort(key=lambda x: x["start_ts"])

    curr_time = start_dt
    curr_pos = (req.start.lat, req.start.lng)
    route, dropped = [], []
    total_walk = 0
    dwell_map = {e.id: (e.dwell_min or 15) for e in req.events}

    for ev in [e.id for e in req.events]:
        picked = None
        for s in sessions_by_event.get(ev, []):
            walk_sec = int(haversine_m(curr_pos, (float(s["lat"]), float(s["lng"]))) / max(req.walkingSpeed, 0.1))
            arrive = max(curr_time + timedelta(seconds=walk_sec), s["start_ts"])
            depart = arrive + timedelta(minutes=dwell_map.get(ev, 15))
            if depart <= s["end_ts"] and depart <= end_dt:
                picked = (s, walk_sec, arrive, depart)
                break
        if picked:
            s, walk_sec, arrive, depart = picked
            total_walk += walk_sec
            route.append(StopOut(
                eventId=ev,
                sessionStart=s["start_ts"],
                sessionEnd=s["end_ts"],
                arrive=arrive,
                depart=depart,
                travelSecFromPrev=int(walk_sec),
                venue=venue_by_event.get(ev, {"name":"", "address":"", "lat":float(s["lat"]), "lng":float(s["lng"])})
            ))
            curr_time, curr_pos = depart, (float(s["lat"]), float(s["lng"]))
        else:
            # Debug detail: why infeasible
            tried = [
                {
                "sessionStart": s["start_ts"].isoformat(),
                "sessionEnd": s["end_ts"].isoformat(),
                "walkSec": int(haversine_m(curr_pos, (float(s["lat"]), float(s["lng"]))) / max(req.walkingSpeed, 0.1)),
                "earliestArrive": (max(curr_time + timedelta(seconds=int(haversine_m(curr_pos, (float(s["lat"]), float(s["lng"]))) / max(req.walkingSpeed, 0.1))), s["start_ts"])).isoformat(),
                "departIfVisited": (max(curr_time + timedelta(seconds=int(haversine_m(curr_pos, (float(s["lat"]), float(s["lng"]))) / max(req.walkingSpeed, 0.1))), s["start_ts"]) + timedelta(minutes=dwell_map.get(ev, 15))).isoformat()
                }
                for s in sessions_by_event.get(ev, [])
            ]
            dropped.append({
                "eventId": ev,
                "reason": "no_feasible_session_within_windows",
                "tried": tried
            })

    return SolveResponse(route=route, dropped=dropped,
                         metrics={"visited":len(route), "dropped":len(dropped),
                                  "totalWalkSec":int(total_walk), "solver":"stub"})

# ---- OR-Tools solver (optional) ----
def solve_ortools(req: SolveRequest) -> SolveResponse:
    if not HAS_ORTOOLS:
        raise RuntimeError("OR-Tools not installed. pip install ortools")

    start_dt, end_dt = req.start.time, req.endTime
    target_date = start_dt.date()
    with psycopg.connect(DSN) as conn:
        event_ids = [e.id for e in req.events]
        rows = fetch_candidates(conn, event_ids, target_date)
    if not rows:
        return SolveResponse(route=[], dropped=[{"reason":"no_sessions_on_date","date":str(target_date)}],
                             metrics={"visited":0,"dropped":len(req.events),"totalWalkSec":0,"solver":"none"})

    dwell_overrides = {e.id: e.dwell_min for e in req.events}

    # Build nodes
    nodes: List[Dict[str, Any]] = []
    # depot
    nodes.append({"event_id": None, "pos": (req.start.lat, req.start.lng),
                  "service": 0, "tw_start": 0, "tw_end": 0,
                  "sessionStart": None, "sessionEnd": None})
    # sessions
    for r in rows:
        s, e = r["start_ts"], r["end_ts"]
        s_off = int((s - start_dt).total_seconds())
        e_off = int((e - start_dt).total_seconds())
        dwell = int(dwell_overrides.get(r["event_id"]) or r.get("min_dwell_min") or 15) * 60
        tw_start = max(0, s_off)
        tw_end = max(0, e_off - dwell)
        nodes.append({
            "event_id": r["event_id"],
            "pos": (float(r["lat"]), float(r["lng"])),
            "service": max(60, dwell),
            "tw_start": tw_start,
            "tw_end": tw_end,
            "sessionStart": s, "sessionEnd": e
        })
    # sink
    nodes.append({"event_id": None, "pos": None, "service": 0,
                  "tw_start": 0, "tw_end": 0, "sessionStart": None, "sessionEnd": None})

    depot, sink = 0, len(nodes) - 1
    horizon = int((end_dt - start_dt).total_seconds())
    nodes[depot]["tw_end"] = horizon
    nodes[sink]["tw_end"] = horizon

    manager = pywrapcp.RoutingIndexManager(len(nodes), 1, [depot], [sink])
    routing = pywrapcp.RoutingModel(manager)

    def travel_time(i: int, j: int) -> int:
        # free to sink
        if j == sink:
            return 0
        pi, pj = nodes[i]["pos"], nodes[j]["pos"]
        if not pi or not pj:
            return 0
        dist_m = haversine_m(pi, pj)
        return int(dist_m / max(req.walkingSpeed, 0.1))

    def transit_cb(from_index, to_index):
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        return max(0, travel_time(i, j) + int(nodes[i]["service"]))

    transit_idx = routing.RegisterTransitCallback(transit_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    routing.AddDimension(transit_idx, 24*3600, horizon, True, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    for ni, n in enumerate(nodes):
        idx = manager.NodeToIndex(ni)
        a, b = int(max(0, n["tw_start"])), int(max(0, n["tw_end"]))
        if b < a:  # infeasible → allow drop
            a, b = 0, 0
        time_dim.CumulVar(idx).SetRange(a, b)

    from collections import defaultdict
    groups = defaultdict(list)
    for ni in range(1, sink):
        groups[nodes[ni]["event_id"]].append(ni)
    PENALTY = 100000
    for _, idxs in groups.items():
        routing.AddDisjunction([manager.NodeToIndex(i) for i in idxs], PENALTY, 1)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(2)

    solution = routing.SolveWithParameters(params)
    if not solution:
        return SolveResponse(route=[], dropped=[{"eventId": e, "reason":"infeasible"} for e in event_ids],
                             metrics={"visited":0,"dropped":len(event_ids),"totalWalkSec":0,"solver":"ortools-none"})

    route: List[StopOut] = []
    visited = set()
    total_walk = 0
    idx = routing.Start(0)
    prev_node = depot
    while not routing.IsEnd(idx):
        node = manager.IndexToNode(idx)
        nxt = solution.Value(routing.NextVar(idx))
        nxt_node = manager.IndexToNode(nxt)

        if 1 <= node < sink:
            n = nodes[node]
            arrive_sec = solution.Value(time_dim.CumulVar(idx))
            depart_sec = arrive_sec + int(n["service"])
            walk_sec = travel_time(prev_node, node)
            total_walk += max(0, walk_sec)
            route.append(StopOut(
                eventId=n["event_id"],
                sessionStart=n["sessionStart"],
                sessionEnd=n["sessionEnd"],
                arrive=start_dt + timedelta(seconds=arrive_sec),
                depart=start_dt + timedelta(seconds=depart_sec),
                travelSecFromPrev=int(max(0, walk_sec)),
                venue={"name":"", "address":"", "lat": n["pos"][0], "lng": n["pos"][1]}
            ))
            visited.add(n["event_id"])

        prev_node = node
        idx = nxt

    dropped = [{"eventId": e, "reason": "dropped_by_solver"} for e in event_ids if e not in visited]
    return SolveResponse(
        route=route, dropped=dropped,
        metrics={"visited": len(route), "dropped": len(dropped), "totalWalkSec": int(total_walk), "solver":"ortools-tsp-tw"}
    )

# ---- Public endpoint with auto-fallback ----
@app.post("/solve", response_model=SolveResponse)
def solve(req: SolveRequest):
    if not req.events:
        raise HTTPException(400, "events[] is empty")
    if req.endTime <= req.start.time:
        raise HTTPException(400, "endTime must be after start.time")

    if USE_ORTOOLS and HAS_ORTOOLS:
        try:
            return solve_ortools(req)
        except Exception as e:
            logging.exception("ORTools crashed, falling back to stub: %s", e)

    return solve_stub(req)
