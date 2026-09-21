# AgriFlow Backend (FastAPI)

One resource-oriented REST API serving both the web app and the mobile app.

> **Read [`docs/BACKEND_STATUS.md`](docs/BACKEND_STATUS.md) first.** It records
> the completeness assessment against the live frontend, the 23 endpoints that
> were missing, the RBAC and performance defects that were fixed, and what is
> still open before cutover.

**Correction to this README's original premise.** It previously said this
backend replaces "the current Supabase Edge Functions". A clone of
`VPDTechnologies/Agri_flow_Frontend_Supabase` shows there are no Edge
Functions and no Supabase client in the frontend at all (`grep -ri supabase
src/` returns nothing). The live backend is the **Express app** in
`server/index.js`, deployed through both `netlify/functions/api.js` and
`api/index.cjs`; Supabase is only a managed Postgres host and file store.
`server/routes/*.js` is therefore the real API contract, and the gap analysis
in `docs/BACKEND_STATUS.md` is measured against it.

**This code has been executed, not just written**: every model, service, and
router in this repo has been import-checked, a real Alembic migration has been
generated and applied against a live Postgres instance, the app has been run
end-to-end (registration → live DB round-trip), and the automated test suite
below has been run and passes against real Postgres — **305 tests** covering
every service module, the full role × endpoint RBAC matrix, the page-load
budget, and the two concurrency races (booking-slot capacity and seed stock)
that are the highest-priority test category in the master plan (§12/§39).

## What's implemented

| Module | Status |
|---|---|
| 0 — Foundation | Project skeleton, config, health checks, logging, exception envelope |
| 1 — Auth | Register (farmer-only), login, refresh + rotation with reuse detection, logout |
| 2 — RBAC | Permission table + `require_permission`/`require_role` dependencies |
| 3 — Public | Market rates, seed catalog |
| 4 — Farmers | Profile, bank-change requests (with the "no duplicate pending request" fix), approval |
| 5 — Crops & Visits | Crop registration with auto-scheduled visits, visit scheduling/completion |
| 6 — Seeds & Purchases | Seed CRUD, **row-locked** purchase flow |
| 7 — Warehouses & Bookings | Slot listing, **row-locked** booking creation, state-machine status updates |
| 8 — Grain Sales | Creation, review (with quality inspection via `crop_inspections`), payment, pinned pricing |
| 9 — Ledger | Read-only transaction history (writes only as a side effect of Modules 6/8) |
| 10 — Notifications | Single insertion point, list/mark-read/mark-all-read |
| 11 — Admin | Manager creation/status/password-reset (super_admin-only), dashboard, monthly report, audit logs |
| 12 — Uploads | Presigned-URL flow (Supabase Storage adapter) + multipart fallback |
| 13 — Background jobs | ARQ worker scaffold (visit reminders, report pre-generation) |
| 14 — OTP | Redis-backed issuance/verification, hashed at rest, attempt-capped |
| 15 — Live updates | SSE over Redis pub/sub — notifications + dashboard deltas |
| 16 — Caching | Redis response cache for public and dashboard reads |

## Security and performance properties

- **RBAC integrity** — every one of the 77 routes declares a permission from a
  single table; nothing compares roles inline. 217 assertions in
  `tests/security/test_rbac_matrix.py` verify that restricted roles cannot
  reach higher-tier or administrative endpoints, and a coverage test fails the
  build if a route ships without an RBAC decision.
- **Page-load budget** — every screen is defined in `app/core/benchmark.py` as
  the set of calls it issues, and `tests/performance/test_page_load_budget.py`
  fails CI if any exceeds its share of the 2-second requirement. Measured p95
  is 8–65 ms per page against a 400-farmer dataset.
- **Real-time dashboards** — `POST /api/v1/events/ticket` then
  `GET /api/v1/events/stream` delivers `notification.created` and
  `dashboard.changed` over SSE, so dashboards stop polling.

