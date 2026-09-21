# Sri Siva Sai Seeds — Farmer App ⇄ Backend Integration Guide

**Audience:** the Flutter team moving the Android farmer app (`com.srisivasai.seeds`) off Supabase.
**Backend:** this repository (FastAPI · PostgreSQL · Redis · S3).
**Live base URL:** `https://agriflow-backend-t8o4.onrender.com/api/v1/farmer`
**Interactive docs:** `https://agriflow-backend-t8o4.onrender.com/api/v1/docs` (filter by the `farmer ·` tags)

Every JSON example below was captured from the running code, not written by hand.

---

## 1. Status against the requirements spec

| Area | Spec | Implemented |
|---|---|---|
| Farmer API endpoints | 32 | **34 / 32** (+ `GET /seeds/{id}`, `GET /warehouses/{id}` single-resource detail views) |
| Database tables | 16 | **16 / 16** (plus refresh tokens, audit log, inspections, staff profiles used by the web app) |
| Storage areas | 4 (`seeds/`, `avatars/`, `documents/`, `crop_scans/`) | **4 / 4** |
| Atomic seed purchase (`FOR UPDATE`) | required | ✅ row lock on the seed, stock moved onto hold |
| Atomic slot booking (`FOR UPDATE`) | required | ✅ row lock on the slot, weight **and** booking-count limits |
| Identity from token only (no IDOR) | required | ✅ no route accepts a `farmer_id`; foreign resources return 404 |
| Bank number masking | required | ✅ `*******7561` |
| KYC files private | required | ✅ stored as paths, served as 15-minute signed URLs |
| FCM push | required | ✅ **live and verified in production** — a real dry-run send against Google succeeded; `/health/ready` reports `"push": true` |
| Weather advisory | required | ✅ live (Open-Meteo, no API key), cached 15 min |
| Login rate limit 5/min, 60/min per farmer | required | ✅ |
| Farmer records survive delete/cancel | required | ✅ crops soft-delete (`deleted_at`); bookings soft-cancel (`status=cancelled`) — neither is ever physically removed |

