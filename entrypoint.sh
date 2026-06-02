#!/bin/sh
set -e

cd /app/src

echo "============================================"
echo " Indexando documentación en ChromaDB..."
echo "============================================"
python ingest.py

echo "============================================"
echo " Iniciando servidor FastAPI en :8000..."
echo "============================================"
exec uvicorn api:app --host 0.0.0.0 --port 8000
