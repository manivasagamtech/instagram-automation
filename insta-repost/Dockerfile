# ── Instagram Meme Repost Bot — production image ───────────────────────────────
#
# Build:  docker build -t memebot .
# Run:    docker run --env-file .env -p 8080:8080 memebot
#
# Must be built from the insta-repost/ directory (where this file lives).
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

LABEL org.opencontainers.image.title="instagram-meme-repost-bot"
LABEL org.opencontainers.image.description="Automated Instagram meme repost bot"

# ── System deps ────────────────────────────────────────────────────────────────
# ffmpeg — required by yt-dlp for video remuxing / format conversion
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps (cached layer — rebuilds only when requirements.txt changes) ───
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Application source ─────────────────────────────────────────────────────────
# Copy only what the app needs — .dockerignore excludes tests/, .env, etc.
COPY app/ ./app/
COPY main.py .

# ── Non-root user (security best-practice) ────────────────────────────────────
RUN addgroup --system botgroup \
    && adduser  --system --ingroup botgroup --no-create-home botuser \
    && chown -R botuser:botgroup /app

USER botuser

# ── Runtime config ─────────────────────────────────────────────────────────────
# Railway injects PORT automatically; default is 8080.
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

# ── Entrypoint ─────────────────────────────────────────────────────────────────
# Single worker — BackgroundScheduler runs inside the worker process.
# Increasing --workers causes duplicate scheduler instances → double-posting.
CMD ["gunicorn", \
     "--bind",         "0.0.0.0:8080", \
     "--workers",      "1", \
     "--threads",      "4", \
     "--timeout",      "120", \
     "--access-logfile", "-", \
     "--error-logfile",  "-", \
     "main:app"]
