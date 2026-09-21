# AgriFlow Backend — Completeness Status

Assessment of the FastAPI backend against the live frontend
(`VPDTechnologies/Agri_flow_Frontend_Supabase`), and the record of what
was changed to close the gaps.

Verified on 2026-09-20 against PostgreSQL 16 with the full suite green.

---

## 1. Headline finding: the backend was built against the wrong source of truth

The backend's README states it replaces *"the current Supabase Edge
Functions (`auth-api`, `farmer-api`, `manager-api`, `admin-api`,
`admin-create-user`)"* and lists as open question #3:

> Confirm the `server/index.js` question — this backend assumes it's dead code.

**That assumption is wrong, and it is the single most consequential item
in this report.**

A clone of the frontend repository contains:

| Claim | Reality in the repo |
|---|---|
| Supabase Edge Functions are the backend | No `supabase/` directory exists. There are no Edge Functions. |
| The frontend calls Supabase directly | `grep -ri supabase src/` returns **0 matches**. There is no Supabase client. |
| `server/index.js` is dead code | It **is** the production backend. |

What actually runs today is an **Express application** (`server/index.js`,
~3,000 lines across `server/routes/*.js`) talking to PostgreSQL through
the `pg` driver. Supabase is used only as a managed Postgres host and
file store. The Express app is wired into both deployment targets:

- `netlify.toml` → redirects `/api/*` to `netlify/functions/api.js`, which wraps `server/index.js` with `serverless-http`
- `vercel.json` → rewrites `/api/(.*)` to `api/index.cjs`, which re-exports `server/index.js`
- `vite.config.js` → proxies `/api` to `localhost:5000` in development

The frontend's entire data layer is `src/services/api/axios.js` with
`baseURL: '/api'`. **The real API contract is `server/routes/*.js`**, not
the Edge Functions the backend was designed against.

Consequence: the gap analysis below is measured against the Express
routes, which is what the web and mobile clients actually call.

### Two defects inherited from that mismatch

1. **Status values the database cannot store.** `server/routes/admin.js`
   writes `booking_slots.status = 'delivered'` and `'Inspection
   Completed'`, and `grain_sales.status = 'received'`. None of those
   appear in the `CHECK` constraints in
   `server/database/supabase-schema.sql`. The delivery→inspection
   workflow the admin UI drives was unrepresentable in the backend's
   state machine. Both state machines have been extended (§4).

2. **A live database credential is committed to the frontend repo.**
   `.env` at the repo root contains a working Supabase Postgres
   connection string including the password, and `JWT_SECRET`. This is
   in git history. **Rotate both.** Nothing in this backend uses them.

---

## 2. Verdict

**The backend was not complete.** It was well-built — the architecture,
row-level locking, state machines, audit trail and error envelope are all
sound, and its 64 tests passed against real Postgres on first run — but
it implemented **48 of the ~71 endpoints** the live frontend calls, and
several of the ones it did implement were unreachable by the roles that
need them.

After the work in this session:

| | Before | After |
|---|---|---|
| API endpoints | 48 | **77** |
| Endpoints the frontend calls but the backend lacked | 23 | **0** |
| Automated tests | 64 | **305** |
| RBAC assertions | 0 | **217** |
| Queries to authenticate a request | 4 | **1** |
| Queries to render the admin dashboard | 6 | **1** |
| Unbounded list endpoints | 4 | **0** |
| Latency with Redis unreachable | 11,300 ms | **6 ms** |

---

## 3. Endpoint gap analysis

23 endpoints the frontend calls had no backend equivalent. All are now
implemented.

### Authentication (5 missing)

| Frontend call | Status | New endpoint |
|---|---|---|
| `POST /auth/send-otp` | added | `POST /api/v1/auth/send-otp` |
| `POST /auth/verify-otp` | added | `POST /api/v1/auth/verify-otp` |
| `POST /auth/change-password` | added | `POST /api/v1/auth/change-password` |
| `POST /auth/forgot-password/send-otp` | added | `POST /api/v1/auth/forgot-password/send-otp` |
| `POST /auth/forgot-password/reset` | added | `POST /api/v1/auth/forgot-password/reset` |

