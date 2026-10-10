# Product Manufacturing Planner — production image (M12).
# Configuration comes only from environment variables / the host's secret store (never baked into the image):
#   APP_ENV=production DATA_BACKEND=supabase AUTH_MODE=supabase SUPABASE_URL SUPABASE_PUBLISHABLE_KEY
#   SCHEDULE_SIGNING_KEY [ANTHROPIC_API_KEY ASSISTANT_MODEL LOG_LEVEL PORT]
# Run ONE worker per container: solver jobs, assistant conversations, pending confirmations and rate limits are
# kept in process memory (a shared queue is needed before scaling out — see development-khaled/operations.md).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production \
    PORT=8000

WORKDIR /app
RUN useradd --create-home --uid 10001 app

COPY requirements.txt .
RUN pip install -r requirements.txt

# app/static/css/app.css is built and committed (pytailwindcss), so no Node or Tailwind step is needed here.
COPY app ./app
COPY planning ./planning
COPY scripts ./scripts

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/livez' % os.environ.get('PORT', '8000'), timeout=4)"

# --proxy-headers: the platform's TLS proxy sets X-Forwarded-For/-Proto (client IPs for rate limits, https URLs).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
