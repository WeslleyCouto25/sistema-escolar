"""Cobrança avulsa do MEW: contrato e evidências ANTES de expor pagamento.

Links PicPay e QR Codes são fornecidos pelo operador: NÃO se deve marcar como
pago automaticamente a partir de retorno, clique ou captura de tela.
O fluxo não cria matrícula nem altera os pagamentos dos alunos existentes.
"""
import hashlib
import io
import json
import re
import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import (
    Blueprint, abort, current_app, redirect, render_template, request,
    session, url_for,
)
from PIL import Image, ImageOps, UnidentifiedImageError

from db_pool import get_db_connection
from r2_storage import (
    is_configured as r2_is_configured, make_key,
    upload_bytes as r2_upload_bytes, delete_object,
    presigned_url as r2_presigned_url,
)

cobrancas_avulsas_bp = Blueprint("cobrancas_avulsas", __name__)
_VERSION = "2026.1"
_MAX_BODY = 9_000_000
_MAX_PHOTO = 3_000_000
_MAX_SIGNATURE = 1_500_000
_MAX_QR = 3_000_000
_TIMEZONE = ZoneInfo("America/Sao_Paulo")

TERMO_CONTRATO = (
    "Declaro que tive acesso ao instrumento contratual individualizado, "
    "li seu conteúdo e concordo com as condições, o objeto, o valor total "
    "e as condições de pagamento nele registrados."
)
TERMO_FOTO = (
    "Autorizo a coleta e o armazenamento desta fotografia exclusivamente "
    "como evidência da celebração eletrônica deste contrato. "
    "A fotografia não será utilizada nesta etapa para identificação facial automatizada."
)


