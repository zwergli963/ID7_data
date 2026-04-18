# Roadmap

Ordered list of phases. Each phase has a narrow, verifiable goal — when the verification passes, the phase is done and we move to the next one.

---

## ✅ Phase 1 — Exploration spike (done)

**Goal:** Prove we can log in to WeConnect and see what data the ID.7 exposes.

**Delivered:**
- `explore.py` — authenticates, fetches full domain tree, writes timestamped JSON dump.
- Confirmed field coverage (SoC, range, odometer, charging, climate, position, temperatures, capabilities).
- Documented ~40 capability flags and noted which are restricted on this account.

**Plan file:** `~/.claude/plans/nifty-skipping-pond.md` (Phase 1 section).

---

## ✅ Phase 2 — Collection, storage, Grafana dashboards (done, pending launchd install)

**Goal:** Continuously collect readings into SQLite, derive charging sessions and trips, and put live + historical dashboards on top in Grafana.

**Delivered:**
- `schema.sql` + `init_db.py` — 4 tables (`readings`, `charging_sessions`, `trips`, `metadata`), UNIQUE constraints, indexes, seed values for `schema_version`, `battery_kwh_usable`, `last_poll_at`.
- `collect.py` — one-shot fetch → flatten via `vehicle.as_dict()` → content-hash dedup → `INSERT OR IGNORE` → update `last_poll_at`. Prints a one-line summary. Every insert also stores the full raw payload in `raw_json` for future-proofing.
- `derive_sessions.py` — idempotent full rebuild of `charging_sessions` and `trips` from `readings`. Sessions group contiguous `charging_state='charging'` rows; trips group contiguous `vehicle_state='driving'` rows.
- `launchd/com.id7data.collect.plist` (5-min interval) + `launchd/com.id7data.derive.plist` (15-min interval) + `launchd/README.md` covering install, verify, kickstart, uninstall, troubleshoot.
- `grafana/docker-compose.yml` + provisioned `frser-sqlite-datasource` + provisioned dashboard provider + `grafana/dashboards/id7.json`.
- 10-panel dashboard: live stats (SoC gauge, range, outside temp, doors) → SoC + odometer timeseries → charging sessions table + kWh/day bar → trips table + geomap (CartoDB Dark Matter basemap, orange marker).

**Notable design decisions / deviations from the original plan:**
- **Dedup is content-hash based**, not `carCapturedTimestamp`-based. The CarConnectivity library's per-field `upd` values mix VW-server times and library wall-clock — unreliable as a change signal. Instead we hash the dynamic fields (state, odometer, SoC, charging state, power, position) and skip the insert when the latest stored row matches.
- **Dashboard time picker clips client-side.** The `frser-sqlite-datasource` plugin doesn't expose `$__timeFrom()` / `$__timeTo()` / `$__timeFilter()` macros. Queries return the full table; Grafana narrows the X-axis visually. Fine at current data volumes; revisit in Phase 3 if it becomes slow.
- **Consumption clamped to ≥ 0** in `trips` so a mid-trip regen blip doesn't show negative kWh. May under-report for downhill-heavy trips — flagged for the Phase 3 data-quality pass.

**Verification checklist:**

- [x] `python init_db.py` creates `data/id7.db` with 4 tables.
- [x] `python collect.py` inserts 1 row; second run skips (content-hash dedup).
- [x] `derive_sessions.py` runs cleanly on empty and populated data; synthetic charging+trip data produces correct kWh and kWh/100 km.
- [x] Grafana reachable at `localhost:3000` with green SQLite data source; dashboard loaded from provisioned JSON, survives `docker compose restart`.
- [ ] **User action pending:** install launchd jobs (`launchd/README.md`). After install: `launchctl list | grep id7` shows 2 jobs; `SELECT COUNT(*) FROM readings` grows over 30 min.

**Plan file:** `docs/plans/phase2-collection-storage-grafana.md`

---

## 🔨 Phase 3 — Server deployment (next)

**Goal:** Move the full stack from the local Mac to a Hetzner Cloud VPS so data collection runs 24/7 and Grafana is accessible from anywhere.

