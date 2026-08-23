FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000

WORKDIR /app
COPY pyproject.toml ./
COPY parcelpilot ./parcelpilot
COPY seed ./seed

RUN pip install --no-cache-dir .

EXPOSE 10000
CMD ["sh", "-c", "uvicorn parcelpilot.main:app --host 0.0.0.0 --port ${PORT}"]
