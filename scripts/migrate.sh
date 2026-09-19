#!/bin/bash
set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate || true
echo "=== migrate.sh ==="
python manage.py makemigrations core || true
python manage.py migrate
python manage.py ensure_admin
python manage.py collectstatic --noinput -v 0
echo "=== migrate.sh terminado ==="