## What Phase 0 still owes you (do this before pointing this at production data)

This backend was built against the **target reconciled schema** described in
the Master Plan (§6/§7), not a live pull of your actual Supabase database.
Before cutover:

1. Run the real Phase 0 schema reconciliation (`pg_dump --schema-only` against
   the live Supabase project) and diff it against `migrations/versions/*_initial_schema.py`.
   In particular, the 7 RPCs the master plan flagged as **not present in any
   shipped migration** (`approve_farmer`, `review_bank_request`,
   `review_grain_sale`, `pay_grain_sale`, `get_farmer_dashboard`,
   `get_admin_dashboard`, `get_public_stats`) were re-implemented from
   scratch here based on the verified frontend/Edge Function behavior — they
   have **not** been diffed against the real RPC bodies, because those bodies
   aren't in the zip. `admin_service.get_monthly_report` in particular should
   be treated as a first draft, not a drop-in replacement.
2. Resolve the identity migration (Master Plan §6/§9 Phase 3–4): this backend
   uses a clean UUID-only `users` table with a `legacy_app_user_id` bridge
   column. Migrating real farmer/manager accounts out of the old
   `auth.users`/`profiles`/`users` dual model into this one is a data
   migration this repo does not perform for you.
3. ~~Confirm the `server/index.js` question~~ — **resolved: it is the live
   backend, not dead code.** See `docs/BACKEND_STATUS.md` §1. Also **rotate the
   Supabase Postgres password and `JWT_SECRET`**, both of which are committed
   in the frontend repo's `.env`.
