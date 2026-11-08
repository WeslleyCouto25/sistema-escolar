"""Testes isolados da integração PicPay (sem chamadas externas ou DB real)."""
import ast
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

# db_pool é substituído antes do import; estes testes não acessam PostgreSQL.
fake_db = types.ModuleType("db_pool")
fake_db.get_db_connection = lambda: (_ for _ in ()).throw(AssertionError("DB não deve ser acessado"))
sys.modules["db_pool"] = fake_db

from flask import Flask
import picpay_mew as pp


class TestPicPayMew(unittest.TestCase):
    def test_sintaxe_python_do_projeto(self):
        for filename in ("app.py", "picpay_mew.py", "migrate.py"):
            with self.subTest(filename=filename):
                ast.parse(Path(filename).read_text(encoding="utf-8"), filename=filename)

    def test_conversao_decimal_exata(self):
        self.assertEqual(pp._centavos("506.92"), 50692)
        self.assertEqual(pp._centavos("649.90"), 64990)
        with self.assertRaises(ValueError):
            pp._centavos(0)

    def test_endpoint_sandbox_e_producao(self):
        with patch.dict(os.environ, {"PICPAY_ENV": "sandbox"}):
            self.assertEqual(pp._settings(), ("https://api.ms.qa.limbo.work", "/sandbox/v1"))
        with patch.dict(os.environ, {"PICPAY_ENV": "production"}):
            self.assertEqual(pp._settings(), ("https://ecommerce-api.svc.picpay.com", "/v1"))

    def test_criacao_envia_valor_em_centavos_e_maximo_parcelas(self):
        with patch.object(pp, "_api", return_value={
            "link": "https://link.picpay.com/p/abc123", "amount": 50692
        }) as api, patch.dict(os.environ, {"PICPAY_MAX_INSTALLMENTS": "8"}):
            link, link_id = pp._criar_link_picpay(
                "Aluno Teste", 123, 50692, "SE123456789ABC", "https://sigeueducacional.com.br/"
            )
        self.assertEqual(link_id, "abc123")
        self.assertIn("picpay.com", link)
        args, kwargs = api.call_args
        self.assertEqual(args[:2], ("POST", "/paymentlink/create"))
        self.assertEqual(kwargs["payload"]["charge"]["amounts"]["product"], 50692)
        self.assertEqual(kwargs["payload"]["options"]["card_max_installment_number"], 8)

    def test_webhook_sem_chave_valida_rejeita(self):
        app = Flask(__name__)
        app.register_blueprint(pp.picpay_bp)
        with patch.dict(os.environ, {"PICPAY_WEBHOOK_API_KEY": "secret-key"}):
            r = app.test_client().post("/webhook/picpay", json={"type": "PAYMENT"})
        self.assertEqual(r.status_code, 401)

    def test_pagamento_precisa_ser_validado_na_api_picpay(self):
        payload = {"originId": "link-123", "transactions": [
            {"transactionId": "tx-1", "status": "PAYED", "amount": 5000},
            {"transactionId": "tx-2", "status": "PENDING", "amount": 5000},
        ]}
        with patch.object(pp, "_api", return_value=payload):
            self.assertTrue(pp._confirmar_transacao("link-123", "tx-1", 5000))
            self.assertFalse(pp._confirmar_transacao("link-123", "tx-1", 4900))
            self.assertFalse(pp._confirmar_transacao("link-123", "tx-2", 5000))

    def test_cobranca_rejeita_link_sem_https(self):
        with patch.object(pp, "_api", return_value={
            "link": "http://inseguro.local/p/abc", "amount": 5000
        }):
            with self.assertRaises(pp.PicPayError):
                pp._criar_link_picpay("Aluno", 1, 5000, "SE1234", "https://exemplo.org/")


if __name__ == "__main__":
    unittest.main()
