# Hugging Face Space: builds the public replay-only demo straight from the GitHub repository (no key in the image).
# To pick up new commits: Space settings -> "Factory rebuild".
FROM node:22-slim AS src
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
ARG REPO=https://github.com/aleksandrahodzzik/musecell.git
ARG REF=main
RUN git clone --depth 1 --branch "$REF" "$REPO" /src
WORKDIR /src/frontend
RUN npm ci --no-audit --no-fund && npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=7860 DATA_DIR=/app/.no-data
WORKDIR /app
COPY --from=src /src/backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY --from=src /src/backend/app backend/app
COPY --from=src /src/demo demo
COPY --from=src /src/eval eval
COPY --from=src /src/LICENSE /src/DATA_LICENSE.md ./
COPY --from=src /src/frontend/dist frontend/dist
RUN useradd -m -u 1000 app && mkdir -p /app/runs /app/.no-data && chown -R app /app
USER app
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT}"]
