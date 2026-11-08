"""Testes sem banco nem conta PicPay, para o fluxo de cobrança avulsa do MEW."""
import ast
from datetime import datetime, timezone
import io
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from PIL import Image, ImageDraw

fake_db = sys.modules.setdefault("db_pool", types.ModuleType("db_pool"))
fake_db.get_db_connection = lambda: (_ for _ in ()).throw(AssertionError("Banco real não permitido"))
fake_r2 = types.ModuleType("r2_storage")
fake_r2.is_configured = lambda: True
fake_r2.make_key = lambda *args: "/".join(str(p) for p in args)
fake_r2.upload_bytes = lambda *args, **kw: None
fake_r2.delete_object = lambda key: None
fake_r2.presigned_url = lambda key, **kw: "https://r2-exemplo.test/" + key
sys.modules["r2_storage"] = fake_r2

import cobrancas_avulsas as av


class FakeConnection:
    def __init__(self, obj):
        self.obj = obj
        self.executed = []
        self.rowcount = 1
        self.commits = 0
    def cursor(self):
        return self
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
    def fetchone(self):
        return self.obj
    def commit(self):
        self.commits += 1
    def rollback(self):
        pass
    def close(self):
        pass


def app_for_tests():
    app = Flask(__name__, template_folder="../templates")
    app.secret_key = "test-key-only"
    app.register_blueprint(av.cobrancas_avulsas_bp)
    app.testing = True
    return app


def example_item(status="aguardando_assinatura"):
    return {
        "id": 4,
        "token": "z" * 48,
        "nome": "Cliente Teste",
        "numero_contrato": "SIGEU-AV-TESTE",
        "servico": "Disciplina isolada",
        "cpf": "52998224725",
        "email": "cliente@example.com",
        "descricao": "Uma disciplina de 80 horas com prazo contratual específico.",
        "forma_cobranca": "link",
        "link_pagamento": "https://link.picpay.com/p/8ad7f361-96ee-45b9-85ce-c5e9b1598bce",
        "valor_centavos": 95984,
        "parcelas": 8,
        "juros": "sem_juros",
        "status": status,
        "contrato_html": "<!doctype html><html><body><main>Contrato pré-assinatura</main></body></html>",
        "contrato_sha256": "c" * 64,
        "assinado_em": datetime.now(timezone.utc) if status != "aguardando_assinatura" else None,
        "hash_aceite": "A" * 64,
        "foto_r2_key": "foto/teste.png" if status != "aguardando_assinatura" else None,
        "assinatura_r2_key": "assinatura/teste.png" if status != "aguardando_assinatura" else None,
        "ip_assinatura": "127.0.0.1",
        "assinatura_sha256": "b" * 64,
        "qr_r2_key": None,
    }


