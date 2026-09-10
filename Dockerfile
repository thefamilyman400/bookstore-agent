FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0

WORKDIR /app

# Install Python dependencies into the same Python environment
# that will run the application.
COPY requirements.txt .

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY agents/        ./agents/
COPY static/        ./static/
COPY data/          ./data/
COPY app.py         .
COPY auth_service.py .
COPY db.py          .
COPY mcp_server.py  .
COPY rag_store.py   .

# Persistent SQLite database location
ENV DB_PATH=/data/db/bookstore.db

# Prepare runtime directories and permissions
RUN mkdir -p /data/db \
    && chown -R 1001:1001 /app /data

# Run the application as the existing non-root UBI user
USER 1001

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]