#!/usr/bin/env python3
"""Validación final VPS: users legacy, regla IQUITOS, sample acta, dashboard batch 1 acta."""
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
        sys.stdout.write(out[:12000] + ("\n...(trunc)\n" if len(out) > 12000 else "\n"))
    if err.strip() and rc != 0:
        sys.stderr.write("STDERR: " + err[:4000] + "\n")
    if check and rc != 0:
        raise RuntimeError(f"Exit {rc}: {cmd[:160]}\n{err[:2000]}")
    return out, err, rc

PY_INIT = """
import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings")
django.setup()
import django
from core.models_legacy import UsuarioLegacy, ActaEscrutinioLegacy, MesaLegacy
from core.processors import ActaProcessor
from core.models import Acta

print("== USUARIOS HABILITADOS (estado=1, rol in {SUPER_ADMIN/ADMINISTRADOR}) ==")
qs_u = list(UsuarioLegacy.objects.filter(estado=True).values("idusuario","usuario","rol").order_by("rol","usuario"))
for u in qs_u:
    print(f"  [{u['rol']:15s}] id={u['idusuario']:<3} {u['usuario']}")
print(f"  TOTAL HABILITADOS: {len(qs_u)}")

# Contadores básicos
print("\\n== CONTADORES TABLAS LEGADAS ==")
print(f"  Mesa:       {MesaLegacy.objects.count()}")
print(f"  ActaEscrut: {ActaEscrutinioLegacy.objects.count()}")

# Muestra mesas con distrito IQUITOS case-insensitive
print("\\n== DETECCIÓN IQUITOS: mesas con distrito normalizado == IQUITOS ==")
mesas_iquitos = []
for m in MesaLegacy.objects.only("id_mesa","num_mesa","distrito").iterator():
    if MesaLegacy.normalize_distrito(m.distrito) == "IQUITOS":
        mesas_iquitos.append((m.id_mesa, m.num_mesa, m.distrito))
print(f"  Total mesas distrito IQUITOS: {len(mesas_iquitos)}")
for mid, n, d in mesas_iquitos[:5]:
    print(f"    id={mid} num={n!r} distrito={d!r}")

# ActaEscrutinioLegacy IQUITOS (join mesa_numero -> num_mesa) y tipo PROVINCIAL_DISTRITAL
print("\\n== ACTAS ESPECIALES: IQUITOS + PROVINCIAL_DISTRITAL (solo 1 columna) ==")
count_special = 0
sample_special_leg_ids = []
for ae in ActaEscrutinioLegacy.objects.only("id","mesa_numero","tipo_acta").iterator():
    try:
        m = MesaLegacy.objects.get(num_mesa=ae.mesa_numero.strip())
        if MesaLegacy.normalize_distrito(m.distrito)=="IQUITOS" and ae.tipo_acta=="PROVINCIAL_DISTRITAL":
            count_special += 1
            sample_special_leg_ids.append(ae.id)
    except MesaLegacy.DoesNotExist:
        pass
print(f"  TOTAL actas IQUITOS/PROV-DIST (single column): {count_special}")
print(f"  Muestra ids: {sample_special_leg_ids[:3]}")

# Procesar 1 acta legada cualquiera (la primera) como prueba de humo de todo el pipeline
# (solo registra entradas en tabla vps_actas / vps_acta_vote_entries)
print("\\n== PRUEBA PIPELINE: process_legacy_acta_escrutinio sobre 1ra ActaEscrutinioLegacy existente ==")
first = ActaEscrutinioLegacy.objects.order_by("id").first()
if first:
    print(f"  Acta legada id={first.id}, mesa={first.mesa_numero!r}, tipo_acta={first.tipo_acta}, file_path={first.file_path_disco[:80]}")
    proc = ActaProcessor()
    t0 = time.time()
    try:
        r = proc.process_legacy_acta_escrutinio(first.id)
        dt = time.time() - t0
        print(f"  result.ok={r.ok} status={r.status} time={dt:.2f}s error={r.error}")
        if hasattr(r, "stats"):
            print(f"  stats={r.stats}")
        a = Acta.objects.filter(acta_escrutinio_legacy_id=first.id).first()
        if a:
            from core.models import ActaVoteEntry
            entries = ActaVoteEntry.objects.filter(acta=a)
            n_ambitos = set(entries.values_list("ambito",flat=True))
            print(f"  Acta nueva id={a.id} status={a.status} total_entries={entries.count()} ambitos={sorted(n_ambitos)}")
            for amb in sorted(n_ambitos):
                n = entries.filter(ambito=amb).count()
                vs = entries.filter(ambito=amb,entry_category="organizacion").count()
                print(f"    ambito={amb:<25s}: entries={n} orgs={vs}")
    except Exception as e:
        print(f"  PIPELINE EXCEPTION: {e}")
        import traceback; traceback.print_exc()
else:
    print("  No hay actas_escrutinio en DB VPS (saltando prueba)")

"""

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SSH_HOST, port=22, username=SSH_USER, password=SSH_PASS, timeout=30)
    try:
        init_py = "/tmp/vps_validate_" + secrets.token_hex(6) + ".py"
        with ssh.open_sftp() as sftp:
            with sftp.open(init_py, "w") as f:
                f.write(PY_INIT)
        # Ejecutar via manage.py shell (que ya configura DJANGO_SETTINGS_MODULE)
        # Pasar por stdin para que DJANGO_SETTINGS_MODULE esté definido al importar
        run(ssh, f"cd /opt/elecciones2026 && . .venv/bin/activate && export PYTHONDONTWRITEBYTECODE=1 OMP_THREAD_LIMIT=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 OPENCV_IO_MAX_IMAGE_PIXELS=350000000 && time python {init_py} 2>&1 | tail -80", sudo=False, timeout=900)
    finally:
        ssh.close()
    print("\n=== VALIDACIÓN VPS TERMINADA ===")

if __name__ == "__main__":
    main()
