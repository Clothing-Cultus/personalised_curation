# EC2 Deployment — Personal Curation API

Deploys the FastAPI curator on **its own EC2 instance** so Parth's staging
frontend/server can call it. Mirrors the proven pattern from the Django box
(Ubuntu 24.04 · Mumbai · Elastic IP · nginx → app · systemd · certbot) and avoids
the mistakes that box hit (see §9).

> **Rule 0 — separate instance.** Do NOT deploy this on the WhatsApp/Django EC2
> (`13.235.206.145`). Spec §7: the swipe loop's latency must never compete with a
> webhook spike or memory leak in the WhatsApp code. Launch a **new** instance.

---

## 1. Provision the instance (AWS console)

| Setting | Value |
|---|---|
| AMI | **Ubuntu Server 24.04 LTS (HVM), SSD Volume Type** — **64-bit (x86)**, *not* Arm (torch wheels) and *not* the `with SQL Server` bundle (it forces a bigger instance type + a license charge, and fails to launch on t3.small) |
| Region | ap-south-1 (Mumbai) |
| Type | **t3.small** (2 GB) if using real CLIP embeddings; t3.micro OK for `hash_fallback` (see §8) |
| Storage | 20 GB gp3 (torch + model + artifacts need room) |
| Key pair | create/download a **new** `.pem` (e.g. `pc-api-key.pem`) — store locally, **never commit** |
| Elastic IP | **allocate + associate one** — a static IP that survives reboots (the Django doc's "wrong IP" pain came from not doing this) |

**Security group (inbound):**
| Port | Source | Why |
|---|---|---|
| 22 (SSH) | your IP (or 0.0.0.0/0 — key-only, so acceptable) | admin |
| 80 (HTTP) | 0.0.0.0/0 | **must stay permanently open** — certbot ACME renewal uses it |
| 443 (HTTPS) | 0.0.0.0/0 | the API |
| **8000** | **do NOT open** | app listens on 127.0.0.1 only; nginx proxies to it |

> **Launch-wizard gotcha (hit this once).** Changing the AMI mid-wizard pops
> *"Some of your current settings will be changed or removed"* and **resets the
> storage size and the security-group rules** in the form. It touches nothing that
> already exists — not your other instances, not their volumes — only the draft.
> After confirming, re-set **20 GB gp3** and re-add **22/80/443**.
>
> If a launch fails *after* "Creating security groups: Succeeded", the SG was
> already created: on the retry pick **Select existing security group →
> `pc-api-sg`**, or creating it again fails on the duplicate name. Same for the key
> pair — reuse the one you downloaded; a second `.pem` just orphans the first.

**DNS:** point a subdomain (e.g. `pc-api.cultus.in`) at the Elastic IP (Vercel DNS,
A record), same as `django.cultus.in`. Confirm `nslookup pc-api.cultus.in` returns
the Elastic IP **before** running certbot (§6) — the ACME challenge resolves the
domain and hits port 80 on this box.

## 2. First-time server setup

```bash
# fix key perms once (Windows): icacls "pc-api-key.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"
ssh -i "pc-api-key.pem" ubuntu@<ELASTIC_IP>

sudo apt update && sudo apt install -y python3-venv python3-pip nginx git

# clone (repo is private — use a deploy key or PAT)
cd /home/ubuntu
git clone https://github.com/Clothing-Cultus/personalised_curation.git
cd personalised_curation

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt          # includes torch/open-clip for real embeddings
```

> **t3.micro OOM guard (if you use a small instance):** add swap before installing
> torch or running the pipeline, or the build/embedding will get killed:
> ```bash
> sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
> sudo mkswap /swapfile && sudo swapon /swapfile
> echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
> ```

## 3. Create `.env` on the server (never committed)

```bash
nano /home/ubuntu/personalised_curation/.env
```
```
SUPABASE_URL=https://<staging-ref>.supabase.co
SUPABASE_SERVICE_KEY=<staging service_role key>   # bypasses RLS — secret, server-only
INTERNAL_KEY=<a long random shared secret>        # Parth sends this as X-Internal-Key
PC_ENABLE_DB_WRITES=true
PC_EMBEDDING_BACKEND=clip                          # or hash_fallback (see §8)
PC_ENV=staging
```
Generate `INTERNAL_KEY`: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
and give the value to Parth (out-of-band, not in git).

## 4. Migrations + first artifact build

1. **Migrations (you, by hand)** — run our 3 migrations on the **staging** Supabase
   (Studio SQL editor or psql). See `migrations/README.md`:
   `001_pc_tables.sql`, `002_add_bought_as_gift.sql`, `003_pc_session_events.sql`.
   Staging must also have **products** (the pipeline builds recommendations from them).
2. **Build artifacts once** (the API returns 503 until they exist):
   ```bash
   source venv/bin/activate
   python -m ml.pipeline.run_pipeline      # reads staging products → ml/artifacts/
   ```

## 5. systemd service (keeps it running + restarts on reboot/crash)

```bash
sudo cp deploy/personalised-curation.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now personalised-curation
sudo systemctl status personalised-curation        # should be active (running)
# logs: sudo journalctl -u personalised-curation -n 50 --no-pager
```
(App listens on `127.0.0.1:8000`, single worker — see the unit file's comments.)

## 6. nginx + HTTPS (certbot nginx plugin — auto-renewing)

```bash
# edit the server_name to your domain first:
sudo cp deploy/nginx-pc.conf /etc/nginx/sites-available/personalised-curation
sudo nano /etc/nginx/sites-available/personalised-curation   # set server_name
sudo ln -s /etc/nginx/sites-available/personalised-curation /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx                 # ALWAYS nginx -t first

sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d pc-api.cultus.in                     # adds the 443 block + 80→443 redirect
sudo certbot renew --dry-run                                 # confirm auto-renew works
```
This uses the **nginx authenticator** (not `manual`) and keeps **port 80 open** — the
two things that broke SSL renewal on the Django box.

## 7. Nightly cron (pipeline + vector rebuild + purchase reconciliation)

```bash
crontab -e
```
```
# 03:30 IST — rebuild the matrix, replay stale vectors, reconcile purchases
30 22 * * * cd /home/ubuntu/personalised_curation && /home/ubuntu/personalised_curation/venv/bin/python -m scripts.nightly >> /home/ubuntu/pc-nightly.log 2>&1
```
(22:00 UTC = 03:30 IST. Pick a low-traffic hour.) The API hot-reloads the new
matrix within ~60s — **unless the feature layout changed**, in which case restart
the service after the nightly run (see §9).

## 8. Real CLIP vs hash_fallback (recommendation quality)

- **`PC_EMBEDDING_BACKEND=clip`** → real image/text embeddings → meaningful
  recommendations. Needs torch + open-clip (heavy) and the pipeline downloads the
  CLIP model (~350 MB) + product images. Use t3.small + swap.
- **`PC_EMBEDDING_BACKEND=hash_fallback`** → light/fast, runs on t3.micro, but
  similarity is random — fine for **API/frontend integration** on staging, not for
  judging recommendation quality.
- Start with whichever fits the goal; switching is one `.env` change + re-run the
  pipeline + restart the service.

## 9. Mistakes from the Django box — and how this avoids them

| Django-box problem | How we avoid it here |
|---|---|
| **SSL cert expired → everything silently died** | `certbot --nginx` auto-renew + `renew --dry-run` check; port 80 stays open |
| **certbot "manual plugin non-interactive"** | we install `python3-certbot-nginx` and use the nginx authenticator from the start |
| **certbot challenge timeout (port 80 blocked)** | security group opens 80 permanently (see §1) |
| **Wrong IP in docs/config** | Elastic IP + a domain; reference the **domain**, not a raw IP |
| **Editing code on EC2 directly** | deploy flow is local → GitHub → EC2 `git pull` (see §10) |
| **service_role key exposed** | `.env` on server only, gitignored; never in logs/frontend |
| **broken nginx reload took site down** | always `sudo nginx -t` before reload |
| **.pem committed / bad perms** | new `.pem`, gitignored, `icacls` once |

**PC-specific gotchas (new vs Django):**
- **API 503s until artifacts exist** — run the pipeline once before first traffic (§4).
- **Single worker only** — don't raise `--workers`; the caches are per-process.
- **Feature/layout changes need a restart**, not just hot-reload — the app refuses
  to auto-adopt a layout-changed matrix (it would cold-start everyone). Sequence:
  deploy code → run `scripts.nightly` → `sudo systemctl restart personalised-curation`.
- **torch is heavy** — swap + ≥2 GB RAM, or use hash_fallback (§8).

## 10. Deploy flow (after the first setup) — local → GitHub → EC2

```bash
# local
git add . && git commit -m "…" && git push origin main

# EC2 (one-liner)
ssh -i "pc-api-key.pem" ubuntu@<ELASTIC_IP> \
  "cd /home/ubuntu/personalised_curation && git pull origin main && \
   sudo systemctl restart personalised-curation && \
   sudo systemctl status personalised-curation --no-pager"
```
- **New pip packages:** `git pull` → `source venv/bin/activate` → `pip install -r requirements.txt` → restart.
- **New migration:** you run the SQL on staging Supabase by hand (never from code), then restart.
- **Feature/layout change:** pull → run `python -m scripts.nightly` → restart (per §9).

## 11. What Parth needs

- **Base URL:** `https://pc-api.cultus.in`
- **Auth:** send header `X-Internal-Key: <the INTERNAL_KEY value>` on every request.
- **Endpoints + payloads:** `docs/API.md`.
- His staging **server** calls the API (server-to-server) and hydrates full product
  data from Supabase — the API returns `product_id` + `percent_match` only.

## 12. Post-deploy checklist

- [ ] `curl https://pc-api.cultus.in/healthz` → `{"status":"ok", ...}` with a real `artifact_version`.
- [ ] A `start_session` + `suggest` round-trip works (with the `X-Internal-Key` header).
- [ ] Rows appear in staging `pc_sessions` / `pc_session_items` / `user_taste`.
- [ ] `sudo certbot renew --dry-run` passes.
- [ ] `sudo systemctl is-enabled personalised-curation` → enabled (survives reboot).
- [ ] Nightly cron logged a run to `pc-nightly.log`.
- [ ] Elastic IP + domain confirmed; this doc's domain/IP are correct.
