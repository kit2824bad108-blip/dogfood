FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
# Retries and a real timeout, because a clean `docker compose up --build` is a
# supported path and a flaky index should not be what breaks it: the first build
# on a slow connection failed on `greenlet` (a SQLAlchemy dependency) while the
# Next.js image was downloading at the same time.
RUN pip install --no-cache-dir --retries 6 --timeout 90 -r requirements.txt

COPY . .
RUN chmod +x scripts/entrypoint.sh

EXPOSE 8000

CMD ["./scripts/entrypoint.sh"]
