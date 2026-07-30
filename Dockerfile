# syntax=docker/dockerfile:1
# Hardened single-unit image for the personal assistant runtime
# (ADR-004, layer B). One image packages the modular monolith: the FastAPI
# app and, when REMINDER_WORKER_ENABLED=true with PostgreSQL, the embedded
# reminder worker. Secrets arrive only through environment variables at
# start time; the image and its build context contain no credentials.

FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir --prefix=/install '.[api,postgres]'

FROM python:3.12-slim
LABEL org.opencontainers.image.title="personal-assistant" \
      org.opencontainers.image.description="Local-first personal assistant runtime (hardened alpha)" \
      org.opencontainers.image.licenses="Proprietary"

# Never read a .env file inside the container: configuration and secrets
# come from the process environment only.
ENV APP_ENV_FILE=disabled \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 assistant \
    && useradd --system --uid 10001 --gid assistant \
       --no-create-home --shell /usr/sbin/nologin assistant

COPY --from=builder /install /usr/local

# Runtime file catalogs resolved relative to the Python installation:
# prompts at parents[3]/prompts and locales found by walking parents upward.
COPY prompts /usr/local/lib/python3.12/prompts
COPY locales /usr/local/lib/python3.12/locales

USER assistant
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/livez', timeout=4)" || exit 1

# The container binds 0.0.0.0 so the compose proxy network can reach it; the
# host publishes the port on loopback only, and the public HTTPS edge still
# forwards exactly POST /webhooks/telegram (see hardened-local-deployment.md).
CMD ["python", "-m", "uvicorn", "personal_assistant.infrastructure.http:app", \
     "--host", "0.0.0.0", "--port", "8000"]
