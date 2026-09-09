# Single async worker per container; scale horizontally, not vertically.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so a code change does not invalidate the install layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY penny ./penny
COPY eval ./eval
COPY scripts ./scripts
COPY web ./web
COPY data/sample_transactions.csv ./data/sample_transactions.csv
# The enriched dataset is produced by scripts/enrich_transactions.py and is
# expected to be mounted or generated at deploy time.

RUN useradd --create-home --uid 10001 penny && chown -R penny:penny /app
USER penny

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status==200 else 1)"

# One worker: the app holds per-process session state, so concurrency comes
# from the event loop and scale comes from container count.
CMD ["uvicorn", "penny.presentation.http.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