**Still needed from you before file uploads work in production:**
1. **S3 secret access key** for bucket `srisivasai-gallery` (endpoint/region/access-key-id are already configured for Supabase Storage's S3-compatible API) — until then every upload (documents, avatar, crop scan) returns `422 Storage is not configured`.
2. An **SMS provider** if you want OTP-based password reset from the app.

FCM push no longer needs anything from you — it's configured and verified live.

### Where the backend intentionally differs from the spec

Read these before writing models — they are the only places the contract does not match the spec document word-for-word.

| Spec says | Backend does | Why |
|---|---|---|
| IDs are `BIGINT` (`"id": 14`) | IDs are **UUID strings** (`"id": "40c5c6c7-…"`) | One database serves the web admin and the app; its keys are UUIDs. **Use `String id` in every Dart model.** |
| Responses are bare objects/arrays | Every response is wrapped: `{"success": true, "data": …}` | One envelope for web + mobile; one parsing path in the client. |
| Lists use `limit` / `offset` | Lists use `page` / `page_size` and return a `pagination` block | Same pagination as the rest of the API. |
| A new booking is `confirmed` | A new booking is **`pending`**; staff confirm it | Capacity is reserved immediately; confirmation is an operational step in the admin portal. The farmer gets a notification + push when it changes. |
| Crop `status` = Sowing / Growing / Maturity / Harvest | ✅ same values in `status`; the separate lifecycle (`growing`/`harvested`/`failed`/`sold`) is in `lifecycle_status` | |
| Grain offer statuses `pending/approved/completed/rejected` | `pending → received → approved → paid`, or `rejected` | `received` = weighed at the warehouse; `paid` = money released. Show `paid` as "Completed". |
| Validation errors → `400` | Validation errors → **`422`** | FastAPI standard; body format is the spec's error envelope. |
| `mandi_prices[].change_percentage` stored | **Computed** from the last two effective rates | A stored figure goes stale; this one can't. |
| Cancel someone else's booking → 403 or 404 | **403** | |

**HTTP method compatibility:** four routes also accept `PUT` where this guide documents `PATCH`/`POST` — profile update, crop update, and both notification read-state routes. This is unadvertised: the OpenAPI schema and every example below only shows the canonical verb, and Dart codegen from the schema will only ever see that one. It exists purely so the app isn't broken if the HTTP client happens to send `PUT` for a partial update; **use the documented verb**, the alias is a safety net, not the contract.

---

## 2. How the backend is built

```
Flutter Android app ─┐
Web admin portal ────┤ HTTPS · JSON · Bearer JWT
                     ▼
        FastAPI (Render, Singapore)
        ├─ /api/v1/farmer/*   ← mobile contract (this guide)
        ├─ /api/v1/*          ← web / admin contract
        │    both call the SAME service layer:
        ├─ services/   business rules, row locks, state machines, audit, notifications
        ├─ Redis       rate limits · response cache · OTP · live events
        ├─ S3          seed images · avatars · KYC documents · crop scans
        └─ FCM         push notifications (sent only after the DB transaction commits)
                     ▼
        PostgreSQL 17 (Supabase, Singapore) via transaction pooler
```

The farmer API is a thin, mobile-shaped layer. Placing an order from the phone and from the web runs the exact same locked transaction, so the two can never oversell each other.

### 2.1 Tables

| Table | Holds | Key relations & rules |
|---|---|---|
| `users` | Every account (farmer, manager, super_admin) | `phone` unique (stored as 10 digits); `role`; `status` = pending / active / rejected / suspended |
| `farmer_profiles` | Farm, address, agri & bank details, KYC file paths | 1:1 `users` (cascade delete) |
| `refresh_tokens` | Hashed, rotating refresh tokens | reuse of a rotated token revokes every session |
| `seeds` | Catalog: price, `old_price`, `stock_kg`, `on_hold_kg`, `crop_type`, home `warehouse_id` | purchases lock this row |
| `seed_purchases` | Orders: qty, price, total, `grade`, `pickup_date`, invoice, payment status | → `users`, `seeds`, `warehouses` |
| `crops` | Farmer fields: `crop_name`, `crop_type`, `acres`, dates, `stage`, lifecycle `status` | → `users`; visits cascade on delete |
| `farm_visits` | Scheduled inspections **and** farmer scans: `status`, `image_path`, `diagnosis`, `recommendation` | → `crops`, `users` |
| `warehouses` | Hubs: capacity, current load, `location`, `contact_number` | |
| `warehouse_slots` | Bookable delivery windows: capacity, `booked_kg`, `max_bookings`, `current_booking_count` | bookings lock this row |
| `booking_slots` | A farmer's delivery reservation | → `users`, `warehouses`, `warehouse_slots` |
| `grain_sales` | Farmer offers / procurement: qty, `offered_price_per_kg`, final price, yield | → `users`, optional own `crops` |
| `crop_inspections` | Quality check when grain arrives at the warehouse | → `booking_slots`, `grain_sales` |
| `market_rates` | Mandi prices per crop · grade · `variety` · date | history kept; latest wins |
| `bank_change_requests` | Pending bank-detail changes | profile updated only on approval |
| `farmer_documents` | Every KYC upload (type, path, size, MIME) | → `users` |
| `transactions` | Ledger (seed purchase debits, grain payment credits) | written only as a side effect — no API creates one directly |
| `notifications` | In-app messages + read state | → `users` |
| `fcm_device_tokens` | One row per device; moves if another farmer logs in on it | → `users` |
| `audit_logs` | Who changed what, when | every write |

70+ indexes cover the farmer-scoped filters (`farmer_id + status`, `user_id + is_read`, `warehouse_id + slot_date + status`, …).

### 2.2 Performance

Measured server-side against 400 farmers / 5,000 audit rows (CI fails if any screen regresses past 400 ms):

| App screen | Calls | p95 |
|---|---|---|
| Home | `/dashboard` + `/notifications/unread-count` | 15.8 ms |
| Seeds | `/seeds` + `/seeds/purchases` | 10.6 ms |
| Grain & bookings | `/warehouses` + `/grain-sales/bookings` + `/grain-sales/offers` | 14.2 ms |
| Account | `/profile` + `/transactions` | 10.0 ms |

The home screen is one aggregated call instead of the 4–5 PostgREST queries the app makes today.

---

## 3. Conventions

### 3.1 Request headers
```http
Authorization: Bearer <access_token>
Content-Type: application/json          (multipart/form-data for uploads)
Accept: application/json
```

### 3.2 Success envelope
```json
{ "success": true, "data": { … }, "message": "Success" }
```
Lists that can grow are paginated:
```json
{ "success": true, "data": [ … ], "pagination": { "page": 1, "page_size": 20, "total": 1, "total_pages": 1 } }
```
Query parameters: `page` (≥ 1, default 1), `page_size` (1–100, default 20), and on most lists `status`.

### 3.3 Error envelope
```json
{
  "success": false,
  "error": { "code": "INSUFFICIENT_STOCK", "message": "Only 4.50 kg of 'TDN-58 Groundnut' remain in stock",
             "details": { "available_kg": "4.50", "requested_kg": "5" } },
  "request_id": "b1f0…"
}
```
Always show `error.message` to the user; branch on `error.code`. Quote `request_id` in bug reports — it is in the server logs.

| HTTP | `error.code` | Meaning / what the app should do |
|---|---|---|
| 401 | `UNAUTHORIZED` | Token missing/expired → refresh once, then send to login |
| 403 | `FORBIDDEN` | Pending / suspended account, staff account, or not allowed |
| 404 | `NOT_FOUND` | Doesn't exist **or isn't yours** (deliberately indistinguishable) |
| 409 | `CONFLICT`, `INSUFFICIENT_STOCK`, `CAPACITY_EXCEEDED` | Business rule — show the message, let the user retry |
| 422 | `VALIDATION_ERROR` | Field errors in `details.errors[]` (`loc`, `msg`) |
| 422 | `INVALID_STATE_TRANSITION` | e.g. cancelling an already-completed booking |
| 429 | `RATE_LIMITED` | Honour the `Retry-After` header (seconds) |
| 503 | `RATE_LIMITER_UNAVAILABLE` | Retry after `Retry-After` |

Validation example (sending a forbidden field):
```json
{ "success": false, "error": { "code": "VALIDATION_ERROR", "message": "Request validation failed",
  "details": { "errors": [ { "type": "extra_forbidden", "loc": ["body", "status"], "msg": "Extra inputs are not permitted", "input": "active" } ] } } }
```

### 3.4 Data types

| Kind | Wire format | Dart |
|---|---|---|
| IDs | UUID string | `String` |
| Money, kg, acres, % | JSON number (`425.0`) | `double` (`(json['x'] as num).toDouble()`) |
| Dates | `"2026-09-25"` | `DateTime.parse` (date only) |
| Timestamps | ISO-8601 UTC `"2026-09-21T06:13:41.168283Z"` | `DateTime.parse(...).toLocal()` |
| Nullable | `null` present, never omitted | nullable fields |

**Units:** weights are always kilograms. Show quintals as `kg / 100` — slots already include `available_weight_qtl`, market rates include `price_per_qtl`.

### 3.5 Rate limits
- `POST /auth/login`: 5 per minute per network address.
- Everything else: 900 per 15 minutes (≈ 60/min) **per signed-in farmer** — not per IP, so farmers behind the same mobile-carrier NAT don't share a budget.
- Writes: 30 per 5 minutes per farmer; uploads: 10 per 15 minutes.

Every response carries `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

---

## 4. Authentication

### 4.1 Token lifecycle
```
login ──► access_token (15 min) + refresh_token (30 days)
  │
  ├─ every request: Authorization: Bearer <access_token>
  ├─ on 401: POST /auth/refresh once → new pair (old refresh token is now dead)
  │          if that fails → clear storage → login screen
  └─ logout: POST /auth/logout → every session for this farmer is revoked
```
Refresh tokens **rotate**: each refresh returns a new one and invalidates the old. Replaying an old refresh token revokes *all* sessions — so two concurrent refreshes race and one loses. The interceptor in §6.3 serialises refreshes to avoid exactly that.

Store both tokens in `flutter_secure_storage`, never `SharedPreferences`.

### 4.2 `POST /auth/login` — public
Accepts `9502662924`, `+91 95026 62924`, `09502662924` — all normalised to the same 10 digits.
```json
{ "phone": "9502662924", "password": "FarmerPassword123" }
```
`200`:
```json
{
  "success": true,
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
    "refresh_token": "ufCj96PDdVq57zlkFW95VtZYMQFbUAcC63r9XZYiQZShJmiQxxgenPziPuEJI96j",
    "token_type": "bearer",
    "expires_in": 900,
    "user": { "id": "40c5c6c7-11e3-47c1-9f73-a2e5e998921c", "name": "Asha", "phone": "9502662924",
              "email": "asha@srisivasaiseeds.com", "role": "farmer", "status": "active" },
    "farmer_profile": { "farmer_id": "40c5c6c7-11e3-47c1-9f73-a2e5e998921c", "farm_name": "Kaveri Farm",
                        "village": "Kalluru", "district": "Kurnool", "state": "Andhra Pradesh",
                        "acres_of_land": 12.0, "bank_account_number": "*******7561", "avatar_url": null, "…": "see §5.1" }
  },
  "message": "Signed in"
}
```
| Status | When |
|---|---|
| 401 | wrong phone or password (same response for both) |
| 403 | account pending approval (`"Your registration is awaiting approval"`), suspended, rejected, or a staff account |
| 422 | phone isn't a valid Indian mobile number, password < 6 chars |
| 429 | more than 5 attempts in a minute |

### 4.3 `POST /auth/refresh` — public
```json
{ "refresh_token": "ufCj96PDdVq57zlk…" }
```
`200` → `{ "data": { "access_token", "refresh_token", "token_type", "expires_in": 900 } }`. `401` → sign the user out.

### 4.4 `POST /auth/logout`
Optional body removes this device from push:
```json
{ "fcm_token": "eK3…device_token…" }
```

---

## 5. Endpoint reference

All paths are relative to `…/api/v1/farmer`. All require a farmer access token except login and refresh.

### 5.1 Profile

**`GET /profile`**
```json
{ "success": true, "data": {
  "farmer_id": "40c5c6c7-11e3-47c1-9f73-a2e5e998921c", "name": "Asha", "phone": "9502662924",
  "email": "asha@srisivasaiseeds.com", "status": "active",
  "farm_name": "Kaveri Farm", "address": null, "village": "Kalluru", "district": "Kurnool",
  "state": "Andhra Pradesh", "crop_address": null, "acres_of_land": 12.0,
  "soil_type": "Black Cotton Soil", "irrigation_type": "Drip Irrigation",
  "primary_crop": "Cotton", "secondary_crop": null,
  "bank_name": "State Bank of India", "bank_account_number": "*******7561",
  "bank_ifsc": "SBIN0001234", "upi_id": "asha@sbi", "bank_status": "approved",
  "avatar_url": null, "aadhaar_url": null, "passbook_url": null, "land_proof_url": null } }
