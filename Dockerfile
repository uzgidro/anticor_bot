FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Application sources. .dockerignore keeps .env and the local .venv out of the
# build context — without it `COPY . .` bakes real secrets into the image.
COPY pyproject.toml alembic.ini ./
COPY bot ./bot
COPY alembic ./alembic

RUN pip install --upgrade pip && pip install . \
    # pip is a build-time tool only; the container just runs `python -m bot`.
    # Removing it also removes its vendored packages (msgpack, setuptools),
    # which the image scanner flags and we cannot patch independently.
    && pip uninstall -y pip setuptools wheel

# Run as a non-root user.
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

CMD ["python", "-m", "bot"]
