"""Teste do pacote criptografado; nao toca em banco nem conta R2 reais."""
import ast
import base64
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import backup_sigeu as backup


class BackupSigeuTests(unittest.TestCase):
    def setUp(self):
        self.key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
        self.patch_key = patch.dict(os.environ, {"SIGEU_BACKUP_KEY": self.key})
        self.patch_key.start()

    def tearDown(self):
        self.patch_key.stop()

    def test_modulo_parseavel(self):
        ast.parse(Path("backup_sigeu.py").read_text("utf8"))
        ast.parse(Path("migrate.py").read_text("utf8"))

    def test_key_segura_obrigatoria(self):
        self.assertEqual(len(backup._aes_key()), 32)
        with patch.dict(os.environ, {"SIGEU_BACKUP_KEY": ""}):
            with self.assertRaises(RuntimeError):
                backup._aes_key()
        with patch.dict(os.environ, {"SIGEU_BACKUP_KEY": "a"}):
            with self.assertRaises(RuntimeError):
                backup._aes_key()

    def test_encriptacao_autenticada_sem_texto_claro(self):
        plain = b"PRIVATE - CPF 000.000.000-00 - contrato " * 700
        with tempfile.TemporaryFile(mode="w+b") as source, tempfile.TemporaryFile(mode="w+b") as target:
            source.write(plain)
            digest = backup._encrypt_to_file(source, target)
            encrypted = target.read()
        self.assertTrue(encrypted.startswith(backup.MAGIC))
        self.assertNotIn(b"CPF 000.000", encrypted)
        self.assertEqual(len(digest), 64)
        offset = len(backup.MAGIC)
        nonce = encrypted[offset:offset+12]
        body, tag = encrypted[offset+12:-16], encrypted[-16:]
        decryptor = Cipher(algorithms.AES(b"k"*32), modes.GCM(nonce, tag)).decryptor()
        self.assertEqual(decryptor.update(body) + decryptor.finalize(), plain)

    def test_arquivo_enviado_confere_tamanho_sem_expor_dados(self):
        class Client:
            def __init__(self):
                self.data = b""
            def upload_fileobj(self, obj, bucket, key, ExtraArgs):
                self.data = obj.read()
                self.key = key
                self.metadata = ExtraArgs["Metadata"]
            def head_object(self, Bucket, Key):
                return {"ContentLength": len(self.data)}
        client = Client()
        manifest = {
            "source_id": "testing1234",
            "tables": [{"name": "cobrancas_avulsas", "rows": 2}],
        }
        def tar():
            tmp = tempfile.TemporaryFile(mode="w+b")
            tmp.write(b"sigilo-contrato-cpf")
            tmp.seek(0)
            return tmp, manifest
        with patch.object(backup, "_export_tar_gz", side_effect=tar), \
             patch.object(backup, "is_configured", return_value=True), \
             patch.object(backup, "get_client", return_value=client), \
             patch.object(backup, "bucket_name", return_value="bucket-teste"):
            result = backup.create_backup()
        self.assertTrue(result["backup_id"].startswith(backup.PREFIX))
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["critical_counts"]["cobrancas_avulsas"], 2)
        self.assertNotIn(b"sigilo-contrato-cpf", client.data)
        self.assertEqual(client.metadata["sha256"], result["sha256"])


if __name__ == "__main__":
    unittest.main()