```
`*_url` fields are **signed URLs valid for 15 minutes**. Don't cache them to disk — re-fetch the profile when you need to show a document again.

**`PATCH /profile`** — send only what changed. Allowed:
`name, email, farm_name, address, village, district, state, crop_address, acres_of_land, soil_type, irrigation_type, primary_crop, secondary_crop`.
Any other field (`status`, `role`, `user_id`, bank fields, …) → `422`. Email already used by someone else → `409`. Returns the updated profile.

**`POST /profile/bank-request`** → `201`
```json
{ "bank_name": "State Bank of India", "account_number": "10293847561", "ifsc_code": "SBIN0001234", "upi_id": "asha@sbi" }
```
```json
{ "success": true, "data": { "request_id": "…", "status": "pending" }, "message": "Bank change request submitted for review" }
```
Rules: account number 9–18 digits; IFSC like `SBIN0001234` (lower-case accepted); one pending request at a time (`409`). **Profile bank details don't change until staff approve** — the farmer gets a notification + push either way.

### 5.2 Dashboard & market

**`GET /dashboard`** — the whole home screen in one call:
```json
{ "success": true, "data": {
  "farmer_id": "40c5c6c7-…", "farmer_name": "Asha", "farm_name": "Kaveri Farm",
  "active_crops_count": 2, "total_acres": 11.0, "pending_bookings": 1,
  "recent_earnings": 0.0, "unread_notifications": 2,
  "weather": { "temperature_c": 31, "feels_like_c": 34, "condition": "Clear",
               "advisory": "Good day to irrigate before noon", "humidity_percent": 58, "wind_kmh": 9 },
  "crops": [
    { "id": "8d15b86b-…", "crop_name": "Cotton Plot B", "crop_type": "Cotton", "acres": 6.8,
      "stage": "Sowing", "stage_progress_percent": 12, "health_status": "Not inspected yet" },
    { "id": "21b8c1a7-…", "crop_name": "North Paddy", "crop_type": "Rice", "acres": 4.2,
      "stage": "Growing", "stage_progress_percent": 75, "health_status": "Not inspected yet" } ],
  "mandi_prices": [
    { "crop_type": "Paddy", "grade": "A", "variety": "Common", "price_per_kg": 23.5,
      "price_per_qtl": 2350.0, "change_percentage": 2.49, "effective_date": "2026-09-21" } ],
  "recent_orders": [
    { "id": "d07aad79-…", "reference": "GBK-D07AAD79", "type": "grain_booking",
      "title": "Grain Booking - Kurnool Central Warehouse Hub", "date": "2026-09-25", "status": "Pending", "amount": 0.0 },
    { "id": "b237299f-…", "reference": "SP-20260921-0053DC36", "type": "seed_purchase",
      "title": "TDN-58 Groundnut", "date": "2026-09-21", "status": "Unpaid (Pay at Warehouse)", "amount": 425.0 } ] } }
