CREATE TABLE IF NOT EXISTS venues (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  address TEXT,
  lat DOUBLE PRECISION NOT NULL,
  lng DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  venue_id TEXT NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
  event_name TEXT NOT NULL,
  event_type TEXT NOT NULL,
  url TEXT,
  short_description TEXT,
  artist TEXT,
  require_booking BOOLEAN NOT NULL DEFAULT FALSE,
  booking_detail TEXT,
  subactivity_times JSONB,
  min_dwell_min INT NOT NULL DEFAULT 15,
  max_dwell_min INT NOT NULL DEFAULT 30,
  UNIQUE (venue_id, event_name, url)
);

CREATE TABLE IF NOT EXISTS event_sessions (
  id BIGSERIAL PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  start_ts TIMESTAMPTZ NOT NULL,
  end_ts   TIMESTAMPTZ NOT NULL,
  duration_min INT GENERATED ALWAYS AS (CEIL(EXTRACT(EPOCH FROM (end_ts - start_ts))/60.0)) STORED,
  UNIQUE (event_id, start_ts)
);
