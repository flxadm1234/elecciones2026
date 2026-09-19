#!/bin/bash
set -e
cd "$(dirname "$0")/.."
echo "=== bootstrap.sh - aprovisionamiento en uno ==="
bash scripts/install_ubuntu.sh
bash scripts/setup_env.sh
bash scripts/migrate.sh
source .venv/bin/activate
python manage.py seed_catalogs --reset
echo "=== bootstrap.sh finalizado ==="
echo "Ahora puedes iniciar manualmente:"
echo "  systemctl start actas-web"
echo "  systemctl start actas-celery-worker"
echo "  systemctl start actas-celery-beat"
echo "Nginx debería servir el proxy ya configurado."
