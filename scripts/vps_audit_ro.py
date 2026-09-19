"""Read-only SSH auditor del VPS. Genera reporte a /tmp/vps_audit.txt"""
import sys
import json
from pathlib import Path

try:
    import paramiko
except ImportError:
    sys.exit("Instala paramiko: pip install paramiko")

HOST = "31.220.84.86"
USER = "felix"
PASS = "flxadm1234abc"
DB_USER = "admin_felix"
DB_PASS = "flxadm1234abc"
DB_NAME = "electoral_personeros_vps"

COMMANDS = [
    ("hostname", "hostnamectl; uname -a; whoami; pwd"),
    ("resources", "nproc; free -h; df -hT /; echo ---; lsblk -o NAME,SIZE,TYPE,MOUNTPOINT 2>/dev/null | head -20"),
    ("webservers", "ps -eo pid,user,cmd | egrep 'nginx|apache|httpd|gunicorn|uvicorn|node|php' | head -40"),
    ("nginx_sites", "ls -la /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null; echo ---; find /etc/nginx -name '*.conf' -maxdepth 4 | xargs -I{} sh -c 'echo ===== {}; cat {}' 2>/dev/null | head -400"),
    ("apache_sites", "ls -la /etc/apache2/sites-enabled/ 2>/dev/null; httpd -S 2>/dev/null; apache2ctl -S 2>/dev/null"),
    ("pg", "which psql; pg_isready 2>/dev/null; sudo -n -u postgres psql -c '\\l' 2>&1 | head -30"),
    ("services", "systemctl list-units --type=service --state=running --no-pager 2>/dev/null | head -40"),
    ("listening_ports", "ss -lntp 2>/dev/null | head -30 || netstat -lntp 2>/dev/null | head -30"),
    ("folders_apps", "ls -la /var/www/ /opt/ /srv/ /home/felix/ 2>/dev/null | head -80"),
]

TABLES = [
    "usuarios",
    "actas_escrutinio",
    "mesa",
    "mesa_numero",
    "organizaciones_politicas",
    "candidatos",
    "elecciones",
    "procesos_electorales",
    "departamentos",
    "provincias",
    "distritos",
]

def run(client, cmd):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return (out + "\nSTDERR: " + err).strip()


def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()

    report = []
    report.append("================================ VPS AUDIT REPORT ================================")
    for name, cmd in COMMANDS:
        report.append(f"\n------------------- CMD [{name}] -------------------\n{cmd}")
        report.append(run(client, cmd))

    # Tables
    report.append("\n================================ DB STRUCTURE ================================")
    psql_prefix = f"PGPASSWORD='{DB_PASS}' psql -h 127.0.0.1 -U {DB_USER} -d {DB_NAME} --no-align -P pager=off "

    # List tables
    cmd = psql_prefix + f""" -c "SELECT schemaname, tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;" """
    report.append("\n--- Public tables ---")
    report.append(run(client, cmd))

    # Count rows and columns per target table
    for t in TABLES:
        report.append(f"\n\n===== TABLE: {t} =====")
        cmd_count = psql_prefix + f" -c 'SELECT COUNT(*) FROM {t};' 2>&1"
        report.append(f"[count] {run(client, cmd_count)}")
        cmd_cols = psql_prefix + f" -c '\\d {t}' 2>&1"
        report.append(f"[schema] {run(client, cmd_cols)}")
        cmd_sample = psql_prefix + f" -c 'SELECT * FROM {t} LIMIT 3;' 2>&1"
        report.append(f"[sample 3 rows] {run(client, cmd_sample)}")

    # Real sample image path
    report.append("\n--- Sample actas file_path_disco (10 rows) ---")
    cmd = psql_prefix + f" -c 'SELECT id, tipo_acta, mesa_numero, file_path_disco FROM actas_escrutinio LIMIT 10;' 2>&1"
    report.append(run(client, cmd))

    # Verify file existence of first image
    report.append("\n--- Verificar existencia de primera imagen (file -ls) ---")
    first = psql_prefix + f" -c \"SELECT file_path_disco FROM actas_escrutinio WHERE file_path_disco IS NOT NULL LIMIT 1;\""
    out_first = run(client, first)
    report.append(out_first)
    # Try to parse a path
    for line in out_first.splitlines():
        parts = line.split("|")
        if len(parts) >= 1 and "/" in parts[-1]:
            candidate = parts[-1].strip().strip("'\"")
            if candidate.startswith("/"):
                report.append(run(client, f"ls -lah '{candidate}' 2>&1 | head -3; echo ---; file '{candidate}' 2>&1 | head -3"))
                break

    report.append("\n================================ END ================================")

    Path("vps_audit_report.txt").write_text("\n".join(report), encoding="utf-8")
    print("Reporte escrito en vps_audit_report.txt")
    sftp.close()
    client.close()


if __name__ == "__main__":
    main()