def init_cobrancas_avulsas_db():
    """Executar em migrate.py, antes da inicialização do Gunicorn."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cobrancas_avulsas (
                id BIGSERIAL PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                numero_contrato TEXT UNIQUE NOT NULL,
                nome TEXT NOT NULL,
                cpf TEXT NOT NULL,
                email TEXT NOT NULL,
                telefone TEXT,
                endereco TEXT,
                servico TEXT NOT NULL,
                descricao TEXT NOT NULL,
                valor_centavos BIGINT NOT NULL CHECK (valor_centavos > 0),
                parcelas SMALLINT NOT NULL CHECK (parcelas BETWEEN 1 AND 12),
                juros TEXT NOT NULL CHECK (juros IN ('sem_juros','conforme_checkout')),
                forma_cobranca TEXT NOT NULL CHECK (forma_cobranca IN ('link','qr')),
                link_pagamento TEXT,
                qr_r2_key TEXT,
                status TEXT NOT NULL DEFAULT 'aguardando_assinatura',
                contrato_html TEXT NOT NULL,
                contrato_sha256 TEXT NOT NULL,
                versao TEXT NOT NULL,
                assinatura_r2_key TEXT,
                foto_r2_key TEXT,
                assinatura_sha256 TEXT,
                foto_sha256 TEXT,
                hash_aceite TEXT,
                ip_assinatura TEXT,
                user_agent_assinatura TEXT,
                aceitou_contrato BOOLEAN NOT NULL DEFAULT FALSE,
                aceitou_foto BOOLEAN NOT NULL DEFAULT FALSE,
                criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                assinado_em TIMESTAMPTZ,
                pago_em TIMESTAMPTZ,
                referencia_pagamento TEXT,
                observacao_pagamento TEXT,
                atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cob_avulsas_data ON cobrancas_avulsas(id DESC)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _admin():
    return bool(session.get("mew_admin"))


def _csrf():
    token = session.get("cobranca_avulsa_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["cobranca_avulsa_csrf"] = token
    return token


def _check_csrf():
    if not secrets.compare_digest(
        str(session.get("cobranca_avulsa_csrf") or ""),
        str(request.form.get("csrf_token") or ""),
    ) or not session.get("cobranca_avulsa_csrf"):
        abort(403, "Formulário inválido. Atualize a página e tente novamente.")


def _money(cents):
    return f"{cents / 100:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")


def _parse_value(raw):
    raw = (raw or "").strip().replace("R$", "").replace(" ", "")
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        val = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("Informe um valor válido em reais.")
    if val <= 0 or val > Decimal("999999.99"):
        raise ValueError("O valor precisa ser positivo e inferior a R$ 1.000.000.")
    return int(val * 100)


def _picpay_url(raw):
    parsed = urlparse((raw or "").strip())
    if (parsed.scheme != "https" or parsed.hostname != "link.picpay.com"
        or parsed.username or parsed.password or parsed.port
        or not re.fullmatch(r"/p/[A-Za-z0-9-]{15,120}/?", parsed.path)
        or parsed.fragment):
        raise ValueError("Use um endereço oficial https://link.picpay.com/p/... .")
    return parsed.geturl()


def _verified_image(data, allowed, max_bytes, destination="PNG"):
    if not data or len(data) > max_bytes:
        raise ValueError("Imagem vazia ou acima do tamanho permitido.")
    try:
        Image.MAX_IMAGE_PIXELS = 18_000_000
        image = Image.open(io.BytesIO(data))
        if image.format not in allowed:
            raise ValueError("Formato da imagem não autorizado.")
        image = ImageOps.exif_transpose(image)
        if image.width < 20 or image.height < 20 or image.width * image.height > 18_000_000:
            raise ValueError("Dimensões de imagem inválidas.")
        image.thumbnail((1800, 1800))
        converted = image.convert("RGBA" if destination == "PNG" else "RGB")
        out = io.BytesIO()
        converted.save(out, format=destination, optimize=True)
        result = out.getvalue()
        if len(result) > max_bytes:
            raise ValueError("Imagem processada acima do tamanho permitido.")
        return result, ("image/png" if destination == "PNG" else "image/jpeg")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Imagem inválida. Envie uma foto PNG, JPEG ou WebP.") from exc


def _get(token):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{30,100}", token):
        abort(404)
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM cobrancas_avulsas WHERE token=%s", (token,))
        item = cur.fetchone()
    finally:
        conn.close()
    if not item:
        abort(404)
    return item


def _get_admin(id_):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM cobrancas_avulsas WHERE id=%s", (id_,))
        item = cur.fetchone()
    finally:
        conn.close()
    if not item:
        abort(404)
    return item


def _contract_data(c):
    parcelas = int(c["parcelas"])
    cents = int(c["valor_centavos"])
    now = datetime.now(_TIMEZONE)
    return {
        "c": c, "valor": _money(cents),
        "parcela": _money((Decimal(cents) / Decimal(parcelas)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        "timestamp": now.strftime("%d/%m/%Y %H:%M"),
        "versao": _VERSION,
        "valor_extenso": "",
    }


def _create_admin():
    nome = (request.form.get("nome") or "").strip()
    cpf = re.sub(r"\D", "", request.form.get("cpf") or "")
    email = (request.form.get("email") or "").strip().lower()
    telefone = (request.form.get("telefone") or "").strip()
    endereco = (request.form.get("endereco") or "").strip()
    servico = (request.form.get("servico") or "").strip()
    descricao = (request.form.get("descricao") or "").strip()
    forma = (request.form.get("forma_cobranca") or "").strip()
    juros = (request.form.get("juros") or "").strip()
    numero = (request.form.get("numero_contrato") or "").strip().upper()
    if not numero:
        numero = "SIGEU-AV-" + secrets.token_hex(5).upper()
    if not re.fullmatch(r"[A-Z0-9-]{5,48}", numero):
        raise ValueError("Número contratual inválido (use letras, números e hífen).")
    if len(nome) < 5 or len(nome) > 160 or len(cpf) != 11 or "@" not in email or len(email) > 180:
        raise ValueError("Informe nome completo, CPF de 11 dígitos e e-mail válido.")
    if not 5 <= len(servico) <= 150 or not 8 <= len(descricao) <= 1800:
        raise ValueError("Informe o serviço e descreva o objeto contratado.")
    if len(telefone) > 40 or len(endereco) > 300:
        raise ValueError("Telefone ou endereço excessivamente longo.")
    cents = _parse_value(request.form.get("valor"))
    try:
        parcelas = int(request.form.get("parcelas") or "1")
    except ValueError:
        raise ValueError("Número de parcelas inválido.")
    if parcelas < 1 or parcelas > 12 or juros not in ("sem_juros", "conforme_checkout"):
        raise ValueError("Parcelamento inválido.")
    if forma not in ("link", "qr"):
        raise ValueError("Escolha Link PicPay ou QR Code.")
    if not r2_is_configured():
        raise ValueError("É necessário configurar o Cloudflare R2 antes de gerar contratos com foto.")
    link = None
    qr_key = None
    if forma == "link":
        link = _picpay_url(request.form.get("link_pagamento"))
    else:
        upload = request.files.get("qr_arquivo")
        raw = upload.read(_MAX_QR + 1) if upload else b""
        cleaned, mime = _verified_image(raw, {"PNG", "JPEG", "WEBP"}, _MAX_QR)
        qr_key = make_key("cobrancas-avulsas/qrcode", "codigo.png", secrets.token_hex(10))
        r2_upload_bytes(cleaned, qr_key, mime, {"tipo": "qr_pagamento"})
    token = secrets.token_urlsafe(36)
    c = {
        "nome": nome, "cpf": cpf, "email": email, "telefone": telefone,
        "endereco": endereco, "servico": servico, "descricao": descricao,
        "numero_contrato": numero, "valor_centavos": cents, "parcelas": parcelas,
        "juros": juros, "forma_cobranca": forma,
    }
    html = render_template("contrato_cobranca_avulsa.html", **_contract_data(c))
    contrato_sha = hashlib.sha256(html.encode("utf-8")).hexdigest()
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO cobrancas_avulsas
              (token,numero_contrato,nome,cpf,email,telefone,endereco,servico,descricao,
               valor_centavos,parcelas,juros,forma_cobranca,link_pagamento,qr_r2_key,
               contrato_html,contrato_sha256,versao)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (token, numero, nome, cpf, email, telefone, endereco, servico,
              descricao, cents, parcelas, juros, forma, link, qr_key,
              html, contrato_sha, _VERSION))
        conn.commit()
    except Exception:
        conn.rollback()
        if qr_key:
            try:
                delete_object(qr_key)
            except Exception:
                pass
        raise
    finally:
        conn.close()
    return token


@cobrancas_avulsas_bp.route("/mew/cobrancas-avulsas", methods=["GET", "POST"])
def administrar_cobrancas():
    if not _admin():
        return redirect("/mew/login")
    erro = None
    if request.method == "POST":
        _check_csrf()
        if request.form.get("acao") == "criar":
            try:
                token = _create_admin()
                return redirect(url_for(".administrar_cobrancas", criada=token))
            except (ValueError, OSError):
                erro = "Revise os dados, o link e a imagem. Não foi possível gerar a cobrança."
                current_app.logger.exception("Validação/armazenamento de cobrança avulsa")
            except Exception:
                erro = "Não foi possível salvar. Verifique se o número do contrato já existe."
                current_app.logger.exception("Falha ao gerar cobrança avulsa")
        else:
            abort(400)
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id,token,numero_contrato,nome,valor_centavos,parcelas,juros,
                   forma_cobranca,status,criado_em,assinado_em,pago_em,referencia_pagamento
            FROM cobrancas_avulsas ORDER BY id DESC LIMIT 60
        """)
        rows = cur.fetchall()
    finally:
        conn.close()
    base = (request.url_root or "https://sigeueducacional.com.br/").rstrip("/")
    return render_template(
        "mew/cobrancas_avulsas.html", itens=rows, erro=erro,
        csrf_token=_csrf(), criada=request.args.get("criada"),
        base_url=base, money=_money,
    )