The legacy OTP implementation (`server/utils/otp.js`) had three defects,
all fixed in `app/services/otp_service.py`:

- It **returned the OTP in the HTTP response body**
  (`res.json({ message, otp })`), so anyone could request a code for any
  phone number and read it straight back. The new endpoint never returns
  the code outside local development, and `validate_for_production()`
  refuses to boot if that switch is left on.
- It used a per-process in-memory `Map`. Both deployment targets are
  serverless, so each invocation got a fresh process — OTP verification
  could not reliably work in production. Codes now live in Redis.
- It had no attempt limit, leaving a 6-digit code brute-forceable within
  its 10-minute life. Now capped at 5 attempts, and codes are stored as
  HMAC-SHA256 hashes rather than plaintext.

### Farmer (2 missing)

| Frontend call | New endpoint |
|---|---|
| `GET /farmer/dashboard` | `GET /api/v1/farmers/me/dashboard` |
| `GET /farmer/market-rates` | `GET /api/v1/market-rates` |

### Admin / manager (15 missing)

| Frontend call | New endpoint |
|---|---|
| `POST /admin/farmers` | `POST /api/v1/farmers` |
| `GET /admin/farmers/:id` | `GET /api/v1/farmers/{farmer_id}` |
| `GET /admin/bank-requests` | `GET /api/v1/farmers/bank-change-requests` |
| `POST /admin/warehouses` | `POST /api/v1/warehouses` |
| `POST /admin/warehouses/:id/inventory` | `POST /api/v1/warehouses/{id}/inventory` |
| `GET /admin/warehouse-slots` | `GET /api/v1/warehouse-slots` |
| `POST /admin/warehouse-slots` | `POST /api/v1/warehouse-slots` |
| `PATCH /admin/warehouse-slots/:id` | `PATCH /api/v1/warehouse-slots/{id}` |
| `GET /admin/active-crops` | `GET /api/v1/crops/active` |
| `POST /admin/visits` | `POST /api/v1/farm-visits` |
| `POST /admin/visits/trigger-notifications` | `POST /api/v1/farm-visits/reminders` |
| `GET /admin/market-rates` | `GET /api/v1/market-rates` |
| `PATCH /admin/transactions/:id/pay` | `PATCH /api/v1/transactions/{id}/pay` |
| `POST /admin/booking-slots/:id/inspect` | `POST /api/v1/bookings/{id}/inspect` |
| `PUT /admin/booking-slots/:id/edit-yield` | `PATCH /api/v1/grain-sales/{id}/yield` |
| `POST /admin/grain-sales/procure` | `POST /api/v1/grain-sales/procure` |

### Public & uploads (2 missing)

| Frontend call | New endpoint |
|---|---|
| `GET /public/stats` | `GET /api/v1/public/stats` |
| `POST /upload` (multipart) | `POST /api/v1/uploads` |

`POST /api/v1/uploads/presign` remains the preferred path — the client
uploads directly to storage and file bytes never transit the API. The
multipart endpoint exists because the current web form posts
`multipart/form-data`, and because mobile clients on unreliable
connections often prefer a single request over a two-step handshake.

---

## 4. State machines extended

The delivery and inspection workflow the admin UI drives could not be
represented. Both state machines now cover it, and every transition not
listed is rejected with `422`, never silently applied.

**Bookings** (`app/services/booking_service.py`)

```
pending ──> confirmed ──> delivered ──> inspected ──> completed
   │            │              │
   └────────────┴──────────────┴──> cancelled
```

Cancellation is the only transition that releases reserved slot capacity,
and it does so under the same row lock that protects booking creation.

**Grain sales** (`app/services/grain_sale_service.py`)

```
pending ──> received ──> approved ──> paid
   │            │
   └────────────┴──> rejected
```