- **Provision server** — Hetzner CX11 (2 vCPU, 4 GB RAM, ~€3.29/month), Ubuntu 24.04 LTS.
- **Install dependencies** — Python 3.11, Docker, Docker Compose, git via `apt`.
- **Deploy project** — copy source files to server; manually transfer `config.json` and existing `data/id7.db`.
- **Set up scheduling** — Linux `cron` replaces macOS launchd; same 5-min / 15-min intervals.
- **Start Grafana** — `docker compose up -d`; dashboard accessible at `http://<server-ip>:3000`.
- **Firewall** — open ports 22 (SSH) and 3000 (Grafana) only via Hetzner's web UI.
- **Stop local launchd jobs** — unload Mac agents once server is confirmed working.
- **Optional:** point a free DuckDNS subdomain to the server IP for a memorable URL.

**Plan file:** `docs/plans/phase3-server-deployment.md`

---

## 📅 Phase 4 — Smarter polling & data quality

**Goal:** Reduce pointless API calls without losing resolution where it matters, and fix any data-quality issues found after a week of real collection.

- **Adaptive polling** — poll every 5 min while plugged in or recently active, every 30 min while idle. Implementation: small state check in `collect.py` that reads the latest `readings` row and early-exits if it's too soon for the current regime.
- **Charge power curve** — capture higher-resolution samples during active DC charge sessions to build clean power-vs-SoC curves.
- **Data quality pass** — after ~7 days, review readings for anomalies (e.g. odometer going backwards, position jumps, regen pushing `kwh_used` negative). Add sanity filters to the derivation scripts if needed.
- **Dashboard time filtering** — evaluate whether client-side X-axis clipping is still good enough. If not, either migrate `captured_at` to a parallel unix-epoch column (and use `$__unixEpochGroupSeconds`) or switch plugin.
- **Session boundary rules** — consider ending a charging session on `connector_connection='disconnected'` rather than on `charging_state != 'charging'`, so we capture the full plug-in-to-plug-out window.

---

## 📅 Phase 5 — Notifications & automations

**Goal:** Get proactive alerts instead of having to look at the dashboard.

- Charge-complete notification (SoC reached target, or charging state went `charging` → `off`).
- Door-unlock-while-parked alert.
- Low-SoC warning when not plugged in.
- Send via [ntfy.sh](https://ntfy.sh) (free, no account) or Pushover (nicer app).
- Small `notify.py` script reading the newest `readings` rows, keyed off a `last_notified_at` in `metadata`.

---

## 📅 Phase 6 — Nicer visuals / public-style dashboard

**Goal:** A dashboard that's genuinely a pleasure to look at, not just functional.

- Polish Grafana panels: custom colours, thresholds, icons, unit formatting.
- Consider a custom Streamlit / Observable Framework page for a hand-crafted "this year in my car" style report (annual / monthly summaries, maps, personal bests).
- Optionally: expose a read-only public dashboard (Grafana snapshot URL or static Observable page) for sharing highlights.

---

## 📅 Phase 7 — Home Assistant bolt-on (optional)

**Goal:** Get the HA features we intentionally skipped (mobile app, geofencing, integration with other smart-home things).

- Install Home Assistant (server Docker or dedicated Pi).
- Run `carconnectivity-plugin-mqtt_homeassistant` + Mosquitto broker — this is the "official" path and publishes all sensors via MQTT auto-discovery.
- Alternative: write a small `mqtt_publisher.py` that reads the newest row from our SQLite and publishes to an MQTT broker HA listens on — keeps SQLite as the source of truth.

---

## 💤 Backlog (no committed timing)

- **Remote commands from the API** — start/stop charging, start/stop climatization, lock/unlock doors. The library supports these; wire them into a small CLI.
- **Charging cost tracking** — join `charging_sessions` with home vs. public electricity prices.
- **Departure planner** — schedule pre-conditioning via `climatisationTimers` capability.
- **Nav destinations push** — send a destination from a script/phone to the car via the `destinations` capability.
- **Multi-car support** — schema already has room for a `vin` column; `collect.py` loops over garage today, just need to carry VIN through the derivations.
