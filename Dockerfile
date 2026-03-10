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

RUN pip install --no-cache-dir .

ENTRYPOINT ["cerebro-mcp"]
