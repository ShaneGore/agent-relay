FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install runtime deps first for better layer caching.
# Matches pyproject.toml dependencies.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
      "fastapi>=0.141.1" \
      "psycopg[binary]>=3.3.5" \
      "pydantic-settings>=2.15.0" \
      "sqlalchemy>=2.0.52" \
      "uvicorn[standard]>=0.52.4"

# Copy app source (DB, venv, caches excluded via .dockerignore).
COPY main.py database.py storage.py schemas.py errors.py worker.py dashboard.py dashboard.html ./

EXPOSE 8000

# 0.0.0.0 is required so docker -p publishing works from outside the container.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
