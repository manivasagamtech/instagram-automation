# ── Stage: production image ────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL org.opencontainers.image.description="Instagram Meme Repost Bot"

# ffmpeg  — required by yt-dlp for video remuxing
# libmagic — optional but avoids yt-dlp warnings on some platforms
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer-cached until requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# ── Runtime config ─────────────────────────────────────────────────────────────
# Railway injects PORT automatically; 8080 is the fallback default.
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# Single worker: the BackgroundScheduler runs inside the worker process.
# Increasing --workers without a distributed job-store causes double-posting.
CMD ["gunicorn", "main:application", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
