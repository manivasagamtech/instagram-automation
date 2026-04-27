# Deployment Record

## Railway — Production

| Field | Value |
|---|---|
| **Live URL** | https://instagram-automation-production-ca37.up.railway.app |
| **Railway project** | https://railway.com/project/bac13f6c-a23f-423b-a988-ef939f0e6e85 |
| **Project name** | Instagram-automation |
| **Service ID** | 8d675a88-80c2-4893-999c-1905eb1c52f9 |
| **Environment** | production |
| **Build method** | Dockerfile |
| **First deploy** | 2026-04-27 — SUCCESS |

## GitHub

| Field | Value |
|---|---|
| **Repository** | https://github.com/manivasagamtech/instagram-automation |
| **Branch** | main |
| **Source path** | `insta-repost/` |

---

## Connecting GitHub for auto-deploy (one-time manual step)

Because Railway's GitHub integration requires OAuth in the browser, this
must be done once via the Railway dashboard:

1. Open https://railway.com/project/bac13f6c-a23f-423b-a988-ef939f0e6e85
2. Click the **Instagram-automation** service tile
3. Go to **Settings → Source**
4. Click **Connect GitHub Repo**
5. Authorize Railway to access `manivasagamtech/instagram-automation`
6. Select branch: **main**
7. Set **Root Directory** to `insta-repost`
8. Enable **Auto Deploy on Push** ✅

After this, every `git push origin main` will trigger a Railway build automatically.

---

## Environment variables — credential checklist

All variables are pre-populated in Railway. The following **must be replaced**
with real values before the bot can post to Instagram:

| Variable | Dashboard path | Notes |
|---|---|---|
| `APP_PASSWORD` | Variables → APP_PASSWORD | Choose a strong password for the dashboard |
| `IG_USER_ID` | Variables → IG_USER_ID | Numeric ID of your IG Business account |
| `IG_ACCESS_TOKEN` | Variables → IG_ACCESS_TOKEN | 60-day long-lived token from Graph API Explorer |
| `FB_APP_ID` | Variables → FB_APP_ID | From https://developers.facebook.com |
| `FB_APP_SECRET` | Variables → FB_APP_SECRET | From https://developers.facebook.com |
| `GOOGLE_CREDENTIALS_JSON` | Variables → GOOGLE_CREDENTIALS_JSON | Paste full service account JSON as **one line** |
| `IG_LOGIN_USER` | Variables → IG_LOGIN_USER | Burner Instagram username for instaloader |
| `IG_LOGIN_PASS` | Variables → IG_LOGIN_PASS | Burner Instagram password |

### How to convert service account JSON to one line

```bash
# macOS / Linux
cat your-service-account.json | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)))"

# Windows PowerShell
Get-Content your-service-account.json | python -c "import sys,json; print(json.dumps(json.load(sys.stdin)))"
```

Paste the output (a single line starting with `{"type":"service_account",...}`)
into the Railway Variables panel.

---

## Smoke test

```bash
# Health check
curl https://instagram-automation-production-ca37.up.railway.app/healthz
# → ok

# Login page
curl -L https://instagram-automation-production-ca37.up.railway.app/
# → 200 with login form HTML
```

---

## ⚠️  Access token expiry reminder

The Instagram long-lived access token **expires every 60 days**.

The `token_refresh_job` (runs every Sunday 03:00 UTC) automatically extends it,
but the new token is only **logged** — it is NOT persisted automatically.

After each Sunday refresh:
1. Open Railway Logs, search for `TOKEN REFRESHED`
2. Copy the new token value
3. Update `IG_ACCESS_TOKEN` in Railway Variables
4. Railway will redeploy automatically

If the bot is offline for 60+ consecutive days, the token expires permanently
and you must generate a new one via the Graph API Explorer.
