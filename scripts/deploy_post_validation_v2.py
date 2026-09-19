#!/usr/bin/env python3
"""Validación final VPS v2: users, IQUITOS, 1 acta pipeline. Forzando CWD + PYTHONPATH."""
from __future__ import annotations
import os, sys, time, secrets, paramiko

SSH_HOST = "31.220.84.86"
SSH_USER = "felix"
SSH_PASS = "flxadm1234abc"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def run(ssh, cmd, check=True, timeout=900, sudo=True):
    prefix = "sudo -H -S bash -c " if sudo else "bash -c "
    full = f"{prefix}'{cmd.replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'"
    sys.stdout.write(f"\n$ {cmd[:200]}\n")
    sys.stdout.flush()
    stdin, stdout, stderr = ssh.exec_command(full, timeout=timeout, get_pty=True)
    if sudo:
        stdin.write(SSH_PASS + "\n")
        stdin.flush()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    if out.strip():
        sys.stdout.write(out[:16000] + ("\n...(trunc)\n" if len(out) > 16000 else "\n"))
    if err.strip() and rc != 0:
        sys.stderr.write("STDERR: " + err[:4000] + "\n")
    if check and rc != 0:
        raise RuntimeError(f"Exit {rc}: {cmd[:160]}\n{err[:2000]}")
    return out, err, rc

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SSH_HOST, port=22, username=SSH_USER, password=SSH_PASS, timeout=30)
    try:
        init_py = "/opt/elecciones2026/__vps_validate.py"
        PY_CODE = r"""
import os, sys, time
os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings")
import django
django.setup()

from core.models_legacy import UsuarioLegacy, ActaEscrutinioLegacy, MesaLegacy
from core.processors import ActaProcessor
from core.models import Acta, ActaVoteEntry

print("==" * 30)
print("== [1] USUARIOS HABILITADOS: estado=1 + rol in {SUPER_ADMIN, ADMINISTRADOR}")
qs_u = list(UsuarioLegacy.objects.filter(estado=True).values("idusuario","usuario","rol").order_by("rol","usuario"))
for u in qs_u:
    mark = "  * " if u["rol"] in ("SUPER_ADMIN","ADMINISTRADOR") else "    "
    print(f"{mark}[{u['rol']:15s}] id={u['idusuario']:<3} {u['usuario']}")
print(f"TOTAL: {len(qs_u)} usuarios activos; "
      f"ACCESO WEB (SUPER/ADMIN): {sum(1 for u in qs_u if u['rol'] in ('SUPER_ADMIN','ADMINISTRADOR'))}")

print("\n" + "=="*30)
print("== [2] TABLAS LEGADAS: contadores")
print(f"  MesaLegacy:             {MesaLegacy.objects.count()}")
print(f"  ActaEscrutinioLegacy:   {ActaEscrutinioLegacy.objects.count()}")
print(f"  Acta (nueva vps_actas): {Acta.objects.count()}")

print("\n" + "=="*30)
print("== [3] REGLA IQUITOS: mesas con distrito normalizado == IQUITOS")
mesas_iquitos = []
for m in MesaLegacy.objects.only("id_mesa","num_mesa","distrito").iterator():
    if MesaLegacy.normalize_distrito(m.distrito) == "IQUITOS":
        mesas_iquitos.append(m)
print(f"  Mesas en distrito IQUITOS: {len(mesas_iquitos)}")
for m in mesas_iquitos[:5]:
    print(f"    id={m.id_mesa} num={m.num_mesa!r} distrito_original={m.distrito!r}")

print("\n" + "=="*30)
print("== [4] ACTAS LEGADAS IQUITOS PROVINCIAL_DISTRITAL: single-column rule")
count_special = 0
sample_special = []
for ae in ActaEscrutinioLegacy.objects.only("id","mesa_numero","tipo_acta","file_path_disco").iterator():
    try:
        m = MesaLegacy.objects.get(num_mesa=ae.mesa_numero.strip())
        if MesaLegacy.normalize_distrito(m.distrito)=="IQUITOS" and ae.tipo_acta=="PROVINCIAL_DISTRITAL":
            count_special += 1
            if len(sample_special) < 3:
                sample_special.append(ae.id)
    except MesaLegacy.DoesNotExist:
        pass
print(f"  Casos IQUITOS special (single column): {count_special}")
print(f"  Muestra de IDs: {sample_special}")

print("\n" + "=="*30)
print("== [5] MUESTRA TIPOS ACTA y 11 PRIMERAS ACTAS (file_path_disco)")
from collections import Counter
tipos = Counter(ActaEscrutinioLegacy.objects.values_list("tipo_acta", flat=True))
print("  Tipos acta en DB:", dict(tipos))
for ae in ActaEscrutinioLegacy.objects.only("id","mesa_numero","tipo_acta","file_path_disco").order_by("id")[:11]:
    try:
        m = MesaLegacy.objects.get(num_mesa=ae.mesa_numero.strip())
        dist = m.distrito
    except Exception:
        dist = "?"
    fp = (ae.file_path_disco or "")
    print(f"    {ae.id:<4} mesa={ae.mesa_numero!s:<8} tipo={ae.tipo_acta:<22} distrito={dist!r:20} exists={os.path.isfile(fp)} path_tail={fp[-65:]!r}")
"""
        with ssh.open_sftp() as sftp:
            with sftp.open(init_py, "w") as f:
                f.write(PY_CODE)
        run(ssh, f"chown felix:felix {init_py}", sudo=False)

        run(ssh, f"cd /opt/elecciones2026 && . .venv/bin/activate && export PYTHONPATH=/opt/elecciones2026:$PYTHONPATH && export PYTHONDONTWRITEBYTECODE=1 OMP_THREAD_LIMIT=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 OPENCV_IO_MAX_IMAGE_PIXELS=350000000 && "
                 f"time python {init_py} 2>&1 | tail -100", sudo=False, timeout=900)

        run(ssh, f"rm -f {init_py}", sudo=False)
    finally:
        ssh.close()
    print("\n=== VALIDACIÓN FINAL OK ===")
    print("Dashboard: https://elecciones2026.whadox.com/")
    print("Admin:     https://elecciones2026.whadox.com/admin/ (Config IA solo SUPER_ADMIN)")

if __name__ == "__main__":
    main()
