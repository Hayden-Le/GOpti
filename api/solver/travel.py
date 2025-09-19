"""Travel time providers used by the solver."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Sequence, Tuple

import httpx

from ..cache import MatrixCacheKey, MatrixCacheRepository
from .util import haversine_m

logger = logging.getLogger(__name__)

TravelResult = Tuple[int, Dict[str, object]]


class TravelTimeProvider:
    """Abstract travel-time provider."""

    def travel_seconds(
        self,
        origin: Sequence[float],
        destination: Sequence[float],
        *,
        departure: Optional[datetime] = None,
    ) -> TravelResult:
        raise NotImplementedError


@dataclass
class StraightLineTravel(TravelTimeProvider):
    walking_speed: float = 1.35

    def travel_seconds(
        self,
        origin: Sequence[float],
        destination: Sequence[float],
        *,
        departure: Optional[datetime] = None,
    ) -> TravelResult:
        dist = haversine_m(origin, destination)
        speed = max(self.walking_speed, 0.05)
        seconds = int(dist / speed)
        return seconds, {"provider": "straight_line", "distanceM": dist}


class MapboxMatrixProvider(TravelTimeProvider):
    base_url = "https://api.mapbox.com/directions-matrix/v1/mapbox"

    def __init__(
        self,
        token: str,
        *,
        profile: str = "walking",
        timeout: float = 5.0,
    ) -> None:
        if not token:
            raise ValueError("Mapbox access token required")
        self.token = token
        self.profile = profile
        self.timeout = timeout

    def travel_seconds(
        self,
        origin: Sequence[float],
        destination: Sequence[float],
        *,
        departure: Optional[datetime] = None,
    ) -> TravelResult:
        coords = f"{origin[1]:.6f},{origin[0]:.6f};{destination[1]:.6f},{destination[0]:.6f}"
        params = {
            "access_token": self.token,
            "annotations": "duration,distance",
        }
        url = f"{self.base_url}/{self.profile}/{coords}"
        response = httpx.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        durations = data.get("durations") or []
        distances = data.get("distances") or []
        if len(durations) < 1 or len(durations[0]) < 2:
            raise RuntimeError(f"unexpected matrix response: {json.dumps(data)[:120]}")
        seconds = int(durations[0][1])
        distance_m = None
        if distances and len(distances[0]) >= 2:
            distance_m = distances[0][1]
        meta = {
            "provider": "mapbox",
            "profile": self.profile,
            "distanceM": distance_m,
        }
        return seconds, meta


class TravelTimeWrapper(TravelTimeProvider):
    """Delegate provider that can override walking speed at runtime."""

    def __init__(self, inner: TravelTimeProvider, walking_speed: float) -> None:
        self.inner = inner
        self.walking_speed = walking_speed

    def travel_seconds(
        self,
        origin: Sequence[float],
        destination: Sequence[float],
        *,
        departure: Optional[datetime] = None,
    ) -> TravelResult:
        if isinstance(self.inner, StraightLineTravel):
            clone = StraightLineTravel(walking_speed=self.walking_speed)
            return clone.travel_seconds(origin, destination, departure=departure)
        return self.inner.travel_seconds(origin, destination, departure=departure)


class CachedTravelProvider(TravelTimeProvider):
    def __init__(
        self,
        repo: MatrixCacheRepository,
        provider: TravelTimeProvider,
        *,
        fallback: Optional[TravelTimeProvider] = None,
        provider_name: str = "mapbox",
        mode: str = "walk",
        bucket: Optional[str] = None,
    ) -> None:
        self.repo = repo
        self.provider = provider
        self.fallback = fallback or StraightLineTravel()
        self.provider_name = provider_name
        self.mode = mode
        self.bucket = bucket

    def travel_seconds(
        self,
        origin: Sequence[float],
        destination: Sequence[float],
        *,
        departure: Optional[datetime] = None,
    ) -> TravelResult:
        key = MatrixCacheKey(self.provider_name, self.mode, tuple(origin), tuple(destination), self.bucket)
        cached = self.repo.get(key)
        if cached:
            return int(cached["duration_sec"]), {
                "provider": self.provider_name,
                "mode": self.mode,
                "cached": True,
                "distanceM": cached.get("distance_m"),
            }
        try:
            seconds, meta = self.provider.travel_seconds(origin, destination, departure=departure)
            distance = meta.get("distanceM") if isinstance(meta, dict) else None
            self.repo.store(key, seconds, int(distance) if distance is not None else None, meta if isinstance(meta, dict) else {})
            return seconds, meta | {"cached": False}
        except Exception as exc:  # pragma: no cover - network branch
            logger.warning("matrix provider failed (%s); falling back to straight-line", exc)
            seconds, meta = self.fallback.travel_seconds(origin, destination, departure=departure)
            return seconds, meta | {"provider": meta.get("provider", "straight_line"), "fallback": True}
