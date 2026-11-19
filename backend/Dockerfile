# syntax=docker/dockerfile:1
FROM python:3.12-slim

# System deps for Pillow + build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock

# Copy source
COPY . .

# Create data directory (persisted via Docker volume)
RUN mkdir -p /app/data/demo

EXPOSE 8000

# Shell form so Railway's $PORT env var is picked up at runtime
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
