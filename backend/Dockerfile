FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# EXTRAS=dev for local development and CI; the production image is built with EXTRAS="".
ARG EXTRAS=dev
COPY pyproject.toml ./
COPY app/__init__.py app/__init__.py
RUN if [ -n "$EXTRAS" ]; then pip install -e ".[$EXTRAS]"; else pip install -e .; fi

COPY . .

RUN useradd --create-home kernel && chown -R kernel /app
USER kernel

# Cloud Run sets PORT (8080). Migrations are not run here: locally docker-compose runs them
# before starting the API, and in production the deploy workflow runs them as a Cloud Run job.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
