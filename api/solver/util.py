"""Solver utilities."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def haversine_m(a: Sequence[float], b: Sequence[float]) -> float:
    (lat1, lon1), (lat2, lon2) = a, b
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@dataclass
class SessionNode:
    event_id: Optional[str]
    session_start: Optional[datetime]
    session_end: Optional[datetime]
    lat: Optional[float]
    lng: Optional[float]
    service_sec: int
    tw_start: int
    tw_end: int
    venue: Dict[str, object]
    index: int = -1

    def to_debug_dict(self) -> Dict[str, object]:
        return {
            "eventId": self.event_id,
            "sessionStart": self.session_start.isoformat() if self.session_start else None,
            "sessionEnd": self.session_end.isoformat() if self.session_end else None,
            "lat": self.lat,
            "lng": self.lng,
            "serviceSec": self.service_sec,
            "twStart": self.tw_start,
            "twEnd": self.tw_end,
        }


def seconds_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds()))


def clamp_time_window(tw_start: int, tw_end: int) -> Dict[str, int]:
    start = max(0, tw_start)
    end = max(start, tw_end)
    return {"start": start, "end": end}


def dwell_seconds(preferred_min: Optional[int], default_min: Optional[int], floor_sec: int = 60) -> int:
    val_min = preferred_min if preferred_min is not None else default_min if default_min is not None else 15
    return max(floor_sec, int(val_min) * 60)


def earliest_departure(arrive: datetime, dwell_sec: int) -> datetime:
    return arrive + timedelta(seconds=dwell_sec)


def summarise_drop_reason(event_id: str, tws: Iterable[Dict[str, int]], horizon: int) -> Dict[str, object]:
    tws_list = list(tws)
    if not tws_list:
        return {"eventId": event_id, "reason": "no_sessions_for_date"}
    window_lengths = [tw["end"] - tw["start"] for tw in tws_list]
    if all(length <= 0 for length in window_lengths):
        return {"eventId": event_id, "reason": "time_window_conflict", "sessionsConsidered": len(tws_list)}
    if all(tw["start"] >= horizon for tw in tws_list):
        return {"eventId": event_id, "reason": "beyond_trip_horizon", "sessionsConsidered": len(tws_list)}
    return {"eventId": event_id, "reason": "dropped_by_solver", "sessionsConsidered": len(tws_list)}


def encode_polyline(points: Sequence[Tuple[float, float]], precision: int = 5) -> str:
    factor = 10 ** precision
    output: List[str] = []
    prev_lat = 0
    prev_lng = 0
    for lat, lng in points:
        lat_i = int(round(lat * factor))
        lng_i = int(round(lng * factor))
        output.append(_encode_value(lat_i - prev_lat))
        output.append(_encode_value(lng_i - prev_lng))
        prev_lat, prev_lng = lat_i, lng_i
    return "".join(output)


def _encode_value(value: int) -> str:
    value = value << 1
    if value < 0:
        value = ~value
    result = []
    while value >= 0x20:
        result.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    result.append(chr(value + 63))
    return "".join(result)