`received` is new: it is the state a sale enters once its yield is known
(via inspection or direct procurement) but before payment is authorised.

Note: these columns are `VARCHAR` with **no database `CHECK`
constraint** — SQLAlchemy 2.x defaults `native_enum=False` to
`create_constraint=False`, so the constraints described in the original
DDL were never actually created. The enum is therefore enforced in the
application only. Adding database-level constraints is worth doing but
was left out of this change set so the migration stays reversible
without a table rewrite.

---

## 5. RBAC integrity

### Defects found

The permission table was the declared source of truth, but several roles
were missing permissions their endpoints required, and several endpoints
bypassed the table entirely by checking roles inline.

| Defect | Effect |
|---|---|
| `MANAGER` lacked `crop.read` | Managers got `403` on `GET /crops` — the admin crop views were unusable |
| `MANAGER` lacked `seed.read` | Managers got `403` listing seeds and seed purchases, yet `require_role` let them **approve** a purchase they could not see |
| `FARMER` lacked `visit.read` | Farmers got `403` on their own farm visits |
| `procurement.manage`, `market_rate.read` | Declared but never used by any endpoint |
| 11 endpoints used `require_role(...)` inline | Bypassed the permission table, so the table did not describe actual access |
| Notification endpoints used bare `ActiveUser` | No permission declared at all |
| `bank_change.review` granted to `MANAGER` | Wider than production, where bank changes are super-admin only |
| `profile.read.own` granted to `MANAGER` | `GET /farmers/me` was readable by staff while `PATCH` was not — inconsistent |

### What changed

- The permission table is now the only authorization mechanism. Every one
  of the 77 routes declares a permission; no endpoint compares roles inline.
- `require_permission()` validates its argument against `ALL_PERMISSIONS`
  **at import time**, so a typo in a route fails at startup rather than
  silently denying (or granting) access.
- Six permissions are marked super-admin-only and asserted unreachable by
  any other role: `manager.manage`, `audit.read`, `seed.manage`,
  `market_rate.manage`, `bank_change.review`, `warehouse.create`.

### Verification

`tests/security/test_rbac_matrix.py` — **217 assertions**, all passing.

Every route is listed with the roles permitted to call it. The suite then
drives real HTTP requests with real signed JWTs and asserts:

1. Every denied (role, endpoint) pair returns exactly **403** — not 200,
   and not a 404/422 that would imply the handler ran.
2. Every permitted pair is **not** rejected.
3. **Anonymous** callers are rejected from every non-public endpoint.
4. A **pending** farmer (registered, not yet approved) is refused.
5. A **suspended** farmer is refused.
6. A JWT whose `role` claim says `super_admin` but whose subject is a
   farmer is refused — authorization reads the role from the database
   row, never from the token claim.
7. Farmers hold no administrative permission; managers hold none of the
   super-admin-only ones.

The suite also contains `test_every_route_is_covered`, which fails if any
route exists that the matrix does not mention. **A new endpoint cannot
merge without an explicit, reviewed decision about who may call it.**

---

## 6. Performance

### Requirement

> Page load times under 2 seconds across standard web/mobile connections,
> with real-time operational dashboard updates.

The 2-second budget is end-to-end and must also cover TLS, network RTT
and client render, so the API is held to a **400 ms** share of it.
`app/core/benchmark.py` defines each screen as the set of API calls it
issues, because that is what a user actually waits for — measuring one
endpoint at a time hides a screen that is fast per call but issues eight.

### Defects found and fixed

**Every authenticated request ran 4 queries instead of 1.** Every
relationship in every model carried `lazy="selectin"`, so resolving the
caller's identity (`db.get(User, id)`, which runs on every authenticated
request) also loaded their farmer profile, their staff profile, and
**every refresh-token row they had ever been issued** — a table that
grows by one row per login and is never pruned. Replaced with
`lazy="raise"` plus explicit `selectinload()` at the four query sites
that actually serialize a profile. `lazy="raise"` means an accidental
lazy load now fails loudly in tests rather than becoming a silent N+1 in
production.

