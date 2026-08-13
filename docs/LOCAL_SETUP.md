# Local setup — run everything against a local clone of prod

Goal: run the pipeline, API, and DB writes **entirely locally**, against a local
Supabase that has prod's tables (with some product data). Production is never
read or written by the app — the code only ever talks to whatever `SUPABASE_URL`
points to. The only prod contact is one **read-only** copy of schema + a few
products, done by hand below.

> **`pcenv` is your Python virtualenv** (activate: `pcenv\Scripts\activate`).
> The **secrets file must be named `.env`** (the app reads `./.env`) — not `pcenv`.

---

## The principle (why this is safe)

- The app has no hardcoded project — it connects to `SUPABASE_URL` /
  `SUPABASE_SERVICE_KEY` from `.env`. Point those at local → the app is 100% local.
- `ENABLE_DB_WRITES` gates writes. Local → set it `True` (writes are wanted).
- Nothing in the app writes to prod under any setting once `.env` is local.

---

## Part 1 — Manual (you)

### 1. Install prerequisites
- **Docker Desktop** (Supabase local runs in Docker) — start it.
- **Supabase CLI** — `npm i -g supabase`, or `scoop install supabase`, or the
  Windows binary from the Supabase releases page.

### 2. Start local Supabase
From the repo root:
```bash
supabase init          # creates supabase/ (commit supabase/config.toml)
supabase start         # boots local Postgres + APIs in Docker
```
`supabase start` prints local credentials — **copy these**:
- `API URL`  → `http://127.0.0.1:54421`
- `service_role key` (a long token)
- `DB URL`   → `postgresql://postgres:postgres@127.0.0.1:54422/postgres`

(`supabase status` reprints them anytime.)

> **Non-default ports.** This project runs on the **544xx** block (api 54421,
> db 54422, studio 54423, mailpit 54424), not Supabase's 543xx defaults,
> because the separate `cultus_web` local project already occupies 54321-54327
> on this machine. Both stacks can run at once. The ports live in
> `supabase/config.toml`; `supabase start` will always print the real values.

### 3. Clone prod's SCHEMA into local (read-only on prod)
You need the prod DB connection string (Supabase Dashboard → Project Settings →
Database → Connection string / URI, with the DB password).

```bash
# dump prod schema only (no data, no changes to prod)
supabase db dump --db-url "<PROD_DB_URL>" -f prod_schema.sql

# load it into local
psql "<LOCAL_DB_URL>" -f prod_schema.sql
```
If you don't have `psql`, run the SQL via `supabase db reset` after placing
`prod_schema.sql` under `supabase/migrations/` — or paste it into the local
Studio SQL editor (`http://127.0.0.1:54423`).

### 4. Create our new tables in local
Run our migration against local (Studio SQL editor, or psql):
```bash
psql "<LOCAL_DB_URL>" -f migrations/001_pc_tables.sql
```

### 5. Point the app at local
Create `.env` (copy from `.env.example`) with the **local** values from step 2:
```
SUPABASE_URL=http://127.0.0.1:54421
SUPABASE_SERVICE_KEY=<local service_role key from `supabase start`>
INTERNAL_KEY=dev-secret-123        # any string; the header you'll send
```
Then flip the write switch — edit `core/config.py`:
```python
ENABLE_DB_WRITES = True
```

### 6. Seed some products (read-only on prod)
Give the seed script prod's read-only creds via env vars (its destination is
your local `.env`):
```bash
set PROD_SUPABASE_URL=https://<prod-ref>.supabase.co
set PROD_SUPABASE_SERVICE_KEY=<prod-service-role-key>
python scripts/seed_local_from_prod.py --limit 80
```
It copies categories + 80 products + their topwear/bottomwear into local. It
**refuses to run if source and destination URLs match**, so it can't touch prod.
(Users/orders are not copied — see the script header.)

---

## Part 2 — Run it (local)

```bash
pcenv\Scripts\activate
python -m ml.pipeline.run_pipeline      # reads LOCAL products → ml/artifacts/
python -m app.main                      # http://127.0.0.1:8000/docs
```
Smoke test (sends the auth header):
```bash
curl -X POST http://127.0.0.1:8000/api/start_session/test-user-1 ^
  -H "X-Internal-Key: dev-secret-123"
```
Because `ENABLE_DB_WRITES=True` and `.env` is local, sessions/swipes/taste
vectors now persist to your **local** `pc_*` / `user_taste` tables. Verify in
local Studio (`http://127.0.0.1:54423`).

> No `torch` installed? Set `PC_EMBEDDING_BACKEND=hash_fallback` before the
> pipeline (the API doesn't need it). Real image embeddings need
> `torch` + `open-clip-torch` and outbound access to the product image CDN
> (public URLs — not the prod DB).

---

## What I set up for you (already in the repo)

- `scripts/seed_local_from_prod.py` — the read-only prod→local product copier
  (with the same-URL safety guard).
- `catalog_loader` now selects `topwear`/`bottomwear` with `*`, so a schema
  missing `hooded` / `fade` / `closure_type` won't error.
- `.gitignore` now excludes `pcenv/`, `venv/`, `.env`, and local Supabase state.

## What stays manual (only you can)

- Installing Docker + Supabase CLI; `supabase init` / `start`.
- The prod schema dump + load, and our migration into local (needs prod DB
  password + local DB URL — I don't have or handle credentials).
- Creating `.env` with the local keys, and setting `ENABLE_DB_WRITES = True`.

## Going to staging later

Same code, just a different `.env` (staging `SUPABASE_URL`/key). Run
`migrations/001_pc_tables.sql` on staging by hand, keep `ENABLE_DB_WRITES=True`.
No code change between local → staging → prod — only `.env` differs.