@cobrancas_avulsas_bp.post("/mew/cobrancas-avulsas/<int:id_>/confirmar-pagamento")
def confirmar_manual(id_):
    if not _admin():
        abort(403)
    _check_csrf()
    item = _get_admin(id_)
    if item["status"] != "aguardando_pagamento":
        abort(409, "O contrato precisa estar assinado e ainda não quitado.")
    if _parse_value(request.form.get("valor_conferido")) != item["valor_centavos"]:
        abort(409, "O valor conferido não coincide com o valor contratual.")
    ref = (request.form.get("referencia_pagamento") or "").strip()
    if len(ref) < 5 or len(ref) > 120:
        abort(400, "Informe um identificador verificável da transação.")
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE cobrancas_avulsas SET status='pago',pago_em=NOW(),
                referencia_pagamento=%s,observacao_pagamento=%s,atualizado_em=NOW()
            WHERE id=%s AND status='aguardando_pagamento'
        """, (ref, "Baixa manual após conferência do administrador", id_))
        if cur.rowcount != 1:
            conn.rollback()
            abort(409)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for(".administrar_cobrancas"))


@cobrancas_avulsas_bp.get("/mew/cobrancas-avulsas/<int:id_>/evidencia/<tipo>")
def evidencia_admin(id_, tipo):
    if not _admin():
        abort(403)
    item = _get_admin(id_)
    keys = {"foto": "foto_r2_key", "assinatura": "assinatura_r2_key", "qr": "qr_r2_key"}
    if tipo not in keys or not item.get(keys[tipo]):
        abort(404)
    return redirect(r2_presigned_url(item[keys[tipo]], inline=True))


@cobrancas_avulsas_bp.get("/mew/cobrancas-avulsas/<int:id_>/contrato")
def contrato_admin(id_):
    if not _admin():
        abort(403)
    item = _get_admin(id_)
    return _contrato_documento(item)


@cobrancas_avulsas_bp.get("/cobranca/<token>/contrato")
def contrato_publico(token):
    return _contrato_documento(_get(token))


def _contrato_documento(item):
    # O HTML assinado é um snapshot da proposta tal como apresentada ao contratante.
    from markupsafe import Markup
    extra = ""
    if item["assinado_em"]:
        stamp = item["assinado_em"].astimezone(_TIMEZONE).strftime("%d/%m/%Y %H:%M:%S")
        # Evidências não são inseridas na página pública; só na área autenticada do MEW.
        extra = (
            "<section style='border:1px solid #9ca3af;padding:16px;margin:22px auto;max-width:900px'>"
            "<strong>ACEITE ELETRÔNICO REGISTRADO</strong><br>"
            "Data/hora: " + stamp + "<br>Hash do aceite: " + (item["hash_aceite"] or "") +
            "<br>As evidências fotográfica e de assinatura estão preservadas em armazenamento privado.</section>"
        )
    response = current_app.response_class(
        item["contrato_html"] + extra,
        mimetype="text/html; charset=utf-8",
    )
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


@cobrancas_avulsas_bp.route("/cobranca/<token>", methods=["GET", "POST"])
def pagina_cliente(token):
    item = _get(token)
    erro = None
    if request.method == "POST":
        if item["status"] != "aguardando_assinatura":
            return redirect(url_for(".pagina_cliente", token=token))
        if (request.content_length or 0) > _MAX_BODY:
            abort(413)
        expected = session.get("cobranca_avulsa_public_csrf", {}).get(token)
        if not expected or not secrets.compare_digest(
            expected, (request.form.get("csrf_token") or "")
        ):
            abort(403, "Sessão de assinatura expirada. Reabra a página.")
        if request.form.get("aceite_contrato") != "1" or request.form.get("aceite_foto") != "1":
            erro = "É necessário aceitar o contrato e autorizar o registro fotográfico."
        else:
            try:
                sig_file = request.files.get("assinatura")
                photo_file = request.files.get("foto")
                signature = sig_file.read(_MAX_SIGNATURE + 1) if sig_file else b""
                photo = photo_file.read(_MAX_PHOTO + 1) if photo_file else b""
                sig_clean, sig_mime = _verified_image(
                    signature, {"PNG", "JPEG"}, _MAX_SIGNATURE, "PNG"
                )
                photo_clean, photo_mime = _verified_image(
                    photo, {"PNG", "JPEG", "WEBP"}, _MAX_PHOTO, "JPEG"
                )
                _sign(item, sig_clean, sig_mime, photo_clean, photo_mime)
                return redirect(url_for(".pagina_cliente", token=token))
            except (ValueError, OSError):
                erro = "Fotografia ou assinatura inválida; revise as imagens e tente novamente."
            except Exception:
                current_app.logger.exception("Erro no registro de assinatura da cobrança %s", item["id"])
                erro = "Não foi possível registrar as evidências. Tente novamente."
    nonce = session.setdefault("cobranca_avulsa_public_csrf", {})
    # Um token de sessão para a cobrança atual. Não manter sessões ilimitadas no cookie.
    if token not in nonce:
        session["cobranca_avulsa_public_csrf"] = {token: secrets.token_urlsafe(24)}
    csrf = session["cobranca_avulsa_public_csrf"][token]
    return render_template(
        "cobranca_avulsa_cliente.html", c=item, erro=erro,
        csrf_token=csrf, money=_money,
        termo_contrato=TERMO_CONTRATO, termo_foto=TERMO_FOTO,
    )


def _sign(c, signature, signature_mime, photo, photo_mime):
    if not r2_is_configured():
        raise RuntimeError("Armazenamento privado indisponível.")
    hash_sig = hashlib.sha256(signature).hexdigest()
    hash_photo = hashlib.sha256(photo).hexdigest()
    stamp = datetime.now(_TIMEZONE).isoformat(timespec="seconds")
    ip = (request.remote_addr or "")[:60]
    agent = (request.headers.get("User-Agent") or "")[:512]
    metadata = json.dumps({
        "numero_contrato": c["numero_contrato"], "contrato_sha256": c["contrato_sha256"],
        "assinatura_sha256": hash_sig, "foto_sha256": hash_photo, "data": stamp,
        "ip": ip, "user_agent": agent, "aceite_contrato": TERMO_CONTRATO,
        "aceite_foto": TERMO_FOTO, "versao": _VERSION,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    hash_aceite = hashlib.sha256(metadata.encode("utf-8")).hexdigest().upper()
    key_sig = make_key("cobrancas-avulsas/assinaturas", "assinatura.png", c["id"])
    key_foto = make_key("cobrancas-avulsas/fotos", "foto.jpg", c["id"])
    uploaded = []
    try:
        r2_upload_bytes(signature, key_sig, signature_mime,
                        {"contrato": str(c["id"]), "sha256": hash_sig})
        uploaded.append(key_sig)
        r2_upload_bytes(photo, key_foto, photo_mime,
                        {"contrato": str(c["id"]), "sha256": hash_photo})
        uploaded.append(key_foto)
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE cobrancas_avulsas
                SET assinatura_r2_key=%s,foto_r2_key=%s,
                    assinatura_sha256=%s,foto_sha256=%s,
                    hash_aceite=%s,ip_assinatura=%s,user_agent_assinatura=%s,
                    aceitou_contrato=TRUE,aceitou_foto=TRUE,
                    status='aguardando_pagamento',assinado_em=NOW(),atualizado_em=NOW()
                WHERE id=%s AND status='aguardando_assinatura'
            """, (key_sig, key_foto, hash_sig, hash_photo, hash_aceite, ip, agent, c["id"]))
            if cur.rowcount != 1:
                conn.rollback()
                abort(409, "O contrato já foi assinado.")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        for key in uploaded:
            try:
                delete_object(key)
            except Exception:
                pass
        raise


@cobrancas_avulsas_bp.get("/cobranca/<token>/qrcode")
def qrcode_cliente(token):
    item = _get(token)
    if item["status"] not in ("aguardando_pagamento", "pago") or not item["qr_r2_key"]:
        abort(403)
    return redirect(r2_presigned_url(item["qr_r2_key"], inline=True))


@cobrancas_avulsas_bp.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
