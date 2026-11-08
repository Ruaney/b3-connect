# ==============================================================================
# B3 Integration Atlas — Turnkey Container Production Stack
# Multi-Stage Build Ultra-Leve (Python 3.11-slim, Zero Pip, Non-Root b3user)
# 100% Python Standard Library (Zero External Dependencies)
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Builder (Bytecode Pre-compilation, No external pip packages)
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS builder

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
COPY observability /build/observability
COPY pyproject.toml setup.py README.md /build/
COPY index.html* /build/

# Pré-compilação de bytecode Python para arranque instantâneo (sem pip)
RUN python3 -m compileall -q /build/src /build/examples /build/scripts

# ------------------------------------------------------------------------------
# Stage 2: Runtime (Hardened Non-Root Security, Native Healthcheck)
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="B3 Integration Atlas Dashboard & API" \
      org.opencontainers.image.description="B3 Integration Atlas Turnkey Production Container" \
      org.opencontainers.image.version="2.3.0" \
      org.opencontainers.image.vendor="B3 Integration Atlas"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8080 \
    HOST=0.0.0.0

# Criação de grupo e usuário não-root restrito b3user
RUN groupadd -g 10001 b3user && \
    useradd -u 10001 -g b3user -m -s /sbin/nologin b3user && \
    mkdir -p /app && \
    chown -R b3user:b3user /app

WORKDIR /app
COPY --from=builder --chown=b3user:b3user /build /app

# Execução estrita sob privilégios do usuário b3user
USER b3user
EXPOSE 8080

# Healthcheck nativo com biblioteca padrão (urllib) sem curl/wget
HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)" || exit 1

ENTRYPOINT ["python3", "-m", "src.cli", "ui", "--host", "0.0.0.0", "--port", "8080", "--no-browser"]
