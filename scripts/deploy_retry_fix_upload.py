#!/usr/bin/env python3
"""Deploy fix #1: re-subida correcta con separadores / (no Windows \), arranca servicios, smoke tests.
Pasos 1-15 del deploy original ya completados (backup, venv, migrate, collectstatic, units, nginx).
"""
from __future__ import annotations

import os
import sys
import time
import secrets
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


def run(ssh: paramiko.SSHClient, cmd: str, check: bool = True, timeout: int = 600, sudo: bool = True):
    prefix = "sudo -H -S bash -c " if sudo else "bash -c "
    cmd_clean = cmd.replace("'", "'\\''")
    full = f"{prefix}'{cmd_clean}'"
    sys.stdout.write(f"\n$ {cmd[:160]}\n")
    sys.stdout.flush()
    stdin, stdout, stderr = ssh.exec_command(full, timeout=timeout, get_pty=True)
    if sudo:
        stdin.write(SSH_PASS + "\n")
        stdin.flush()
    out_b = stdout.read()
    err_b = stderr.read()
    try:
        out = out_b.decode("utf-8", errors="replace")
        err = err_b.decode("utf-8", errors="replace")
    except Exception:
        out = str(out_b)
        err = str(err_b)
    rc = stdout.channel.recv_exit_status()
    if out.strip():
        sys.stdout.write(out[:8000] + ("\n...(truncated)\n" if len(out) > 8000 else "\n"))
    if err.strip() and rc != 0:
        sys.stderr.write("STDERR: " + err[:4000] + "\n")
    sys.stdout.flush()
    if check and rc != 0:
        raise RuntimeError(f"Exit {rc} for cmd: {cmd[:160]}\nERR: {err[:2000]}")
    return (out, err, rc)


def sudo_put_text(sftp: paramiko.SFTPClient, ssh: paramiko.SSHClient, remote_path: str, content: str, mode: int = 0o644):
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
    """Corrected: convert Windows Path to POSIX separators when building remote paths."""
    files_uploaded = 0
    for root, dirs, files in os.walk(local_dir):
        dirs[:] = [d for d in dirs if not should_exclude(Path(d)) and not d.startswith(".")]
        local_root_p = Path(root)
        rel = local_root_p.relative_to(local_dir)
        # Convert to POSIX path string - KEY FIX
        rel_posix = rel.as_posix() if str(rel) != "." else ""
        remote_sub_str = str(remote_dir)
        if rel_posix:
            remote_sub_str = remote_sub_str.rstrip("/") + "/" + rel_posix
        try:
            sftp.stat(remote_sub_str)
        except FileNotFoundError:
            run(ssh, f"mkdir -p '{remote_sub_str}' && chown felix:felix '{remote_sub_str}'", sudo=False)
        for fn in files:
            p = local_root_p / fn
            if should_exclude(Path(fn)):
                continue
            remote_fp = remote_sub_str.rstrip("/") + "/" + fn
            if p.stat().st_size > 100 * 1024 * 1024:
                print(f"  [SKIP BIG] {p} >100MB")
                continue
            try:
                sftp.put(str(p), remote_fp)
                files_uploaded += 1
                if files_uploaded % 100 == 0:
                    sys.stdout.write(f"  ... {files_uploaded} archivos subidos\n")
                    sys.stdout.flush()
            except Exception as e:
                sys.stderr.write(f"  ERROR subiendo {p} -> {remote_fp}: {e}\n")
    sys.stdout.write(f"  Total archivos subidos: {files_uploaded}\n")
    sys.stdout.flush()
    return files_uploaded


