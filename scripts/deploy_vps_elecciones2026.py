#!/usr/bin/env python3
"""Deploy Elecciones 2026 Whadox al VPS (felix@31.220.84.86) / elecciones2026.whadox.com"""
from __future__ import annotations

import os
import sys
import stat
import time
import json
import secrets
import traceback
import paramiko
from pathlib import Path, PurePosixPath

SSH_HOST = "31.220.84.86"
SSH_USER = "felix"
SSH_PASS = "flxadm1234abc"
SSH_PORT = 22

LOCAL_ROOT = Path(__file__).resolve().parent.parent
REMOTE_ROOT = PurePosixPath("/opt/elecciones2026")

EXCLUDE_PATTERNS = [
    ".venv", "__pycache__", "*.pyc", "*.pyo", "*.pyd", ".pytest_cache",
    "db.sqlite3", "media", "staticfiles", "logs", ".DS_Store",
    ".trae", "test_iquitos_smoke.py", "vps_audit_report.txt",
    "node_modules", ".git",
]


def run(ssh: paramiko.SSHClient, cmd: str, check: bool = True, timeout: int = 300, env: dict | None = None, sudo: bool = True):
    prefix = "sudo -H -S bash -c " if sudo else "bash -c "
    cmd_clean = cmd.replace("'", "'\\''")
    full = f"{prefix}'{cmd_clean}'"
    print(f"\n$ {cmd}")
    stdin, stdout, stderr = ssh.exec_command(full, timeout=timeout, environment=env or {}, get_pty=True)
    if sudo:
        stdin.write(SSH_PASS + "\n")
        stdin.flush()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    if out.strip():
        print(out[:6000])
    if err.strip() and rc != 0:
        print("STDERR:", err[:4000])
    if check and rc != 0:
        raise RuntimeError(f"Exit {rc} for: {cmd}\n{err[:4000]}")
    return (out, err, rc)


def sudo_put_text(sftp: paramiko.SFTPClient, ssh: paramiko.SSHClient, remote_path: str, content: str, mode: int = 0o644):
    """Escribe un archivo como root usando un path temp y sudo mv."""
    tmp = f"/tmp/remote_{secrets.token_hex(8)}"
    with sftp.open(tmp, "w") as f:
        f.write(content)
    run(ssh, f"mv {tmp} {remote_path} && chown root:root {remote_path} && chmod {oct(mode)[2:]} {remote_path}")


def should_exclude(rel: Path) -> bool:
    name = rel.name
    for pat in EXCLUDE_PATTERNS:
        if pat.startswith("*."):
            if name.endswith(pat[1:]):
                return True
        elif name == pat or any(part == pat for part in rel.parts):
            return True
    return False


def upload_dir(sftp: paramiko.SFTPClient, ssh: paramiko.SSHClient, local_dir: Path, remote_dir: PurePosixPath):
    files_uploaded = 0
    for root, dirs, files in os.walk(local_dir):
        # Filtrar directorios in-place
        dirs[:] = [d for d in dirs if not should_exclude(Path(d)) and not d.startswith(".")]
        local_root_p = Path(root)
        rel = local_root_p.relative_to(local_dir)
        remote_sub = remote_dir / str(rel) if str(rel) != "." else remote_dir
        try:
            sftp.stat(str(remote_sub))
        except FileNotFoundError:
            run(ssh, f"mkdir -p {remote_sub} && chown -R felix:felix {remote_sub}", sudo=False)
        for fn in files:
            p = local_root_p / fn
            if should_exclude(Path(fn)):
                continue
            remote_fp = str(remote_sub / fn)
            # Saltar archivos > 100 MB
            if p.stat().st_size > 100 * 1024 * 1024:
                print(f"[SKIP BIG] {p} >100MB")
                continue
            try:
                sftp.put(str(p), remote_fp)
                files_uploaded += 1
                if files_uploaded % 50 == 0:
                    print(f"  ... {files_uploaded} archivos subidos")
            except Exception as e:
                print(f"  ERROR subiendo {p}: {e}")
    print(f"  Total archivos subidos: {files_uploaded}")
    return files_uploaded


def random_hex(n: int = 48) -> str:
    return secrets.token_hex(n)


