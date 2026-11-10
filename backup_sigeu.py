"""Snapshots somente-leitura e criptografados de TODAS as tabelas publicas do SiGEU.

Exporta CSV nativo PostgreSQL, metadados do esquema e sequencias em tar.gz.
R2 recebe APENAS ciphertext AES-256-GCM; arquivos jamais sao servidos publicamente.
A restauracao deve ser ensaiada numa BASE VAZIA/ISOLADA, nunca automaticamente.
"""
import base64
import hashlib
import io
import logging
import threading
import time
import json
import os
import re
import secrets
import tarfile
import tempfile
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from psycopg2 import sql

from db_pool import get_db_connection
from r2_storage import bucket_name, get_client, is_configured

MAGIC = b"SIGEU-R2-BACKUP-1\0"
PREFIX = "backups-sigeu/v1/"
CHUNK = 1024 * 1024


def _aes_key():
    value = os.getenv("SIGEU_BACKUP_KEY", "").strip()
    if not value:
        raise RuntimeError("SIGEU_BACKUP_KEY ausente")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, base64.binascii.Error) as exc:
        raise RuntimeError("SIGEU_BACKUP_KEY invalida") from exc
    if len(raw) != 32:
        raise RuntimeError("SIGEU_BACKUP_KEY precisa ter 32 bytes")
    return raw


def _insert_json(tar, name, obj):
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str).encode("utf8")
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _sha_file(fileobj):
    h = hashlib.sha256()
    fileobj.seek(0)
    while True:
        data = fileobj.read(CHUNK)
        if not data:
            break
        h.update(data)
    fileobj.seek(0)
    return h.hexdigest()


def _database_signature(cur):
    cur.execute("SELECT current_database() AS db, inet_server_addr()::text AS addr, inet_server_port() AS port")
    row = cur.fetchone()
    source = "|".join(str(row.get(k, "")) for k in ("db", "addr", "port"))
    return hashlib.sha256(source.encode("utf8")).hexdigest()[:16]


