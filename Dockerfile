# Public API (ADR-025). Build context is the repo root.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements-serve.txt .
RUN pip install --no-cache-dir -r requirements-serve.txt

COPY src/ src/
COPY scripts/setup_data.py scripts/

# Chinook is built into the image, and setup_data verifies every row count
# against upstream, so a drifted dump fails the build instead of the demo.
# support.db starts empty; it lives and dies with the instance.
RUN mkdir -p data && python scripts/setup_data.py

# COPY keeps local file modes, and some are 0600. The code stays root-owned
# and read-only to the runtime user; only data/ is writable.
RUN chmod -R a+rX /app/src /app/scripts \
    && useradd --create-home app && chown -R app /app/data
USER app

ENV PORT=8080
CMD exec uvicorn src.api:create_app --factory --host 0.0.0.0 --port ${PORT}