```
- `weather` is **nullable** — render the home screen without the card if it's `null` (the upstream service is down).
- `stage_progress_percent` = days since sowing ÷ (harvest − sowing), or a typical duration for the crop if no harvest date.
- `health_status` comes from the latest officer review: the officer's diagnosis, `"Awaiting officer review"` after a scan, or `"Not inspected yet"`. It is never invented.
- `recent_earnings` = completed credits in the last 30 days.
- Use `reference` (not `id`) as the order number shown to the farmer.

**`GET /market-rates`** → list of the `mandi_prices` objects above, latest per crop and grade.

### 5.3 Seeds

**`GET /seeds`** — query: `q` (name/variety), `crop_type` (`All` = no filter), `min_price`, `max_price`, `warehouse_id`, `in_stock_only` (`true`/`false`).
```json
{ "success": true, "data": [
  { "id": "b3931ff3-65b2-4e5b-bd91-f0d1deac4680", "name": "TDN-58 Groundnut", "crop_type": "Groundnut",
    "variety": "High Oil", "description": null, "price_per_kg": 85.0, "old_price": 98.0,
    "stock_kg": 4500.0, "image_url": null, "warehouse_id": "a5a6c014-d611-4c2e-bca0-a932fb3904e9" } ] }
```
`stock_kg` is what can still be sold (held quantities are already subtracted).

**`POST /seeds/purchase`** → `201`
```json
{ "seed_id": "b3931ff3-…", "quantity_kg": 5, "grade": "A",
  "warehouse_id": "a5a6c014-…", "pickup_date": "2026-09-22", "payment_method": "warehouse" }
```
`warehouse_id` optional (defaults to the seed's home warehouse); `pickup_date` optional, not in the past; `grade` A/B/C.
```json
{ "success": true, "data": {
  "order_id": "b237299f-79d4-4f7a-acf0-14a4c9d4099b", "invoice_number": "SP-20260921-0053DC36",
  "seed_name": "TDN-58 Groundnut", "quantity_kg": 5.0, "grade": "A",
  "warehouse_id": "a5a6c014-…", "warehouse_name": "Kurnool Central Warehouse Hub", "pickup_date": "2026-09-22",
  "price_per_kg": 85.0, "total_amount": 425.0,
  "payment_status": "pending", "payment_status_label": "Unpaid (Pay at Warehouse)" },
  "message": "Order placed successfully!" }
```
What happens: seed row locked → stock checked → quantity moved from `stock_kg` to `on_hold_kg` → order + pending ledger debit + notification + push. When staff mark it **paid** the hold is released; if **failed**, the quantity goes back to stock. Out of stock → `409 INSUFFICIENT_STOCK`.

**`GET /seeds/purchases`** (paginated) — each item embeds the `seed`:
```json
{ "id": "b237299f-…", "invoice_number": "SP-20260921-0053DC36", "quantity_kg": 5.0, "price_per_kg": 85.0,
  "total_amount": 425.0, "grade": "A", "payment_status": "pending", "pickup_date": "2026-09-22",
  "warehouse_id": "a5a6c014-…", "created_at": "2026-09-21T06:13:41.168283Z", "seed": { "…": "as in GET /seeds" } }
```

**`GET /seeds/{seed_id}`** — single-seed detail, same shape as one item of `GET /seeds`. `404` if it doesn't exist (works for a seed that's since been deactivated, so a farmer can still open an old purchase's seed detail).

### 5.4 Crops & inspections

**`GET /crops`** — the farmer's active fields (growing, not deleted). `?include_closed=true` also returns harvested / failed / sold / **deleted** crops — a full history view.
```json
{ "id": "21b8c1a7-…", "farmer_id": "40c5c6c7-…", "crop_name": "North Paddy", "crop_type": "Rice", "acres": 4.2,
  "sowing_date": "2026-06-23", "harvest_date": "2026-10-21", "status": "Growing",
  "lifecycle_status": "growing", "notes": null, "created_at": "2026-09-21T06:13:39.176025Z",
  "deleted_at": null, "is_deleted": false }
```

**`POST /crops`** → `201`
```json
{ "crop_name": "Cotton Plot B", "crop_type": "Cotton", "acres": 6.8, "sowing_date": "2026-09-01",
  "harvest_date": "2027-02-08", "status": "Sowing", "notes": "Near canal" }
```
(`location` is also accepted and stored as `notes` when `notes` is empty.) Inspection visits are scheduled automatically — Cotton at months 1 and 4, Rice/Wheat/Groundnut/Chili at 1 and 3, Maize at 1 and 2, Sugarcane at 2 and 5, Turmeric at 2 and 6, anything else at 1 and 3:
```json
{ "success": true, "data": { "crop": { "…": "crop object" },
  "visits": [ { "id": "7a5d5ba9-…", "crop_id": "8d15b86b-…", "visit_month": 1, "visit_date": "2026-10-01",
                "scheduled_date": "2026-10-01", "status": "scheduled", "notes": null, "report": null,
                "diagnosis": null, "recommendation": null, "image_url": null },
              { "…": "month 4" } ] }, "message": "Crop registered" }