class TestsCobrancasAvulsas(unittest.TestCase):
    def test_modulos_sintaxe(self):
        for file in ("cobrancas_avulsas.py", "app.py", "migrate.py"):
            with self.subTest(file=file):
                ast.parse(Path(file).read_text(encoding="utf-8"), filename=file)

    def test_templates_jinja(self):
        app = app_for_tests()
        for filename in (
            "mew/cobrancas_avulsas.html", "contrato_cobranca_avulsa.html",
            "cobranca_avulsa_cliente.html",
        ):
            with self.subTest(filename=filename):
                app.jinja_env.get_template(filename)

    def test_cpf_com_digitos_verificadores(self):
        self.assertTrue(av._cpf_valido("52998224725"))
        self.assertFalse(av._cpf_valido("11111111111"))
        self.assertFalse(av._cpf_valido("52998224726"))

    def test_formatacao_e_valor_em_centavos(self):
        self.assertEqual(av._parse_value("R$ 959,84"), 95984)
        self.assertEqual(av._parse_value("959.84"), 95984)
        self.assertEqual(av._money(95984), "959,84")
        with self.assertRaises(ValueError):
            av._parse_value("0")

    def test_validar_link_real_picpay_e_impedir_redirecionamento_malicioso(self):
        url = "https://link.picpay.com/p/8ad7f361-96ee-45b9-85ce-c5e9b1598bce"
        self.assertEqual(av._picpay_url(url), url)
        for candidate in (
            "http://link.picpay.com/p/8ad7f361-96ee-45b9-85ce-c5e9b1598bce",
            "https://link.picpay.com.evil.com/p/8ad7f361-96ee-45b9-85ce-c5e9b1598bce",
            "https://evil.org/p/8ad7f361-96ee-45b9-85ce-c5e9b1598bce",
        ):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                av._picpay_url(candidate)

    def test_rejeitar_assinatura_vazia(self):
        blank = io.BytesIO()
        Image.new("RGBA", (440, 190), (0, 0, 0, 0)).save(blank, "PNG")
        self.assertFalse(av._assinatura_visivel(blank.getvalue()))
        good = Image.new("RGBA", (440, 190), (0, 0, 0, 0))
        ImageDraw.Draw(good).line((20, 30, 280, 115), fill=(10, 20, 30, 255), width=5)
        ink = io.BytesIO()
        good.save(ink, "PNG")
        self.assertTrue(av._assinatura_visivel(ink.getvalue()))

    def test_admin_requer_login(self):
        response = app_for_tests().test_client().get("/mew/cobrancas-avulsas")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/mew/login", response.location)

    def test_checkout_nao_exposto_antes_assinatura(self):
        obj = example_item()
        connection = FakeConnection(obj)
        with patch.object(av, "get_db_connection", return_value=connection):
            response = app_for_tests().test_client().get("/cobranca/" + obj["token"])
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Li o contrato e quero continuar", body)
        self.assertNotIn(obj["link_pagamento"], body)
        self.assertIn("signatureCanvas", body)

    def test_checkout_disponivel_apos_assinatura(self):
        obj = example_item(status="aguardando_pagamento")
        connection = FakeConnection(obj)
        with patch.object(av, "get_db_connection", return_value=connection):
            response = app_for_tests().test_client().get("/cobranca/" + obj["token"])
        self.assertEqual(response.status_code, 200)
        self.assertIn(obj["link_pagamento"], response.get_data(as_text=True))
        self.assertIn("iframe", response.get_data(as_text=True))
        self.assertNotIn('id="signatureCanvas"', response.get_data(as_text=True))

    def test_assinatura_registrada_com_hash_antes_de_liberar_pagamento(self):
        obj = example_item()
        connection = FakeConnection(obj)
        with (app_for_tests().test_request_context("/cobranca/" + obj["token"], method="POST"),
              patch.object(av, "get_db_connection", return_value=connection),
              patch.object(av, "r2_upload_bytes") as send):
            av._sign(obj, b"assinatura", "image/png", b"fotografia", "image/jpeg")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(connection.commits, 1)
        sql = " ".join(q for q, params in connection.executed)
        self.assertIn("status='aguardando_pagamento'", sql)
        self.assertIn("status='aguardando_assinatura'", sql)
        self.assertIn("hash_aceite", sql)

    def test_cliente_nao_consegue_confirmar_pagamento(self):
        response = app_for_tests().test_client().post(
            "/mew/cobrancas-avulsas/4/confirmar-pagamento",
            data={"valor_conferido": "959,84", "referencia_pagamento": "ABC123"}
        )
        self.assertEqual(response.status_code, 403)


    def test_http_aceite_foto_assinatura_gatilho_servidor(self):
        obj = example_item()
        app = app_for_tests()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["cobranca_avulsa_public_csrf"] = {obj["token"]: "teste-nonce"}
        sig = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
        ImageDraw.Draw(sig).line((20, 20, 300, 100), fill=(5, 5, 5, 255), width=6)
        sig_bytes = io.BytesIO()
        sig.save(sig_bytes, "PNG")
        foto = io.BytesIO()
        Image.new("RGB", (500, 500), "white").save(foto, "JPEG")
        with (
            patch.object(av, "get_db_connection", return_value=FakeConnection(obj)),
            patch.object(av, "_sign") as sign,
        ):
            result = client.post(
                "/cobranca/" + obj["token"],
                data={"csrf_token": "teste-nonce", "aceite_contrato": "1", "aceite_foto": "1",
                      "assinatura": (io.BytesIO(sig_bytes.getvalue()), "assinatura.png"),
                      "foto": (io.BytesIO(foto.getvalue()), "foto.jpg")},
                content_type="multipart/form-data",
            )
        self.assertEqual(result.status_code, 302)
        self.assertIn("/cobranca/", result.location)
        sign.assert_called_once()

    def test_http_sem_aceites_impede_avanco(self):
        obj = example_item()
        app = app_for_tests()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["cobranca_avulsa_public_csrf"] = {obj["token"]: "teste-nonce"}
        with (
            patch.object(av, "get_db_connection", return_value=FakeConnection(obj)),
            patch.object(av, "_sign") as sign,
        ):
            result = client.post(
                "/cobranca/" + obj["token"],
                data={"csrf_token": "teste-nonce"},
            )
        self.assertEqual(result.status_code, 200)
        sign.assert_not_called()
        self.assertNotIn(obj["link_pagamento"], result.get_data(as_text=True))


    def test_rascunho_nao_gera_contrato_publico(self):
        c = example_item(status="rascunho")
        c["contrato_html"] = ""
        with patch.object(av, "get_db_connection", return_value=FakeConnection(c)):
            response = app_for_tests().test_client().get("/cobranca/" + c["token"])
        self.assertEqual(response.status_code, 404)

    def test_rascunho_nao_divulga_link_antes_da_publicacao(self):
        c = example_item(status="rascunho")
        c["contrato_html"] = ""
        c["nome"] = ""
        c["cpf"] = ""
        c["email"] = ""
        db = FakeConnection(c)
        with patch.object(av, "get_db_connection", return_value=db):
            response = app_for_tests().test_client().get("/cobranca/" + c["token"] + "/contrato")
        self.assertEqual(response.status_code, 404)

    def test_modelo_financeiro_das_duas_cobrancas(self):
        # Evita incorreções no total e garante que a migração preserva o link fornecido.
        import inspect
        source = inspect.getsource(av.seed_rascunhos_cobranca_avulsa)
        self.assertIn("95984, 8, \"sem_juros\"", source)
        self.assertIn("50900, 1", source)
        self.assertIn("8ad7f361-96ee-45b9-85ce-c5e9b1598bce", source)
        self.assertEqual(av._money(95984 // 8), "119,98")
        self.assertIn("ON CONFLICT (numero_contrato) DO NOTHING", source)

    def test_editar_rascunho_sem_sessao_admin(self):
        res = app_for_tests().test_client().get("/mew/cobrancas-avulsas/4/editar")
        self.assertEqual(res.status_code, 403)

if __name__ == "__main__":
    unittest.main()
