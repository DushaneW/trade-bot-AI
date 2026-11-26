# Quorum in a container, for a home server, a Raspberry Pi or a small VPS.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QUORUM_DATA=/data \
    QUORUM_STRATEGIES=/strategies

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY quorum ./quorum
RUN pip install --no-cache-dir --no-deps . && useradd --create-home --uid 1000 quorum \
    && mkdir -p /data /strategies && chown quorum /data /strategies
USER quorum

VOLUME ["/data"]
EXPOSE 8420
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8420/api/health', timeout=4)"
CMD ["quorum", "--host", "0.0.0.0", "--port", "8420", "--no-browser"]
