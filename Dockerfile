# The billing agent as a container (docs/running-it.md stage 2, decision 22).
# Built by CI and published to GitHub Container Registry on a version tag.
# Holds no settings and no secrets: .env is supplied at run time (env_file in
# docker-compose.yml), and data/ is a mounted volume.

FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/ARathie/finance-ops-agent" \
      org.opencontainers.image.description="Icon Technologies billing agent (fops serve)"

# rclone copies the nightly backup to the bucket.
RUN apt-get update \
    && apt-get install -y --no-install-recommends rclone ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.11.32

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    FOPS_DATA_DIR=/data

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev

# A user of its own. /data is created here and owned by it, so a fresh named
# volume mounted there starts out writable by the agent.
RUN useradd --create-home --uid 10001 fops \
    && mkdir -p /data /home/fops/.config/rclone \
    && chown -R fops:fops /data /home/fops
USER fops
WORKDIR /data
VOLUME ["/data"]

# fops is process 1 and handles SIGTERM itself, so `docker compose stop` asks
# it to finish at its next safe point rather than killing it.
CMD ["fops", "serve"]
