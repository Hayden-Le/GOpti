# GOpti — Smart Vivid Itinerary Planner (Rebuild)

> **Plan your Vivid night like a pro.**
> Pick your favourite events, set start/end time, and GOpti builds the shortest **walking** itinerary that respects **time windows** and **dwell times**, with a graceful fallback when everything doesn’t fit.

<p align="center">
  <img alt="GOpti mock" src="docs/hero.png" width="720">
</p>

---

## Table of Contents

* [Features](#features)
* [Architecture](#architecture)
* [Tech Stack](#tech-stack)
* [Monorepo Layout](#monorepo-layout)
* [Quick Start (no external APIs)](#quick-start-no-external-apis)
* [Full Setup (Mapbox + Postgres)](#full-setup-mapbox--postgres)
* [Environment Variables](#environment-variables)
* [Database Schema](#database-schema)
* [Seed Data](#seed-data)
* [Run Locally](#run-locally)
* [API](#api)
* [Solver Logic (OR-Tools)](#solver-logic-or-tools)
* [Caching & Rate Limits](#caching--rate-limits)
* [Testing](#testing)
* [CI/CD](#cicd)
* [Roadmap](#roadmap)
* [License](#license)

---

## Features

* Event browser with filters; map pins for venues.
* Plan by **date**, **start location/time**, **end time**.
* **Optimal walking route** with **time windows** & **dwell time** per event.
* **Explainable fallback**: auto-drop least compatible events if needed.
* Map with polylines + turn-by-turn, timeline with arrive/depart.
* Shareable link and **.ics export** to calendar.
* Caching to keep routing calls fast/cheap.

---

## Architecture

```
Next.js (App Router, TS)
 ├─ UI: event picker, map, itinerary, share/export
 ├─ /api/*: BFF endpoints (validation, caching, rate limits)
 └─ Calls Python solver service

Python FastAPI Solver
 ├─ Pre-check: feasibility filters
 ├─ Primary: OR-Tools TSP with Time Windows (+ optional visits/penalties)
 ├─ Fallback: greedy insert + local search + "drop events" policy
 └─ Travel-time provider: Mapbox Matrix (prod) or Mock (dev)

PostgreSQL (+ optional PostGIS)
 ├─ events, venues, trips, trip_events
 └─ matrix_cache (distance/time) & directions cache

Infra
 ├─ Docker (web & solver)
 ├─ GitHub Actions (lint/test/build/deploy)
 └─ Hosting: Vercel (web) + Fly/Render (solver) or one VPS
```

---

## Tech Stack

* **Frontend:** Next.js 14+, TypeScript, Tailwind, shadcn/ui, Mapbox GL JS, TanStack Query, Zod.
* **Backend (BFF):** Next.js route handlers `/api/*`.
* **Solver:** FastAPI, OR-Tools, Pydantic.
* **DB:** PostgreSQL (Drizzle ORM recommended).
* **Testing:** Vitest + Testing Library (web), PyTest (solver).
* **Monitoring:** Sentry (web/solver), simple Prom metrics on solver.

---

## Monorepo Layout

```
.
├── apps
│   ├── web/                 # Next.js
│   └── solver/              # FastAPI + OR-Tools
├── packages
│   └── shared-schemas/      # Zod (TS) + JSON schemas kept in sync with Pydantic
├── data/
│   ├── venues.json
│   └── events.sample.json
├── docs/
│   └── hero.png
├── docker/
│   └── docker-compose.yml
└── .github/workflows/
    └── ci.yml
```

---

## Quick Start (no external APIs)

> Best for a 5-minute local demo. Uses **mock travel times** and **in-memory storage**.

1. **Prereqs**

* Node 20+, PNPM or NPM
* Python 3.11+
* (Optional) uv/venv for Python

2. **Install**

```bash
# Web
cd apps/web
npm i

# Solver
cd ../solver
python -m venv .venv && source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

3. **Env**
   Create `apps/web/.env.local`:

```ini
# mock mode (no Mapbox):
NEXT_PUBLIC_MAP_MODE=mock
SOLVER_BASE_URL=http://localhost:8000
# optional: disable analytics in dev
SENTRY_DSN=
```

Create `apps/solver/.env`:

```ini
PROVIDER=mock
LOG_LEVEL=info
```

4. **Run**

```bash
# Terminal A (solver)
cd apps/solver
uvicorn main:app --reload --port 8000

# Terminal B (web)
cd apps/web
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) — seed events are loaded from `data/events.sample.json`.
You can plan, solve, view map & timeline, and export `.ics` without any API keys.

---

## Full Setup (Mapbox + Postgres)

### 1) Services via Docker Compose

Create `docker/docker-compose.yml`:

```yaml
version: "3.9"
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: gopti
      POSTGRES_PASSWORD: gopti
      POSTGRES_DB: gopti
    ports: [ "5432:5432" ]
    volumes: [ "pgdata:/var/lib/postgresql/data" ]
volumes:
  pgdata:
```

Run: `docker compose -f docker/docker-compose.yml up -d`

### 2) Env

`apps/web/.env.local`:

```ini
DATABASE_URL=postgres://gopti:gopti@localhost:5432/gopti
MAPBOX_TOKEN=pk.XXXX...
NEXT_PUBLIC_MAP_MODE=mapbox
SOLVER_BASE_URL=http://localhost:8000
```

`apps/solver/.env`:

```ini
PROVIDER=mapbox
MAPBOX_TOKEN=pk.XXXX...
DATABASE_URL=postgres://gopti:gopti@localhost:5432/gopti
LOG_LEVEL=info
```

### 3) Install & Migrate

```bash
# Web (Drizzle)
cd apps/web
npm i
npm run db:generate   # generates SQL from schema
npm run db:migrate    # applies migrations

# Solver
cd ../solver
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head  # if using Alembic, or skip if solver is stateless
```

### 4) Seed

```bash
# Load venues/events into Postgres
npm run db:seed
```

### 5) Run

```bash
# Solver (uses Mapbox Matrix/Directions)
cd apps/solver
uvicorn main:app --host 0.0.0.0 --port 8000

# Web (reads/writes DB and calls solver)
cd ../web
npm run build && npm start
```

---

## Environment Variables

**Web**

* `DATABASE_URL` — Postgres connection string
* `MAPBOX_TOKEN` — Mapbox public token (only when `NEXT_PUBLIC_MAP_MODE=mapbox`)
* `NEXT_PUBLIC_MAP_MODE` — `mock` | `mapbox`
* `SOLVER_BASE_URL` — URL of solver service
* `SENTRY_DSN` — optional

**Solver**

* `PROVIDER` — `mock` | `mapbox`
* `MAPBOX_TOKEN` — required when `PROVIDER=mapbox`
* `DATABASE_URL` — optional for matrix/directions cache
* `LOG_LEVEL` — `debug|info|warn|error`

---

## Database Schema

> **Drizzle** (web) will generate equivalent SQL. A minimal hand-written version:

```sql
-- VENUES (uses your location_* fields)
CREATE TABLE IF NOT EXISTS venues (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  address TEXT,
  lat DOUBLE PRECISION NOT NULL,
  lng DOUBLE PRECISION NOT NULL
);

-- EVENTS (columns map 1:1 to CSV)
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  venue_id TEXT NOT NULL REFERENCES venues(id) ON DELETE CASCADE,
  event_name TEXT NOT NULL,
  event_type TEXT NOT NULL,            -- ideas | light | music | etc.
  url TEXT,
  short_description TEXT,
  artist TEXT,
  require_booking BOOLEAN NOT NULL DEFAULT FALSE,
  booking_detail TEXT,
  subactivity_times JSONB,             -- keep raw if present
  min_dwell_min INT NOT NULL DEFAULT 15,
  max_dwell_min INT NOT NULL DEFAULT 30,
  UNIQUE (venue_id, event_name, url)
);

-- SESSIONS (from CSV.session_times)
CREATE TABLE IF NOT EXISTS event_sessions (
  id BIGSERIAL PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  start_ts TIMESTAMPTZ NOT NULL,       -- compose from date + start_time (Australia/Sydney)
  end_ts   TIMESTAMPTZ NOT NULL,
  duration_min INT GENERATED ALWAYS AS (CEIL(EXTRACT(EPOCH FROM (end_ts - start_ts))/60.0)) STORED,
  UNIQUE (event_id, start_ts)
);
```

---

## Seed Data

Place files under `data/`:

```json
// venues.json
[
  { "id":"v_opt", "name":"Overseas Passenger Terminal", "lat":-33.8581, "lng":151.2100 },
  { "id":"v_cahill", "name":"Cahill Walk", "lat":-33.8618, "lng":151.2125 }
]
```

```json
// events.sample.json
[
  {
    "id":"evt_nocturne",
    "title":"NOCTURNE | An Immersive Journey",
    "venue_id":"v_opt",
    "start_ts":"2025-06-11T17:00:00+10:00",
    "end_ts":"2025-06-11T21:30:00+10:00",
    "min_dwell_min":20,
    "max_dwell_min":35,
    "category":"installation",
    "tags":["harbour","projection"]
  }
]
```

`npm run db:seed` imports these into Postgres (or the web app will read them directly in mock mode).

---

## Run Locally

```bash
# Terminal A (solver)
cd apps/solver
uvicorn main:app --reload --port 8000

# Terminal B (web)
cd apps/web
npm run dev
```

---

## API

### Web BFF

#### `GET /api/events?date=2025-06-11&q=&bbox=...`

Returns events for the date (from DB or seeds).
**200**:

```json
[{ "id":"evt_nocturne","title":"NOCTURNE ...","venue":{"id":"v_opt","lat":-33.8581,"lng":151.21},"window":{"start":"2025-06-11T17:00:00+10:00","end":"2025-06-11T21:30:00+10:00"},"dwell":{"min":20,"max":35},"tags":["harbour"]}]
```

#### `POST /api/solve`

Forwarded to solver after validation.
**Body**

```json
{
  "start": { "lat": -33.8587, "lng": 151.2140, "time": "2025-06-11T17:15:00+10:00" },
  "endTime": "2025-06-11T22:00:00+10:00",
  "endPoint": null,
  "walkingSpeed": 1.35,
  "events": [
    { "id": "evt_nocturne", "venue": { "lat": -33.8581, "lng": 151.2100 },
      "window": { "start": "2025-06-11T17:00:00+10:00", "end": "2025-06-11T21:30:00+10:00" },
      "dwell": { "min": 20, "max": 35 }, "popularity": 0.8 }
  ],
  "weights": { "walk": 1.0, "visitedBonus": 0.4, "latePenalty": 2.0, "waitPenalty": 0.3 }
}
```

**200**

```json
{
  "route": [
    {
      "eventId":"evt_nocturne",
      "arrive":"2025-06-11T17:28:00+10:00",
      "depart":"2025-06-11T17:55:00+10:00",
      "travelSecFromPrev":780,
      "polyline":"_ifpE..."
    }
  ],
  "dropped":[{"eventId":"evt_xyz","reason":"window_conflict"}],
  "metrics":{"totalWalkSec":780,"visited":1,"dropped":1,"solveMs":142}
}
```

### Solver (FastAPI)

* `POST /solve` — same body/response as above.
* `GET /health` — returns `{ "ok": true }`.

---

## Solver Logic (OR-Tools)

* **Model:** TSP with Time Windows (single “vehicle”, depot = start, optional end).
* **Service time:** per event dwell (start at min, can compress in fallback).
* **Optional visits:** each event has a **disjunction with penalty** so solver may drop it if infeasible.
* **Objective:**

  ```
  minimize( total_travel_sec
          + λ1 * late_penalties
          + λ2 * wait_penalties
          - λ3 * visited_bonus )
  ```
* **Fallback (if primary times out):**

  1. Greedy feasible insertion (min delta travel + window fit).
  2. Local search (2-opt / or-opt) under time windows.
  3. Dwell compression to min; if still failing → drop lowest priority.

---

## Caching & Rate Limits

* **Matrix cache**: key = hash(provider|mode|rounded\_points|time\_bucket); TTL 24h.
* **Directions cache**: key = pair(A,B); store duration + polyline; TTL 7d.
* **Rate limit** `/api/solve` per IP (e.g., 30/min).
* **Static revalidation** for `/api/events` every 15–30 min.

---

## Testing

**Web**

```bash
npm run lint
npm run typecheck
npm test         # Vitest
npm run e2e      # Cypress (optional)
```

**Solver**

```bash
pytest -q
```

**What to test**

* Time-window math (arrive ≤ end; depart = arrive + dwell).
* Golden instances (6–10 nodes) with known solutions.
* Contract tests: Zod ↔ Pydantic schemas identical.
* Perf: N=5/10/15 → solve within \~150/400/1200 ms on dev laptop (cached matrix).

---

## CI/CD

* **GitHub Actions** (`.github/workflows/ci.yml`)

  * Node: install, lint, typecheck, unit tests.
  * Python: set up, run `pytest`.
  * Build Docker images for `web` and `solver`.
* **Deploy**

  * **Web** → Vercel (set envs, point to `/apps/web`).
  * **Solver** → Fly.io/Render/Dokku with health checks and autoscale=0–1.

---

## Roadmap

* [ ] Group routing (merge preferences; Pareto itineraries).
* [ ] Editable dwell time per event in UI.
* [ ] Live crowd/closure signals → dynamic penalties.
* [ ] Multi-modal hops (light rail/ferry).
* [ ] On-device small-N solver (WASM) for offline preview.
* [ ] Admin ingest/scraper for annual Vivid schedule.

---

### Credits

Originally prototyped for USYD Coding Fest 2024 as **GOpti**. Rebuilt with a production-minded architecture for a polished developer demo.
