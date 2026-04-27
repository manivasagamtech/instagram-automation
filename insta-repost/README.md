# Instagram Meme Repost Bot 🤖

An automated system that downloads meme posts from Instagram, queues them in
Google Sheets, and republishes them to your own Instagram Business account on
a configurable schedule — all powered by the official Meta Graph API.

---

## Architecture

```
  Web Browser (you)
       │  submit URL / view dashboard
       ▼
  ┌──────────────┐
  │  Flask Web   │  Password-protected UI
  │  (web.py)    │
  └──────┬───────┘
         │ adds row
         ▼
  ┌──────────────────┐        ┌────────────────────┐
  │  Google Sheet    │◄──────►│  queue_client.py   │
  │  (MemeQueue)     │        │  (gspread wrapper) │
  └──────────────────┘        └────────────────────┘
                                       ▲
                                       │ reads ready rows
                              ┌────────┴────────┐
                              │  APScheduler    │  ticks every N minutes
                              │  (scheduler.py) │
                              └────────┬────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                   ▼
           ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
           │ downloader   │  │  uploader    │  │   publisher      │
           │ (instaloader │  │ (Catbox.moe) │  │ (Graph API v21)  │
           │  / yt-dlp)   │  │              │  │                  │
           └──────────────┘  └──────────────┘  └──────────────────┘
```

---

## How to run locally

### 1. Clone and install

```bash
git clone https://github.com/manivasagamtech/instagram-automation.git
cd instagram-automation/insta-repost
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — fill in every required value (see table below)
```

### 3. Run the bot (dev server + scheduler)

```bash
python main.py
```

Expected startup output:

```
[2025-xx-xx xx:xx:xx] INFO main: Config loaded — window 08:00–22:00 UTC | interval 60 min | cap 5/day
[2025-xx-xx xx:xx:xx] INFO app.scheduler: Scheduled publish_job every 60 minutes.
[2025-xx-xx xx:xx:xx] INFO app.scheduler: Scheduled token_refresh_job every Sunday 03:00 UTC.
[2025-xx-xx xx:xx:xx] INFO app.scheduler: Scheduled cleanup_job every day 04:00 UTC.
[2025-xx-xx xx:xx:xx] INFO app.scheduler: BackgroundScheduler started.
[2025-xx-xx xx:xx:xx] INFO main: Scheduler started. Bot is live.
[2025-xx-xx xx:xx:xx] INFO main: Starting Flask development server on port 8080 …
```

Then open **http://localhost:8080** in your browser.

### 4. Run tests

```bash
pytest tests/ -v
```

