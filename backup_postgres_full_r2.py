"""Backup PostgreSQL COMPLETO (schema + dados + indices + objetos) para R2.

Uso em cron/worker com PostgreSQL client instalado:
    python backup_postgres_full_r2.py --create
    python backup_postgres_full_r2.py --verify backups-postgres-full/v1/...sigeu

Seguranca: arquivo sempre criptografado AES-256-GCM, bucket privado e
restauracao NUNCA automatica. Nao modifica a base de producao.
O dump inclui o banco, nao as roles globais do servidor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from psycopg2.extensions import parse_dsn

from backup_sigeu import CHUNK, MAGIC, _aes_key
from r2_storage import bucket_name, get_client, is_configured

PREFIX = "backups-postgres-full/v1/"


def _bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(
            f"{name} nao instalado. Instale PostgreSQL client no ambiente que executa o backup."
        )
    return path


def _pg_environment() -> dict[str, str]:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL ausente")
    conf = parse_dsn(dsn)
    env = os.environ.copy()
    for origem, destino in (
        ("host", "PGHOST"), ("hostaddr", "PGHOSTADDR"), ("port", "PGPORT"),
        ("dbname", "PGDATABASE"), ("user", "PGUSER"), ("password", "PGPASSWORD"),
        ("sslmode", "PGSSLMODE"), ("sslrootcert", "PGSSLROOTCERT"),
        ("sslcert", "PGSSLCERT"), ("sslkey", "PGSSLKEY"),
    ):
        if conf.get(origem):
            env[destino] = str(conf[origem])
    env["PGCONNECT_TIMEOUT"] = "20"
    return env


def _run_pg(program: str, args: list[str], *, stdout=None, stdin=None) -> None:
    result = subprocess.run(
        [_bin(program), *args], env=_pg_environment(),
        stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, timeout=3600,
        check=False,
    )
    if result.returncode != 0:
        # Falhas podem conter nomes de objetos; nunca registrar URL com credenciais.
        raise RuntimeError(f"{program} falhou (codigo {result.returncode}); conferir logs protegidos do cron")


def _check_archive(plain) -> None:
    plain.seek(0)
    _run_pg("pg_restore", ["--list"], stdin=plain, stdout=subprocess.DEVNULL)
    plain.seek(0)


def _encrypt(plain, encrypted) -> tuple[str, int]:
    nonce = secrets.token_bytes(12)
    encryptor = Cipher(algorithms.AES(_aes_key()), modes.GCM(nonce)).encryptor()
    encrypted.write(MAGIC + nonce)
    plain.seek(0)
    while True:
        data = plain.read(CHUNK)
        if not data:
            break
        encrypted.write(encryptor.update(data))
    encrypted.write(encryptor.finalize())
    encrypted.write(encryptor.tag)
    size = encrypted.tell()
    encrypted.seek(0)
    digest = hashlib.sha256()
    while True:
        data = encrypted.read(CHUNK)
        if not data:
            break
        digest.update(data)
    encrypted.seek(0)
    return digest.hexdigest(), size


def create_full_backup() -> dict:
    if not is_configured():
        raise RuntimeError("R2 nao configurado")
    _aes_key()
    # Falhe antes de executar um dump parcial se o cliente postgres nao existir.
    _bin("pg_dump")
    _bin("pg_restore")
    with tempfile.TemporaryFile(mode="w+b") as plain:
        _run_pg(
            "pg_dump",
            ["--format=custom", "--blobs", "--no-password"],
            stdout=plain,
        )
        if plain.tell() < 100:
            raise RuntimeError("Dump inesperadamente pequeno")
        _check_archive(plain)
        with tempfile.TemporaryFile(mode="w+b") as encrypted:
            digest, size = _encrypt(plain, encrypted)
            now = datetime.now(timezone.utc)
            key = (
                f"{PREFIX}{now:%Y/%m/%d}/"
                f"{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(12)}.sigeu"
            )
            client = get_client()
            client.upload_fileobj(
                encrypted, bucket_name(), key,
                ExtraArgs={
                    "ContentType": "application/octet-stream",
                    "Metadata": {
                        "sha256": digest,
                        "format": "pgdump-custom-aes256gcm-v1",
                    },
                },
            )
            head = client.head_object(Bucket=bucket_name(), Key=key)
            if int(head.get("ContentLength", -1)) != size:
                raise RuntimeError("Tamanho R2 divergente: backup nao validado")
            if head.get("Metadata", {}).get("sha256") != digest:
                raise RuntimeError("Hash R2 divergente: backup nao validado")
            return {"id": key, "bytes": size, "sha256": digest, "uploaded": True}


def verify_full_backup(key: str) -> dict:
    if not key.startswith(PREFIX) or not key.endswith(".sigeu"):
        raise ValueError("Chave R2 fora do prefixo de backup integral")
    _bin("pg_restore")
    _aes_key()
    response = get_client().get_object(Bucket=bucket_name(), Key=key)
    meta = response.get("Metadata", {})
    if meta.get("format") != "pgdump-custom-aes256gcm-v1":
        raise RuntimeError("Tipo de backup inesperado")
    with tempfile.TemporaryFile(mode="w+b") as encrypted:
        digest = hashlib.sha256()
        while True:
            chunk = response["Body"].read(CHUNK)
            if not chunk:
                break
            encrypted.write(chunk)
            digest.update(chunk)
        if digest.hexdigest() != meta.get("sha256"):
            raise RuntimeError("Hash SHA-256 divergente no R2")
        size = encrypted.tell()
        if size < len(MAGIC) + 12 + 16 + 100:
            raise RuntimeError("Backup truncado")
        encrypted.seek(0)
        if encrypted.read(len(MAGIC)) != MAGIC:
            raise RuntimeError("Cabecalho criptografico invalido")
        nonce = encrypted.read(12)
        encrypted.seek(size - 16)
        tag = encrypted.read(16)
        encrypted.seek(len(MAGIC) + 12)
        decryptor = Cipher(algorithms.AES(_aes_key()), modes.GCM(nonce, tag)).decryptor()
        remaining = size - len(MAGIC) - 12 - 16
        with tempfile.TemporaryFile(mode="w+b") as plain:
            while remaining:
                block = encrypted.read(min(CHUNK, remaining))
                if not block:
                    raise RuntimeError("Copia R2 incompleta")
                remaining -= len(block)
                plain.write(decryptor.update(block))
            plain.write(decryptor.finalize())
            _check_archive(plain)
        return {"id": key, "bytes": size, "verified": True, "archive_readable": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dump integral PostgreSQL -> R2 privado e criptografado")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", action="store_true")
    action.add_argument("--verify", metavar="R2_KEY")
    args = parser.parse_args()
    result = create_full_backup() if args.create else verify_full_backup(args.verify)
    print(json.dumps(result, ensure_ascii=False))