def main():
    # Fix encoding stdout Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    print(f"=== Retry Deploy: Resubida (fix separators) + servicios  ===")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SSH_HOST, port=SSH_PORT, username=SSH_USER, password=SSH_PASS, timeout=30, banner_timeout=30)
    sftp = ssh.open_sftp()
    try:
        print(">>> [Fix 0] Eliminar carpetas inválidas con backslash \\ en nombres (error Windows bug)")
        run(ssh, "cd /opt/elecciones2026 && find . -name '*\\\\*' -type d -maxdepth 3 -print -delete 2>/dev/null || true ; "
                 "find . -name '*\\\\*' -maxdepth 3 -print 2>/dev/null || true")

        print(">>> [Fix 1] Re-subida COMPLETA del proyecto (SOBREESCRIBIENDO) con paths POSIX correctos")
        upload_dir(sftp, ssh, LOCAL_ROOT, REMOTE_ROOT)

        print(">>> [Fix 2] chown felix:felix /opt/elecciones2026 recursivo")
        run(ssh, f"chown -R felix:felix {REMOTE_ROOT} && chmod 0750 {REMOTE_ROOT}/.env || true")

        print(">>> [Fix 3] Verificar que Python + dependencias sigan OK")
        run(ssh, f"cd {REMOTE_ROOT} && . .venv/bin/activate && python -c 'import django,cv2,tesseract,pytesseract; print(django.get_version()); print(\"OK_DEPS\")' 2>&1 | tail -10", sudo=False, timeout=120)

        print(">>> [Fix 4] Verificar archivos clave: templates, migrations, management existan en ruta correcta")
        run(ssh, f"ls -la {REMOTE_ROOT}/core/templates/core/ ; echo '---' ; ls -la {REMOTE_ROOT}/core/management/commands/ ; echo '---' ; ls -la {REMOTE_ROOT}/core/migrations/ ; echo '---' ; wc -l {REMOTE_ROOT}/core/views.py {REMOTE_ROOT}/core/auth_backend.py {REMOTE_ROOT}/core/processors.py {REMOTE_ROOT}/core/admin.py", sudo=False)

        print(">>> [Fix 5] systemd daemon-reload + restart web + celery")
        run(ssh, "systemctl daemon-reload && "
                 "systemctl restart elecciones2026-web && "
                 "systemctl restart elecciones2026-celery && sleep 6 && "
                 "systemctl --no-pager --full status elecciones2026-web | head -25 && "
                 "echo '==== CELERY ====' && systemctl --no-pager --full status elecciones2026-celery | head -20")

        print(">>> [Fix 6] Smoke test HTTP local 127.0.0.1:8201")
        time.sleep(3)
        run(ssh, "curl -sS -D - -o /dev/null --max-time 25 http://127.0.0.1:8201/accounts/login/ 2>&1 | head -20", sudo=False, check=False)
        run(ssh, "curl -sS -o /dev/null -w 'HTTP_LOCAL=%{http_code}\\n' --max-time 25 http://127.0.0.1:8201/accounts/login/ ; "
                 "curl -skS -o /dev/null -w 'HTTP_HTTPS_RESOLVE=%{http_code}\\n' --max-time 30 https://elecciones2026.whadox.com/accounts/login/ --resolve elecciones2026.whadox.com:443:127.0.0.1",
                 sudo=False, check=False)

        print(">>> [Fix 7] Logs último minuto (web + celery)")
        run(ssh, "journalctl -u elecciones2026-web --no-pager -n 20 2>&1 | tail -20 ; echo '=== CELERY LOG ===' ; journalctl -u elecciones2026-celery --no-pager -n 20 2>&1 | tail -20", check=False)

        print("\n=== RETRY DEPLOY COMPLETADO ===")
        print("  Web  : https://elecciones2026.whadox.com/")
        print("  Login: tabla PostgreSQL usuarios (rol ADMINISTRADOR o SUPER_ADMIN)")
        print("  Config IA (Solo SUPER_ADMIN): https://elecciones2026.whadox.com/admin/core/aiprovidersettings/")
    finally:
        sftp.close()
        ssh.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write(f"\n!! RETRY FALLÓ: {exc}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
