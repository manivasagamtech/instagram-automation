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
                                       │ reads pending rows
                              ┌────────┴────────┐
                              │  APScheduler    │  ticks every N minutes
                              │  (scheduler.py) │
                              └────────┬────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                   ▼
           ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
           │ downloader   │  │  uploader    │  │   publisher      │
           │ (instaloader │  │ (Catbox.moe) │  │ (Graph API v19)  │
           │  / yt-dlp)   │  │              │  │                  │
           └──────────────┘  └──────────────┘  └──────────────────┘
```

---

## How to run locally

### 1. Clone and install

```bash
git clone https://github.com/manivasagamtech/instagram-automation.git
cd instagram-automation/insta-repost
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in all required values (see table below)
```

### 3. Run sanity check (Phase 1)

```bash
python main.py
```

Expected output:
```
[2025-xx-xx xx:xx:xx] INFO main: Bot starting up…
[2025-xx-xx xx:xx:xx] INFO main: Configuration loaded successfully: Config(...)
[2025-xx-xx xx:xx:xx] INFO main: Phase 1 complete — all env vars present.
```

### 4. Run the web server (Phase 5+)

```bash
gunicorn "app.web:create_app()" --bind 0.0.0.0:8080 --workers 1
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `FLASK_SECRET_KEY` | ✅ | — | Random secret for Flask session signing |
| `APP_PASSWORD` | ✅ | — | Dashboard login password |
| `IG_USER_ID` | ✅ | — | Instagram Business account numeric ID |
| `IG_ACCESS_TOKEN` | ✅ | — | Long-lived Graph API access token |
| `FB_APP_ID` | ✅ | — | Facebook App ID |
| `FB_APP_SECRET` | ✅ | — | Facebook App Secret |
| `GOOGLE_CREDENTIALS_JSON` | ✅ | — | Service account key JSON (single-line string) |
| `GOOGLE_SHEET_NAME` | ✅ | — | Exact name of the MemeQueue Google Sheet |
| `IG_LOGIN_USER` | ✅ | — | Burner IG account username (for instaloader) |
| `IG_LOGIN_PASS` | ✅ | — | Burner IG account password |
| `POST_INTERVAL_MINUTES` | ❌ | `60` | Minutes between scheduled post attempts |
| `MAX_POSTS_PER_DAY` | ❌ | `5` | Maximum posts published per calendar day |
| `POSTING_HOURS_START` | ❌ | `8` | Earliest hour to publish (24h, inclusive) |
| `POSTING_HOURS_END` | ❌ | `22` | Latest hour to publish (24h, exclusive) |
| `LOG_LEVEL` | ❌ | `INFO` | Logging level: DEBUG / INFO / WARNING / ERROR |
| `PORT` | ❌ | `8080` | Port Flask / Gunicorn listens on |

---

## Deployment (Railway)

> Full deployment instructions will be added in a later phase.

1. Push the repo to GitHub.
2. Connect the repo to a Railway project.
3. Set all required environment variables in Railway's variable panel.
4. Railway auto-deploys on every `git push`.

---

## Google Sheet schema

Create a Sheet named **MemeQueue** with these headers in **row 1**:

```
shortcode | media_url | caption | source_user | media_type | status | post_id | created_at | posted_at | error
```

Share it with your service account's email (Editor access).

---

## Build phases

| Phase | Description |
|---|---|
| 1 | Project scaffolding (this phase) |
| 2 | Downloader + queue client |
| 3 | Catbox uploader |
| 4 | Instagram Graph API publisher |
| 5 | Flask web UI + APScheduler |
| 6 | Dockerfile + Railway deploy |
