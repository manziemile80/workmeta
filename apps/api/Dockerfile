# WorkMeta AI API — production image.
# Build from the repository root:
#   docker build -f apps/api/Dockerfile -t workmeta-api:VERSION .
# Build from apps/api (needs repo root context for COPY paths below, so the
# former is the supported invocation).
FROM python:3.12-slim-bookworm AS build

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /src
COPY apps/api/pyproject.toml apps/api/README.md ./
COPY apps/api/app ./app
# The "media" extra brings the video decoder (PyAV) and the OCR engine, so a
# deployment can read a training demo without anything installed on the
# machine. Drop it if the deployment never reads a recording.
RUN pip install ".[media]"

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    APP_ENV=production \
    # onnxruntime sizes its thread pools from the machine, which on a small
    # container means a pool far larger than the CPU it may use - and memory
    # to match. Two threads is plenty for one frame at a time.
    OMP_NUM_THREADS=2

# The OCR engine the "media" extra installs runs through OpenCV, which links
# against the X11/GL shared libraries even when it never opens a window. The
# slim base image ships none of them, so reading a frame would fail with
# "libxcb.so.1: cannot open shared object file".
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --no-create-home app

COPY --from=build /opt/venv /opt/venv
COPY apps/api/alembic /opt/venv/platform/alembic
COPY apps/api/alembic.ini /opt/venv/platform/alembic.ini

WORKDIR /app
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./
COPY apps/api/app ./app

# Ephemeral local storage (STORAGE_DIR=/app/var/storage) must exist and be
# writable by the non-root runtime user, otherwise uploads fail with EACCES.
RUN mkdir -p /app/var/storage && chown -R app:app /app/var

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; port=os.environ.get('PORT','8000'); urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/health', timeout=4)"

# Render injects $PORT (default 10000). Run via bash so it is expanded.
CMD ["bash", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips=*"]
