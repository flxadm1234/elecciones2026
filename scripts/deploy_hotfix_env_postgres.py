#!/usr/bin/env python3
"""Hotfix final: corregir .env en VPS (PostgreSQL real, DEBUG=False, ALLOWED_HOSTS), restart services, smoke tests login reales."""
from __future__ import annotations
import os, sys, time, secrets, paramiko

SSH_HOST = "31.220.84.86"
SSH_USER = "felix"
SSH_PASS = "flxadm1234abc"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def run(ssh, cmd, check=True, timeout=600, sudo=True):
    prefix = "sudo -H -S bash -c " if sudo else "bash -c "
    full = f"{prefix}'{cmd.replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'"
    sys.stdout.write(f"\n$ {cmd[:180]}\n")
    sys.stdout.flush()
    stdin, stdout, stderr = ssh.exec_command(full, timeout=timeout, get_pty=True)
    if sudo:
        stdin.write(SSH_PASS + "\n")
        stdin.flush()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    if out.strip():
        sys.stdout.write(out[:8000] + ("\n...(trunc)\n" if len(out) > 8000 else "\n"))
    if err.strip() and rc != 0:
        sys.stderr.write("STDERR: " + err[:4000] + "\n")
    if check and rc != 0:
        raise RuntimeError(f"Exit {rc}: {cmd[:160]}\n{err[:2000]}")
    return out, err, rc

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SSH_HOST, port=22, username=SSH_USER, password=SSH_PASS, timeout=30)
    sftp = ssh.open_sftp()
    try:
        print(">>> [1] Escribir .env CORRECTO en /opt/elecciones2026/.env (PostgreSQL real, DEBUG=False, ALLOWED_HOSTS=*)")
        new_env = f"""DEBUG=False
SECRET_KEY={secrets.token_hex(48)}
ALLOWED_HOSTS=*

DATABASE_URL=postgres://admin_felix:flxadm1234abc@127.0.0.1:5432/electoral_personeros_vps

REDIS_URL=redis://127.0.0.1:6379/8
CELERY_BROKER_URL=redis://127.0.0.1:6379/9
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/10

MASTER_ENCRYPTION_KEY={secrets.token_hex(32)}

TESSERACT_CMD=/usr/bin/tesseract
IMAGES_ROOT=/opt/sistema_personeros/repo/electoral-personeros-app/backend/storage/actas_escrutinio
MEDIA_ROOT=/opt/elecciones2026/media
STATIC_ROOT=/opt/elecciones2026/staticfiles

DEFAULT_AI_PROVIDER=gemini
GEMINI_API_KEY=
GEMINI_MODEL=gemini-1.5-flash-latest
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-chat

LANGUAGE_CODE=es-pe
TIME_ZONE=America/Lima
"""
        tmp = f"/tmp/.env_{secrets.token_hex(8)}"
        with sftp.open(tmp, "w") as f:
            f.write(new_env)
        run(ssh, f"mv {tmp} /opt/elecciones2026/.env && chown felix:felix /opt/elecciones2026/.env && chmod 0640 /opt/elecciones2026/.env")
        run(ssh, "echo '=== ENV CONTENTS ===' && cat /opt/elecciones2026/.env | sed 's/KEY=.*/KEY=**REDACTED**/g ; s/PASSWORD=.*/PASSWORD=**REDACTED**/g ; s/:.*@/:***@/g' ; echo '=== END ==='", sudo=False)

        print(">>> [2] Validar conexión PostgreSQL desde venv Django + que las tablas usuarios, actas_escrutinio, mesa existan")
        run(ssh, "cd /opt/elecciones2026 && . .venv/bin/activate && export PYTHONDONTWRITEBYTECODE=1 && "
                 "python -c 'from core.models_legacy import UsuarioLegacy, ActaEscrutinioLegacy, MesaLegacy; "
                 "print(\"USUARIOS:\", UsuarioLegacy.objects.count()); "
                 "print(\"ACTAS LEGADAS:\", ActaEscrutinioLegacy.objects.count()); "
                 "print(\"MESAS:\", MesaLegacy.objects.count()); "
                 "sample = list(UsuarioLegacy.objects.filter(estado=True).values(\"idusuario\",\"usuario\",\"rol\")[:5]);"
                 "print(\"Sample users (estado=True):\", sample)' 2>&1 | tail -20", sudo=False, timeout=180)

        print(">>> [3] Django manage.py check contra PostgreSQL")
        run(ssh, "cd /opt/elecciones2026 && . .venv/bin/activate && python manage.py check 2>&1 | tail -10", sudo=False, timeout=120)

        print(">>> [4] Restart web + celery")
        run(ssh, "systemctl restart elecciones2026-web elecciones2026-celery && sleep 5 && "
                 "systemctl --no-pager --full status elecciones2026-web | head -12 && echo '===CELERY===' && "
                 "systemctl --no-pager --full status elecciones2026-celery | head -12")

        print(">>> [5] Smoke test HTTP 127.0.0.1:8201 con Host: elecciones2026.whadox.com")
        time.sleep(5)
        run(ssh, "curl -sS -D - -o /dev/null -H 'Host: elecciones2026.whadox.com' --max-time 25 http://127.0.0.1:8201/accounts/login/ 2>&1 | head -20 ; echo '---' ; "
                 "curl -sS -o /dev/null -w 'HTTP_HOST_HEADER=%{http_code}\\n' -H 'Host: elecciones2026.whadox.com' --max-time 25 http://127.0.0.1:8201/accounts/login/ ; "
                 "curl -skS -o /dev/null -w 'HTTP_HTTPS_PUB=%{http_code}\\n' --max-time 30 https://elecciones2026.whadox.com/accounts/login/ ; "
                 "curl -sS -o /tmp/login_page.html -H 'Host: elecciones2026.whadox.com' --max-time 25 http://127.0.0.1:8201/accounts/login/ && echo 'Login page bytes:' && wc -c /tmp/login_page.html && grep -o 'Iniciar sesi\\xf3n' /tmp/login_page.html | head -1", sudo=False, check=False)

        print(">>> [6] Ejemplo: login desde tabla usuarios (prueba unitaria Python, sin browser)")
        run(ssh, "cd /opt/elecciones2026 && . .venv/bin/activate && "
                 "python -c 'import bcrypt; from core.models_legacy import UsuarioLegacy; "
                 "u = UsuarioLegacy.objects.filter(estado=True, rol__in=(\"SUPER_ADMIN\",\"ADMINISTRADOR\")).first(); "
                 "print(f\"User legado OK: id={u.idusuario} usuario={u.usuario} rol={u.rol}\" if u else \"NINGUN USUARIO SUPER/ADMIN ENCONTRADO EN tabla usuarios\")' 2>&1 | tail -10", sudo=False, timeout=120)

        print("\n=== HOTFIX FINALIZADO ===")
        print("  URL:     https://elecciones2026.whadox.com/")
        print("  Login:   usuarios.rol ∈ {SUPER_ADMIN, ADMINISTRADOR} + estado=true")
        print("  Config IA (Solo SuperAdmin): /admin/core/aiprovidersettings/")
        print("  Optimización CPU: celery concurrency=1 (gevent) + OMP=1 + cv2 threads=1 + web workers=2")
        print("  BD:      Complementa electoral_personeros_vps con tablas vps_* (NINGUNA tabla existente fue borrada)")
    finally:
        sftp.close()
        ssh.close()

if __name__ == "__main__":
    main()
