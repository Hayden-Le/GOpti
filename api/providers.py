"""Factory helpers for travel-time and directions providers."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from .cache import DirectionsCacheRepository, MatrixCacheRepository
from .solver.directions import (
    CachedDirectionsProvider,
    DirectionsProvider,
    MapboxDirectionsProvider,
    StraightLineDirectionsProvider,
)
from .solver.travel import (
    CachedTravelProvider,
    MapboxMatrixProvider,
    StraightLineTravel,
    TravelTimeProvider,
)


@lru_cache(maxsize=1)
def _mapbox_token() -> Optional[str]:
    return os.environ.get("MAPBOX_ACCESS_TOKEN")


@lru_cache(maxsize=4)
def get_matrix_repo(dsn: str) -> MatrixCacheRepository:
    ttl = int(os.environ.get("MATRIX_CACHE_TTL_MIN", "1440"))
    return MatrixCacheRepository(dsn, ttl_minutes=ttl)


@lru_cache(maxsize=4)
def get_directions_repo(dsn: str) -> DirectionsCacheRepository:
    ttl = int(os.environ.get("DIRECTIONS_CACHE_TTL_MIN", "10080"))
    return DirectionsCacheRepository(dsn, ttl_minutes=ttl)


def build_travel_provider(dsn: str) -> TravelTimeProvider:
    provider_name = os.environ.get("MATRIX_PROVIDER", "straight").lower()
    if provider_name == "mapbox":
        token = _mapbox_token() or ""
        if not token:
            return StraightLineTravel()
        repo = get_matrix_repo(dsn)
        base = MapboxMatrixProvider(token)
        return CachedTravelProvider(repo, base, provider_name="mapbox", mode="walk")
    return StraightLineTravel()


def build_directions_provider(dsn: str) -> DirectionsProvider:
    provider_name = os.environ.get("DIRECTIONS_PROVIDER", "mapbox").lower()
    token = _mapbox_token() or ""
    if provider_name == "mapbox" and token:
        repo = get_directions_repo(dsn)
        base = MapboxDirectionsProvider(token)
        return CachedDirectionsProvider(repo, base, provider_name="mapbox", mode="walk")
    repo = get_directions_repo(dsn)
    fallback = StraightLineDirectionsProvider()
    return CachedDirectionsProvider(repo, fallback, provider_name="fallback", mode="walk")
