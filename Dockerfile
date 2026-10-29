FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LLM_BACKEND=extractive REVIEW_DB=/app/data/review.db \
    LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false
WORKDIR /app
COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir /app/data && chown app:app /app/data
COPY server.py .
COPY review/ review/
COPY web/ web/
COPY scripts/db_snapshot.py scripts/db_snapshot.py
USER app
EXPOSE 8600
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8600"]
