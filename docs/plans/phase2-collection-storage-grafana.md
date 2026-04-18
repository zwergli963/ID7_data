# Phase 2 — Collection, Storage, Derivations, Grafana

Approved plan (detailed version: `~/.claude/plans/polymorphic-cooking-fern.md`). This file tracks execution. Check boxes as each step passes its verification.

## Deliverable 1 — SQLite schema + init_db.py

- [x] Write `schema.sql` with 4 tables (`readings`, `charging_sessions`, `trips`, `metadata`) + indexes + UNIQUE constraints.
- [x] Write `init_db.py` (loads `schema.sql`, seeds metadata, idempotent).
- [x] Verify: `python init_db.py` creates `data/id7.db` with 4 tables; rerun is a no-op.

## Deliverable 2 — collect.py

- [x] Write `collect.py` (login → fetch → flatten → INSERT OR IGNORE → update `last_poll_at`).
- [x] Verify: first run inserts 1 row; second run within 60 s inserts 0 rows (content-hash dedup — see note in `collect.py`).

## Deliverable 3 — derive_sessions.py

- [x] Write `derive_sessions.py` (wipe + rebuild `charging_sessions` and `trips` from `readings`, single transaction).
- [x] Verify: runs cleanly on empty-ish db (0 rows out); synthetic charging/trip data produces correct kWh / consumption.

## Deliverable 4 — launchd scheduling

- [x] Write `launchd/com.id7data.collect.plist` (StartInterval=300, RunAtLoad=true).
- [x] Write `launchd/com.id7data.derive.plist` (StartInterval=900, RunAtLoad=true).
- [x] Write `launchd/README.md` with install/uninstall/troubleshoot.
- [x] `plutil -lint` passes on both plists.
- [ ] **USER VERIFICATION:** follow install steps in `launchd/README.md`, then `launchctl list | grep id7` shows both jobs; `sqlite3 data/id7.db "SELECT COUNT(*) FROM readings"` grows over 30 min.

## Deliverable 5 — Grafana

- [x] Write `grafana/docker-compose.yml` (grafana-oss + `frser-sqlite-datasource` plugin, SQLite mounted read-only).
- [x] Write `grafana/provisioning/datasources/sqlite.yaml` (declarative datasource).
- [x] Write `grafana/provisioning/dashboards/dashboards.yaml` (dashboard provider).
- [x] Write `grafana/dashboards/id7.json` (10 panels across 4 rows: Live status / Battery & odometer / Charging sessions / Trips & map).
- [x] YAML + JSON syntax validated.
- [ ] **USER VERIFICATION:** install Docker Desktop if not present, then `cd grafana && docker compose up -d`. Browse `http://localhost:3000` (admin/admin), confirm data source green + dashboard populates, then `docker compose restart` and re-confirm.

## End-to-end verification

- [ ] 4 tables in `data/id7.db`.
- [ ] 2 launchd jobs visible in `launchctl list`.
- [ ] `readings` row count grows ~1 per 5 min.
- [ ] Grafana dashboard shows SoC, sessions, trips, parking map.
- [ ] Dashboard persists across container restart.