def database_overview():
    """Apenas contagens e assinatura opaca: nenhuma informacao pessoal."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        source = _database_signature(cur)
        cur.execute("""
            SELECT c.relname AS nome FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='public' AND c.relkind='r'
               AND c.relispartition=false ORDER BY c.relname
        """)
        table_names = {r["nome"] for r in cur.fetchall()}
        # Bancos comuns nao particionados; nome de tabela apenas de pg_catalog.
        counts = {}
        for name in ("cobrancas_avulsas", "cobrancas_avulsas_historico", "contratos_alunos", "alunos"):
            if name in table_names:
                cur.execute(sql.SQL("SELECT count(*) AS n FROM public.{}").format(sql.Identifier(name)))
                counts[name] = int(cur.fetchone()["n"])
            else:
                counts[name] = None
        return {"database_id": source, "tabelas": len(table_names), "contagens": counts}
    finally:
        conn.close()


def _export_tar_gz():
    conn = get_db_connection()
    output = tempfile.TemporaryFile(mode="w+b")
    try:
        cur = conn.cursor()
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        source_id = _database_signature(cur)
        cur.execute("""
            SELECT c.relname AS nome FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname
        """)
        names = [r["nome"] for r in cur.fetchall()]
        if not names:
            raise RuntimeError("Nenhuma tabela publica encontrada; backup interrompido")
        manifest = {
            "format": "sigeu-pg-csv-v1", "created_utc": datetime.now(timezone.utc).isoformat(),
            "source_id": source_id, "source_commit": os.getenv("RENDER_GIT_COMMIT", "unknown"),
            "tables": [], "sequences": [],
            "restore_warning": "Restaurar somente em banco isolado com esquema equivalente. Nunca reimportar automaticamente em producao.",
        }
        with tarfile.open(fileobj=output, mode="w:gz") as tar:
            for index, name in enumerate(names):
                cur.execute("""
                    SELECT a.attname AS coluna, pg_catalog.format_type(a.atttypid,a.atttypmod) AS tipo
                    FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname=%s
                      AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum
                """, (name,))
                columns = cur.fetchall()
                if not columns:
                    continue
                fieldnames = [x["coluna"] for x in columns]
                # COPY nativo conserva corretamente \\N, quebras de linha, JSONB e BYTEA.
                identifiers = sql.SQL(",").join(sql.Identifier(x) for x in fieldnames)
                statement = sql.SQL("COPY public.{} ({}) TO STDOUT WITH (FORMAT CSV, NULL '\\\\N')").format(
                    sql.Identifier(name), identifiers
                )
                with tempfile.TemporaryFile(mode="w+b") as csvfile:
                    cur.copy_expert(statement.as_string(cur), csvfile)
                    size = csvfile.tell()
                    csvhash = _sha_file(csvfile)
                    filename = f"tables/{index:05d}.csv"
                    info = tarfile.TarInfo(filename)
                    info.size = size
                    tar.addfile(info, csvfile)
                cur.execute(sql.SQL("SELECT COUNT(*) AS n FROM public.{}").format(sql.Identifier(name)))
                manifest["tables"].append({
                    "name": name, "file": filename, "rows": int(cur.fetchone()["n"]),
                    "columns": fieldnames, "types": [x["tipo"] for x in columns],
                    "csv_sha256": csvhash,
                })
            cur.execute("""
                SELECT sequence_name FROM information_schema.sequences
                WHERE sequence_schema='public' ORDER BY sequence_name
            """)
            for seq in [x["sequence_name"] for x in cur.fetchall()]:
                try:
                    cur.execute(sql.SQL("SELECT last_value, is_called FROM public.{}").format(sql.Identifier(seq)))
                    x = cur.fetchone()
                    manifest["sequences"].append({"name": seq, "last_value": int(x["last_value"]), "is_called": bool(x["is_called"])})
                except Exception:
                    # Se uma sequence nao for legivel, aborta a transacao inteira e nao
                    # publica snapshot parcialmente confiavel.
                    raise
            _insert_json(tar, "manifest.json", manifest)
        conn.rollback()
        output.seek(0)
        return output, manifest
    except Exception:
        conn.rollback()
        output.close()
        raise
    finally:
        conn.close()


def _encrypt_to_file(plain, encrypted):
    nonce = secrets.token_bytes(12)
    key = _aes_key()
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encrypted.write(MAGIC + nonce)
    plain.seek(0)
    while True:
        block = plain.read(CHUNK)
        if not block:
            break
        encrypted.write(encryptor.update(block))
    encrypted.write(encryptor.finalize())
    encrypted.write(encryptor.tag)
    encrypted.seek(0)
    return _sha_file(encrypted)


def create_backup():
    """Consistencia via REPEATABLE READ; R2 confirma tamanho apos upload."""
    if not is_configured():
        raise RuntimeError("Cloudflare R2 nao configurado")
    _aes_key()
    plain, manifest = _export_tar_gz()
    try:
        with tempfile.TemporaryFile(mode="w+b") as encrypted:
            digest = _encrypt_to_file(plain, encrypted)
            size = encrypted.seek(0, 2)
            encrypted.seek(0)
            now = datetime.now(timezone.utc)
            key = f"{PREFIX}{now:%Y/%m/%d}/{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(12)}.sigeu"
            client = get_client()
            client.upload_fileobj(
                encrypted, bucket_name(), key, ExtraArgs={
                    "ContentType": "application/octet-stream",
                    "Metadata": {
                        "sha256": digest,
                        "format": "aes256gcm-targz-v1",
                        "source": manifest["source_id"],
                    },
                }
            )
            head = client.head_object(Bucket=bucket_name(), Key=key)
            if int(head.get("ContentLength", -1)) != size:
                raise RuntimeError("R2 retornou tamanho divergente; backup nao validado")
            return {
                "backup_id": key, "sha256": digest, "bytes": size,
                "database_id": manifest["source_id"],
                "tables": len(manifest["tables"]),
                "rows": sum(x["rows"] for x in manifest["tables"]),
                "critical_counts": {x["name"]: x["rows"] for x in manifest["tables"]
                                    if x["name"] in ("cobrancas_avulsas", "cobrancas_avulsas_historico", "contratos_alunos", "alunos")},
            }
    finally:
        plain.close()


def recent_backups(limit=4):
    """Lista apenas metadados de backup sem jamais baixar dados pessoais."""
    if not is_configured():
        return []
    result = get_client().list_objects_v2(Bucket=bucket_name(), Prefix=PREFIX, MaxKeys=300)
    objects = sorted(result.get("Contents", []), key=lambda x: x["LastModified"], reverse=True)
    return [{"id": x["Key"], "bytes": int(x["Size"]), "time": x["LastModified"].isoformat()}
            for x in objects[:limit]]

_scheduler_guard = threading.Lock()
_scheduler_started_pid = None


def start_periodic_backups():
    """Backup a cada 6 h no web worker atual, sem contratar outro servico.

    O processo e daemon e nao substitui um cron dedicado. A captura pre-deploy
    permanece a garantia adicional contra mudancas de versao.
    """
    global _scheduler_started_pid
    if os.getenv("SIGEU_BACKUP_PERIODIC") != "1":
        return False
    _aes_key()
    hours = max(1, min(24, int(os.getenv("SIGEU_BACKUP_INTERVAL_HOURS", "6"))))
    with _scheduler_guard:
        if _scheduler_started_pid == os.getpid():
            return True
        _scheduler_started_pid = os.getpid()

        def runner():
            while True:
                time.sleep(hours * 3600)
                try:
                    result = create_backup()
                    logging.getLogger("sigeu.backups").info(
                        "SIGEU PERIODIC BACKUP VERIFIED source=%s tables=%s rows=%s sha256=%s",
                        result["database_id"], result["tables"], result["rows"], result["sha256"])
                except Exception:
                    logging.getLogger("sigeu.backups").exception("SIGEU PERIODIC BACKUP FAILED")

        threading.Thread(target=runner, name="sigeu-r2-backup", daemon=True).start()
        return True
