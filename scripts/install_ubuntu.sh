#!/bin/bash
set -e
echo "=== Instalando dependencias del sistema (Ubuntu 22.04 LTS) ==="
sudo apt-get update -y
sudo apt-get upgrade -y

sudo apt-get install -y \
    build-essential \
    python3 python3-dev python3-venv python3-pip \
    postgresql postgresql-contrib libpq-dev \
    redis-server \
    nginx \
    tesseract-ocr tesseract-ocr-spa \
    libjpeg-dev zlib1g-dev libpng-dev \
    libopencv-dev \
    curl git htop vim \
    pkg-config libfreetype6-dev libtiff-dev libwebp-dev

sudo systemctl enable postgresql
sudo systemctl enable redis-server
sudo systemctl enable nginx

sudo systemctl start postgresql
sudo systemctl start redis-server

echo "=== Creando usuario/BD PostgreSQL (descomentar y editar) ==="
# sudo -u postgres createuser -s actas_user || true
# sudo -u postgres psql -c "ALTER USER actas_user WITH PASSWORD 'DBPASSWD';" || true
# sudo -u postgres createdb -O actas_user actas_elecciones || true

echo "=== install_ubuntu.sh finalizado ==="
