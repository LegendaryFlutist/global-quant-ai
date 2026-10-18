FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.lock /app/
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 30 --retries 2 -r requirements.txt -c requirements.lock
RUN useradd --uid 1000 --create-home quant
COPY --chown=quant:quant backend /app
COPY --chown=quant:quant config/platform /app/config
USER quant
CMD ["sh", "-c", "alembic upgrade head && python -m app.bootstrap && uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
