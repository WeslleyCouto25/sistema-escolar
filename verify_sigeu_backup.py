"""Auditoria offline de snapshot criptografado SIGEU. NUNCA grava no PostgreSQL."""
import argparse
import hashlib
import json
import tempfile
import tarfile

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from backup_sigeu import MAGIC, CHUNK, _aes_key
from r2_storage import bucket_name, get_client


def verify_backup(key):
    if not key.startswith("backups-sigeu/v1/") or not key.endswith(".sigeu"):
        raise ValueError("Identificador de backup nao autorizado")
    with tempfile.TemporaryFile(mode="w+b") as encrypted, tempfile.TemporaryFile(mode="w+b") as plain:
        client = get_client()
        response = client.get_object(Bucket=bucket_name(), Key=key)
        expected = response.get("Metadata", {}).get("sha256")
        digest = hashlib.sha256()
        while True:
            chunk = response["Body"].read(CHUNK)
            if not chunk:
                break
            encrypted.write(chunk)
            digest.update(chunk)
        if not expected or digest.hexdigest() != expected:
            raise RuntimeError("SHA-256 do arquivo cifrado divergente")
        file_size = encrypted.tell()
        if file_size <= len(MAGIC) + 12 + 16:
            raise RuntimeError("Arquivo incompleto")
        encrypted.seek(0)
        if encrypted.read(len(MAGIC)) != MAGIC:
            raise RuntimeError("Formato de backup desconhecido")
        nonce = encrypted.read(12)
        encrypted.seek(file_size - 16)
        tag = encrypted.read(16)
        encrypted.seek(len(MAGIC) + 12)
        decryptor = Cipher(algorithms.AES(_aes_key()), modes.GCM(nonce, tag)).decryptor()
        remaining = file_size - len(MAGIC) - 12 - 16
        while remaining:
            chunk = encrypted.read(min(CHUNK, remaining))
            if not chunk:
                raise RuntimeError("Ciphertext incompleto")
            remaining -= len(chunk)
            plain.write(decryptor.update(chunk))
        plain.write(decryptor.finalize())
        plain.seek(0)
        with tarfile.open(fileobj=plain, mode="r:gz") as tar:
            member = tar.getmember("manifest.json")
            manifest = json.load(tar.extractfile(member))
            if manifest.get("format") != "sigeu-pg-csv-v1":
                raise RuntimeError("Manifesto inesperado")
            for entry in manifest.get("tables", []):
                m = tar.getmember(entry["file"])
                if not m.isfile():
                    raise RuntimeError("Arquivo tabular ausente")
                h = hashlib.sha256()
                stream = tar.extractfile(m)
                while True:
                    chunk = stream.read(CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
                if h.hexdigest() != entry["csv_sha256"]:
                    raise RuntimeError("CSV divergente: " + entry["name"])
            return {
                "database_id": manifest["source_id"],
                "created_utc": manifest["created_utc"],
                "tables": len(manifest["tables"]),
                "rows": sum(t["rows"] for t in manifest["tables"]),
                "verified": True,
            }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verifica hash, AES-GCM e integridade dos CSV no R2.")
    parser.add_argument("key", help="Chave privada do arquivo no Cloudflare R2")
    args = parser.parse_args()
    print(json.dumps(verify_backup(args.key), indent=2))
