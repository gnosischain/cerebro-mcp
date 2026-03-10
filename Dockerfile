# Stage 1: Build the React UI
FROM node:20-slim AS ui-builder

WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ .
RUN npm run build

# Stage 2: Build the Python package
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

# Copy built UI artifact into the Python package
COPY --from=ui-builder /ui/dist/index.html src/cerebro_mcp/static/report.html

RUN pip install --no-cache-dir . && \
    useradd -r -u 1000 cerebro && \
    mkdir -p /data/reports /data/logs /data/saved-queries && \
    chown -R cerebro:cerebro /data

ENV FASTMCP_HOST=0.0.0.0
ENV FASTMCP_PORT=8000
ENV CEREBRO_REPORT_DIR=/data/reports
ENV THINKING_LOG_DIR=/data/logs
ENV CEREBRO_SAVED_QUERIES_DIR=/data/saved-queries

EXPOSE 8000
USER cerebro

ENTRYPOINT ["cerebro-mcp", "--sse"]