```

**`PATCH /crops/{crop_id}`** (also accepts `PUT`, undocumented alias — see §1) — any of `crop_name, crop_type, acres, sowing_date, harvest_date, status, notes`. Harvest before sowing → `422`. A deleted crop → `409`.

**`DELETE /crops/{crop_id}`** — **soft delete.** The crop, its farm visits/inspections, and any linked grain sales are never physically removed — only hidden from the default (active) crop list. History and audit stay intact:
```json
{ "success": true, "message": "Crop field removed from your active list" }
```
- The crop still shows up with `GET /crops?include_closed=true`, flagged `"is_deleted": true, "deleted_at": "2026-09-21T10:04:12Z"`.
- `GET /crops/{crop_id}/inspections` and `GET /crops/visits` keep returning its visit history after deletion.
- Deleting an already-deleted crop is **not an error** — `200` again, so a retried request on a flaky connection never surfaces as a failure.
- A deleted crop is frozen: `PATCH`/`PUT` and `POST .../scan` on it both return `409 CONFLICT`.

**`GET /crops/{crop_id}/inspections`** — visits for one crop; works even after the crop is deleted.
**`GET /crops/visits?crop_id=`** — all of the farmer's visits, soonest first; deleted crops' visits are still included.

**`POST /crops/{crop_id}/scan`** — `multipart/form-data`: `image` (JPEG/PNG, ≤ 5 MB), `notes` (optional) → `201`
```json
{ "success": true, "data": { "inspection": { "…": "visit with status pending_review, image_url signed" },
  "image_url": "https://…signed…" }, "message": "Scan submitted — an officer will review it" }
```
Staff are notified; when an officer completes the review the visit becomes `completed` with `diagnosis` and `recommendation`, the dashboard `health_status` updates, and the farmer gets **"Field inspection — Officer review for {crop} is now available"**.

### 5.5 Warehouses & delivery booking

**`GET /warehouses`**
```json
{ "id": "a5a6c014-…", "name": "Kurnool Central Warehouse Hub", "address": "NH44, Kurnool", "location": "Kurnool",
  "contact_number": "9502662924", "capacity": 100000.0, "available_capacity": 65000.0 }
```

**`GET /warehouses/{warehouse_id}`** — single-warehouse detail, same shape as one item of `GET /warehouses`. `404` if it doesn't exist.

**`GET /warehouses/{warehouse_id}/slots?date=2026-09-25`** — omit `date` for every upcoming slot. Only active, non-past slots are returned.
```json
{ "id": "96507107-…", "warehouse_id": "a5a6c014-…", "slot_date": "2026-09-25", "slot_time": "09:00 AM - 12:00 PM",
  "start_time": "09:00:00", "end_time": "12:00:00", "total_capacity_kg": 50000.0,
  "available_weight_kg": 25000.0, "available_weight_qtl": 250.0,
  "max_bookings": 10, "available_bookings": 4, "status": "active" }
```
Disable a slot in the UI when `available_bookings == 0` or `available_weight_kg < quantity`.

**`POST /grain-sales/book-slot`** → `201`
```json
{ "warehouse_id": "a5a6c014-…", "warehouse_slot_id": "96507107-…", "grain_type": "Cotton",
  "quantity_kg": 2500, "booking_date": "2026-09-25", "delivery_address": "Kalluru village, Kurnool" }
```
```json
{ "success": true, "data": { "booking_id": "d07aad79-be2f-427d-a5c4-3cfac280d557", "status": "pending" },
  "message": "Slot booked successfully" }
```
Rules (all enforced under a row lock on the slot): `booking_date` must equal the slot's date (`422`); past slots can't be booked (`409`); slot full by count or weight → `409 CAPACITY_EXCEEDED`; the same farmer can't hold two active bookings on one slot (`409`). Optional: `grain_sale_id`, `notes`.

**`GET /grain-sales/bookings`** (paginated)
```json
{ "id": "d07aad79-…", "grain_type": "Cotton", "quantity_kg": 2500.0, "booking_date": "2026-09-25",
  "delivery_address": "Kalluru village, Kurnool", "status": "pending", "notes": null,
  "warehouse_slot_id": "96507107-…", "slot_time": "09:00 AM - 12:00 PM", "created_at": "2026-09-21T06:13:41.357791Z",
  "warehouse": { "id": "a5a6c014-…", "name": "Kurnool Central Warehouse Hub", "address": "NH44, Kurnool", "contact_number": "9502662924" } }
```

**`DELETE /grain-sales/bookings/{booking_id}`** — cancels and returns the weight and the booking count to the slot. Allowed while `pending` or `confirmed` (and `delivered`); otherwise `422 INVALID_STATE_TRANSITION`.

Booking status flow: `pending → confirmed → delivered → inspected → completed`, or `cancelled`.

### 5.6 Grain sale offers

**`POST /grain-sales/offers`** → `201`
```json
{ "crop_type": "Cotton", "grade": "A", "quantity_kg": 2500, "price_per_kg": 71.25, "notes": null, "crop_id": null }
```
`price_per_kg` is the farmer's **asking** price; `crop_id` (optional) must be one of their own crops (`404` otherwise).
```json
{ "id": "80ef65d2-…", "crop_type": "Cotton", "grade": "A", "quantity_kg": 2500.0, "offered_price_per_kg": 71.25,
  "price_per_kg": null, "good_material_kg": 0.0, "wastage_kg": 0.0, "total_amount": 0.0,
  "status": "pending", "notes": null, "created_at": "2026-09-21T06:13:41.415958Z" }