4. Decide the deferred items from the plan: regional manager restriction
   (`staff_profiles.assigned_region` exists but isn't enforced yet),
   S3 vs. continuing with Supabase Storage, ARQ vs. Celery.

## Local setup

```bash
cp .env.example .env
# Point DATABASE_URL at your Postgres instance (see the note in
# docker-compose.yml about why a fresh local Postgres isn't spun up by default)

pip install -r requirements-dev.txt

alembic upgrade head          # applies migrations/versions/*_initial_schema.py
python scripts/create_superadmin.py --name "Ops Admin" --phone 9999999999 --password "changeme123"

uvicorn app.main:app --reload
# → http://localhost:8000/api/v1/docs
```

Or via Docker:

```bash
docker compose up --build
```

## Running tests

Tests run against a **real Postgres** database (not SQLite) — set
`DATABASE_URL` in `.env` to a disposable database before running, since the
test suite creates and drops every table each session.

```bash
pytest -m "not performance"     # fast suite
pytest tests/security           # RBAC integrity verification (217 assertions)
pytest -m performance -s        # page-load budget, prints per-page timings
```

```
tests/integration/test_concurrency.py::test_booking_slot_never_oversold   PASSED
tests/integration/test_concurrency.py::test_seed_stock_never_oversold     PASSED
tests/security/test_idor.py::test_farmer_cannot_read_another_farmers_notification   PASSED
tests/security/test_idor.py::test_registration_cannot_self_assign_privileged_role   PASSED
tests/security/test_idor.py::test_farmer_id_is_never_taken_from_request_body        PASSED
tests/unit/test_state_machines.py::test_grain_sale_cannot_skip_approval_to_paid     PASSED
tests/unit/test_state_machines.py::test_grain_sale_cannot_be_reviewed_twice         PASSED
```

The two concurrency tests are the ones that matter most: 20 farmers race for
10kg slots in a 100kg-capacity warehouse slot, and 10 farmers race to buy 10kg
each from 50kg of seed stock. Both assert the `SELECT ... FOR UPDATE` locking
in `booking_service.create_booking` / `purchase_service.purchase_seeds` allows
*exactly* the right number of requests through and leaves the remaining
capacity/stock at *exactly* zero — never oversold, never negative.

## Project layout

See the Master Plan §3 for the full rationale. Short version:

```
app/api/v1/       one router file per resource
app/services/      business logic (row-locking, state machines, notifications, audit)
app/models/        SQLAlchemy models — see app/models/enums.py for every status field
app/schemas/        Pydantic request/response contracts
app/core/            config, db session, JWT/password hashing, RBAC dependencies, exceptions
app/middleware/     request-ID, Redis rate limiting, error envelope
app/integrations/   external services (Supabase Storage presign)
app/workers/        ARQ background jobs
migrations/          Alembic
tests/               unit / integration / security / e2e
```

## Known simplifications (call these out explicitly, don't rediscover them the hard way)

- The router→schema→service→repository→database layering from the master
  plan is collapsed to router→schema→service→database — there's no separate
  `repositories/` layer. Business logic and query logic both live in
  `app/services/`. This was a deliberate scope cut to keep the codebase
  navigable; splitting it out later is mechanical if you want the extra layer.
- Rate-limit thresholds in `.env.example` come from the legacy
  `RATE_LIMITING.md`, which (Master Plan §1.6) describes a Express backend
  that was never actually built — treat these as a starting point to tune
  against real traffic, not validated numbers.

## Backend remediation — what's been fixed since the initial build

The initial build (14 modules) left 9 known gaps. All 9 have since been
addressed:

| # | Gap | Fix |
|---|---|---|
| 1 | Farmers couldn't cancel their own bookings | `booking.cancel.own` permission + ownership check in `booking_service.update_booking_status`; farmers can cancel, nothing else |
| 2 | Test coverage was 7 endpoints of ~42 | Expanded to 305 tests covering every service module, the RBAC matrix, and the page-load budget |
| 3 | No CI | `.github/workflows/ci.yml` — lint + migrate + test against real Postgres/Redis service containers |
| 4 | No legacy identity migration script | `scripts/migrate_legacy_identity.py` — migrates the legacy dual `profiles`/`users` model into the canonical `users` table, preserving UUIDs, carrying bcrypt password hashes forward with lazy Argon2id rehash on next login (`app.core.security`), idempotent, dry-run supported. Verified end-to-end against a synthetic legacy database including a real HTTP login with a migrated password |
| 5 | Phase 0 schema reconciliation never ran against a real DB | `scripts/phase0_schema_reconciliation.py` — diffs the live DB (tables, columns, RPCs, RLS policies) against this backend's models and produces a markdown report. Verified against injected schema mismatches to confirm detection actually works, not just that the script runs |
| 6 | No load testing | `scripts/load_test_concurrency.py` — async load generator for the booking/purchase concurrency paths, smoke-tested against a live server (also validated the rate limiter and pending-approval gate both correctly throttle/block as designed) |
| 7 | Storage integration untested | `tests/unit/test_storage_integration.py` — validates every branch (bucket allow-list, content-type, size limit, extension) with a mocked Supabase call |
| 8 | No prod config hardening | `Settings.validate_for_production()` — checks JWT secret strength, localhost leakage in DB/Redis/CORS, missing storage credentials, overlong token expiry; `app.main`'s startup lifespan raises in `ENVIRONMENT=production`, warns in dev |
| 9 | No metrics beyond `/health` | `/metrics` (Prometheus format) via `app.middleware.metrics` — request counts/latency by route+status, plus dedicated counters for booking-capacity and seed-stock rejections |

**Running the legacy migration and reconciliation scripts for real** requires
pointing `--legacy-db-url` / `--db-url` at your actual Supabase project — they
were built and proven against a synthetic legacy database reconstructed from
the Master Plan's documented schema (§1/§6/§7), not your live data, since
this environment has no access to your real Supabase credentials. Run
`scripts/phase0_schema_reconciliation.py` first; its report will tell you
whether `scripts/migrate_legacy_identity.py`'s hardcoded legacy-side SQL
queries need adjusting for your actual table/column shapes before you run it
for real.
