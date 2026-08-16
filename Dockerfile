# ---- build the web frontend -------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- install the python package ---------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS deps
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN uv sync --no-dev --no-editable --compile-bytecode

# ---- runtime -----------------------------------------------------------------
FROM python:3.12-slim-bookworm
WORKDIR /app
RUN useradd --create-home --uid 1000 immich-dedup
COPY --from=deps /app/.venv /app/.venv
COPY --from=web /build/dist /app/web/dist
ENV PATH="/app/.venv/bin:$PATH" \
    IMMICH_DEDUP_WEB_DIST=/app/web/dist
RUN mkdir -p /app/reports && chown -R immich-dedup /app
USER immich-dedup
VOLUME /app/reports
EXPOSE 8642
# web UI by default; override args for flags, e.g.
#   docker run ... image --token SECRET
# or run the CLI:
#   docker run ... --entrypoint cross-user-dedup image --apply
ENTRYPOINT ["cross-user-dedup-ui"]
CMD ["--host", "0.0.0.0", "--port", "8642"]
