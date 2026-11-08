"""Cobranças PicPay via Link de Pagamento para o dashboard MEW.

Isolado do funil público do Mercado Pago. Configuração no Render:
PICPAY_ENV=sandbox|production
PICPAY_CLIENT_ID=...
PICPAY_CLIENT_SECRET=...
PICPAY_WEBHOOK_API_KEY=... (a chave de URL de notificação no Painel Lojista)
PICPAY_MAX_INSTALLMENTS=8 (depende de habilitação e taxas no PicPay)

Fontes: https://developers-business.picpay.com/payment-link/docs/
"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hmac
import os
import re
import secrets
import threading
import time
from urllib.parse import urlparse

import requests
from flask import Blueprint, abort, jsonify, redirect, render_template_string, request, session

from db_pool import get_db_connection

picpay_bp = Blueprint("picpay_mew", __name__)
_token_lock = threading.Lock()
_access_token = {"value": None, "expires": 0.0}
_paid_callback = None


class PicPayError(RuntimeError):
    pass


def set_paid_callback(callback):
    """Recebe a função de e-mail já existente no sistema, sem import circular."""
    global _paid_callback
    _paid_callback = callback


def _settings():
    mode = os.getenv("PICPAY_ENV", "sandbox").lower().strip()
    if mode == "sandbox":
        return "https://api.ms.qa.limbo.work", "/sandbox/v1"
    if mode == "production":
        return "https://ecommerce-api.svc.picpay.com", "/v1"
    raise PicPayError("PICPAY_ENV deve ser sandbox ou production.")


def _credentials():
    client_id = os.getenv("PICPAY_CLIENT_ID")
    client_secret = os.getenv("PICPAY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise PicPayError("Credenciais PicPay ausentes no ambiente do servidor.")
    return client_id, client_secret


def _bearer_token():
    now = time.monotonic()
    with _token_lock:
        if _access_token["value"] and _access_token["expires"] > now + 20:
            return _access_token["value"]
        client_id, client_secret = _credentials()
        base, _ = _settings()
        try:
            response = requests.post(
                base + "/oauth2/token",
                json={"grant_type": "client_credentials",
                      "client_id": client_id, "client_secret": client_secret},
                headers={"Accept": "application/json"},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            token = payload.get("access_token")
            if not token:
                raise PicPayError("OAuth PicPay não retornou token.")
            ttl = max(60, int(payload.get("expires_in") or 300))
            _access_token.update(value=token, expires=time.monotonic() + ttl)
            return token
        except (requests.RequestException, ValueError) as exc:
            raise PicPayError("Falha na autenticação com PicPay.") from exc


def _api(method, endpoint, payload=None, params=None):
    base, version = _settings()
    try:
        resp = requests.request(
            method, base + version + endpoint,
            json=payload,
            params=params,
            headers={"Authorization": "Bearer " + _bearer_token(),
                     "Accept": "application/json",
                     "Content-Type": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        # Não imprimir corpo ou headers: podem conter dados e credenciais.
        raise PicPayError("Falha na comunicação com a API de cobrança PicPay.") from exc


def _centavos(valor):
    amount = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ValueError("Valor da cobrança precisa ser positivo.")
    return int(amount * 100)


def init_picpay_db():
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pagamentos_picpay (
                id SERIAL PRIMARY KEY,
                aluno_id INTEGER NOT NULL REFERENCES alunos(id),
                contrato_id INTEGER REFERENCES contratos_alunos(id),
                pedido TEXT UNIQUE NOT NULL,
                link_id TEXT UNIQUE NOT NULL,
                checkout_token TEXT UNIQUE NOT NULL,
                checkout_url TEXT NOT NULL,
                valor_centavos INTEGER NOT NULL CHECK (valor_centavos > 0),
                status TEXT NOT NULL DEFAULT 'pendente',
                transaction_id TEXT,
                criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                pago_em TIMESTAMPTZ,
                atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_picpay_aluno ON pagamentos_picpay(aluno_id)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _criar_link_picpay(nome, aluno_id, valor_centavos, pedido, retorno):
    max_parcelas = max(1, min(12, int(os.getenv("PICPAY_MAX_INSTALLMENTS", "8"))))
    data = {
        "charge": {
            "name": f"SIGEU - Aluno {aluno_id}",
            "description": f"Serviços educacionais SIGEU - {nome[:80]}",
            "order_number": pedido,
            "redirect_url": retorno,
            "payment": {"methods": ["BRCODE", "CREDIT_CARD"],
                        "brcode_arrangements": ["PICPAY", "PIX"]},
            "amounts": {"product": valor_centavos},
        },
        "options": {
            "allow_create_pix_key": True,
            "card_max_installment_number": max_parcelas,
        },
    }
    result = _api("POST", "/paymentlink/create", payload=data)
    link = result.get("link")
    link_id = urlparse(link or "").path.rstrip("/").split("/")[-1]
    if not link or not link_id or urlparse(link).scheme != "https":
        raise PicPayError("PicPay não retornou um link de pagamento válido.")
    if int(result.get("amount") or valor_centavos) != valor_centavos:
        raise PicPayError("PicPay retornou valor divergente; cobrança não registrada.")
    return link, link_id


def criar_cobranca_picpay(aluno_id, nome, email, valor_total, contrato_id=None):
    """Cria ou reaproveita a cobrança pendente de um aluno, pelo valor financeiro salvo."""
    if not os.getenv("PICPAY_WEBHOOK_API_KEY"):
        raise PicPayError("Configure a chave do webhook PicPay antes de gerar cobranças.")
    cents = _centavos(valor_total)
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, checkout_token, checkout_url, status, valor_centavos
            FROM pagamentos_picpay WHERE aluno_id=%s AND status='pendente'
            ORDER BY id DESC LIMIT 1
        """, (aluno_id,))
        previous = cur.fetchone()
        if previous and previous["valor_centavos"] == cents:
            return {"id": previous["id"], "pagina": "/pagamento/picpay/" + previous["checkout_token"],
                    "checkout_url": previous["checkout_url"]}
    finally:
        conn.close()

    pedido = "SE" + secrets.token_hex(6).upper()  # 14 caracteres, limite PicPay de 15
    token = secrets.token_urlsafe(24)
    origin = (os.getenv("PUBLIC_BASE_URL") or "https://sigeueducacional.com.br").rstrip("/")
    page = origin + "/pagamento/picpay/" + token
    checkout_url, link_id = _criar_link_picpay(nome, aluno_id, cents, pedido, page)

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO pagamentos_picpay
              (aluno_id, contrato_id, pedido, link_id, checkout_token, checkout_url, valor_centavos)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (aluno_id, contrato_id, pedido, link_id, token, checkout_url, cents))
        new_id = cur.fetchone()["id"]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"id": new_id, "pagina": "/pagamento/picpay/" + token,
            "checkout_url": checkout_url}


