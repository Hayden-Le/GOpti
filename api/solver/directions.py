"""Directions provider implementations."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional, Sequence, Tuple

import httpx

from ..cache import DirectionsCacheKey, DirectionsCacheRepository
from .util import encode_polyline

logger = logging.getLogger(__name__)

DirectionsResult = Dict[str, object]


class DirectionsProvider:
    def get_directions(
        self,
        origin: Sequence[float],
        destination: Sequence[float],
        *,
        departure: Optional[datetime] = None,
    ) -> DirectionsResult:
        raise NotImplementedError


class StraightLineDirectionsProvider(DirectionsProvider):
    def get_directions(
        self,
        origin: Sequence[float],
        destination: Sequence[float],
        *,
        departure: Optional[datetime] = None,
    ) -> DirectionsResult:
        points: Tuple[Tuple[float, float], Tuple[float, float]] = (
            (float(origin[0]), float(origin[1])),
            (float(destination[0]), float(destination[1])),
        )
        polyline = encode_polyline(points)
        return {
            "provider": "straight_line",
            "polyline": polyline,
        }


class MapboxDirectionsProvider(DirectionsProvider):
    base_url = "https://api.mapbox.com/directions/v5/mapbox"

    def __init__(self, token: str, *, profile: str = "walking", timeout: float = 8.0) -> None:
        if not token:
            raise ValueError("Mapbox access token required")
        self.token = token
        self.profile = profile
        self.timeout = timeout

    def get_directions(
        self,
        origin: Sequence[float],
        destination: Sequence[float],
        *,
        departure: Optional[datetime] = None,
    ) -> DirectionsResult:
        coords = f"{origin[1]:.6f},{origin[0]:.6f};{destination[1]:.6f},{destination[0]:.6f}"
        params = {
            "access_token": self.token,
            "geometries": "polyline6",
            "overview": "full",
            "steps": "false",
        }
        url = f"{self.base_url}/{self.profile}/{coords}"
        response = httpx.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        routes = data.get("routes") or []
        if not routes:
            raise RuntimeError("no routes in Mapbox response")
        route = routes[0]
        return {
            "provider": "mapbox",
            "polyline": route.get("geometry"),
            "durationSec": int(route.get("duration", 0)),
            "distanceM": int(route.get("distance", 0)),
        }


class CachedDirectionsProvider(DirectionsProvider):
    def __init__(
        self,
        repo: DirectionsCacheRepository,
        provider: DirectionsProvider,
        *,
        fallback: Optional[DirectionsProvider] = None,
        provider_name: str = "mapbox",
        mode: str = "walk",
    ) -> None:
        self.repo = repo
        self.provider = provider
        self.fallback = fallback or StraightLineDirectionsProvider()
        self.provider_name = provider_name
        self.mode = mode

    def get_directions(
        self,
        origin: Sequence[float],
        destination: Sequence[float],
        *,
        departure: Optional[datetime] = None,
    ) -> DirectionsResult:
        key = DirectionsCacheKey(self.provider_name, self.mode, tuple(origin), tuple(destination))
        cached = self.repo.get(key)
        if cached:
            return {
                "provider": self.provider_name,
                "polyline": cached.get("polyline"),
                "durationSec": cached.get("duration_sec"),
                "distanceM": cached.get("distance_m"),
                "cached": True,
            }
        try:
            result = self.provider.get_directions(origin, destination, departure=departure)
            self.repo.store(
                key,
                result.get("polyline"),
                result.get("durationSec"),
                result.get("distanceM"),
                result,
            )
            result["cached"] = False
            return result
        except Exception as exc:  # pragma: no cover - network branch
            logger.warning("directions provider failed (%s); using straight polyline", exc)
            return self.fallback.get_directions(origin, destination, departure=departure)
