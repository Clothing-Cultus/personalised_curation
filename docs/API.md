# Personal Curation API

Server-to-server only. **The browser never calls this API directly** — your
server authenticates the user, calls these endpoints, then hydrates full product
data from Supabase (keyed on the returned `product_id`s) before responding to the
browser.

- **Base URL:** `https://<curator-host>` (EC2). Local dev: `http://127.0.0.1:8000`
- **Auth:** every request sends header `X-Internal-Key: <shared secret>`.
  Missing/wrong → `401`. (`/healthz` needs no auth.)
- **What we return:** `product_id` + PC fields only — never title, price, images,
  or sizes. Hydrate those yourself from Supabase, in the returned array order.
- **Interactive docs:** `GET /docs` (Swagger UI).

---

## 1. Start a session

```
POST /api/start_session/{user_id}
Headers: X-Internal-Key: <secret>
Body:    (none)
```

**200 →** a `SessionData` object with the first batch of cards.

```json
{
  "session_id": "b1f2...-uuid",
  "user_id": "user-123",
  "type": "continue",
  "data": [
    { "product_id": "p0af...", "percent_match": 91, "taste_notification_content": null },
    { "product_id": "p13c...", "percent_match": 88, "taste_notification_content": null }
  ],
  "total_swipes": 0,
  "right_swipes_left": 8,
  "products_left": 40,
  "superlikes_left": 1
}
```

Call this when the user opens the feed. Resuming is automatic: within the renewal
window the same session (and its remaining allotment) is returned; after it, a
fresh one is created. You always just call `start_session` — no resume flag.

---

## 2. Send a swipe / get more cards

```
POST /api/suggest
Headers: X-Internal-Key: <secret>
Body:    SuggestRequestBody
```

Call once per swipe. Most calls return an **ack** (`data: []`); when the client
queue runs low the response includes the **next batch** in `data`.

**Request:**
```json
{
  "session_id": "b1f2...-uuid",
  "interactions": [
    {
      "product_id": "p0af...",
      "swipe": "right",
      "details_read": false,
      "last_image_discovered": 2,
      "bought_in_session": false,
      "size_selected": "M",
      "size_scope": "category"
    }
  ],
  "products_swiped": 12
}
```

| Field | Type | Notes |
|---|---|---|
| `session_id` | uuid | from `start_session` |
| `interactions[]` | array | usually one per call; a small batch is fine |
| `interactions[].swipe` | `left` \| `right` \| `superlike` | required |
| `interactions[].details_read` | bool | did they open the detail view |
| `interactions[].last_image_discovered` | int | how many images they browsed |
| `interactions[].bought_in_session` | bool | bought straight from the card |
| `interactions[].size_selected` | string \| null | e.g. `"M"`, `"32"` |
| `interactions[].size_scope` | `this_product` \| `category` \| null | `category` updates the user's standing size for that garment type; `this_product` is logged only |
| `products_swiped` | int | advisory only — backend is the source of truth |

**200 → `SessionData`.** Two shapes:

*Continuing (ack or refill):*
```json
{
  "session_id": "b1f2...", "user_id": "user-123", "type": "continue",
  "data": [],                       // or a fresh array of cards on a refill
  "total_swipes": 12, "right_swipes_left": 3, "products_left": 28, "superlikes_left": 0
}
```

*Ended:* `data` becomes a summary object.
```json
{
  "session_id": "b1f2...", "user_id": "user-123", "type": "ended",
  "data": { "total_swipes": 15, "right_swipes": 8, "superlikes": 1 },
  "total_swipes": 15, "right_swipes_left": 0, "products_left": 25, "superlikes_left": 0
}
```

**Client rules**
1. `data` is always an **array of cards** while `type == "continue"` (often empty).
2. When `type == "ended"`, `data` is the summary object — stop the feed and show it.
3. Keep a local queue; expect a refill batch roughly when your queue drops to ~3.
4. Array order **is** display order.
5. Hydrate each `product_id` from Supabase before rendering.

---

## 3. Health

```
GET /healthz          (no auth)
→ 200 { "status": "ok", "artifact_version": "…", "artifact_age_seconds": 12.3,
        "n_products": 574, "vector_dim": 585, "db_writes_enabled": false }
```

---

## 4. Reset a user (staging only)

```
POST /api/reset_user/{user_id}      (X-Internal-Key)
→ 200 { "user_id": "…", "sessions_deleted": 3 }
```

Wipes everything PC holds for the user, in the DB **and** in process memory:
sessions, served cards, swipe history (so cooldowns and "seen" state go too),
lifecycle events, and the taste vector. Their next `start_session` is a brand-new,
cold-start user. The purchase ledger is kept. Deleting the rows from Supabase alone
isn't enough, because the running container keeps serving from its caches.

Returns **403** when `PC_ENV=production`. Unknown users return 200 with
`sessions_deleted: 0`.

```
POST /api/reset_all                 (X-Internal-Key)
→ 200 { "sessions_deleted": 42 }
```

The same wipe for **every** user at once: all sessions, cards, events and taste
vectors, in the DB and in memory. The purchase ledger is kept. Also **403** when
`PC_ENV=production`.

---

## Response field reference

**SessionData**

| Field | Type | Meaning |
|---|---|---|
| `session_id` | uuid | current session |
| `user_id` | string | echoed back |
| `type` | `continue` \| `ended` | feed continues, or is over |
| `data` | `ProductData[]` \| summary | cards while continuing; summary when ended |
| `total_swipes` | int | swipes so far this session |
| `right_swipes_left` | int | remaining right+superlike budget |
| `products_left` | int | remaining products this cycle |
| `superlikes_left` | int | remaining superlikes |

**ProductData** — `product_id` (uuid), `percent_match` (0–100 taste-match), `taste_notification_content` (string or null; a short "we're learning your style" line, shown when present).

## Errors

| Code | When |
|---|---|
| `401` | missing/invalid `X-Internal-Key` |
| `404` | `session_id` not found (start a new session) |
| `503` | recommendation artifacts not loaded yet (pipeline hasn't run) |