def ultima_cobranca_picpay(aluno_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, aluno_id, status, checkout_url, checkout_token, valor_centavos
            FROM pagamentos_picpay WHERE aluno_id=%s ORDER BY id DESC LIMIT 1
        """, (aluno_id,))
        return cur.fetchone()
    finally:
        conn.close()


@picpay_bp.get("/pagamento/picpay/<token>")
def pagina_pagamento_picpay(token):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,80}", token):
        abort(404)
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT pp.status, pp.valor_centavos, pp.checkout_url, a.nome, a.ra
            FROM pagamentos_picpay pp JOIN alunos a ON a.id=pp.aluno_id
            WHERE pp.checkout_token=%s
        """, (token,))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        abort(404)

    html = """<!doctype html>
    <html lang="pt-br"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="robots" content="noindex,nofollow">
    <title>Pagamento seguro | SIGEU Educacional</title>
    <style>
    body{margin:0;background:#f3f6fa;font-family:system-ui,Arial,sans-serif;color:#18283d}
    main{max-width:570px;margin:9vh auto;background:white;border:1px solid #dce5ee;
      border-radius:16px;padding:clamp(22px,5vw,40px);box-shadow:0 12px 38px #16253d16}
    small{color:#586679}h1{font-size:1.6rem;margin:12px 0}
    .total{font-size:2rem;font-weight:750;letter-spacing:-.03em}
    .campo{border-bottom:1px solid #e4e8ed;padding:12px 0}
    .btn{display:block;background:#006fba;color:white;text-decoration:none;
      padding:15px;border-radius:10px;text-align:center;font-weight:700;margin-top:24px}
    .obs{font-size:.87rem;color:#526071;line-height:1.55}
    </style></head><body><main>
      <small>SIGEU Educacional · Pagamento de matrícula</small>
      <h1>{{ 'Pagamento confirmado' if pago else 'Finalizar pagamento' }}</h1>
      <div class="campo"><small>Aluno</small><div>{{ nome }}</div></div>
      <div class="campo"><small>Matrícula / RA</small><div>{{ ra }}</div></div>
      <div class="campo"><small>Valor total</small><div class="total">R$ {{ valor }}</div></div>
      {% if pago %}
        <p>O pagamento consta como confirmado no sistema.</p>
      {% else %}
        <a class="btn" href="{{ checkout_url }}" rel="noopener noreferrer">Pagar com PicPay, Pix ou cartão</a>
        <p class="obs">O pagamento é processado no ambiente seguro do PicPay.
        A liberação acadêmica ocorre após confirmação pelo provedor, não apenas pelo retorno desta página.</p>
      {% endif %}
      <p class="obs">Em caso de dúvida, entre em contato com a secretaria da instituição.</p>
    </main></body></html>"""
    valor = "{:,.2f}".format(row["valor_centavos"] / 100).replace(",", "X").replace(".", ",").replace("X", ".")
    return render_template_string(html, nome=row["nome"], ra=row["ra"], valor=valor,
                                  pago=row["status"] == "pago", checkout_url=row["checkout_url"])


