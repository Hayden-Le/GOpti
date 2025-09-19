from api.solver.travel import CachedTravelProvider, StraightLineTravel


class DummyRepo:
    def __init__(self) -> None:
        self.stored = None
        self.cached = None

    def get(self, key):  # noqa: D401 - interface contract
        return self.cached

    def store(self, key, duration_sec, distance_m, meta):  # noqa: D401
        self.stored = (key, duration_sec, distance_m, meta)
        self.cached = {
            "duration_sec": duration_sec,
            "distance_m": distance_m,
            "meta": meta,
        }


def test_cached_travel_provider_stores_and_reads_from_cache():
    repo = DummyRepo()
    provider = CachedTravelProvider(repo, StraightLineTravel(walking_speed=1.0), provider_name="test", mode="walk")
    origin = (-33.86, 151.21)
    dest = (-33.861, 151.212)

    first_duration, first_meta = provider.travel_seconds(origin, dest)
    assert repo.stored is not None
    assert first_meta["cached"] is False

    second_duration, second_meta = provider.travel_seconds(origin, dest)
    assert second_duration == first_duration
    assert second_meta["cached"] is True
    assert "provider" in second_meta
