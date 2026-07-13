FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src:/app
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 gcc g++ && rm -rf /var/lib/apt/lists/*
COPY ops/requirements-platform.txt ops/requirements-platform.txt
COPY ops/requirements-api.txt ops/requirements-api.txt
RUN pip install --no-cache-dir -r ops/requirements-api.txt
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn web.api.main:app --host 0.0.0.0 --port 8000"]
