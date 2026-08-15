# Run the API in Docker

A small, self-contained image for the Personal Curation API. You only need
**Docker Desktop**. Two ways to run it — pick one.

---

## Mode 1 — In-memory (quickest, no database)

Synthetic products, no DB, no auth. Best for exercising the **API contract**.

```bash
docker compose up --build
```
Open **http://localhost:8000/docs**.

- `POST /api/start_session/{user_id}` — any UUID for `user_id`
  (e.g. `11111111-1111-1111-1111-111111111111`) → 10 cards.
- `POST /api/suggest` — send swipes / get refills. Body:
  ```json
  {
    "session_id": "<from start_session>",
    "ProductInteraction": [
      { "product_id": "<a product_id from the deck>", "swipe": "right" }
    ],
    "products_swiped": 0
  }
  ```
- No auth header needed. Sessions live in memory and **reset on restart**.
- Products are synthetic and recommendations aren't meaningful here (hash_fallback
  embeddings) — this mode is for the **request/response shapes and session flow**,
  not recommendation quality.

---

## Mode 2 — Against your own local Supabase (real products + persistence)

Use this to integrate your frontend against real product IDs and see sessions
persist in your `pc_*` tables.

**One-time setup on your Supabase:**
1. Apply our migrations (creates `user_taste`, `pc_sessions`, `pc_session_items`,
   `pc_purchase_weights`, `pc_job_runs`, `pc_session_events`):
   ```bash
   psql "<YOUR_LOCAL_DB_URL>" -f migrations/001_pc_tables.sql
   psql "<YOUR_LOCAL_DB_URL>" -f migrations/002_add_bought_as_gift.sql
   psql "<YOUR_LOCAL_DB_URL>" -f migrations/003_pc_session_events.sql
   ```
   (Or paste each into the Supabase Studio SQL editor. See `migrations/README.md`.)
2. Make sure that Supabase has **products** (the pipeline builds recommendations
   from them).

**Then** edit `docker-compose.yml` — comment out `PC_ENABLE_DB_WRITES: "false"`
and set:
```yaml
      PC_ENABLE_DB_WRITES: "true"
      SUPABASE_URL: "http://host.docker.internal:54321"   # your Supabase API port
      SUPABASE_SERVICE_KEY: "<your local service_role key>"
      # INTERNAL_KEY: "dev-secret"   # optional; then send header X-Internal-Key
```
> **Important:** from inside the container, your machine is `host.docker.internal`,
> **not** `127.0.0.1`. Use `http://host.docker.internal:<api-port>` for `SUPABASE_URL`.

```bash
docker compose up --build
```
On startup the container reads your products, builds the matrix, and serves the
API with writes going to your Supabase (`docker compose down` to stop).

---

## Notes

- The image uses **`hash_fallback`** embeddings (no torch) so it's small and fast.
  Real CLIP embeddings (recommendation quality) need the full `requirements.txt`
  and a GPU/CPU with the model — not needed for API/integration testing.
- The API returns `product_id` + `percent_match` only; the calling server hydrates
  full product data from Supabase. Full endpoint reference: `docs/API.md`.
- No `.env` or secrets are baked into the image (excluded via `.dockerignore`).
