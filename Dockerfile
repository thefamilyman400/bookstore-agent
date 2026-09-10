# ── Build stage ────────────────────────────────────────────────────────────────
FROM registry.access.redhat.com/ubi9/python-311:latest AS builder

WORKDIR /app

# Install dependencies into a local prefix so the runtime stage can copy them
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM registry.redhat.io/ubi9/python-311-minimal:latest

# Temporarily switch to root to create the application user
USER 0

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source (no .env, no .venv, no tests, no __pycache__)
COPY agents/        ./agents/
COPY static/        ./static/
COPY data/          ./data/
COPY app.py         .
COPY auth_service.py .
COPY db.py          .
COPY mcp_server.py  .
COPY rag_store.py   .

# SQLite DB lives in /data/db at runtime (mount an EFS volume here in production)
ENV DB_PATH=/data/db/bookstore.db
RUN mkdir -p /data/db && chown -R 1001:1001 /app /data

# Switch to non-root user
USER 1001

# Bind to localhost is not appropriate in a container — bind to all interfaces
# but rely on the VPC security group / ALB to restrict external access
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