```
`price_per_kg`, `good_material_kg`, `wastage_kg`, `total_amount` are filled in by the warehouse at weighing/review.

**`GET /grain-sales/offers`** (paginated, `?status=`).

### 5.7 Notifications & push

| Endpoint | Returns |
|---|---|
| `GET /notifications` (paginated) | `{ id, title, message, type, is_read, reference_type, reference_id, created_at }` |
| `GET /notifications/unread-count` | `{ "unread_count": 2 }` — use for the bell badge |
| `PATCH /notifications/{id}/read` | marks one read (`404` if not yours) |
| `POST /notifications/read-all` | marks all read |
| `POST /notifications/fcm-token` | registers this device: `{ "fcm_token": "…", "device_type": "android" }` |

`type` ∈ `info | success | warning | error`. `reference_type` ∈ `seed_purchase | booking_slot | grain_sale | farm_visit | bank_change_request | transaction` — use it with `reference_id` for deep links.

Push payload (`RemoteMessage.data`): `notification_id`, `type`, `reference_type`, `reference_id` — the same values as the in-app row. Pushes are sent for every farmer notification: order placed, booking submitted / confirmed / cancelled, inspection completed, bank request reviewed, grain sale reviewed / paid, account approved. A push is only sent after the change is committed to the database, so it never announces something that rolled back.

### 5.8 Ledger & documents

**`GET /transactions`** (paginated)
```json
{ "id": "cf1118e9-…", "reference_type": "seed_purchase", "reference_id": "b237299f-…", "amount": 425.0,
  "direction": "debit", "status": "pending", "description": "Seed purchase: TDN-58 Groundnut (5 kg)",
  "invoice_number": "SP-20260921-0053DC36", "transaction_id": null, "created_at": "2026-09-21T06:13:41.168283Z" }
```
The statement PDF stays client-side — page through this endpoint to collect the rows.

**`POST /documents/upload`** — `multipart/form-data`: `file`, `doc_type` ∈ `avatar | aadhaar | passbook | land` → `201`
```json
{ "success": true, "data": { "document_id": "…", "document_type": "aadhaar", "url": "https://…signed…",
  "content_type": "application/pdf", "size_bytes": 184233 }, "message": "Document uploaded" }
```
| `doc_type` | Allowed | Max | Updates profile field |
|---|---|---|---|
| `avatar` | JPEG, PNG | 2 MB | `avatar_url` |
| `aadhaar` | JPEG, PNG, PDF | 5 MB | `aadhaar_url` |
| `passbook` | JPEG, PNG, PDF | 5 MB | `passbook_url` |
| `land` | JPEG, PNG, PDF | 5 MB | `land_proof_url` |

Stored as `farmer_{id}/{doc_type}_{timestamp}.{ext}`. Every upload is kept in `farmer_documents`; the profile points at the latest one.

---

## 6. Flutter integration

### 6.1 Dependencies
```yaml
dependencies:
  dio: ^5.7.0
  flutter_secure_storage: ^9.2.2
  firebase_core: ^3.6.0
  firebase_messaging: ^15.1.3
```
Remove `supabase_flutter` once every repository method below is switched.

### 6.2 Configuration
```dart
class AppConstants {
  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'https://agriflow-backend-t8o4.onrender.com/api/v1/farmer',
  );
}
```
Build with `--dart-define=API_BASE_URL=https://api.srisivasaiseeds.com/api/v1/farmer` once you have a custom domain. Mobile apps aren't subject to CORS; no backend change is needed.

The Render free plan sleeps after 15 minutes idle; the first request can take 30–60 s. Set `connectTimeout` ≥ 60 s until the service is on a paid plan.

### 6.3 API client with automatic refresh
```dart
import 'dart:async';
import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class TokenStore {
  static const _s = FlutterSecureStorage();
  Future<String?> get access => _s.read(key: 'access_token');
  Future<String?> get refresh => _s.read(key: 'refresh_token');
  Future<void> save(String access, String refresh) async {
    await _s.write(key: 'access_token', value: access);
    await _s.write(key: 'refresh_token', value: refresh);
  }
  Future<void> clear() => _s.deleteAll();
}

class ApiException implements Exception {
  ApiException(this.status, this.code, this.message, [this.details]);
  final int status;
  final String code;
  final String message;
  final Map<String, dynamic>? details;
  @override
  String toString() => message;
}

class ApiClient {
  ApiClient(this._tokens, {required this.onSessionExpired}) {
    dio = Dio(BaseOptions(
      baseUrl: AppConstants.apiBaseUrl,
      connectTimeout: const Duration(seconds: 60),
      receiveTimeout: const Duration(seconds: 30),
      headers: {'Accept': 'application/json'},
    ));
    dio.interceptors.add(QueuedInterceptorsWrapper(
      onRequest: (options, handler) async {
        final token = await _tokens.access;
        if (token != null) options.headers['Authorization'] = 'Bearer $token';
        handler.next(options);
      },
      onError: (error, handler) async {
        final isAuthCall = error.requestOptions.path.startsWith('/auth/');
        if (error.response?.statusCode == 401 && !isAuthCall &&
            error.requestOptions.extra['retried'] != true) {
          if (await _refreshOnce()) {
            final retry = error.requestOptions..extra['retried'] = true;
            retry.headers['Authorization'] = 'Bearer ${await _tokens.access}';
            return handler.resolve(await dio.fetch(retry));
          }
          await _tokens.clear();
          onSessionExpired();
        }
        handler.next(error);
      },
    ));
  }

  final TokenStore _tokens;
  final void Function() onSessionExpired;
  late final Dio dio;
  Future<bool>? _refreshing;

  Future<bool> _refreshOnce() => _refreshing ??= _doRefresh().whenComplete(() => _refreshing = null);

  Future<bool> _doRefresh() async {
    final refresh = await _tokens.refresh;
    if (refresh == null) return false;
    try {
      final res = await Dio(BaseOptions(baseUrl: AppConstants.apiBaseUrl))
          .post('/auth/refresh', data: {'refresh_token': refresh});
      final data = res.data['data'];
      await _tokens.save(data['access_token'], data['refresh_token']);
      return true;
    } on DioException {
      return false;
    }
  }

  Future<T> call<T>(Future<Response> Function(Dio d) request, T Function(dynamic data) parse) async {
    try {
      final res = await request(dio);
      return parse(res.data['data']);
    } on DioException catch (e) {
      final body = e.response?.data;
      if (body is Map && body['error'] is Map) {
        final err = body['error'];
        throw ApiException(e.response!.statusCode ?? 0, err['code'], err['message'],
            (err['details'] as Map?)?.cast<String, dynamic>());
      }
      throw ApiException(e.response?.statusCode ?? 0, 'NETWORK', 'Check your internet connection');
    }
  }

  Future<Page<T>> page<T>(Future<Response> Function(Dio d) request, T Function(Map<String, dynamic>) item) async {
    try {
      final res = await request(dio);
      final p = res.data['pagination'];
      return Page(
        (res.data['data'] as List).map((e) => item(e as Map<String, dynamic>)).toList(),
        page: p['page'], total: p['total'], totalPages: p['total_pages'],
      );
    } on DioException catch (e) {
      final err = (e.response?.data as Map?)?['error'];
      throw ApiException(e.response?.statusCode ?? 0, err?['code'] ?? 'NETWORK', err?['message'] ?? 'Network error');
    }
  }
}

class Page<T> {
  Page(this.items, {required this.page, required this.total, required this.totalPages});
  final List<T> items;
  final int page, total, totalPages;
  bool get hasMore => page < totalPages;
}
```
`QueuedInterceptorsWrapper` plus the shared `_refreshing` future means ten requests expiring at once trigger **one** refresh. That matters because refresh tokens rotate (§4.1).

