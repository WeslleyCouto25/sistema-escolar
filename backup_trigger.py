"""Aciona endpoint privado do web service SIGEU. Usado somente pelo Render Cron."""
import json
import os
from urllib.request import Request, urlopen


def main():
    url = os.environ["SIGEU_BACKUP_URL"].strip()
    token = os.environ["SIGEU_BACKUP_TRIGGER_TOKEN"].strip()
    if not url.startswith("https://") or not url.endswith("/internal/sigeu/backup/v1"):
        raise RuntimeError("SIGEU_BACKUP_URL invalida")
    if len(token) < 40:
        raise RuntimeError("Segredo de agendamento ausente")
    req = Request(url, data=b"", method="POST", headers={
        "X-SIGEU-Backup-Token": token,
        "User-Agent": "SIGEU-Backup-Cron/1",
        "Accept": "application/json",
    })
    with urlopen(req, timeout=175) as response:
        if response.status != 200:
            raise RuntimeError("Backup remoto nao confirmado")
        result = json.load(response)
    if result.get("ok") is not True:
        raise RuntimeError("Backup remoto nao confirmou conclusao")
    print("SIGEU_CRON_BACKUP_OK id=%s tables=%s rows=%s" % (
        result.get("database_id"), result.get("tables"), result.get("rows")
    ), flush=True)


if __name__ == "__main__":
    main()
