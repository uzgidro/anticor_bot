FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY . .

# Run as a non-root user.
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

CMD ["python", "-m", "bot"]