### 6.4 Repository mapping

Replace each `FarmerRepository` method's body with the call on the right. The screens don't change.

| `FarmerRepository` method (today) | New call |
|---|---|
| `signInWithMobileOrEmail` | `POST /auth/login` → `TokenStore.save(...)`; keep `user` + `farmer_profile` |
| session restore (splash) | read tokens; if present, `GET /profile` (the interceptor refreshes as needed) |
| `signOut` | `POST /auth/logout {fcm_token}` → `TokenStore.clear()` |
| `getFarmerProfile` | `GET /profile` |
| `updateProfile` | `PATCH /profile` (changed fields only) |
| `requestBankChange` | `POST /profile/bank-request` |
| `getDashboard` (+ the 4–5 queries behind it) | `GET /dashboard` |
| `getMarketRates` | `GET /market-rates` |
| `getSeeds` | `GET /seeds?q=&crop_type=&min_price=&max_price=&warehouse_id=&in_stock_only=` |
| `purchaseSeedsV2` (RPC + fallback insert) | `POST /seeds/purchase` — **delete the fallback insert path** |
| `getSeedPurchases` | `GET /seeds/purchases` |
| `getCrops` | `GET /crops` |
| `addCrop` (+ manual `farm_visits` insert) | `POST /crops` — visits are created server-side |
| `updateCrop` | `PATCH /crops/{id}` |
| `deleteCrop` (2 deletes) | `DELETE /crops/{id}` |
| `getVisits` | `GET /crops/visits?crop_id=` or `GET /crops/{id}/inspections` |
| `submitCropInspection` (storage upload + insert) | `POST /crops/{id}/scan` (multipart) |
| `getWarehouses` | `GET /warehouses` |
| (new) slot picker | `GET /warehouses/{id}/slots?date=` |
| `bookDeliverySlot` (RPC + fallback insert) | `POST /grain-sales/book-slot` — **delete the fallback** |
| `getBookingSlots` | `GET /grain-sales/bookings` |
| `cancelBookingSlot` | `DELETE /grain-sales/bookings/{id}` |
| `submitGrainSale` | `POST /grain-sales/offers` |
| `getGrainSales` | `GET /grain-sales/offers` |
| `getNotifications` | `GET /notifications` |
| badge (client-side filter) | `GET /notifications/unread-count` |
| `markNotificationRead` | `PATCH /notifications/{id}/read` |
| `markAllNotificationsRead` | `POST /notifications/read-all` |
| (new) push registration | `POST /notifications/fcm-token` |
| `getTransactions` | `GET /transactions?page=&page_size=` |
| `uploadDocument` (bucket fallbacks) | `POST /documents/upload` (multipart) |

Delete the `app_user_id` / `profiles` lookup, the `resolvePhoneToEmail` edge-function call and the `@agriseq.local` synthetic email — the token *is* the identity now.