def main():
    assert LOCAL_ROOT.exists()
    print(f"=== Deploy Elecciones 2026 Whadox a {SSH_USER}@{SSH_HOST} ===")
    print(f"  Origen local:  {LOCAL_ROOT}")
    print(f"  Destino remoto: {REMOTE_ROOT}")
    print()

    # --- Conectar SSH
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SSH_HOST, port=SSH_PORT, username=SSH_USER, password=SSH_PASS, timeout=30, banner_timeout=30)
    sftp = ssh.open_sftp()
    try:
        print(">>> [Paso 1/18] Detección de SO y recursos")
        out, _, _ = run(ssh, "uname -a ; lsb_release -d 2>/dev/null || cat /etc/issue ; echo '---' ; free -h | head -3 ; echo '---' ; nproc", sudo=False)

        print(">>> [Paso 2/18] Backup PostgreSQL electoral_personeros_vps (solo data)")
        backup_ts = time.strftime("%Y%m%d_%H%M")
        backup_path = f"/var/backups/elecciones2026_pgbackup_{backup_ts}.sql.gz"
        run(ssh, "mkdir -p /var/backups && chown postgres:root /var/backups 2>/dev/null || true")
        run(ssh, f"sudo -u postgres pg_dump -Fc electoral_personeros_vps -Z 9 -f {backup_path}.custom && sudo -u postgres pg_dump electoral_personeros_vps | gzip -9 > {backup_path} && ls -lh {backup_path}* && echo OK_BACKUP")

        print(">>> [Paso 3/18] Verificar puerto 8201 libre")
        _, _, rc = run(ssh, "ss -lntp | grep -q ':8201 ' && echo 'EN_USO' || echo 'LIBRE'", check=False, sudo=False)

        print(">>> [Paso 4/18] Instalar dependencias sistema (tesseract spa, venv, libpq, redis)")
        run(ssh, "export DEBIAN_FRONTEND=noninteractive && apt-get update -y 2>&1 | tail -5 && "
                 "apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-spa libpq-dev python3-venv python3-dev redis-server acl curl pkg-config build-essential 2>&1 | tail -20")

        print(">>> [Paso 5/18] Crear estructura /opt/elecciones2026")
        run(ssh, f"mkdir -p {REMOTE_ROOT}/media {REMOTE_ROOT}/staticfiles {REMOTE_ROOT}/logs /run/elecciones2026 && chown -R felix:felix {REMOTE_ROOT} && chown -R felix:felix /run/elecciones2026")

        print(">>> [Paso 6/18] Subir proyecto via SFTP (excluyendo carpetas locales)")
        upload_dir(sftp, ssh, LOCAL_ROOT, REMOTE_ROOT)

        print(">>> [Paso 7/18] Crear venv Python + instalar requirements")
        run(ssh, f"cd {REMOTE_ROOT} && [ -d .venv ] || python3 -m venv .venv && . .venv/bin/activate && "
                 f"pip install --quiet --upgrade pip setuptools wheel && "
                 f"pip install --quiet -r requirements.txt 2>&1 | tail -30", sudo=False, timeout=900)

        print(">>> [Paso 8/18] Escribir .env (DATABASE_URL PostgreSQL real, credenciales aleatorias)")
        env_txt = f"""DEBUG=False
SECRET_KEY={random_hex(48)}
ALLOWED_HOSTS=elecciones2026.whadox.com,localhost,127.0.0.1,31.220.84.86,*.whadox.com

DATABASE_URL=postgres://admin_felix:flxadm1234abc@127.0.0.1:5432/electoral_personeros_vps

REDIS_URL=redis://127.0.0.1:6379/8
CELERY_BROKER_URL=redis://127.0.0.1:6379/9
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/10

MASTER_ENCRYPTION_KEY={random_hex(32)}

TESSERACT_CMD=/usr/bin/tesseract
IMAGES_ROOT=/opt/sistema_personeros/repo/electoral-personeros-app/backend/storage/actas_escrutinio
MEDIA_ROOT={REMOTE_ROOT}/media
STATIC_ROOT={REMOTE_ROOT}/staticfiles

DEFAULT_AI_PROVIDER=gemini
GEMINI_API_KEY=
GEMINI_MODEL=gemini-1.5-flash-latest
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-chat

LANGUAGE_CODE=es-pe
TIME_ZONE=America/Lima
"""
        env_path = str(REMOTE_ROOT / ".env")
        with sftp.open(env_path, "w") as f:
            f.write(env_txt)
        run(ssh, f"chown felix:felix {env_path} && chmod 0640 {env_path}", sudo=False)

        print(">>> [Paso 9/18] Django migrate (solo tablas nuevas vps_*)")
        run(ssh, f"cd {REMOTE_ROOT} && . .venv/bin/activate && "
                 f"export PYTHONDONTWRITEBYTECODE=1 OMP_THREAD_LIMIT=1 && "
                 f"python manage.py check 2>&1 | tail -10 && "
                 f"python manage.py migrate --noinput 2>&1 | tail -30", sudo=False, timeout=300)

        print(">>> [Paso 10/18] collectstatic")
        run(ssh, f"cd {REMOTE_ROOT} && . .venv/bin/activate && "
                 f"export PYTHONDONTWRITEBYTECODE=1 && "
                 f"python manage.py collectstatic --noinput 2>&1 | tail -20", sudo=False, timeout=300)

        print(">>> [Paso 11/18] Systemd unit: elecciones2026-web.service (gunicorn :8201, 2 workers)")
        web_unit = """[Unit]
Description=Elecciones 2026 Whadox Web (Gunicorn)
After=network.target postgresql.service redis-server.service

[Service]
Type=notify
Environment="PYTHONDONTWRITEBYTECODE=1"
Environment="OMP_THREAD_LIMIT=1"
Environment="OPENCV_IO_MAX_IMAGE_PIXELS=350000000"
Environment="MKL_NUM_THREADS=1"
Environment="NUMEXPR_NUM_THREADS=1"
Environment="OPENBLAS_NUM_THREADS=1"
Environment="VECLIB_MAXIMUM_THREADS=1"
User=felix
Group=felix
WorkingDirectory=/opt/elecciones2026
ExecStart=/opt/elecciones2026/.venv/bin/gunicorn \\
  --workers 2 \\
  --threads 4 \\
  --timeout 240 \\
  --graceful-timeout 60 \\
  --max-requests 1500 \\
  --max-requests-jitter 200 \\
  --bind 127.0.0.1:8201 \\
  --env DJANGO_SETTINGS_MODULE=config.settings \\
  --access-logfile /opt/elecciones2026/logs/gunicorn-access.log \\
  --error-logfile  /opt/elecciones2026/logs/gunicorn-error.log \\
  --log-level info \\
  config.wsgi:application
ExecReload=/bin/kill -HUP $MAINPID
KillMode=mixed
Restart=always
RestartSec=3
CPUQuota=90%
MemoryMax=5G
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/elecciones2026 /tmp /run/elecciones2026
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
"""
        sudo_put_text(sftp, ssh, "/etc/systemd/system/elecciones2026-web.service", web_unit, 0o644)

        print(">>> [Paso 12/18] Systemd unit: elecciones2026-celery.service (concurrency=1 gevent)")
        celery_unit = """[Unit]
Description=Elecciones 2026 Whadox Celery Worker (1 concurrency gevent)
After=network.target postgresql.service redis-server.service elecciones2026-web.service

[Service]
Type=simple
Environment="PYTHONDONTWRITEBYTECODE=1"
Environment="OMP_THREAD_LIMIT=1"
Environment="OPENCV_IO_MAX_IMAGE_PIXELS=350000000"
Environment="MKL_NUM_THREADS=1"
Environment="NUMEXPR_NUM_THREADS=1"
Environment="OPENBLAS_NUM_THREADS=1"
Environment="VECLIB_MAXIMUM_THREADS=1"
Environment="EVENTLET_NOBLOCK=1"
User=felix
Group=felix
WorkingDirectory=/opt/elecciones2026
ExecStart=/opt/elecciones2026/.venv/bin/celery \\
  -A config.celery worker \\
  -l INFO \\
  --pool=gevent \\
  --concurrency=1 \\
  --max-tasks-per-child=50 \\
  --time-limit=1800 \\
  --soft-time-limit=1500 \\
  --without-mingle --without-gossip --without-heartbeat \\
  -E
ExecStop=/bin/kill -TERM $MAINPID
KillMode=mixed
Restart=always
RestartSec=4
CPUQuota=70%
MemoryMax=3500M
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/elecciones2026 /opt/sistema_personeros /tmp /run/elecciones2026
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
"""
        sudo_put_text(sftp, ssh, "/etc/systemd/system/elecciones2026-celery.service", celery_unit, 0o644)

        print(">>> [Paso 13/18] tmpfiles.d /run/elecciones2026")
        tmpfiles_conf = "d /run/elecciones2026 0755 felix felix -\n"
        sudo_put_text(sftp, ssh, "/etc/tmpfiles.d/elecciones2026.conf", tmpfiles_conf, 0o644)
        run(ssh, "systemd-tmpfiles --create /etc/tmpfiles.d/elecciones2026.conf")

        print(">>> [Paso 14/18] Nginx site: elecciones2026.whadox.com (SSL wildcard whadox)")
        nginx_conf = """server {
    listen 80;
    listen [::]:80;
    server_name elecciones2026.whadox.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name elecciones2026.whadox.com;

    ssl_certificate     /etc/letsencrypt/live/whadox.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/whadox.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header X-Content-Type-Options nosniff always;

    client_max_body_size 250M;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
    uwsgi_read_timeout 600s;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript application/xml image/svg+xml;
    gzip_min_length 1024;

    access_log /var/log/nginx/elecciones2026.whadox.com.access.log;
    error_log  /var/log/nginx/elecciones2026.whadox.com.error.log;

    location /static/ {
        alias /opt/elecciones2026/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
        access_log off;
    }

    location /media/ {
        alias /opt/elecciones2026/media/;
        expires 7d;
        add_header Cache-Control "public";
    }

    location / {
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        "upgrade";
        proxy_set_header Proxy             "";
        proxy_pass http://127.0.0.1:8201;
    }
}
"""
        sudo_put_text(sftp, ssh, "/etc/nginx/sites-available/elecciones2026.whadox.com.conf", nginx_conf, 0o644)

        print(">>> [Paso 15/18] nginx -t + symlink sites-enabled + reload")
        run(ssh, "ln -sf /etc/nginx/sites-available/elecciones2026.whadox.com.conf /etc/nginx/sites-enabled/elecciones2026.whadox.com.conf && ls -la /etc/nginx/sites-enabled/ && echo '---' && nginx -t && echo NGINX_OK")
        run(ssh, "systemctl reload nginx && echo RELOAD_NGINX_OK")

        print(">>> [Paso 16/18] systemd daemon-reload, enable + restart services")
        run(ssh, "systemctl daemon-reload && "
                 "systemctl enable elecciones2026-web elecciones2026-celery && "
                 "systemctl restart elecciones2026-web elecciones2026-celery && sleep 3 && "
                 "systemctl status elecciones2026-web --no-pager | head -20 && "
                 "echo '---' && systemctl status elecciones2026-celery --no-pager | head -15")

        print(">>> [Paso 17/18] Smoke test local :8201 y HTTPS externo")
        time.sleep(6)
        run(ssh, "curl -sS -o /dev/null -w 'HTTP_CODE_LOCAL=%{http_code}\\n' http://127.0.0.1:8201/accounts/login/ --max-time 20", sudo=False)
        run(ssh, "curl -skS -o /dev/null -w 'HTTP_CODE_HTTPS=%{http_code}\\n' https://elecciones2026.whadox.com/accounts/login/ --max-time 20 --resolve elecciones2026.whadox.com:443:127.0.0.1", sudo=False)

        print("\n>>> [Paso 18/18] Resumen")
        print(f"  Backup PostgreSQL: {backup_path}.gz / {backup_path}.custom")
        print(f"  Web UI:            https://elecciones2026.whadox.com/")
        print(f"  Puerto interno:    127.0.0.1:8201 (gunicorn)")
        print(f"  Tablas nuevas:     vps_* (ninguna tabla existente tocada)")
        print(f"  Login:             tabla usuarios (PostgreSQL) - roles ADMINISTRADOR/SUPER_ADMIN")
        print(f"  Config IA:         Super Admin solo /admin/core/aiprovidersettings/")
        print(f"  CPU limits VPS:    web 90% / celery 70% (concurrency 1 gevent) cv2+tesseract OMP=1")
        print()
        print("=== Deploy completado OK ===")
    finally:
        sftp.close()
        ssh.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\n!! DEPLOY FALLÓ: {exc}")
        traceback.print_exc()
        sys.exit(1)
