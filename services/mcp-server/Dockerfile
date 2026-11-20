FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

WORKDIR /srv

RUN groupadd --system app && useradd --system --gid app --home-dir /srv --no-create-home app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY data/Mobile_Provider_Billing_Guide.md ./data/

# Index the guide at build time; the server only loads data/index.json.
RUN python -m app.indexer && chown -R app:app /srv

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["python", "-m", "app.server"]
