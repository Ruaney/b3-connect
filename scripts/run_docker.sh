#!/usr/bin/env bash
set -e

echo "================================================================================"
echo " B3 Integration Atlas — Inicialização do Dashboard em Docker Container"
echo "================================================================================"

# Verifica se o Docker daemon está ativo
if ! docker info >/dev/null 2>&1; then
    echo "Aviso: O Docker daemon não está rodando nesta sessão ou requer privilégios de root."
    echo "Executando o dashboard diretamente no Python nativo local em porta 8080..."
    exec python3 -m src.cli ui --host 0.0.0.0 --port 8080 --no-browser
fi

echo "Construindo imagem Docker b3-integration-atlas..."
docker build -t b3-integration-atlas:latest .

echo "Iniciando container b3-atlas-dashboard na porta 8080..."
docker run --rm -d \
    --name b3-atlas-dashboard \
    -p 8080:8080 \
    b3-integration-atlas:latest

echo ""
echo "Dashboard B3 rodando com sucesso no Docker!"
echo "Acesse no navegador: http://127.0.0.1:8080"
echo "Para verificar os logs: docker logs -f b3-atlas-dashboard"
echo "Para parar o container: docker stop b3-atlas-dashboard"
echo "================================================================================"
