# ==============================================================================
# B3 Integration Atlas — Local Interactive Dashboard & Verification Dockerfile
# 100% Python Standard Library (Zero External Dependencies)
# ==============================================================================

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

COPY src /build/src
COPY catalog /build/catalog
COPY fixtures /build/fixtures
COPY raw_docs /build/raw_docs
COPY examples /build/examples
COPY docs /build/docs
COPY scripts /build/scripts
COPY pyproject.toml setup.py README.md /build/

# Pré-compilação de bytecode para arranque instantâneo
RUN python3 -m compileall -q /build/src /build/examples

# ==============================================================================
# Runtime Stage (Non-Root Security Hardened)
# ==============================================================================
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="B3 Integration Atlas Dashboard" \
      org.opencontainers.image.description="Dashboard visual interativo off-line, simulador de mercado e acervo documental B3" \
      org.opencontainers.image.version="2.2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8080 \
    HOST=0.0.0.0

RUN groupadd -g 10001 b3atlas && \
    useradd -u 10001 -g b3atlas -m -s /sbin/nologin b3atlas && \
    mkdir -p /app && \
    chown -R b3atlas:b3atlas /app

WORKDIR /app
COPY --from=builder --chown=b3atlas:b3atlas /build /app

USER b3atlas
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)" || exit 1

ENTRYPOINT ["python3", "-m", "src.cli", "ui", "--host", "0.0.0.0", "--port", "8080", "--no-browser"]
