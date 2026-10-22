# yarbie: map editor backend (and the built UI, served at /).

FROM node:22-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
# git: yarbo-local is installed from its repository at a pinned commit.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 yarbie
WORKDIR /app

COPY pyproject.toml ./
COPY backend/ backend/
RUN pip3 install --no-cache-dir .

COPY --from=frontend /src/frontend/dist /app/frontend

ENV YARBIE_BIND=0.0.0.0 \
    YARBIE_PORT=8765 \
    YARBIE_DATA_DIR=/data \
    YARBIE_FRONTEND_DIR=/app/frontend \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data && chown yarbie:yarbie /data
VOLUME /data
USER yarbie
EXPOSE 8765
CMD ["yarbie"]