Example methods:
```dart
class FarmerRepository {
  FarmerRepository(this.api);
  final ApiClient api;

  Future<Dashboard> getDashboard() =>
      api.call((d) => d.get('/dashboard'), (j) => Dashboard.fromJson(j));

  Future<List<Seed>> getSeeds({String? q, String? cropType, bool inStockOnly = false}) =>
      api.call((d) => d.get('/seeds', queryParameters: {
            if (q != null && q.isNotEmpty) 'q': q,
            if (cropType != null && cropType != 'All') 'crop_type': cropType,
            'in_stock_only': inStockOnly,
          }), (j) => (j as List).map((e) => Seed.fromJson(e)).toList());

  Future<PurchaseReceipt> purchaseSeeds({required String seedId, required double quantityKg,
          String grade = 'A', String? warehouseId, DateTime? pickupDate}) =>
      api.call((d) => d.post('/seeds/purchase', data: {
            'seed_id': seedId,
            'quantity_kg': quantityKg,
            'grade': grade,
            if (warehouseId != null) 'warehouse_id': warehouseId,
            if (pickupDate != null) 'pickup_date': pickupDate.toIso8601String().substring(0, 10),
            'payment_method': 'warehouse',
          }), (j) => PurchaseReceipt.fromJson(j));

  Future<Page<Booking>> getBookings({int page = 1}) =>
      api.page((d) => d.get('/grain-sales/bookings', queryParameters: {'page': page, 'page_size': 20}),
          Booking.fromJson);

  Future<String?> uploadDocument(String docType, String filePath, String mime) =>
      api.call((d) async => d.post('/documents/upload',
              data: FormData.fromMap({
                'doc_type': docType,
                'file': await MultipartFile.fromFile(filePath, contentType: DioMediaType.parse(mime)),
              })),
          (j) => j['url'] as String?);

  Future<void> submitCropScan(String cropId, String imagePath, {String? notes}) =>
      api.call((d) async => d.post('/crops/$cropId/scan',
              data: FormData.fromMap({
                'image': await MultipartFile.fromFile(imagePath, contentType: DioMediaType.parse('image/jpeg')),
                if (notes != null) 'notes': notes,
              })),
          (_) {});
}
```
Always pass `contentType` on uploads — the server checks the MIME type, and Dio otherwise sends `application/octet-stream`, which is rejected.

Model parsing — the one thing to get right is numbers and IDs:
```dart
class Seed {
  Seed.fromJson(Map<String, dynamic> j)
      : id = j['id'] as String,
        name = j['name'] as String,
        cropType = j['crop_type'] as String?,
        pricePerKg = (j['price_per_kg'] as num).toDouble(),
        oldPrice = (j['old_price'] as num?)?.toDouble(),
        stockKg = (j['stock_kg'] as num).toDouble(),
        imageUrl = j['image_url'] as String?;
  final String id, name;
  final String? cropType, imageUrl;
  final double pricePerKg, stockKg;
  final double? oldPrice;
}
```

### 6.5 Push notifications
```dart
Future<void> registerForPush(ApiClient api) async {
  final fcm = FirebaseMessaging.instance;
  await fcm.requestPermission();
  Future<void> send(String token) => api.call(
      (d) => d.post('/notifications/fcm-token', data: {'fcm_token': token, 'device_type': 'android'}), (_) {});
  final token = await fcm.getToken();
  if (token != null) await send(token);
  fcm.onTokenRefresh.listen(send);
}
```
Call it right after login and on every cold start while signed in. Route taps with `message.data['reference_type']` / `['reference_id']`, then call `GET /notifications/unread-count` to refresh the badge.

The backend needs `FIREBASE_CREDENTIALS_JSON` (see §8) — the app side can ship before that; tokens are stored and pushes start as soon as credentials are added.

### 6.6 Error handling in screens
```dart
try {
  final receipt = await repo.purchaseSeeds(seedId: seed.id, quantityKg: qty);
  showInvoice(receipt);
} on ApiException catch (e) {
  switch (e.code) {
    case 'INSUFFICIENT_STOCK':
    case 'CAPACITY_EXCEEDED':
      showSnack(e.message);
      reloadCatalog();
    case 'VALIDATION_ERROR':
      showFieldErrors(e.details?['errors']);
    case 'RATE_LIMITED':
      showSnack('Too many attempts — please wait a moment');
    default:
      showSnack(e.message);
  }
}
```

---

## 7. Enum reference

| Field | Values |
|---|---|
| `user.status` | `pending`, `active`, `rejected`, `suspended` |
| crop `status` (stage) | `Sowing`, `Growing`, `Maturity`, `Harvest` |
| crop `lifecycle_status` | `growing`, `harvested`, `failed`, `sold` |
| visit `status` | `scheduled`, `pending_review`, `completed`, `cancelled` |
| booking `status` | `pending`, `confirmed`, `delivered`, `inspected`, `completed`, `cancelled` |
| grain offer `status` | `pending`, `received`, `approved`, `rejected`, `paid` |
| `grade` | `A`, `B`, `C` |
| `payment_status` | `pending`, `paid`, `failed` |
| transaction `direction` / `status` | `credit`, `debit` / `pending`, `completed`, `failed` |
| notification `type` | `info`, `success`, `warning`, `error` |
| `doc_type` | `avatar`, `aadhaar`, `passbook`, `land` |
| `bank_status` | `pending`, `approved`, `rejected` |

---

## 8. Go-live checklist (backend)

| # | Item | Where |
|---|---|---|
| 1 | S3 secret key + endpoint for `srisivasai-gallery` → `S3_SECRET_ACCESS_KEY`, `S3_ENDPOINT_URL`, `S3_REGION` | Render → Environment |
| 2 | Firebase service-account JSON → `FIREBASE_CREDENTIALS_JSON` | Render → Environment |
| 3 | Switch `ENVIRONMENT=staging` → `production` once 1–2 are set (production refuses to boot with anything unsafe) | Render → Environment |
| 4 | Paid Render plan (free sleeps after 15 min) | Render |
| 5 | Custom domain `api.srisivasaiseeds.com` → update `API_BASE_URL` in the app build | Render + DNS |
| 6 | Create farmer accounts: self-registration (`POST /api/v1/auth/register`, then approval in the admin portal) or staff-created (`POST /api/v1/farmers`) | Admin portal |
| 7 | Load seeds, warehouses, slots, market rates through the admin API/portal | Admin portal |

### Migrating existing Supabase farmers
Existing farmers can't be moved with their Supabase password hashes in a usable form automatically. Options, in order of preference:
1. Run `scripts/migrate_legacy_identity.py` — it carries bcrypt hashes across and upgrades them to Argon2id on first login, so farmers keep their passwords.
2. Otherwise, staff create the accounts and farmers reset their password via OTP (needs an SMS provider).