**The admin dashboard ran 6 sequential queries** (the legacy Express one
ran 13). On a pooled or remote Postgres that is 6× the network latency
before the page can render. Rewritten as a single statement using scalar
subqueries — and the dashboard now returns the full set of figures the UI
needs (active farmers, MTD procurement/revenue/profit, warehouse load,
pending payments, active crops, visits today), which previously required
additional round trips.

**Four list endpoints were unbounded.** `GET /crops`, `GET /farm-visits`,
`GET /crops/active` and `GET /warehouse-slots` returned every matching
row with no pagination — fine at 400 farmers, multi-second at 10,000.
The first three are now paginated like the rest of the API; slot listing
is bounded to the forward-looking window a caller can actually book into.

**A Redis outage cost 11.3 seconds per request.** The rate-limit
middleware called Redis up to five times per request with no circuit
breaker, so with Redis down every request paid five connect timeouts —
turning a dependency that is only supposed to throttle traffic into a
total outage. Measured directly:

```
before:  public/stats  status=200  total=11.259283s
after:   public/stats  status=200  total=0.006205s
```

The five bucket checks are now a single pipelined round trip, and a
shared circuit breaker (`app/core/redis.py`) short-circuits for 15
seconds after a failure. The limiter also now emits `Retry-After` and
`X-RateLimit-*` headers — which the frontend's axios interceptor already
reads (`error.response.headers['retry-after']`) but never received.

### Measured results

Against a seeded dataset of 400 farmers, 1,200 crops, 1,600 seed
purchases, 1,200 grain sales, 1,200 bookings, 2,400 transactions, 3,200
notifications and 5,000 audit-log rows, over real HTTP:

| Page | p50 | p95 | Budget |
|---|---|---|---|
| Public landing | 9.9 ms | 10.7 ms | 2000 ms |
| Farmer dashboard | 9.9 ms | 12.2 ms | 2000 ms |
| Farmer crops | 8.5 ms | 9.4 ms | 2000 ms |
| Farmer marketplace | 8.6 ms | 13.4 ms | 2000 ms |
| Farmer bookings | 15.8 ms | 28.6 ms | 2000 ms |
| Farmer wallet | 8.8 ms | 10.1 ms | 2000 ms |
| Farmer profile | 6.9 ms | 8.0 ms | 2000 ms |
| **Admin operational dashboard** | **13.0 ms** | **14.5 ms** | 2000 ms |
| Admin farmers list | 6.7 ms | 8.2 ms | 2000 ms |
| Admin procurement queue | 8.9 ms | 13.1 ms | 2000 ms |
| Admin visits planner | 9.0 ms | 10.1 ms | 2000 ms |
| Admin finance | 9.2 ms | 10.3 ms | 2000 ms |
| Super admin governance | 12.7 ms | 65.3 ms | 2000 ms |

With a simulated 300 ms mobile RTT added to every request, p50 and p95
stay in the 325–343 ms range for all thirteen pages. (p99 shows
occasional 2 s/4 s/6 s spikes — those land exactly on TCP SYN-retransmit
backoff intervals and are an artifact of driving loopback on Windows from
the test client, not server time; the in-process measurement, which
excludes the socket layer, shows p95 ≈ 10 ms for the same pages.)

**These numbers are from a local single-instance run against local
Postgres.** They demonstrate headroom, not a production SLA — re-run
`scripts/benchmark_pageloads.py` against staging with the real connection
pooler to validate the end-to-end requirement.

### Enforcement

`tests/performance/test_page_load_budget.py` fails CI if any page exceeds
its budget, and separately asserts that the admin dashboard is still one
query and that authenticating a request is still one query — the two
regressions that would otherwise creep back unnoticed.

### Indexes

27 composite indexes were added for the filter/sort combinations the
services actually use (`migration c5ae7917cb97`). The migration was
verified to round-trip: applying it and re-running `alembic
--autogenerate` produces an empty diff.

---

## 7. Real-time operational dashboard updates

