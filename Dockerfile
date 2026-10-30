# MuseCell in replay-only mode: recorded live runs, dossiers and the benchmark, no API key and no BBBC021 download.
# Nothing here can spend credits: no key is in the image, so live runs are off and the start screen says so.
#
#   docker build -t musecell .
#   docker run --rm -p 7860:7860 musecell        # then open http://localhost:7860
#
# The public demo is the static build on GitHub Pages (.github/workflows/pages.yml); this image is for running it yourself.

FROM node:22-slim AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PORT=7860 DATA_DIR=/app/.no-data
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend/app backend/app
COPY demo demo
COPY eval eval
COPY LICENSE DATA_LICENSE.md ./
COPY --from=web /src/frontend/dist frontend/dist
# Run as an unprivileged user that owns the app folder (runs/ and caches are created here).
RUN useradd -m -u 1000 app && mkdir -p /app/runs /app/.no-data && chown -R app /app
USER app
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT}"]
