# ---- Stage 1: builder ----
# Use uv's official image which bundles uv on top of Python 3.12 slim.
# "slim" = Debian-based but stripped of extras, so smaller than the full image.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# All following commands run inside /app in the container.
WORKDIR /app

# uv settings:
# - compile bytecode now so startup is faster at runtime
# - copy packages into the venv instead of symlinking to uv's cache (cache won't exist in final stage)
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Copy ONLY the dependency manifests first (not the app code yet).
# This is the caching trick: as long as these two files don't change,
# Docker reuses the cached install layer even when your app code changes.
COPY pyproject.toml uv.lock ./

# Install dependencies into a local .venv, using the lockfile exactly.
# --frozen = fail if the lock is out of date (reproducible builds).
# --no-install-project = install deps only, not your own app yet (it's not copied in).
# --no-dev = skip dev/test dependencies; production image stays lean.
RUN uv sync --frozen --no-install-project --no-dev

# Now copy the actual application code.
COPY . .

# Install the project itself into the venv (links your app code in).
RUN uv sync --frozen --no-dev

# ---- Stage 2: final runtime ----
# Start fresh from a clean Python 3.12 image — none of uv's build tooling carries over.
FROM python:3.12-slim-bookworm

WORKDIR /app

# Copy the finished virtualenv and your code from the builder stage.
# This is the only thing we carry forward — the build tools are left behind.
COPY --from=builder /app /app

# Put the venv's binaries on PATH so "uvicorn" and "python" resolve to the venv versions.
ENV PATH="/app/.venv/bin:$PATH"

# Document that the app listens on 8080. App Runner expects 8080 by default.
EXPOSE 8080

# Default command: run the API. The worker service will OVERRIDE this later.
# --host 0.0.0.0 so it's reachable from outside the container (not just localhost).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]