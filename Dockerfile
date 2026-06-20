FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system aria \
    && useradd --system --gid aria --home-dir /app --shell /usr/sbin/nologin aria

COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip install .

COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts/docker-entrypoint /usr/local/bin/aria-entrypoint
COPY scripts/backup /usr/local/bin/aria-backup

RUN chmod 0755 /usr/local/bin/aria-entrypoint /usr/local/bin/aria-backup \
    && mkdir -p /app/data \
    && chown -R aria:aria /app

USER aria

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

ENTRYPOINT ["aria-entrypoint"]
CMD ["python", "-m", "app"]
