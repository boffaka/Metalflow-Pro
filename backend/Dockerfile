# =============================================================================
# MetalFlow Pro v5 — Backend Production Dockerfile
# Multi-stage: builder → runtime
# =============================================================================
# Build args (injected by CI):
#   BUILD_DATE  — ISO timestamp
#   GIT_SHA     — commit hash
# =============================================================================

ARG PYTHON_VERSION=3.11

# ── Stage 1: Builder (compilers + pip install) ────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libcairo2-dev pkg-config python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install Python deps into a prefix (discarded from runtime layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime (slim, non-root) ────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG BUILD_DATE="unknown"
ARG GIT_SHA="unknown"
ARG METALFLOW_BUILD_ID=""

ENV METALFLOW_BUILD_ID=${METALFLOW_BUILD_ID}

LABEL org.opencontainers.image.title="MetalFlow Pro API" \
      org.opencontainers.image.description="Industrial gold mining project management platform" \
      org.opencontainers.image.version="5.0.0" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.licenses="Proprietary"

# Runtime-only system libs
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Non-root user for security
RUN groupadd -r mpdpms && useradd -r -g mpdpms -d /app -s /sbin/nologin mpdpms

# Copy pre-built Python packages from builder stage
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application code (owned by non-root user)
COPY --chown=mpdpms:mpdpms . .

# Create writable runtime directories
RUN mkdir -p /app/uploads /app/storage/reports /app/ml /app/logs \
    && chown -R mpdpms:mpdpms /app/uploads /app/storage /app/ml /app/logs

# Switch to non-root
USER mpdpms

EXPOSE 8000

# Health check — respects $PORT for Railway compatibility
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f "http://localhost:${PORT:-8000}/api/v1/health" || exit 1

# Production start command
# - $PORT injected by Railway (defaults to 8000)
# - $UVICORN_WORKERS defaults to 4 (tune per Railway plan)
# - proxy-headers required behind Railway/nginx reverse proxy
CMD ["sh", "-c", \
     "python -m uvicorn main:app \
      --host 0.0.0.0 \
      --port ${PORT:-8000} \
      --workers ${UVICORN_WORKERS:-4} \
      --access-log \
      --proxy-headers \
      --forwarded-allow-ips '*' \
      --log-level ${LOG_LEVEL:-info}"]