def _confirmar_transacao(link_id, transaction_id, cents):
    """Consulta servidor a servidor; não confia apenas no corpo do webhook."""
    result = _api("GET", "/paymentlink/" + link_id + "/transactions",
                  params={"status": "PAYED", "perPage": 100})
    if str(result.get("originId")) != str(link_id):
        return False
    for item in result.get("transactions", []):
        tid = item.get("transactionId") or item.get("id")
        if (str(tid) == str(transaction_id)
                and item.get("status") == "PAYED"
                and int(item.get("amount") or 0) == cents):
            return True
    return False


@picpay_bp.post("/webhook/picpay")
def webhook_picpay():
    expected = os.getenv("PICPAY_WEBHOOK_API_KEY", "").strip()
    received = request.headers.get("Authorization", "").strip()
    if received.lower().startswith("bearer "):
        received = received[7:].strip()
    if not expected or not hmac.compare_digest(expected, received):
        return jsonify(ok=False), 401
    if request.headers.get("event_type", "").lower() not in ("transactionpaymentmessage", ""):
        return jsonify(ok=True), 200
    event = request.get_json(silent=True) or {}
    data = event.get("data") or {}
    transaction = data.get("transaction") or {}
    charge = data.get("charge") or {}
    link_id = str(charge.get("paymentLinkId") or "")
    transaction_id = str(transaction.get("id") or "")
    status = transaction.get("status")
    if not link_id or not transaction_id:
        return jsonify(ok=True), 200
    if status not in ("PAYED", "REFUNDED", "PARTREFUNDED"):
        return jsonify(ok=True), 200

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM pagamentos_picpay WHERE link_id=%s", (link_id,))
        cob = cur.fetchone()
    finally:
        conn.close()
    if not cob:
        return jsonify(ok=True), 200

    if status != "PAYED":
        # Estorno requer conciliação manual; não desfazer a matrícula silenciosamente.
        return jsonify(ok=True, conciliacao="estorno_a_revisar"), 200
    if int(transaction.get("amount") or 0) != cob["valor_centavos"]:
        return jsonify(ok=False, erro="valor_divergente"), 409
    try:
        if not _confirmar_transacao(link_id, transaction_id, cob["valor_centavos"]):
            return jsonify(ok=False, erro="pagamento_nao_confirmado"), 409
    except PicPayError:
        return jsonify(ok=False), 503

    conn = get_db_connection()
    novo_pagamento = False
    aluno_id = cob["aluno_id"]
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, status FROM pagamentos_picpay WHERE link_id=%s FOR UPDATE", (link_id,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return jsonify(ok=True), 200
        if row["status"] != "pago":
            cur.execute("""
                UPDATE pagamentos_picpay SET status='pago', transaction_id=%s,
                    pago_em=NOW(), atualizado_em=NOW() WHERE id=%s
            """, (transaction_id, row["id"]))
            cur.execute("""
                UPDATE situacao_financeira
                SET status='pago', parcelas_pagas=parcelas_total,
                    forma_pagamento='picpay'
                WHERE id=(SELECT id FROM situacao_financeira
                          WHERE aluno_id=%s ORDER BY id DESC LIMIT 1)
            """, (aluno_id,))
            novo_pagamento = True
        conn.commit()
    except Exception:
        conn.rollback()
        return jsonify(ok=False), 500
    finally:
        conn.close()

    if novo_pagamento:
        # Evita que o mesmo link continue aceitando novas vendas após a primeira baixa.
        # Uma falha de inativação não desfaz um pagamento já confirmado.
        try:
            _api("POST", "/paymentlink/" + link_id + "/inactive")
        except PicPayError:
            pass
        if _paid_callback:
            try:
                _paid_callback(aluno_id, referencia="picpay:" + transaction_id + ":boas_vindas",
                               pagamento_id=transaction_id)
            except Exception:
                # Não reverter a confirmação financeira por falha no serviço de e-mail.
                pass
    return jsonify(ok=True), 200
