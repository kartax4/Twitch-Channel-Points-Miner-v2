# syntax=docker/dockerfile:1

# ---- Builder stage -------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

COPY pyproject.toml README.md ./
COPY twitch_miner ./twitch_miner

# Build a self-contained wheel and install it into an isolated prefix so the
# runtime image stays minimal (no build tooling, no caches).
RUN python -m pip install --upgrade pip build \
    && python -m build --wheel --outdir /dist \
    && python -m pip install --prefix=/install /dist/*.whl

# ---- Runtime stage -------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

# Run as an unprivileged user.
RUN groupadd --system miner \
    && useradd --system --gid miner --create-home --home-dir /app miner

WORKDIR /app

COPY --from=builder /install /usr/local

# Volume mount points (also declared in docker-compose.yml).
RUN mkdir -p /app/config /app/cookies /app/analytics /app/logs \
    && chown -R miner:miner /app

USER miner

VOLUME ["/app/cookies", "/app/analytics", "/app/logs"]

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import twitch_miner" || exit 1

ENTRYPOINT ["python", "-m", "twitch_miner"]
CMD ["--config", "/app/config/config.yaml"]