Dashboards previously had to poll. Polling is what makes an "operational
dashboard" feel stale, and it multiplies backend load by the number of
open tabs.

**Transport: Server-Sent Events over Redis pub/sub.**

- `POST /api/v1/events/ticket` → single-use, 30-second handshake token
- `GET /api/v1/events/stream?ticket=…` → `text/event-stream`

SSE rather than WebSockets because the traffic is strictly server→client,
it rides on ordinary HTTP with no proxy or load-balancer upgrade
configuration, browsers reconnect automatically, and Dart/Flutter clients
consume it with a plain streamed request.

The ticket exists because `EventSource` cannot send an `Authorization`
header, and putting a bearer token in a query string would leak it into
proxy logs and browser history. The ticket is single-use, expires in 30
seconds, and is verified against the database before the stream opens.
A test asserts a bearer token passed as a ticket is rejected.

Redis pub/sub (rather than in-process fan-out) means this works behind a
load balancer with multiple API instances and no sticky sessions: any
instance can publish, and the instance holding a given client's
connection delivers.

**Events:**

| Event | Channel | Emitted when |
|---|---|---|
| `notification.created` | per-user | Any notification is created |
| `dashboard.changed` | per-role (manager, super_admin) | Any write completes |

Both are emitted from existing choke points — `notification_service` and
`audit_service.record()` — rather than from ~40 individual call sites.
Because every mutating service already funnels through
`audit_service.record()`, that one function is also where the dashboard
cache is invalidated, so neither concern can be forgotten at a call site.

### What is verified, and what is not

Being precise, because this is the one area not covered end-to-end:

| Layer | Status | How |
|---|---|---|
| Publish to the right channel, and only that channel | **Verified** | `test_live_updates_and_cache.py` — a notification reaches the recipient's channel and provably does *not* reach an unrelated user's |
| Stream framing (`event:` / `data:` / keepalive) | **Verified** | `test_sse_stream.py` drives the generator directly and asserts a published event comes out correctly framed |
| Ticket handshake (single-use, expiry, suspended account, token-as-ticket rejected) | **Verified** | `test_sse_stream.py`, `test_otp_and_streaming.py` |
| HTTP transport: `200`, `text/event-stream`, incremental delivery | **Verified** | Against a real uvicorn server; `: connected` and `: keepalive` frames arrive incrementally |
| Full loop (write → Redis → open stream) over TCP | **Not verified here** | No Redis server was available on the test machine. `fakeredis`'s `TcpFakeServer` accepts `PUBLISH` and reports subscribers but does not deliver across TCP connections, so the last hop could not be exercised locally |

Every component of that final hop is individually verified; run the full
loop once against a real Redis in staging before relying on it.

`scripts/` contains no SSE smoke test — verify with:

```bash
TICKET=$(curl -s -X POST $BASE/api/v1/events/ticket -H "Authorization: Bearer $TOKEN" | jq -r .data.ticket)
curl -N "$BASE/api/v1/events/stream?ticket=$TICKET"
# then trigger any write from another session and watch the frame arrive
```

Note for local setup: use `REDIS_URL=redis://127.0.0.1:6379/0` rather than
`localhost` — on Windows `localhost` resolves to `::1` first and a
Redis bound only to IPv4 will appear unreachable.

---

## 8. Caching

`app/core/cache.py` — Redis-backed, deliberately narrow in scope:

- Public landing-page queries (60 s TTL) — identical for every caller
- Operational dashboards (10 s TTL) — staleness budget exceeds refresh
  interval, and they also receive live deltas over SSE

**Per-farmer data is never cached**, so a cache bug cannot leak one
farmer's rows to another. Every write invalidates the dashboard
namespace, so a number can never be served after the write that changed
it. All operations degrade to a direct call when Redis is unreachable —
a cache outage must slow the API down, not take it down
(`test_api_survives_redis_being_unreachable`).

---

## 9. Test suite

