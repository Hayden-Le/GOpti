import csv, json, uuid, os
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
import psycopg

# Data is vivid_event_data.csv from C:\Users\huyle\Downloads\Github Projects\gopti\vivid_event_data.csv
CSV_PATH = os.environ.get("CSV_PATH", "vivid_event_data.csv")
DSN = os.environ.get(
  "DATABASE_URL",
  "postgresql://gopti:gopti@127.0.0.1:5432/gopti"   # force IPv4 + 'postgresql://' scheme
)
TZ = ZoneInfo("Australia/Sydney")

def jloads(x):
    if not x or str(x).strip().upper() == "NA": return None
    return json.loads(x)

with psycopg.connect(DSN, autocommit=True) as conn, open(CSV_PATH, newline='', encoding="utf-8") as f:
    cur = conn.cursor()
    for r in csv.DictReader(f):
        # venue
        loc = jloads(r["location_coord"]) or {}
        lat, lng = float(loc.get("latitude")), float(loc.get("longitude"))
        venue_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, r["location_name"]+"|"+r["location_address"]))
        cur.execute("""
          INSERT INTO venues(id,name,address,lat,lng)
          VALUES (%s,%s,%s,%s,%s)
          ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,address=EXCLUDED.address,lat=EXCLUDED.lat,lng=EXCLUDED.lng
        """,(venue_id, r["location_name"], r["location_address"], lat, lng))

        # event
        event_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, r["event_name"]+"|"+r["url"]))
        sub = jloads(r["subactivity_times"])
        require_booking = str(r["require_booking"]).strip().lower() == "true"
        cur.execute("""
          INSERT INTO events(id,venue_id,event_name,event_type,url,short_description,artist,require_booking,booking_detail,subactivity_times)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT (id) DO UPDATE SET event_type=EXCLUDED.event_type,url=EXCLUDED.url,
            short_description=EXCLUDED.short_description,artist=EXCLUDED.artist,
            require_booking=EXCLUDED.require_booking,booking_detail=EXCLUDED.booking_detail,
            subactivity_times=EXCLUDED.subactivity_times
        """, (event_id, venue_id, r["event_name"], r["event_type"], r["url"], r["short_description"],
              r["artist"], require_booking, r["booking_detail"], json.dumps(sub) if sub else None))

        # sessions
        sess = jloads(r["session_times"]) or {}
        for d, t in sess.items():
            start = datetime.fromisoformat(f"{d}T{t['start_time']}").replace(tzinfo=TZ)
            end   = datetime.fromisoformat(f"{d}T{t['end_time']}").replace(tzinfo=TZ)
            if end <= start:
                end += timedelta(days=1)  # handle windows crossing midnight
            cur.execute("""
              INSERT INTO event_sessions(event_id,start_ts,end_ts)
              VALUES (%s,%s,%s)
              ON CONFLICT (event_id,start_ts) DO NOTHING
            """,(event_id, start, end))

print("✅ Ingestion complete")
