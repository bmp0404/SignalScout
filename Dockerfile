# Stage 1: build the React frontend into frontend/dist
FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime serving the API + built frontend
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY scripts/ scripts/
COPY data/ data/
COPY --from=frontend /build/dist frontend/dist/

ENV PORT=8000
EXPOSE 8000
# Expand Railway's injected port while preserving uvicorn as PID 1 for clean shutdown.
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