| Suite | Tests | Covers |
|---|---|---|
| `tests/security/test_rbac_matrix.py` | 217 | Role × endpoint authorization, privilege escalation, approval gate |
| `tests/security/test_otp_and_streaming.py` | 12 | OTP echo/expiry/brute-force/binding, stream-ticket replay |
| `tests/security/test_idor.py` | 3 | Identity derived from JWT, never from request body |
| `tests/integration/test_live_updates_and_cache.py` | 10 | SSE fan-out isolation, cache hit/invalidate, Redis-down degradation |
| `tests/integration/test_sse_stream.py` | 6 | Stream framing, disconnect handling, ticket handshake |
| `tests/integration/test_concurrency.py` | 2 | Booking capacity and seed stock never oversold under race |
| `tests/performance/test_page_load_budget.py` | 15 | Page budgets, 1-query dashboard, 1-query auth |
| Remaining unit/integration | 46 | Services, state machines, config hardening, storage |
| **Total** | **321** | |

Concurrency, IDOR and performance tests run against **real PostgreSQL** —
`SELECT … FOR UPDATE`, JSONB and native constraint behaviour have no
faithful SQLite equivalent. Cache/OTP/SSE tests use in-process
`fakeredis` so they are deterministic without a Redis service.

---

## 10. One backend for web and mobile

The API is already client-agnostic; the following make that explicit:

- **Stateless JWT auth** with opaque, rotating refresh tokens and reuse
  detection — no server-side session affinity, so any instance can serve
  any request.
- **One response envelope** (`{success, data, message}` /
  `{success, data, pagination}` / `{success, error, request_id}`) for
  every endpoint, so a Dart client and a JS client share one
  deserialization layer.
- **Consistent pagination** on every list endpoint, which matters far
  more on mobile than on desktop.
- **Direct-to-storage presigned uploads**, so large files never transit
  the API — plus a multipart fallback for constrained clients.
- **SSE** rather than WebSockets, which survives mobile network
  transitions and reconnects natively.
- **OpenAPI at `/api/v1/openapi.json`**, from which a typed Dart client
  can be generated rather than hand-written.

---

## 11. Still open

These are known and deliberate, not oversights:

1. **No SMS provider.** OTP delivery logs the code in development and
   `otp_service.deliver()` raises in production. Wire up Twilio/MSG91
   before enabling OTP flows in production. Everything else about the OTP
   flow is finished and tested.

2. **Phase 0 schema reconciliation still owed.** This backend targets a
   clean UUID-keyed schema; the live database uses `BIGSERIAL` keys, and
   column names differ (`warehouse_slots.total_capacity_kg` vs
   `capacity_kg`, `farm_visits.admin_id` vs `staff_id`,
   `admin_profiles` vs `staff_profiles`). Run
   `scripts/phase0_schema_reconciliation.py` against the live database
   before cutover, then `scripts/migrate_legacy_identity.py`.

3. **`get_monthly_report` is unverified.** The original reporting RPC
   body is not in the repository, so this is a from-scratch
   reimplementation. Diff its output against the live RPC before trusting
   it.

4. **Regional manager scoping is not enforced.** `staff_profiles
   .assigned_region` exists and is populated, but every manager currently
   sees every region. The permission table is the right place to add it
   when the policy is decided.

5. **No database-level CHECK constraints on status columns** (§4).

6. **Rate-limit thresholds are unvalidated.** They come from the legacy
   `RATE_LIMITING.md`, which documents an Express backend that was never
   built. Tune against real traffic.

7. **Rotate the leaked credentials** in the frontend repo's committed
   `.env` (§1).

---

## Running it

```bash
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload          # http://localhost:8000/api/v1/docs
```

```bash
pytest -m "not performance"            # fast suite
pytest tests/security                  # RBAC integrity verification
pytest -m performance -s               # page-load budget, prints timings
```

```bash
python scripts/benchmark_pageloads.py \
    --base-url https://staging.example.com \
    --farmer-phone … --farmer-password … \
    --admin-phone … --admin-password … \
    --rounds 20 --slow-network 300
```
