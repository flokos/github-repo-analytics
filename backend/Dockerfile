# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.13

# ---- base: runtime dependencies, installed into the image's own interpreter (no virtualenv) ------
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app

COPY requirements.txt ./
RUN pip install --requirement requirements.txt

# ---- test: adds pytest and ruff; used only by the `backend-tests` Compose service ----------------
FROM base AS test

COPY requirements-dev.txt ./
RUN pip install --requirement requirements-dev.txt

# /app is read-only for the unprivileged user, so tool caches go elsewhere.
ENV RUFF_CACHE_DIR=/tmp/ruff-cache \
    PYTEST_ADDOPTS="-p no:cacheprovider"

COPY --chown=app:app . .
USER app
CMD ["pytest"]

# ---- runtime: what `api`, `django-worker` and `backend-migrate` run -------------------------------
FROM base AS runtime

COPY --chown=app:app . .
USER app
EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "4", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
