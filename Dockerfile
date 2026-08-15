# Shareable image for the Personal Curation API. Small (no torch — uses
# hash_fallback embeddings). Runs in-memory by default, or against your own
# Supabase when configured. Artifacts are built at startup (see entrypoint).
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PC_EMBEDDING_BACKEND=hash_fallback

# Deps first (better layer caching)
COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

# App code (see .dockerignore for exclusions: .env, pcenv, artifacts, etc.)
COPY . .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