All 232 tests should pass.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `FLASK_SECRET_KEY` | ✅ | — | Random secret for Flask session signing (use `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `APP_PASSWORD` | ✅ | — | Dashboard login password |
| `IG_USER_ID` | ✅ | — | Instagram Business account numeric ID |
| `IG_ACCESS_TOKEN` | ✅ | — | Long-lived Graph API access token (60-day TTL; bot refreshes it weekly) |
| `FB_APP_ID` | ✅ | — | Facebook App ID |
| `FB_APP_SECRET` | ✅ | — | Facebook App Secret |
| `GOOGLE_CREDENTIALS_JSON` | ✅ | — | Service account key JSON as a **single-line string** (see note below) |
| `GOOGLE_SHEET_NAME` | ✅ | — | Exact name of the MemeQueue Google Sheet |
| `IG_LOGIN_USER` | ✅ | — | Burner Instagram username (used by instaloader to download posts) |
| `IG_LOGIN_PASS` | ✅ | — | Burner Instagram password |
| `POST_INTERVAL_MINUTES` | ❌ | `60` | Minutes between publish attempts |
| `MAX_POSTS_PER_DAY` | ❌ | `5` | Maximum posts published per UTC calendar day (hard cap: 20) |
| `POSTING_HOURS_START` | ❌ | `8` | Earliest UTC hour to publish (inclusive) |
| `POSTING_HOURS_END` | ❌ | `22` | Latest UTC hour to publish (exclusive) |
| `LOG_LEVEL` | ❌ | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `PORT` | ❌ | `8080` | Port Gunicorn listens on (Railway sets this automatically) |

> **GOOGLE_CREDENTIALS_JSON tip** — Railway's variable panel handles multiline
> values poorly. Convert your service account JSON to a single line before
> pasting:
>
> ```bash
> # macOS / Linux
> cat your-service-account.json | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)))"
>
> # Windows PowerShell
> Get-Content your-service-account.json | python -c "import sys,json; print(json.dumps(json.load(sys.stdin)))"
> ```
>
> Copy the output (a single long line starting with `{"type":"service_account",...}`)
> and paste it as the value of `GOOGLE_CREDENTIALS_JSON`.

---

## Deployment (Railway)

### Prerequisites

Before deploying:
- [ ] Instagram Business account with Graph API access (v21.0)
- [ ] Facebook App with `instagram_basic`, `instagram_content_publish`, `pages_read_engagement` permissions
- [ ] Long-lived access token obtained via the Graph API Explorer
- [ ] Google Cloud service account with Sheets + Drive API enabled
- [ ] A Google Sheet named **MemeQueue** shared with the service account (Editor)
- [ ] A **burner** Instagram account for instaloader downloads (keep separate from your main account)

### Step-by-step

#### 1. Push the repo to GitHub

```bash
git push origin main
```

#### 2. Create a Railway project

1. Go to [railway.app](https://railway.app) → **New Project**
2. Choose **Deploy from GitHub repo**
3. Select `instagram-automation` → confirm the repo root is `insta-repost/`

> If Railway can't find the Dockerfile automatically, set the **Root Directory**
> to `insta-repost` in **Settings → Source**.

#### 3. Set environment variables

In your Railway project → **Variables** tab, add every variable from the
table above. Pay attention to these:

| Variable | Notes |
|---|---|
| `FLASK_SECRET_KEY` | Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GOOGLE_CREDENTIALS_JSON` | Must be a **single-line** JSON string (see tip above) |
| `IG_ACCESS_TOKEN` | Long-lived token — bot refreshes it every Sunday at 03:00 UTC and logs the new value at WARN level; copy it into this variable after each refresh |
| `PORT` | **Do not set** — Railway injects this automatically |

#### 4. Deploy

Railway triggers an automatic build and deploy on every `git push origin main`.

- Build progress: Railway dashboard → **Deployments** tab
- Runtime logs: **Logs** tab — look for `BackgroundScheduler started` to confirm the scheduler is running
- Health check: Railway polls `GET /healthz` every 60 s; a `200 OK` means the container is healthy

#### 5. Verify

After a successful deploy:

```
https://<your-railway-domain>/       → redirects to /login
https://<your-railway-domain>/healthz → { "ok" }   (HTTP 200)
```

Log in with `APP_PASSWORD`, submit an Instagram URL, approve the row in the
queue dashboard, and wait for the next scheduler tick.

### Docker (local verification)

Verify the image before pushing to Railway:

```bash
# Build
docker build -t memebot .

# Run (requires a populated .env file)
docker run --env-file .env -p 8080:8080 memebot

# Smoke test
curl http://localhost:8080/healthz   # → "ok"
```

---

## Google Sheet schema

Create a Sheet named **MemeQueue** (or whatever you set in `GOOGLE_SHEET_NAME`)
with these column headers in **row 1**, in this exact order:

```
shortcode | media_url | caption | source_user | media_type | status | post_id | created_at | posted_at | error
```

Share the sheet with your service account's email address and grant **Editor** access.

---

## Scheduler jobs

| Job | Schedule | What it does |
|---|---|---|
| `publish_job` | Every `POST_INTERVAL_MINUTES` minutes | Publishes the next `status=ready` row to Instagram |
| `token_refresh_job` | Every Sunday 03:00 UTC | Refreshes the long-lived IG access token; logs the new value at WARN |
| `cleanup_job` | Every day 04:00 UTC | Deletes `downloads/` folders older than 24 hours |

---

## Build phases

| Phase | Description |
|---|---|
| 1 | Project scaffolding, config, logger |
| 2 | Instagram downloader (instaloader + yt-dlp fallback) |
| 3 | Media uploader (Catbox.moe + 0x0.st fallback) |
| 4 | Google Sheets queue client |
| 5 | Flask web UI (submit, preview, queue dashboard) |
| 6 | Instagram Graph API publisher |
| 7 | APScheduler background worker + main.py integration |
| 8 | Dockerfile, railway.toml, .dockerignore, deployment docs |
