# ── Build stage ────────────────────────────────────────────────────────────────
FROM registry.access.redhat.com/ubi9/python-311:latest AS builder

USER 0

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0

WORKDIR /app

# Copy Python packages from the builder's actual site-packages location
COPY --from=builder /opt/app-root/lib64/python3.11/site-packages \
    /opt/app-root/lib64/python3.11/site-packages

# Copy application source
COPY agents/        ./agents/
COPY static/        ./static/
COPY data/          ./data/
COPY app.py         .
COPY auth_service.py .
COPY db.py          .
COPY mcp_server.py  .
COPY rag_store.py   .

ENV DB_PATH=/data/db/bookstore.db

RUN mkdir -p /data/db && \
    chown -R 1001:1001 /app /data

USER 1001

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]