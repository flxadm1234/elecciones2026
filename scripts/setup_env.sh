#!/bin/bash
set -e
cd "$(dirname "$0")/.."
echo "=== setup_env.sh ==="
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Copiado .env desde .env.example"
    echo "Edita .env antes de continuar (DB, AI keys, llave maestra)."
else
    echo ".env ya existe"
fi

mkdir -p media media/images staticfiles logs
echo "=== setup_env.sh terminado ==="
