from pydoc import html
import re
from werkzeug.utils import secure_filename
import json
import time
import random
from flask import flash
import string
from datetime import datetime
import hashlib
import random
from datetime import datetime
from flask import Flask, render_template, render_template_string, request, redirect, session, url_for, jsonify, flash, send_file
import os
import secrets
import mercadopago
from werkzeug.security import generate_password_hash, check_password_hash
import hashlib
import secrets
import qrcode
import qrcode.image.svg
import base64
from io import BytesIO
from markupsafe import escape
from pathlib import Path
import hashlib
import json
import plano_ensino
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

from api_planos import planos_bp
app.register_blueprint(planos_bp, url_prefix='/api')

import os
import tempfile
import threading
import psycopg2
from openpyxl import Workbook, load_workbook

from db_pool import get_db_connection, pool_stats
from perf_monitor import install_perf_monitor, current_rss_mb
from pdf_tools import render_html_to_pdf_file, render_html_to_pdf_bytes, merge_pdf_files
from r2_storage import (
    R2NotConfigured, decode_data_url, delete_object, extension_for_mime,
    guess_content_type, is_configured as r2_is_configured, make_key,
    presigned_url as r2_presigned_url, upload_bytes as r2_upload_bytes,
    upload_fileobj as r2_upload_fileobj, upload_path as r2_upload_path,
)

# Evita uploads ilimitados. O arquivo é transmitido para o R2 sem ser transformado em base64.
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024
install_perf_monitor(app)

# Bloqueio barato de scanners conhecidos antes de abrir conexão com o banco.
_SCANNER_PATH_RE = re.compile(
    r"(?:^|/)(?:\.env(?:\.|$)|\.git(?:/|$)|phpinfo(?:\.php)?$|wp-config(?:\.php)?|"
    r"service-account\.json$|credentials\.json$|gcp-(?:key|credentials)\.json$|firebase-(?:key|adminsdk)\.json$|"
    r"_profiler(?:/|$)|_ignition(?:/|$)|server-status(?:\.php)?$)", re.I
)

@app.before_request
def bloquear_scanners_comuns():
    caminho = request.path or "/"
    if _SCANNER_PATH_RE.search(caminho):
        return "Not Found", 404


@app.errorhandler(413)
def arquivo_grande_demais(_erro):
    limite = int(os.getenv("MAX_UPLOAD_MB", "50"))
    return f"Arquivo acima do limite permitido de {limite} MB.", 413


@app.route("/mew/diagnostico-recursos")
def mew_diagnostico_recursos():
    """Diagnóstico somente leitura para memória, pool, R2 e tamanho do PostgreSQL."""
    if not session.get("mew_admin"):
        return jsonify({"error": "Não autorizado"}), 403

    diagnostico = {
        "rss_mb": current_rss_mb(),
        "db_pool": pool_stats(),
        "r2_configurado": r2_is_configured(),
        "max_upload_mb": int(os.getenv("MAX_UPLOAD_MB", "50")),
        "cache_aplicacao": "não configurado (sem Redis/Flask-Caching/cachetools)",
    }
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT ROUND(pg_database_size(current_database()) / 1048576.0, 2) AS mb")
        diagnostico["database_mb"] = float((cursor.fetchone() or {}).get("mb") or 0)
        cursor.execute("""
            SELECT relname AS tabela,
                   ROUND(pg_total_relation_size(relid) / 1048576.0, 2) AS mb
            FROM pg_catalog.pg_statio_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            LIMIT 12
        """)
        diagnostico["maiores_tabelas"] = [
            {"tabela": r["tabela"], "mb": float(r.get("mb") or 0)} for r in cursor.fetchall()
        ]
        cursor.execute("""
            SELECT
              (SELECT COUNT(*) FROM contratos_alunos
                 WHERE assinatura_base64 IS NOT NULL OR foto_assinatura_base64 IS NOT NULL OR pdf_assinado IS NOT NULL) AS contratos_legados,
              (SELECT COUNT(*) FROM solicitacoes_documentos_integrados
                 WHERE pdf_previa IS NOT NULL OR pdf_final IS NOT NULL) AS documentos_integrados_legados,
              (SELECT COUNT(*) FROM projetos_finais
                 WHERE arquivo_path IS NOT NULL OR arquivo_atividade_path IS NOT NULL) AS projetos_locais_legados
        """)
        legado = cursor.fetchone() or {}
        diagnostico["armazenamento_legado_pendente"] = dict(legado)
    except Exception as exc:
        diagnostico["database_diagnostico_erro"] = str(exc)
    finally:
        conn.close()
    return jsonify(diagnostico)


def _normalizar_resposta_correta(valor):
    texto = str(valor or "").strip().upper()
    mapa = {"1": "A", "2": "B", "3": "C", "4": "D"}
    texto = mapa.get(texto, texto)
    if texto.startswith("A"):
        return "A"
    if texto.startswith("B"):
        return "B"
    if texto.startswith("C"):
        return "C"
    if texto.startswith("D"):
        return "D"
    raise ValueError(f"Resposta correta inválida: {valor!r}. Use A, B, C ou D.")


def _questao_interna(pergunta, a, b, c, d, resposta):
    pergunta = str(pergunta or "").strip()
    opcoes = [str(x or "").strip() for x in (a, b, c, d)]
    if not pergunta or not all(opcoes):
        raise ValueError("Cada questão precisa de pergunta e das quatro alternativas A, B, C e D.")
    return {
        "pergunta": pergunta,
        "opcoes": {"A": opcoes[0], "B": opcoes[1], "C": opcoes[2], "D": opcoes[3]},
        "resposta_certa": _normalizar_resposta_correta(resposta),
    }


def parse_questoes_texto(texto):
    """Aceita JSON antigo ou linhas coladas do Excel/Sheets em 6 colunas."""
    texto = (texto or "").strip()
    if not texto:
        return []
    # Compatibilidade: conteúdos antigos em JSON continuam funcionando.
    if texto[:1] in "[{":
        dados = json.loads(texto)
        if isinstance(dados, dict):
            dados = dados.get("questoes", [])
        if not isinstance(dados, list):
            raise ValueError("O conteúdo JSON precisa ser uma lista de questões.")
        saida = []
        for q in dados:
            op = q.get("opcoes") if isinstance(q, dict) else None
            if isinstance(op, dict):
                saida.append(_questao_interna(q.get("pergunta"), op.get("A"), op.get("B"), op.get("C"), op.get("D"), q.get("resposta_certa") or q.get("resposta_correta")))
            elif isinstance(q, dict):
                saida.append(_questao_interna(q.get("pergunta"), q.get("opcao_a"), q.get("opcao_b"), q.get("opcao_c"), q.get("opcao_d"), q.get("resposta_correta") or q.get("resposta_certa")))
        return saida

    linhas = [l for l in texto.replace("\r\n", "\n").replace("\r", "\n").split("\n") if l.strip()]
    saida = []
    for n, linha in enumerate(linhas, 1):
        partes = linha.split("\t")
        if len(partes) < 6 and ";" in linha:
            partes = [x.strip() for x in linha.split(";")]
        if len(partes) < 6:
            raise ValueError(f"Linha {n}: cole 6 colunas: Pergunta | A | B | C | D | Resposta.")
        if n == 1 and str(partes[0]).strip().lower() in {"pergunta", "questão", "questao"}:
            continue
        saida.append(_questao_interna(*partes[:6]))
    return saida


def questoes_xlsx_upload(arquivo):
    if not arquivo or not getattr(arquivo, "filename", ""):
        return []
    wb = load_workbook(arquivo.stream, read_only=True, data_only=True)
    try:
        ws = wb.active
        saida = []
        for n, row in enumerate(ws.iter_rows(values_only=True), 1):
            vals = list(row[:6])
            if not any(v not in (None, "") for v in vals):
                continue
            if len(vals) < 6:
                vals += [None] * (6-len(vals))
            if n == 1 and str(vals[0] or "").strip().lower() in {"pergunta", "questão", "questao"}:
                continue
            saida.append(_questao_interna(*vals[:6]))
        return saida
    finally:
        wb.close()


def questoes_para_tabela(questoes_json):
    try:
        dados = json.loads(questoes_json or "[]")
    except Exception:
        return ""
    linhas = []
    for q in dados if isinstance(dados, list) else []:
        op = q.get("opcoes") or {}
        vals = [q.get("pergunta", ""), op.get("A", ""), op.get("B", ""), op.get("C", ""), op.get("D", ""), q.get("resposta_certa") or q.get("resposta_correta") or ""]
        linhas.append("\t".join(str(v).replace("\t", " ").replace("\n", " ") for v in vals))
    return "\n".join(linhas)


def _hash_e_rebobinar(fileobj):
    h = hashlib.sha256()
    try:
        fileobj.seek(0)
    except Exception:
        pass
    while True:
        bloco = fileobj.read(1024 * 1024)
        if not bloco:
            break
        h.update(bloco)
    fileobj.seek(0)
    return h.hexdigest()


def init_pagamentos_db():
    """Garante a tabela de cobranças do Mercado Pago no PostgreSQL."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pagamentos_mercadopago (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER NOT NULL,
            contrato_id INTEGER,
            external_reference TEXT UNIQUE NOT NULL,
            preference_id TEXT,
            payment_id TEXT,
            valor_total NUMERIC(12,2) NOT NULL,
            checkout_url TEXT,
            sandbox_checkout_url TEXT,
            status TEXT DEFAULT 'nao_pago',
            status_mp TEXT,
            data_criacao TEXT,
            data_atualizacao TEXT,
            data_pagamento TEXT,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id),
            FOREIGN KEY (contrato_id) REFERENCES contratos_alunos(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pagamentos_mp_aluno ON pagamentos_mercadopago(aluno_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pagamentos_mp_payment ON pagamentos_mercadopago(payment_id)")
    conn.commit()
    conn.close()


def get_mercadopago_sdk():
    token = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("MERCADOPAGO_ACCESS_TOKEN não configurado no Render.")
    return mercadopago.SDK(token)


def criar_preferencia_mercadopago(aluno_id, nome, email, valor_total, contrato_id=None, base_url=None, item_title=None, metadata_extra=None):
    valor = round(float(valor_total), 2)
    external_reference = f"SIGEU-ALUNO-{aluno_id}-{int(time.time())}-{secrets.token_hex(3)}"
    base_url = (base_url or "https://campusvirtualfacop.com.br").rstrip("/")
    preference_data = {
        "items": [{
            "id": f"aluno-{aluno_id}",
            "title": item_title or f"Serviços educacionais - aluno {nome}",
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": valor
        }],
        "payer": {"name": nome, "email": email},
        "external_reference": external_reference,
        "back_urls": {
            "success": f"{base_url}/pagamento/mercadopago/sucesso",
            "pending": f"{base_url}/pagamento/mercadopago/pendente",
            "failure": f"{base_url}/pagamento/mercadopago/falha"
        },
        "auto_return": "approved",
        "notification_url": f"{base_url}/webhook/mercadopago",
        "metadata": {
            "aluno_id": str(aluno_id),
            "contrato_id": str(contrato_id) if contrato_id else "",
            **({str(k): str(v) for k, v in (metadata_extra or {}).items() if v is not None})
        }
    }
    sdk = get_mercadopago_sdk()
    resultado = sdk.preference().create(preference_data)
    resposta = resultado.get("response", {}) if isinstance(resultado, dict) else {}
    preference_id = resposta.get("id")
    init_point = resposta.get("init_point")
    sandbox_init_point = resposta.get("sandbox_init_point")
    if not preference_id or not (init_point or sandbox_init_point):
        raise RuntimeError(f"Mercado Pago não retornou um checkout válido: {resposta}")
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO pagamentos_mercadopago
        (aluno_id, contrato_id, external_reference, preference_id, valor_total,
         checkout_url, sandbox_checkout_url, status, status_mp, data_criacao, data_atualizacao)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'nao_pago', 'pending', %s, %s)
        RETURNING id
    """, (aluno_id, contrato_id, external_reference, preference_id, valor,
          init_point, sandbox_init_point, agora, agora))
    cobranca_id = cursor.fetchone()["id"]
    conn.commit()
    conn.close()
    return {
        "id": cobranca_id,
        "preference_id": preference_id,
        "checkout_url": (
            sandbox_init_point
            if str(os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")).startswith("TEST-") and sandbox_init_point
            else (init_point or sandbox_init_point)
        ),
        "external_reference": external_reference
    }


def criar_contrato_aluno(aluno_id):
    """Cria apenas o registro do contrato padrão; o conteúdo vem de templates/contrato_padrao.html."""
    data_envio = datetime.now().strftime("%d/%m/%Y %H:%M")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Evita duplicar contrato pendente para o mesmo aluno.
    cursor.execute("""
        SELECT id, pdf_path
        FROM contratos_alunos
        WHERE aluno_id = %s AND status = 'pendente'
        ORDER BY id DESC
        LIMIT 1
    """, (aluno_id,))
    existente = cursor.fetchone()

    if existente:
        conn.close()
        return existente["id"]

    cursor.execute("""
        INSERT INTO contratos_alunos (aluno_id, pdf_path, status, data_envio)
        VALUES (%s, %s, 'pendente', %s)
        RETURNING id
    """, (aluno_id, "/contrato/registro/pendente", data_envio))

    contrato_id = cursor.fetchone()["id"]
    caminho = f"/contrato/registro/{contrato_id}"

    cursor.execute(
        "UPDATE contratos_alunos SET pdf_path = %s WHERE id = %s",
        (caminho, contrato_id)
    )

    conn.commit()
    conn.close()
    return contrato_id

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Tabela de alunos (com RA)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alunos (
            id SERIAL PRIMARY KEY,
            nome TEXT,
            email TEXT,
            ra TEXT UNIQUE,
            senha TEXT
        )
    """)

    # Tabela de disciplinas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS disciplinas (
            id SERIAL PRIMARY KEY,
            nome TEXT
        )
    """)

    # Tabela de capítulos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS capitulos (
            id SERIAL PRIMARY KEY,
            disciplina_id INTEGER,
            titulo TEXT,
            video_url TEXT,
            pdf_url TEXT
        )
    """)

    # Tabela de provas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS provas (
            id SERIAL PRIMARY KEY,
            capitulo_id INTEGER,
            questoes_json TEXT
        )
    """)

    # Tabela de notas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notas (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER,
            disciplina_id INTEGER,
            capitulo INTEGER,
            nota INTEGER
        )
    """)

    # Tabela aluno ↔ disciplina
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS aluno_disciplina (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER,
            disciplina_id INTEGER,
            UNIQUE(aluno_id, disciplina_id)
        )
    """)

    # Tabela de solicitações de material didático
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS solicitacoes_material (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER,
            disciplina_id INTEGER,
            material TEXT,
            data_solicitacao TEXT,
            entregue INTEGER DEFAULT 0
        )
    """)

    # Tabela de solicitações de declarações
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS solicitacoes_declaracoes (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER,
            tipo TEXT,
            detalhes TEXT,
            data_solicitacao TEXT,
            entregue INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS solicitacoes_documentos (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER,
            tipo_documento TEXT, -- 'conclusao', 'plano_ensino', 'historico', 'sugestao', 'outros'
            disciplinas_ids TEXT, -- IDs das disciplinas separados por vírgula
            detalhes TEXT,
            data_solicitacao TEXT,
            status TEXT DEFAULT 'pendente', -- 'pendente', 'processando', 'concluido'
            resposta TEXT,
            arquivo_url TEXT,
            data_resposta TEXT,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dados_pessoais (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER UNIQUE,
            cpf TEXT,
            rg TEXT,
            telefone TEXT,
            endereco TEXT,
            cidade TEXT,
            estado TEXT,
            cep TEXT,
            curso_referencia TEXT,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id)
        )
    """)

    # Nova tabela: situacao_financeira do aluno
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS situacao_financeira (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER,
            forma_pagamento TEXT, -- 'avista', 'cartao', 'boleto_pix'
            status TEXT, -- 'pago', 'pendente', 'parcial'
            parcelas_total INTEGER,
            parcelas_pagas INTEGER,
            data_vencimento TEXT,
            valor_total REAL,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS aluno_disciplina_datas (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER,
            disciplina_id INTEGER,
            data_inicio TEXT,
            data_fim_previsto TEXT,
            prova_final_aberta INTEGER DEFAULT 0,
            frequencia REAL,
            progresso_manual INTEGER,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id),
            FOREIGN KEY (disciplina_id) REFERENCES disciplinas(id),
            UNIQUE(aluno_id, disciplina_id)
        )
    """)
    cursor.execute("ALTER TABLE aluno_disciplina_datas ADD COLUMN IF NOT EXISTS frequencia REAL")
    cursor.execute("ALTER TABLE aluno_disciplina_datas ADD COLUMN IF NOT EXISTS progresso_manual INTEGER")
    # Notas administrativas aceitam décimos (ex.: 7,5). Instalações antigas usavam INTEGER.
    cursor.execute("ALTER TABLE notas ALTER COLUMN nota TYPE REAL USING nota::REAL")

    # Tabela para controlar liberação da prova final por disciplina
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS liberacao_final (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER,
            disciplina_id INTEGER,
            data_liberacao TEXT, -- Data em que a prova será liberada (DD/MM/AAAA)
            liberada INTEGER DEFAULT 0, -- 0 = não liberada, 1 = liberada
            data_criacao TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(aluno_id, disciplina_id),
            FOREIGN KEY (aluno_id) REFERENCES alunos(id),
            FOREIGN KEY (disciplina_id) REFERENCES disciplinas(id)
        )
    """)

# Tabela para notas finais
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notas_finais (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER,
            disciplina_id INTEGER,
            nota_final REAL,
            media_disciplina REAL,
            media_final REAL,
            status TEXT,
            data_realizacao TEXT,
            UNIQUE(aluno_id, disciplina_id),
            FOREIGN KEY (aluno_id) REFERENCES alunos(id),
            FOREIGN KEY (disciplina_id) REFERENCES disciplinas(id)
        )
    """)

# Tabela para questões da prova final (30 questões por disciplina)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questoes_finais (
            id SERIAL PRIMARY KEY,
            disciplina_id INTEGER,
            pergunta TEXT,
            opcao_a TEXT,
            opcao_b TEXT,
            opcao_c TEXT,
            opcao_d TEXT,
            resposta_correta TEXT,
            FOREIGN KEY (disciplina_id) REFERENCES disciplinas(id)
        )
    """)

    # Adicione também uma tabela para a prova final
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS provas_finais (
            id SERIAL PRIMARY KEY,
            disciplina_id INTEGER,
            questoes_json TEXT,
            FOREIGN KEY (disciplina_id) REFERENCES disciplinas(id)
        )
    """)

    # Tabela para Projeto Final
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS projetos_finais (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER NOT NULL,
            disciplina_id INTEGER NOT NULL,
            liberado INTEGER DEFAULT 0,
            titulo_atividade TEXT,
            conteudo_atividade TEXT,
            arquivo_atividade_path TEXT,
            nome_arquivo_atividade TEXT,
            arquivo_path TEXT,
            nome_arquivo TEXT,
            data_envio TEXT,
            nota REAL,
            corrigido INTEGER DEFAULT 0,
            data_correcao TEXT,
            data_liberacao TEXT,
            UNIQUE(aluno_id, disciplina_id),
            FOREIGN KEY (aluno_id) REFERENCES alunos(id),
            FOREIGN KEY (disciplina_id) REFERENCES disciplinas(id)
        )
    """)

    # Garante os novos campos mesmo se a tabela projetos_finais já existir no PostgreSQL
    cursor.execute("ALTER TABLE projetos_finais ADD COLUMN IF NOT EXISTS titulo_atividade TEXT")
    cursor.execute("ALTER TABLE projetos_finais ADD COLUMN IF NOT EXISTS conteudo_atividade TEXT")
    cursor.execute("ALTER TABLE projetos_finais ADD COLUMN IF NOT EXISTS arquivo_atividade_path TEXT")
    cursor.execute("ALTER TABLE projetos_finais ADD COLUMN IF NOT EXISTS nome_arquivo_atividade TEXT")

    conn.commit()
    conn.close()

def init_contratos_db():
    """Garante a estrutura dos contratos e das evidências de assinatura eletrônica."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contratos_alunos (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER NOT NULL,
            pdf_path TEXT NOT NULL,
            status TEXT DEFAULT 'pendente',
            assinatura_base64 TEXT,
            arquivo_assinado_path TEXT,
            data_envio TEXT,
            data_assinatura TEXT,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id)
        )
    """)

    # Campos adicionados sem apagar contratos já existentes.
    cursor.execute("ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS foto_assinatura_base64 TEXT")
    cursor.execute("ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS ip_assinatura TEXT")
    cursor.execute("ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS user_agent_assinatura TEXT")
    cursor.execute("ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS aceite_contrato BOOLEAN DEFAULT FALSE")
    cursor.execute("ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS aceite_foto BOOLEAN DEFAULT FALSE")
    cursor.execute("ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS texto_aceite TEXT")
    cursor.execute("ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS versao_contrato TEXT DEFAULT '3.0'")
    cursor.execute("ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS hash_assinado TEXT")
    cursor.execute("ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS pdf_assinado BYTEA")

    conn.commit()
    conn.close()


VERSAO_CONTRATO = "3.0"

TEXTO_ACEITE_CONTRATO = """Declaro, para todos os fins de direito, que li integralmente, compreendi e concordo expressamente com todas as cláusulas e condições deste Contrato de Prestação de Serviços Educacionais, referente à contratação do(s) curso(s), disciplina(s), Unidade(s) Curricular(es) Isolada(s), atividade(s) de extensão, capacitação ou demais serviços educacionais nele individualizados. Declaro estar ciente de que minha matrícula administrativa e a prestação dos serviços educacionais contratados são realizadas pelo Grupo Educacional Unificado [UNIGEU] / SIGEU Educacional, responsável pela oferta, organização, execução e acompanhamento acadêmico e operacional dos serviços educacionais contratados, e de que a FACULDADE DO CENTRO OESTE PAULISTA LTDA. (FACOP), Instituição de Ensino Superior devidamente credenciada e submetida à regulação e supervisão do Ministério da Educação (MEC), atua como INSTITUIÇÃO CERTIFICADORA nos termos da parceria/convênio educacional existente entre as instituições, realizando a certificação e/ou emissão dos documentos acadêmicos que lhe couberem, quando aplicável e observados os requisitos acadêmicos, documentais e legais pertinentes. Confirmo que os dados pessoais e acadêmicos apresentados neste instrumento, inclusive nome, CPF e Matrícula/RA, correspondem aos meus dados. Ao assinar eletronicamente este instrumento, manifesto minha concordância livre, expressa e inequívoca com a contratação e reconheço como minha a assinatura grafada abaixo, realizada por meio eletrônico, autorizando seu registro juntamente com a data e hora da assinatura, código individual do contrato, hash de integridade e demais evidências técnicas vinculadas à celebração eletrônica deste instrumento."""

TEXTO_ACEITE_FOTO = """Autorizo a captura e o armazenamento da fotografia realizada neste ato exclusivamente para compor o registro de evidências da celebração eletrônica deste contrato, vinculada à minha identificação, Matrícula/RA, data e hora da assinatura e código individual do instrumento. A fotografia será utilizada como evidência documental da celebração eletrônica e não será submetida, por este procedimento, a reconhecimento facial ou identificação biométrica automatizada."""


def agora_brasilia():
    return datetime.now(ZoneInfo("America/Sao_Paulo"))


def obter_ip_cliente():
    encaminhado = request.headers.get("X-Forwarded-For", "").strip()
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return (request.remote_addr or "").strip()


def validar_data_image(data_url, tipos_permitidos, limite_bytes):
    """Valida data URL de imagem e limita o tamanho para não aceitar conteúdo arbitrário."""
    if not data_url or not isinstance(data_url, str) or "," not in data_url:
        return False
    cabecalho, conteudo = data_url.split(",", 1)
    if cabecalho not in tipos_permitidos:
        return False
    try:
        bruto = base64.b64decode(conteudo, validate=True)
    except Exception:
        return False
    return 0 < len(bruto) <= limite_bytes


def gerar_hash_documento(conteudo, ra, timestamp):
    """
    Gera um hash único para o documento baseado no conteúdo
    """
    string_base = f"{conteudo}{ra}{timestamp}{secrets.token_hex(8)}"
    hash_obj = hashlib.sha256(string_base.encode('utf-8'))
    return hash_obj.hexdigest()

def gerar_qrcode_base64(dados):
    """Gera QR Code e retorna como base64 para incorporar no HTML"""
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(dados)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode()

        return f"data:image/png;base64,{img_base64}"
    except Exception as e:
        print(f"Erro ao gerar QR Code: {e}")
        return None

def gerar_qrcode_simples_texto(dados):
    """
    Gera QR Code em formato texto (ASCII) para fallback
    """
    try:
        qr = qrcode.QRCode()
        qr.add_data(dados)
        qr.make()

        # Gerar versão em ASCII
        qr_ascii = qr.print_ascii(invert=True)
        return qr_ascii
    except:
        return None

def gerar_link_validacao(codigo, base_url=None):
    """
    Gera link para validação do documento
    """
    if base_url:
        return f"{base_url}/validar-documento/{codigo}"
    return f"/validar-documento/{codigo}"

def criar_metadados_documento(aluno_id, tipo_documento, codigo, hash_val):
    """
    Cria metadados estruturados para o documento
    """
    metadados = {
        "aluno_id": aluno_id,
        "tipo": tipo_documento,
        "codigo": codigo,
        "hash": hash_val,
        "data_emissao": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "data_validade": (datetime.now() + timedelta(days=365*5)).strftime("%d/%m/%Y"),  # 5 anos
        "versao": "1.0",
        "sistema": "SiGEu - FACOP"
    }
    return json.dumps(metadados, ensure_ascii=False)

def extrair_metadados_qrcode(qr_data):
    """
    Extrai informações do QR Code (para validação)
    """
    try:
        # Tentar parse como JSON primeiro
        if qr_data.startswith('{'):
            return json.loads(qr_data)
        # Se não for JSON, retornar como string
        return {"dados": qr_data}
    except:
        return {"dados": qr_data}


def gerar_ra():
    """Gera um RA de 8 dígitos aleatório"""
    return str(random.randint(10000000, 99999999))


def _normalizar_status_academico(valor):
    """Normaliza o status gravado no banco sem depender de maiúsculas/minúsculas."""
    texto = str(valor or "").strip().lower()
    if texto in {"aprovado", "aprovada", "approved"}:
        return "aprovado"
    if texto in {"reprovado", "reprovada", "reprovado(a)", "failed"}:
        return "reprovado"
    if texto in {"aguardando_final", "aguardando final"}:
        return "aguardando_final"
    if texto in {"cursando", "em andamento", "andamento"}:
        return "cursando"
    return texto


def _capitulos_ordenados(cursor, disciplina_id):
    cursor.execute(
        "SELECT id, titulo FROM capitulos WHERE disciplina_id = %s ORDER BY id",
        (disciplina_id,),
    )
    return [dict(r) for r in cursor.fetchall()]


def _notas_logicas_disciplina(cursor, aluno_id, disciplina_id, capitulos=None):
    """Retorna uma nota por unidade, aceitando banco legado (1..4) e IDs reais de capítulos.

    O formato canônico passa a ser o ID real de `capitulos.id`, mas esta função continua lendo
    registros antigos sem quebrar o histórico do aluno.
    """
    capitulos = capitulos if capitulos is not None else _capitulos_ordenados(cursor, disciplina_id)
    por_id = {int(c["id"]): idx for idx, c in enumerate(capitulos, start=1)}
    cursor.execute(
        """
        SELECT id, capitulo, nota
        FROM notas
        WHERE aluno_id = %s AND disciplina_id = %s
        ORDER BY id
        """,
        (aluno_id, disciplina_id),
    )
    escolhidas = {}
    for row in cursor.fetchall():
        r = dict(row)
        try:
            chave = int(r.get("capitulo"))
        except Exception:
            continue
        if chave in por_id:
            ordem = por_id[chave]
            canonico = chave
            prioridade = 2
        elif 1 <= chave <= len(capitulos):
            ordem = chave
            canonico = int(capitulos[ordem - 1]["id"])
            prioridade = 1
        else:
            continue
        atual = escolhidas.get(ordem)
        # Prefere o formato canônico; em empate, o registro mais recente.
        candidato = (prioridade, int(r.get("id") or 0))
        if not atual or candidato >= atual[0]:
            escolhidas[ordem] = (candidato, r, canonico)

    resultado = []
    for ordem, cap in enumerate(capitulos, start=1):
        escolhido = escolhidas.get(ordem)
        resultado.append({
            "ordem": ordem,
            "capitulo_id": int(cap["id"]),
            "titulo": cap.get("titulo") or f"Unidade {ordem}",
            "nota": float(escolhido[1]["nota"]) if escolhido and escolhido[1].get("nota") is not None else None,
            "nota_id": int(escolhido[1]["id"]) if escolhido else None,
        })
    return resultado


def _media_notas_logicas(cursor, aluno_id, disciplina_id, capitulos=None):
    notas = _notas_logicas_disciplina(cursor, aluno_id, disciplina_id, capitulos)
    valores = [n["nota"] for n in notas if n["nota"] is not None]
    return (sum(valores) / len(valores)) if valores else 0.0


def _salvar_nota_logica(cursor, aluno_id, disciplina_id, capitulo_recebido, nota):
    """Salva uma unidade usando o ID real do capítulo e absorve eventual registro legado."""
    capitulos = _capitulos_ordenados(cursor, disciplina_id)
    if not capitulos:
        raise ValueError("Disciplina sem capítulos cadastrados.")
    try:
        recebido = int(capitulo_recebido)
    except Exception:
        raise ValueError("Capítulo inválido.")

    ids = [int(c["id"]) for c in capitulos]
    if recebido in ids:
        ordem = ids.index(recebido) + 1
        capitulo_id = recebido
    elif 1 <= recebido <= len(capitulos):
        ordem = recebido
        capitulo_id = ids[ordem - 1]
    else:
        raise ValueError("Capítulo não pertence à disciplina.")

    cursor.execute(
        """
        SELECT id, capitulo FROM notas
        WHERE aluno_id = %s AND disciplina_id = %s AND capitulo IN (%s, %s)
        ORDER BY CASE WHEN capitulo = %s THEN 0 ELSE 1 END, id DESC
        """,
        (aluno_id, disciplina_id, capitulo_id, ordem, capitulo_id),
    )
    existentes = [dict(r) for r in cursor.fetchall()]
    if existentes:
        manter = existentes[0]["id"]
        cursor.execute(
            "UPDATE notas SET capitulo = %s, nota = %s WHERE id = %s",
            (capitulo_id, nota, manter),
        )
        extras = [r["id"] for r in existentes[1:]]
        if extras:
            cursor.execute("DELETE FROM notas WHERE id = ANY(%s)", (extras,))
    else:
        cursor.execute(
            "INSERT INTO notas (aluno_id, disciplina_id, capitulo, nota) VALUES (%s, %s, %s, %s)",
            (aluno_id, disciplina_id, capitulo_id, nota),
        )
    return capitulo_id, ordem


def _excluir_nota_logica(cursor, aluno_id, disciplina_id, capitulo_recebido):
    capitulos = _capitulos_ordenados(cursor, disciplina_id)
    ids = [int(c["id"]) for c in capitulos]
    recebido = int(capitulo_recebido)
    if recebido in ids:
        ordem = ids.index(recebido) + 1
        capitulo_id = recebido
    elif 1 <= recebido <= len(capitulos):
        ordem = recebido
        capitulo_id = ids[ordem - 1]
    else:
        raise ValueError("Capítulo inválido.")
    cursor.execute(
        "DELETE FROM notas WHERE aluno_id = %s AND disciplina_id = %s AND capitulo IN (%s, %s)",
        (aluno_id, disciplina_id, capitulo_id, ordem),
    )


def gerar_codigos_autenticacao():
    """Gera todos os códigos aleatórios simples para autenticação"""

    # Código simples (6 letras/números)
    letras_numeros = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codigo_simples = ''.join(random.choice(letras_numeros) for _ in range(6))

    # Código de barras (apenas números)
    codigo_barras = ''.join(random.choice("0123456789") for _ in range(12))

    # Número hash grande (apenas para visual)
    numero_hash = ''.join(random.choice("0123456789ABCDEF") for _ in range(64))

    # Data/hora atual
    data_hora = datetime.now().strftime("%d/%m/%Y às %H:%M:%S")

    return {
        'codigo_simples': codigo_simples,
        'codigo_barras_simples': codigo_barras,
        'numero_hash': numero_hash,
        'data_hora_completa': data_hora
    }

def verificar_disciplina_concluida(aluno_id, disciplina_id):
    """Verifica se o aluno completou todos os capítulos da disciplina"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Uma unidade pode existir no formato legado (1..N) ou no ID real do capítulo.
    unidades = _notas_logicas_disciplina(cursor, aluno_id, disciplina_id)
    total_unidades = len(unidades)
    total_provas = sum(1 for u in unidades if u["nota"] is not None)

    cursor.execute("""
        SELECT progresso_manual
        FROM aluno_disciplina_datas
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))
    progresso_row = cursor.fetchone() or {}
    progresso_manual = progresso_row.get("progresso_manual")
    percurso_concluido = (progresso_manual is not None and int(progresso_manual or 0) >= 100) or (total_unidades > 0 and total_provas >= total_unidades)

    # Verificar se já fez a prova final
    cursor.execute("""
        SELECT id FROM notas_finais
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))

    fez_final = cursor.fetchone() is not None

    conn.close()

    # Disciplina está concluída se:
    # 1. Fez todas as 4 provas dos capítulos E
    # 2. Já fez a prova final
    if percurso_concluido and fez_final:
        return True, "concluida_com_final"
    elif percurso_concluido and not fez_final:
        return True, "aguardando_final"
    else:
        return False, "em_andamento"


def calcular_data_liberacao_final(aluno_id, disciplina_id):
    """Calcula a data de liberação da prova final (3 dias após a última prova)"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar data da última prova feita
    cursor.execute("""
        SELECT MAX(data_realizacao) as ultima_data
        FROM notas_finais
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))

    resultado = cursor.fetchone()
    ultima_data = resultado["ultima_data"] if resultado and resultado["ultima_data"] else None

    conn.close()

    if ultima_data:
        from datetime import datetime, timedelta
        try:
            # Converter string para datetime
            ultima_dt = datetime.strptime(ultima_data, "%d/%m/%Y %H:%M")
            # Adicionar 3 dias
            liberacao_dt = ultima_dt + timedelta(days=3)
            return liberacao_dt.strftime("%d/%m/%Y %H:%M")
        except:
            return None

    return None

def gerar_declaracao_conclusao(aluno_id, disciplina_id, dados_aluno, dados_disciplina, ano_manual=None):
    # Declaração acadêmica P&B, sem assinatura manuscrita simulada.
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT nome_pai, nome_mae, naturalidade, nacionalidade,
               data_nascimento, sexo, estado_civil, curso_referencia
        FROM dados_pessoais
        WHERE aluno_id = %s
    ''', (aluno_id,))
    dados_adicionais = cursor.fetchone() or {}
    cursor.execute('''
        SELECT nf.media_final, nf.status, nf.data_realizacao,
               addd.data_inicio, addd.data_fim_previsto
        FROM notas_finais nf
        LEFT JOIN aluno_disciplina_datas addd
          ON nf.aluno_id = addd.aluno_id AND nf.disciplina_id = addd.disciplina_id
        WHERE nf.aluno_id = %s AND nf.disciplina_id = %s
    ''', (aluno_id, disciplina_id))
    info_final = cursor.fetchone() or {}
    conn.close()

    nome_aluno = str(dados_aluno.get('nome') or '')
    ra_aluno = str(dados_aluno.get('ra') or '')
    cpf_aluno = str(dados_aluno.get('cpf_formatado') or '')
    nome_disciplina = str(dados_disciplina.get('nome') or '')
    carga_horaria = int(dados_disciplina.get('carga') or dados_disciplina.get('carga_horaria') or 80)
    nota = info_final.get('media_final')
    nota_final = f"{float(nota):.2f}" if nota is not None else "N/I"
    frequencia = dados_disciplina.get('frequencia')
    if frequencia is None:
        frequencia = dados_aluno.get('frequencia')
    try:
        frequencia_txt = f"{float(frequencia):.0f}%" if frequencia is not None else "N/I"
    except Exception:
        frequencia_txt = escape(str(frequencia or 'N/I'))
    data_conclusao = str(info_final.get('data_realizacao') or datetime.now().strftime('%d/%m/%Y')).split(' ')[0]
    unidade_curricular = str(dados_adicionais.get('curso_referencia') or dados_aluno.get('curso_referencia') or 'Disciplinas / Unidades Curriculares')
    docente = str(dados_disciplina.get('docente') or 'Docente / responsável acadêmico')

    codigo_autenticacao = f"{ra_aluno}-{disciplina_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    codigo = f"DECL-{codigo_autenticacao}"
    dados_qr = f"https://campusvirtualfacop.com.br/validar-documento/{codigo}"
    qrcode_base64 = gerar_qrcode_base64(dados_qr)
    hash_visual = hashlib.sha256(f"{codigo}|{nome_aluno}|{nome_disciplina}|{nota_final}".encode('utf-8')).hexdigest()

    return f'''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
    <title>Declaração de Conclusão - {escape(nome_disciplina)}</title>
    <style>
    @page{{size:A4;margin:17mm}}
    *{{box-sizing:border-box}}
    body{{font-family:Arial,Helvetica,sans-serif;color:#000;background:#fff;line-height:1.55;margin:0;font-size:10.5pt}}
    .doc{{border:1px solid #000;padding:16mm 13mm;background:#fff;min-height:255mm}}
    .cab{{border-bottom:2px solid #000;padding-bottom:9px;display:flex;justify-content:space-between;gap:16px;align-items:flex-start}}
    .brand{{font-size:14pt;font-weight:700;letter-spacing:.2px}} .sub{{font-size:8.5pt;margin-top:3px}}
    .cert{{font-size:7.7pt;text-align:right;max-width:52%;line-height:1.35}} .cert b{{font-size:9pt}}
    h1{{text-align:center;font-size:20pt;margin:23mm 0 16mm;line-height:1.2;letter-spacing:.2px}}
    p{{text-align:justify;font-size:11.3pt;margin:0 0 10px}}
    .dados{{border:1px solid #000;margin:15px 0;padding:8px 10px;display:grid;grid-template-columns:1fr 1fr;gap:6px 18px}}
    .dados .wide{{grid-column:1/-1}}
    .assinatura{{margin:20mm auto 9mm;text-align:center;max-width:88mm}}
    .assinatura strong{{display:block;font-size:11pt}} .assinatura span{{display:block;font-size:8.5pt;margin-top:2px}}
    .assinatura small{{display:block;font-size:6.8pt;margin-top:5px}}
    .auth{{margin-top:13px;border-top:1px solid #000;padding-top:10px;display:grid;grid-template-columns:82px 1fr;gap:12px;align-items:center}}
    .auth img{{width:78px;height:78px}} .hash{{font-family:monospace;font-size:6.4pt;word-break:break-all;margin-top:4px}}
    .rodape{{margin-top:9px;border-top:1px solid #000;padding-top:6px;font-size:6.6pt;text-align:center}}
    @media print{{body,.doc{{background:#fff}}}}
    </style></head><body><div class="doc">
      <div class="cab">
        <div><div class="brand">GRUPO EDUCACIONAL UNIFICADO</div><div class="sub">SIGEU Educacional • Sistema Integrado de Gestão Educacional</div></div>
        <div class="cert"><b>FACOP CERTIFICADORA</b><br>Faculdade do Centro Oeste Paulista LTDA<br>CNPJ 04.344.730/0001-60 • Portaria MEC nº 887 de 26/07/2017</div>
      </div>
      <h1>DECLARAÇÃO DE CONCLUSÃO DE DISCIPLINA</h1>
      <p>O <b>GRUPO EDUCACIONAL UNIFICADO</b>, por meio do <b>SIGEU Educacional</b>, declara, para os devidos fins, que <b>{escape(nome_aluno)}</b>, CPF {escape(cpf_aluno)}, matrícula/RA <b>{escape(ra_aluno)}</b>, concluiu com aproveitamento o componente curricular <b>{escape(nome_disciplina)}</b>, com carga horária de <b>{carga_horaria} horas</b>, frequência acadêmica registrada de <b>{frequencia_txt}</b> e média final <b>{nota_final}</b>.</p>
      <p>A conclusão foi registrada em {escape(data_conclusao)}. A certificação documental, emitida pela <b>FACOP CERTIFICADORA</b>, preserva os dados institucionais e de certificação vinculados ao registro acadêmico do(a) aluno(a).</p>
      <div class="dados"><div><b>Unidade Curricular:</b> {escape(unidade_curricular)}</div><div><b>Situação:</b> APROVADO</div><div class="wide"><b>Documento:</b> emissão acadêmica eletrônica autenticada por código, QR Code e hash.</div></div>
      <div class="assinatura"><strong>Tatiane R. L. Costa</strong><span>Documento assinado eletronicamente</span><small>Assinatura validada pela certificação institucional.</small></div>
      <div class="auth"><img src="{qrcode_base64}" alt="QR Code"><div><b>Código:</b> {escape(codigo)}<br><b>Emissão:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}<div class="hash">SHA-256: {hash_visual}</div></div></div>
      <div class="rodape">GRUPO EDUCACIONAL UNIFICADO • SIGEU Educacional • FACOP CERTIFICADORA</div>
    </div></body></html>'''


def verificar_acesso_disciplina(aluno_id, disciplina_id):
    """Verifica se o aluno pode acessar a disciplina baseado na data"""
    from datetime import datetime

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT data_inicio, data_fim_previsto
        FROM aluno_disciplina_datas
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))

    data_info = cursor.fetchone()
    conn.close()

    if not data_info:
        return False, "Disciplina não encontrada ou não matriculada"

    # Converter data string para objeto datetime
    try:
        data_inicio = datetime.strptime(data_info['data_inicio'], "%d/%m/%Y")
        hoje = datetime.now()

        if hoje < data_inicio:
            data_formatada = data_inicio.strftime("%d/%m/%Y")
            data_fim = datetime.strptime(data_info['data_fim_previsto'], "%d/%m/%Y")
            data_fim_formatada = data_fim.strftime("%d/%m/%Y")
            return False, f"Suas aulas iniciarão apenas em {data_formatada} com término máximo previsto para {data_fim_formatada}"

        return True, "Acesso permitido"
    except ValueError:
        return False, "Erro na data de início"

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ra = request.form.get("ra")
        senha = request.form.get("senha")

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alunos WHERE ra = %s", (ra,))
        aluno = cursor.fetchone()

        senha_valida = False
        if aluno:
            senha_armazenada = str(aluno.get("senha") or "")
            # Novas senhas ficam protegidas com o hash do Werkzeug.
            if senha_armazenada.startswith(("scrypt:", "pbkdf2:")):
                try:
                    senha_valida = check_password_hash(senha_armazenada, senha or "")
                except Exception:
                    senha_valida = False
            else:
                # Compatibilidade com cadastros antigos em texto simples.
                senha_valida = secrets.compare_digest(senha_armazenada, str(senha or ""))
                if senha_valida and senha_armazenada:
                    cursor.execute(
                        "UPDATE alunos SET senha = %s WHERE id = %s",
                        (generate_password_hash(str(senha)), aluno["id"])
                    )
                    conn.commit()

        conn.close()

        if aluno and senha_valida:
            session["aluno_id"] = aluno["id"]
            session["aluno_nome"] = aluno["nome"]
            session["aluno_ra"] = aluno["ra"]
            session["aluno_email"] = aluno["email"]
            return redirect(url_for("dashboard"))
        else:
            return '''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Erro no Login</title>
                <link rel="stylesheet" href="/static/css/style.css">
            </head>
            <body>
                <div class="login-container">
                    <div class="error-box">
                        <h2>❌ RA ou senha inválidos</h2>
                        <p>Verifique suas credenciais e tente novamente.</p>
                        <a href="/login" class="btn btn-primary" style="display: inline-block; margin-top: 20px;">↩️ Tentar Novamente</a>
                    </div>
                </div>
            </body>
            </html>
            '''

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM dados_pessoais WHERE aluno_id = %s", (aluno_id,))
        dados_pessoais = cursor.fetchone()
        cursor.execute("SELECT * FROM situacao_financeira WHERE aluno_id = %s ORDER BY id DESC LIMIT 1", (aluno_id,))
        situacao_financeira = cursor.fetchone()

        cursor.execute("""
            SELECT d.id, d.nome,
                   COUNT(DISTINCT c.id) AS total_capitulos,
                   COUNT(DISTINCT n.capitulo) AS provas_realizadas,
                   nf.nota_final, nf.media_final, nf.status AS status_final,
                   addd.progresso_manual, addd.frequencia, addd.data_inicio
            FROM disciplinas d
            JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
            LEFT JOIN capitulos c ON c.disciplina_id = d.id
            LEFT JOIN notas n ON n.aluno_id = ad.aluno_id AND n.disciplina_id = d.id
            LEFT JOIN notas_finais nf ON nf.aluno_id = ad.aluno_id AND nf.disciplina_id = d.id
            LEFT JOIN aluno_disciplina_datas addd
              ON addd.aluno_id = ad.aluno_id AND addd.disciplina_id = d.id
            WHERE ad.aluno_id = %s
            GROUP BY d.id, d.nome, nf.nota_final, nf.media_final, nf.status,
                     addd.progresso_manual, addd.frequencia, addd.data_inicio
            ORDER BY d.nome
        """, (aluno_id,))
        disciplinas = []
        for row in cursor.fetchall():
            d = dict(row)
            total = int(d.get("total_capitulos") or 0)
            unidades_dashboard = _notas_logicas_disciplina(cursor, aluno_id, d["id"])
            feitas = sum(1 for u in unidades_dashboard if u["nota"] is not None)
            status_final = _normalizar_status_academico(d.get("status_final"))

            if d.get("progresso_manual") is not None:
                progresso = max(0, min(100, int(d.get("progresso_manual") or 0)))
            elif status_final in {"aprovado", "reprovado"} or d.get("nota_final") is not None:
                progresso = 100
            elif total <= 0:
                progresso = 0
            else:
                bruto = round((feitas / total) * 100)
                progresso = 100 if bruto >= 100 else 75 if bruto >= 75 else 50 if bruto >= 50 else 25 if bruto > 0 else 0

            # Verde significa disciplina academicamente finalizada. Progresso de 100%
            # sem avaliação final continua em andamento/aguardando final (amarelo).
            if status_final in {"aprovado", "reprovado"} or d.get("nota_final") is not None or d.get("media_final") is not None:
                status_visual = "finalizada"
            elif progresso <= 0 and not d.get("data_inicio") and feitas == 0:
                status_visual = "nao_iniciada"
            elif progresso <= 0 and feitas == 0:
                status_visual = "nao_iniciada"
            else:
                status_visual = "cursando"

            d["provas_realizadas"] = feitas
            d["progresso"] = progresso
            d["status_visual"] = status_visual
            d["status_final"] = status_final
            disciplinas.append(d)

        cursor.execute("""
            SELECT n.disciplina_id, n.capitulo, n.nota, d.nome AS disciplina_nome
            FROM notas n JOIN disciplinas d ON n.disciplina_id = d.id
            WHERE n.aluno_id = %s ORDER BY n.disciplina_id, n.capitulo
        """, (aluno_id,))
        notas = cursor.fetchall()
        cursor.execute("""
            SELECT sm.*, d.nome AS disciplina_nome FROM solicitacoes_material sm
            LEFT JOIN disciplinas d ON sm.disciplina_id = d.id
            WHERE sm.aluno_id = %s ORDER BY sm.data_solicitacao DESC
        """, (aluno_id,))
        solicitacoes_material = cursor.fetchall()
        cursor.execute("SELECT * FROM solicitacoes_declaracoes WHERE aluno_id = %s ORDER BY data_solicitacao DESC", (aluno_id,))
        solicitacoes_declaracoes = cursor.fetchall()

        # Estatística geral sem duplicar unidades legadas/canônicas.
        todas_notas = []
        for d in disciplinas:
            todas_notas.extend([x["nota"] for x in _notas_logicas_disciplina(cursor, aluno_id, d["id"]) if x["nota"] is not None])
        total_provas = len(todas_notas)
        media_geral = round(sum(todas_notas) / len(todas_notas), 2) if todas_notas else 0

        cursor.execute("SELECT COUNT(*) AS pendente FROM solicitacoes_material WHERE aluno_id=%s AND entregue=0", (aluno_id,))
        material_pendente = (cursor.fetchone() or {}).get("pendente") or 0
        cursor.execute("SELECT COUNT(*) AS pendente FROM solicitacoes_declaracoes WHERE aluno_id=%s AND entregue=0", (aluno_id,))
        declaracoes_pendentes = (cursor.fetchone() or {}).get("pendente") or 0
        cursor.execute("SELECT COUNT(*) AS total FROM documentos_enviados WHERE aluno_id=%s AND status='enviado'", (aluno_id,))
        nao_visualizados = (cursor.fetchone() or {}).get("total") or 0
        cursor.execute("""
            SELECT da.*,
                   COUNT(ada2.id) AS total_anexos,
                   AVG(ada2.nota) FILTER (WHERE ada2.nota IS NOT NULL) AS media_nota
            FROM disciplinas_alternativas da
            JOIN aluno_disciplina_alternativa rel ON da.id=rel.disciplina_id AND rel.aluno_id=%s
            LEFT JOIN anexos_disciplina_alternativa ada2 ON ada2.aluno_id=%s AND ada2.disciplina_id=da.id
            WHERE da.ativa=1
            GROUP BY da.id
            ORDER BY da.nome
        """, (aluno_id, aluno_id))
        disciplinas_alternativas=[]
        for row in cursor.fetchall():
            d=dict(row); media=float(d.get('media_nota') or 0); d['progresso']=min(100,int(media*10)) if media else 0; disciplinas_alternativas.append(d)
    finally:
        conn.close()

    return render_template(
        "dashboard.html", aluno_nome=session.get("aluno_nome"), aluno_ra=session.get("aluno_ra"),
        aluno_email=session.get("aluno_email"), dados_pessoais=dados_pessoais,
        situacao_financeira=situacao_financeira, disciplinas=disciplinas,
        disciplinas_alternativas=disciplinas_alternativas, notas=notas,
        solicitacoes_material=solicitacoes_material, solicitacoes_declaracoes=solicitacoes_declaracoes,
        total_provas_realizadas=total_provas, media_geral=media_geral,
        material_pendente=material_pendente, declaracoes_pendentes=declaracoes_pendentes,
        nao_visualizados=nao_visualizados
    )


@app.route("/mew/notas/capitulos/<int:aluno_id>/<int:disciplina_id>")
def mew_notas_capitulos(aluno_id, disciplina_id):
    """Gerenciar notas dos capítulos e prova final de um aluno em uma disciplina"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar informações do aluno
    cursor.execute("SELECT id, nome, ra FROM alunos WHERE id = %s", (aluno_id,))
    aluno = cursor.fetchone()

    if not aluno:
        conn.close()
        return "Aluno não encontrado", 404

    # Buscar informações da disciplina
    cursor.execute("SELECT id, nome FROM disciplinas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()

    if not disciplina:
        conn.close()
        return "Disciplina não encontrada", 404

    # Buscar notas existentes dos capítulos
    cursor.execute("""
        SELECT id, capitulo, nota
        FROM notas
        WHERE aluno_id = %s AND disciplina_id = %s
        ORDER BY capitulo
    """, (aluno_id, disciplina_id))
    notas_capitulos = cursor.fetchall()

    # Buscar nota da prova final
    cursor.execute("""
        SELECT nota_final
        FROM notas_finais
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))
    nota_final_row = cursor.fetchone()
    nota_final = nota_final_row.get("nota_final") if nota_final_row else None

    conn.close()

    return render_template(
        "mew/notas_capitulos.html",
        aluno=aluno,
        disciplina=disciplina,
        notas_capitulos=notas_capitulos,
        nota_final=nota_final
    )


@app.route("/mew/questoes-final/<int:disciplina_id>", methods=["GET", "POST"])
def mew_questoes_final(disciplina_id):
    """Cadastrar questões da prova final - VERSÃO CORRIGIDA"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar disciplina
    cursor.execute("SELECT * FROM disciplinas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()

    if request.method == "POST":
        pergunta = request.form.get("pergunta")
        opcao_a = request.form.get("opcao_a")
        opcao_b = request.form.get("opcao_b")
        opcao_c = request.form.get("opcao_c")
        opcao_d = request.form.get("opcao_d")
        resposta_correta = request.form.get("resposta_correta")

        if not all([pergunta, opcao_a, opcao_b, opcao_c, opcao_d, resposta_correta]):
            conn.close()
            return redirect(f"/mew/questoes-final/{disciplina_id}?erro=Dados+incompletos")

        # Inserir questão
        cursor.execute("""
            INSERT INTO questoes_finais
            (disciplina_id, pergunta, opcao_a, opcao_b, opcao_c, opcao_d, resposta_correta)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (disciplina_id, pergunta, opcao_a, opcao_b, opcao_c, opcao_d, resposta_correta))

        conn.commit()
        conn.close()
        return redirect(f"/mew/questoes-final/{disciplina_id}?sucesso=Questão+adicionada")

    # GET: Listar questões existentes
    cursor.execute("SELECT * FROM questoes_finais WHERE disciplina_id = %s ORDER BY id", (disciplina_id,))
    questoes = cursor.fetchall()

    total_questoes = len(questoes)

    conn.close()

    return render_template(
        "mew/questoes_final.html",
        disciplina=disciplina,
        questoes=questoes,
        total_questoes=total_questoes
    )

@app.route("/mew/deletar-questao/<int:questao_id>")
def deletar_questao(questao_id):
    """Deleta uma questão da prova final"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar disciplina_id antes de deletar para redirecionar
    cursor.execute("SELECT disciplina_id FROM questoes_finais WHERE id = %s", (questao_id,))
    questao = cursor.fetchone()
    disciplina_id = questao["disciplina_id"] if questao else None

    cursor.execute("DELETE FROM questoes_finais WHERE id = %s", (questao_id,))

    conn.commit()
    conn.close()

    if disciplina_id:
        return redirect(f"/mew/questoes-final/{disciplina_id}?sucesso=Questão+removida")
    else:
        return redirect("/mew/avaliacao-final?erro=Questão+não+encontrada")

@app.route("/mew/verificar-questoes/<int:disciplina_id>")
def verificar_questoes(disciplina_id):
    """Retorna quantas questões uma disciplina tem"""
    if not session.get("mew_admin"):
        return jsonify({"error": "Não autorizado"})

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as total FROM questoes_finais WHERE disciplina_id = %s", (disciplina_id,))
    resultado = cursor.fetchone()
    total = resultado["total"] if resultado else 0

    conn.close()

    return jsonify({
        "disciplina_id": disciplina_id,
        "total": total,
        "pronta": total >= 30
    })

@app.route("/mew/salvar-nota-final", methods=["POST"])
def mew_salvar_nota_final():
    """Compatibilidade com a tela antiga: salva a final no mesmo modelo usado pelo MEW atual."""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"}), 403

    conn = None
    try:
        data = request.get_json(silent=True) or {}
        aluno_id = int(data.get("aluno_id"))
        disciplina_id = int(data.get("disciplina_id"))
        valor = data.get("nota_final")
        nota_final = None if valor in (None, "") else float(str(valor).replace(",", "."))
        if nota_final is not None and not 0 <= nota_final <= 10:
            raise ValueError("A nota final deve estar entre 0 e 10.")

        conn = get_db_connection()
        cursor = conn.cursor()
        media_disciplina = round(_media_notas_logicas(cursor, aluno_id, disciplina_id), 2)
        media_final = round((media_disciplina + nota_final) / 2, 2) if nota_final is not None else None
        status = "aprovado" if media_final is not None and media_final >= 7 else ("reprovado" if media_final is not None else "cursando")

        cursor.execute("""
            INSERT INTO notas_finais
            (aluno_id, disciplina_id, nota_final, media_disciplina, media_final, status, data_realizacao)
            VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (aluno_id, disciplina_id) DO UPDATE SET
                nota_final = EXCLUDED.nota_final,
                media_disciplina = EXCLUDED.media_disciplina,
                media_final = EXCLUDED.media_final,
                status = EXCLUDED.status,
                data_realizacao = EXCLUDED.data_realizacao
        """, (aluno_id, disciplina_id, nota_final, media_disciplina, media_final, status))
        conn.commit()
        return jsonify({
            "success": True,
            "message": "Nota final salva com sucesso!",
            "media_disciplina": media_disciplina,
            "media_final": media_final,
            "status": status,
        })
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 400
    finally:
        if conn:
            conn.close()


@app.route("/disciplina/<int:disciplina_id>")
def disciplina(disciplina_id):
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    # VERIFICAR SE DISCIPLINA ESTÁ CONCLUÍDA
    concluida, status = verificar_disciplina_concluida(aluno_id, disciplina_id)

    if concluida and status == "concluida_com_final":
        return render_template("disciplina_concluida.html",
                             mensagem="✅ Disciplina Concluída!",
                             detalhes="Esta disciplina já foi totalmente concluída, incluindo a avaliação final.",
                             disciplina_id=disciplina_id)

    if concluida and status == "aguardando_final":
        # Calcular data de liberação da prova final
        data_liberacao = calcular_data_liberacao_final(aluno_id, disciplina_id)

        if data_liberacao:
            detalhes = f"Você completou todos os 4 capítulos. A prova final estará disponível em {data_liberacao}."
        else:
            detalhes = "Você completou todos os 4 capítulos. A prova final estará disponível em até 3 dias úteis."

        return render_template("disciplina_concluida.html",
                             mensagem="📚 Disciplina com Capítulos Concluídos!",
                             detalhes=detalhes,
                             disciplina_id=disciplina_id,
                             data_liberacao=data_liberacao)

    # Resto da função continua igual...
    # Verificar datas de liberação dos capítulos
    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar data de início da disciplina para este aluno
    cursor.execute("""
        SELECT data_inicio FROM aluno_disciplina_datas
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))

    data_info = cursor.fetchone()

    if not data_info or not data_info['data_inicio']:
        conn.close()
        return render_template("acesso_bloqueado.html",
                             mensagem="Disciplina não configurada")

    # Calcular dias desde o início
    from datetime import datetime
    try:
        data_inicio = datetime.strptime(data_info['data_inicio'], "%d/%m/%Y")
        hoje = datetime.now()
        dias_desde_inicio = (hoje - data_inicio).days

        # Determinar capítulos liberados
        capitulos_liberados = 0
        if dias_desde_inicio >= 12:
            capitulos_liberados = 4
        elif dias_desde_inicio >= 9:
            capitulos_liberados = 3
        elif dias_desde_inicio >= 6:
            capitulos_liberados = 2
        elif dias_desde_inicio >= 3:
            capitulos_liberados = 1
    except:
        capitulos_liberados = 0

    # Buscar disciplina e capítulos
    cursor.execute("SELECT * FROM disciplinas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()

    cursor.execute("""
        SELECT c.id, c.titulo, c.video_url, c.pdf_url, p.id AS prova_id
        FROM capitulos c
        LEFT JOIN provas p ON p.capitulo_id = c.id
        WHERE c.disciplina_id = %s
        ORDER BY c.id
    """, (disciplina_id,))
    capitulos = cursor.fetchall()

    conn.close()

    return render_template(
        "disciplina.html",
        disciplina=disciplina,
        capitulos=capitulos,
        capitulos_liberados=capitulos_liberados
    )

@app.route("/instrucoes/<int:disciplina_id>/<int:capitulo_numero>")
def instrucoes_prova(disciplina_id, capitulo_numero):
    """Página de instruções antes da prova"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    # Verificar se já fez esta prova
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT n.id FROM notas n
        WHERE n.aluno_id = %s AND n.disciplina_id = %s
          AND (
              n.capitulo = %s OR
              n.capitulo = (
                  SELECT c.id FROM capitulos c
                  WHERE c.disciplina_id = %s
                  ORDER BY c.id LIMIT 1 OFFSET %s
              )
          )
        LIMIT 1
    """, (aluno_id, disciplina_id, capitulo_numero, disciplina_id, capitulo_numero - 1))

    if cursor.fetchone():
        conn.close()
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Prova já realizada</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .warning-box {{
                    background: #fff3cd;
                    color: #856404;
                    padding: 30px;
                    border-radius: 10px;
                    margin: 20px auto;
                    max-width: 600px;
                    border: 1px solid #ffeaa7;
                }}
                .btn {{
                    display: inline-block;
                    background: #343a40;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 5px;
                    margin: 10px;
                }}
                .btn-secondary {{
                    background: #6c757d;
                }}
            </style>
        </head>
        <body>
            <div class="warning-box">
                <h2>⚠️ Prova já realizada</h2>
                <p>Você já realizou esta prova. Apenas uma tentativa é permitida por capítulo.</p>
                <p><strong>Se você já fez esta prova, pode ver seus resultados clicando no botão abaixo.</strong></p>
                <div style="margin-top: 30px;">
                    <a href="/resultado/{}/{}" class="btn">📊 Ver Resultado da Prova</a>
                    <a href="/disciplina/{}" class="btn btn-secondary">↩️ Voltar para a Disciplina</a>
                    <a href="/dashboard" class="btn btn-secondary">🏠 Voltar para o Dashboard</a>
                </div>
            </div>
        </body>
        </html>
        '''.format(disciplina_id, capitulo_numero, disciplina_id)

    # Obter informações do aluno
    cursor.execute("SELECT nome FROM alunos WHERE id = %s", (aluno_id,))
    aluno = cursor.fetchone()

    # Obter informações da disciplina e capítulo
    cursor.execute("SELECT nome FROM disciplinas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()

    cursor.execute("""
        SELECT c.titulo, p.questoes_json
        FROM capitulos c
        LEFT JOIN provas p ON p.capitulo_id = c.id
        WHERE c.disciplina_id = %s
        ORDER BY c.id
        LIMIT 1 OFFSET %s
    """, (disciplina_id, capitulo_numero - 1))

    capitulo = cursor.fetchone()
    conn.close()

    if not capitulo or not aluno or not disciplina:
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Informações não encontradas</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .error-box {{
                    background: #f8d7da;
                    color: #721c24;
                    padding: 20px;
                    border-radius: 10px;
                    margin: 20px auto;
                    max-width: 500px;
                    border: 1px solid #f5c6cb;
                }}
                .btn {{
                    display: inline-block;
                    background: #343a40;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 5px;
                    margin-top: 20px;
                }}
            </style>
        </head>
        <body>
            <div class="error-box">
                <h2>❌ Informações não encontradas</h2>
                <p>A disciplina, capítulo ou informações do aluno não foram encontradas.</p>
                <a href="/dashboard" class="btn">🏠 Voltar para o Dashboard</a>
            </div>
        </body>
        </html>
        '''

    # Contar questões
    questoes = json.loads(capitulo["questoes_json"]) if capitulo["questoes_json"] else []

    return render_template(
        "instrucoes_prova.html",
        aluno_nome=aluno["nome"],
        disciplina_nome=disciplina["nome"],
        disciplina_id=disciplina_id,
        capitulo_numero=capitulo_numero,
        capitulo_titulo=capitulo["titulo"],
        total_questoes=len(questoes)
    )


@app.route("/prova/<int:disciplina_id>/<int:capitulo_numero>", methods=["GET", "POST"])
def prova(disciplina_id, capitulo_numero):
    """Página da prova com timer de 1 hora"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    # Verificar se já fez esta prova
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT n.id FROM notas n
        WHERE n.aluno_id = %s AND n.disciplina_id = %s
          AND (
              n.capitulo = %s OR
              n.capitulo = (
                  SELECT c.id FROM capitulos c
                  WHERE c.disciplina_id = %s
                  ORDER BY c.id LIMIT 1 OFFSET %s
              )
          )
        LIMIT 1
    """, (aluno_id, disciplina_id, capitulo_numero, disciplina_id, capitulo_numero - 1))

    if cursor.fetchone():
        conn.close()
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Prova já realizada</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .info-box {{
                    background: #eceff1;
                    color: #4a5157;
                    padding: 30px;
                    border-radius: 10px;
                    margin: 20px auto;
                    max-width: 600px;
                    border: 1px solid #c9d0d5;
                }}
                .btn {{
                    display: inline-block;
                    background: #343a40;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 5px;
                    margin: 10px;
                }}
            </style>
        </head>
        <body>
            <div class="info-box">
                <h2>📋 Redirecionando...</h2>
                <p>Você já realizou esta prova. Estamos redirecionando você para a página de resultados.</p>
                <p>Se o redirecionamento não funcionar, clique no botão abaixo:</p>
                <a href="/resultado/{}/{}" class="btn">📊 Ver Resultado da Prova</a>
            </div>
            <script>
                setTimeout(function() {{
                    window.location.href = "/resultado/{}/{}";
                }}, 2000);
            </script>
        </body>
        </html>
        '''.format(disciplina_id, capitulo_numero, disciplina_id, capitulo_numero)

    # Obter informações do capítulo
    cursor.execute("""
        SELECT c.id, c.titulo
        FROM capitulos c
        WHERE c.disciplina_id = %s
        ORDER BY c.id
        LIMIT 1 OFFSET %s
    """, (disciplina_id, capitulo_numero - 1))
    capitulo_result = cursor.fetchone()

    if not capitulo_result:
        conn.close()
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Capítulo não encontrado</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .error-box {{
                    background: #f8d7da;
                    color: #721c24;
                    padding: 20px;
                    border-radius: 10px;
                    margin: 20px auto;
                    max-width: 500px;
                    border: 1px solid #f5c6cb;
                }}
                .btn {{
                    display: inline-block;
                    background: #343a40;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 5px;
                    margin-top: 20px;
                }}
            </style>
        </head>
        <body>
            <div class="error-box">
                <h2>❌ Capítulo não encontrado</h2>
                <p>O capítulo solicitado não foi encontrado.</p>
                <a href="/dashboard" class="btn">🏠 Voltar para o Dashboard</a>
            </div>
        </body>
        </html>
        '''

    capitulo_id = capitulo_result["id"]

    # Obter questões da prova
    cursor.execute("""
        SELECT questoes_json
        FROM provas
        WHERE capitulo_id = %s
    """, (capitulo_id,))
    prova = cursor.fetchone()
    conn.close()

    if not prova:
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Prova não encontrada</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .error-box {{
                    background: #f8d7da;
                    color: #721c24;
                    padding: 20px;
                    border-radius: 10px;
                    margin: 20px auto;
                    max-width: 500px;
                    border: 1px solid #f5c6cb;
                }}
                .btn {{
                    display: inline-block;
                    background: #343a40;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 5px;
                    margin-top: 20px;
                }}
            </style>
        </head>
        <body>
            <div class="error-box">
                <h2>❌ Mini-prova não encontrada</h2>
                <p>A prova para este capítulo não está disponível.</p>
                <a href="/disciplina/{}" class="btn">↩️ Voltar para a Disciplina</a>
            </div>
        </body>
        </html>
        '''.format(disciplina_id)

    questoes = json.loads(prova["questoes_json"])

    if request.method == "POST":
        acertos = 0
        resultados = []

        for i, q in enumerate(questoes, start=1):
            resposta_aluno = request.form.get(f"resposta_{i}")
            resposta_correta = str(q["resposta_certa"]).strip().upper()
            resposta_aluno = resposta_aluno.strip().upper() if resposta_aluno else ""
            acertou = resposta_aluno == resposta_correta

            if acertou:
                acertos += 1

            resultados.append({
                "pergunta": q["pergunta"],
                "opcoes": q["opcoes"],
                "resposta_correta": q["resposta_certa"],
                "resposta_aluno": resposta_aluno,
                "acertou": acertou
            })

        nota = round(10 * (acertos / len(questoes)))

        # Salvar nota no banco (SEM tempo)
        conn = get_db_connection()
        cursor = conn.cursor()
        _salvar_nota_logica(cursor, aluno_id, disciplina_id, capitulo_id, nota)
        conn.commit()
        conn.close()

        # Guardar resultados na sessão para mostrar depois
        session['ultimos_resultados'] = json.dumps({
            'resultados': resultados,
            'nota': nota,
            'acertos': acertos,
            'total': len(questoes)
        })

        return redirect(url_for("resultado_prova",
                               disciplina_id=disciplina_id,
                               capitulo_numero=capitulo_numero))

    # GET: Mostrar a prova
    return render_template(
        "miniprova.html",
        questoes=questoes,
        disciplina_id=disciplina_id,
        capitulo=capitulo_numero,
        total_questoes=len(questoes)
    )

@app.route("/verificar-acesso/<int:disciplina_id>")
def verificar_acesso(disciplina_id):
    """Verifica acesso à disciplina via AJAX"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"acesso_permitido": False, "mensagem": "Não autenticado"})

    acesso_permitido, mensagem = verificar_acesso_disciplina(aluno_id, disciplina_id)

    return jsonify({
        "acesso_permitido": acesso_permitido,
        "mensagem": mensagem
    })

@app.route("/verificar-conclusao/<int:disciplina_id>")
def verificar_conclusao(disciplina_id):
    """Verifica se a disciplina está concluída para o aluno"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"error": "Não autenticado"})

    concluida, status = verificar_disciplina_concluida(aluno_id, disciplina_id)

    data_liberacao = None
    if status == "aguardando_final":
        data_liberacao = calcular_data_liberacao_final(aluno_id, disciplina_id)

    return jsonify({
        "concluida": concluida,
        "status": status,
        "disciplina_id": disciplina_id,
        "data_liberacao": data_liberacao
    })

@app.route("/resultado/<int:disciplina_id>/<int:capitulo_numero>")
def resultado_prova(disciplina_id, capitulo_numero):
    """Página de resultados após a prova"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    # Verificar se tem resultados na sessão
    resultados_sessao = session.get('ultimos_resultados')

    if resultados_sessao:
        dados = json.loads(resultados_sessao)
        session.pop('ultimos_resultados', None)

        # Buscar informações do aluno e disciplina
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT a.nome AS aluno_nome, d.nome AS disciplina_nome,
                   (SELECT titulo FROM capitulos WHERE disciplina_id = %s
                    ORDER BY id LIMIT 1 OFFSET %s) AS capitulo_titulo
            FROM alunos a, disciplinas d
            WHERE a.id = %s AND d.id = %s
        """, (disciplina_id, capitulo_numero - 1, aluno_id, disciplina_id))

        info = cursor.fetchone()
        conn.close()

        if info and info["capitulo_titulo"]:
            percentual = round((dados['acertos'] / dados['total']) * 100)

            return render_template(
                "resultado_prova.html",
                aluno_nome=info["aluno_nome"],
                disciplina_nome=info["disciplina_nome"],
                disciplina_id=disciplina_id,
                capitulo_numero=capitulo_numero,
                capitulo_titulo=info["capitulo_titulo"],
                nota_final=dados['nota'],
                acertos=dados['acertos'],
                total_questoes=dados['total'],
                percentual=percentual,
                resultados=dados['resultados']
            )

    # Se não tiver resultados na sessão, buscar do banco
    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar nota
    cursor.execute("""
        SELECT n.nota, a.nome AS aluno_nome,
               d.nome AS disciplina_nome
        FROM notas n
        JOIN alunos a ON n.aluno_id = a.id
        JOIN disciplinas d ON n.disciplina_id = d.id
        WHERE n.aluno_id = %s AND n.disciplina_id = %s
          AND (
              n.capitulo = (
                  SELECT c.id FROM capitulos c
                  WHERE c.disciplina_id = %s
                  ORDER BY c.id LIMIT 1 OFFSET %s
              )
              OR n.capitulo = %s
          )
        ORDER BY CASE WHEN n.capitulo = %s THEN 0 ELSE 1 END, n.id DESC
        LIMIT 1
    """, (aluno_id, disciplina_id, disciplina_id, capitulo_numero - 1,
          capitulo_numero, capitulo_numero))

    nota_info = cursor.fetchone()

    if not nota_info:
        conn.close()
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Resultado não encontrado</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .info-box {{
                    background: #eceff1;
                    color: #4a5157;
                    padding: 30px;
                    border-radius: 10px;
                    margin: 20px auto;
                    max-width: 600px;
                    border: 1px solid #c9d0d5;
                }}
                .btn {{
                    display: inline-block;
                    background: #343a40;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 5px;
                    margin: 10px;
                }}
            </style>
        </head>
        <body>
            <div class="info-box">
                <h2>📝 Resultado não encontrado</h2>
                <p>Não encontramos resultados para esta prova. Talvez você ainda não tenha feito a prova deste capítulo.</p>
                <div style="margin-top: 30px;">
                    <a href="/instrucoes/{}/{}" class="btn">📝 Fazer a Prova</a>
                    <a href="/disciplina/{}" class="btn">↩️ Voltar para a Disciplina</a>
                    <a href="/dashboard" class="btn">🏠 Voltar para o Dashboard</a>
                </div>
            </div>
        </body>
        </html>
        '''.format(disciplina_id, capitulo_numero, disciplina_id)

    # Buscar título do capítulo
    cursor.execute("""
        SELECT titulo FROM capitulos
        WHERE disciplina_id = %s
        ORDER BY id
        LIMIT 1 OFFSET %s
    """, (disciplina_id, capitulo_numero - 1))

    capitulo = cursor.fetchone()

    # Buscar questões para calcular acertos
    cursor.execute("""
        SELECT p.questoes_json
        FROM provas p
        JOIN capitulos c ON p.capitulo_id = c.id
        WHERE c.disciplina_id = %s
        ORDER BY c.id
        LIMIT 1 OFFSET %s
    """, (disciplina_id, capitulo_numero - 1))

    prova = cursor.fetchone()
    conn.close()

    if not prova:
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Prova não encontrada</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .error-box {{
                    background: #f8d7da;
                    color: #721c24;
                    padding: 20px;
                    border-radius: 10px;
                    margin: 20px auto;
                    max-width: 500px;
                    border: 1px solid #f5c6cb;
                }}
                .btn {{
                    display: inline-block;
                    background: #343a40;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 5px;
                    margin-top: 20px;
                }}
            </style>
        </head>
        <body>
            <div class="error-box">
                <h2>❌ Prova não encontrada</h2>
                <p>As questões da prova não foram encontradas no banco de dados.</p>
                <a href="/dashboard" class="btn">🏠 Voltar para o Dashboard</a>
            </div>
        </body>
        </html>
        '''

    questoes = json.loads(prova["questoes_json"])
    total_questoes = len(questoes)
    acertos = round((nota_info["nota"] / 10) * total_questoes)
    percentual = round((acertos / total_questoes) * 100)

    # Não temos detalhes das respostas se veio do banco
    resultados_simples = []
    for q in questoes:
        resultados_simples.append({
            "pergunta": q["pergunta"],
            "opcoes": q["opcoes"],
            "resposta_correta": q["resposta_certa"],
            "resposta_aluno": "?",  # Não sabemos a resposta do aluno
            "acertou": None  # Não sabemos se acertou
        })

    return render_template(
        "resultado_prova.html",
        aluno_nome=nota_info["aluno_nome"],
        disciplina_nome=nota_info["disciplina_nome"],
        disciplina_id=disciplina_id,
        capitulo_numero=capitulo_numero,
        capitulo_titulo=capitulo["titulo"] if capitulo else f"Capítulo {capitulo_numero}",
        nota_final=nota_info["nota"],
        acertos=acertos,
        total_questoes=total_questoes,
        percentual=percentual,
        resultados=resultados_simples
    )


@app.route("/solicitar-material-modal")
def solicitar_material_modal():
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT d.id, d.nome
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        WHERE ad.aluno_id = %s
    """, (aluno_id,))
    disciplinas = cursor.fetchall()
    conn.close()

    html = '''
    <div class="declaration-form">
        <div class="form-group">
            <label>Disciplina</label>
            <select class="form-control" id="materialDisciplina">
                <option value="">Selecione uma disciplina</option>
    '''

    for d in disciplinas:
        html += f'<option value="{d["id"]}">{d["nome"]}</option>'

    html += '''
            </select>
        </div>
        <div class="form-group">
            <label>Tipo de Material</label>
            <select class="form-control" id="materialTipo">
                <option value="">Selecione o material</option>
                <option value="livro">Livro Didático</option>
                <option value="apostila">Apostila</option>
                <option value="ambos">Livro + Apostila</option>
            </select>
        </div>
        <div class="form-group">
            <label>Observações (opcional)</label>
            <textarea class="form-control" id="materialObservacoes" rows="3" placeholder="Alguma observação sobre o material..."></textarea>
        </div>
        <p style="font-size: 14px; color: var(--medium-gray); margin-top: 15px;">
            <i class="fas fa-info-circle"></i> O material será enviado em até 15 dias úteis.
        </p>
    </div>
    '''

    return html


@app.route("/solicitar-material", methods=["POST"])
def solicitar_material():
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "message": "Não autenticado"})

    data = request.json
    disciplina_id = data.get("disciplina_id")
    tipo_material = data.get("tipo_material")
    observacoes = data.get("observacoes", "")

    if not disciplina_id or not tipo_material:
        return jsonify({"success": False, "message": "Dados incompletos"})

    # Determinar nome do material
    material_nome = ""
    if tipo_material == "livro":
        material_nome = "Livro Didático"
    elif tipo_material == "apostila":
        material_nome = "Apostila"
    elif tipo_material == "ambos":
        material_nome = "Livro + Apostila"

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar nome da disciplina
    cursor.execute("SELECT nome FROM disciplinas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()
    disciplina_nome = disciplina["nome"] if disciplina else ""

    # Inserir solicitação
    data_solicitacao = datetime.now().strftime("%d/%m/%Y %H:%M")

    detalhes_material = f"{material_nome} - {disciplina_nome}"
    if observacoes:
        detalhes_material += f" ({observacoes})"

    cursor.execute("""
        INSERT INTO solicitacoes_material (aluno_id, disciplina_id, material, data_solicitacao)
        VALUES (%s, %s, %s, %s)
    """, (aluno_id, disciplina_id, detalhes_material, data_solicitacao))

    conn.commit()
    conn.close()

    return jsonify({"success": True, "message": "Solicitação registrada"})


@app.route("/solicitar-declaracao-modal")
def solicitar_declaracao_modal():
    html = '''
    <div class="declaration-form">
        <div class="form-group">
            <label>Tipo de Declaração</label>
            <select class="form-control" id="declaracaoTipo">
                <option value="">Selecione o tipo</option>
                <option value="matricula">Declaração de Matrícula</option>
                <option value="historico">Histórico Parcial</option>
                <option value="outro">Outro</option>
            </select>
        </div>
        <div class="form-group">
            <label>Quantidade de Vias</label>
            <select class="form-control" id="declaracaoVias">
                <option value="1">1 via</option>
                <option value="2">2 vias</option>
                <option value="3">3 vias</option>
            </select>
        </div>
        <div class="form-group">
            <label>Observações (opcional)</label>
            <textarea class="form-control" id="declaracaoObservacoes" rows="3" placeholder="Alguma observação sobre a declaração..."></textarea>
        </div>
        <p style="font-size: 14px; color: var(--medium-gray); margin-top: 15px;">
            <i class="fas fa-info-circle"></i> A declaração será processada em até 5 dias úteis.
        </p>
    </div>
    '''

    return html


@app.route("/solicitar-declaracao", methods=["POST"])
def solicitar_declaracao():
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "message": "Não autenticado"})

    data = request.json
    tipo = data.get("tipo")
    tipo_nome = data.get("tipo_nome", "")
    vias = data.get("vias", "1")
    observacoes = data.get("observacoes", "")

    if not tipo:
        return jsonify({"success": False, "message": "Tipo não especificado"})

    # Determinar nome da declaração
    if not tipo_nome:
        if tipo == "matricula":
            tipo_nome = "Declaração de Matrícula"
        elif tipo == "historico":
            tipo_nome = "Histórico Parcial"
        else:
            tipo_nome = "Declaração"

    detalhes = f"{tipo_nome}"
    if vias != "1":
        detalhes += f" - {vias} vias"

    if observacoes:
        detalhes += f" ({observacoes})"

    conn = get_db_connection()
    cursor = conn.cursor()

    # Inserir solicitação
    data_solicitacao = datetime.now().strftime("%d/%m/%Y %H:%M")
    cursor.execute("""
        INSERT INTO solicitacoes_declaracoes (aluno_id, tipo, detalhes, data_solicitacao)
        VALUES (%s, %s, %s, %s)
    """, (aluno_id, tipo, detalhes, data_solicitacao))

    conn.commit()
    conn.close()

    return jsonify({"success": True, "message": "Solicitação registrada"})


# ==========================
# MEW - PAINEL ADMIN
# ==========================

@app.route("/mew/login", methods=["GET", "POST"])
def mew_login():
    if request.method == "POST":
        email = request.form.get("email")
        senha = request.form.get("senha")

        admin_email = os.environ.get("MEW_ADMIN_EMAIL")
        admin_password_hash = os.environ.get("MEW_ADMIN_PASSWORD_HASH")

        # PRIMEIRO: verifica se o email está correto
        if email != admin_email:
            flash("Email incorreto", "error")
            return render_template("mew/login.html")

        # SEGUNDO: verifica se a senha bate com o hash
        if admin_password_hash and check_password_hash(admin_password_hash, senha):
            session["mew_admin"] = True
            return redirect("/mew/dashboard")
        else:
            flash("Senha incorreta", "error")
            return render_template("mew/login.html")

    return render_template("mew/login.html")
'''

@app.route("/mew/login", methods=["GET", "POST"])
def mew_login():
    if request.method == "POST":
        email = request.form.get("email")
        senha = request.form.get("senha")

        if email == "admin@mew.com" and senha == "123456":
            session["mew_admin"] = True
            return redirect("/mew/dashboard")

    return render_template("mew/login.html")'''


@app.route("/mew/dashboard")
def mew_dashboard():
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
          (SELECT COUNT(*) FROM alunos) AS total_alunos,
          (SELECT COUNT(*) FROM disciplinas) AS total_disciplinas,
          (SELECT COUNT(*) FROM solicitacoes_material WHERE entregue=0) AS material_pendente,
          (SELECT COUNT(*) FROM solicitacoes_declaracoes WHERE entregue=0) AS declaracoes_pendente,
          (SELECT COUNT(*) FROM notas) AS total_provas,
          (SELECT COUNT(*) FROM solicitacoes_documentos WHERE status='pendente') AS documentos_pendente,
          (SELECT COUNT(*) FROM solicitacoes_documentos_integrados
             WHERE status IN ('pendente','erro','aguardando_aprovacao')) AS integrados_pendente,
          (SELECT COUNT(*) FROM documentos_autenticados WHERE tipo='plano_ensino') AS total_planos,
          (SELECT COUNT(*) FROM documentos_autenticados) AS total_documentos
    """)
    resumo = cursor.fetchone() or {}
    conn.close()
    total_alunos = resumo.get("total_alunos") or 0
    total_disciplinas = resumo.get("total_disciplinas") or 0
    material_pendente = resumo.get("material_pendente") or 0
    declaracoes_pendente = resumo.get("declaracoes_pendente") or 0
    total_solicitacoes_pendentes = material_pendente + declaracoes_pendente
    total_provas = resumo.get("total_provas") or 0
    documentos_pendente = resumo.get("documentos_pendente") or 0
    integrados_pendente = resumo.get("integrados_pendente") or 0
    total_planos = resumo.get("total_planos") or 0
    total_documentos = resumo.get("total_documentos") or 0

    return render_template(
        "mew/dashboard.html",
        total_alunos=total_alunos, total_disciplinas=total_disciplinas,
        total_solicitacoes_pendentes=total_solicitacoes_pendentes, total_provas=total_provas,
        total_solicitacoes_documentos_pendentes=documentos_pendente,
        total_documentos_integrados_pendentes=integrados_pendente,
        total_planos=total_planos, total_documentos=total_documentos
    )


@app.route("/mew/alunos", methods=["GET", "POST"])
def mew_alunos():
    if not session.get("mew_admin"):
        return redirect("/mew/login")


    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM disciplinas ORDER BY nome")
    disciplinas = cursor.fetchall()

    if request.method == "POST":
        nome = request.form.get("nome")
        email = request.form.get("email")
        senha = request.form.get("senha")
        cpf = request.form.get("cpf")
        cpf_somente_numeros = re.sub(r"\D", "", cpf or "")
        if len(cpf_somente_numeros) == 11:
            senha = cpf_somente_numeros
        rg = request.form.get("rg")
        telefone = request.form.get("telefone")
        endereco = request.form.get("endereco")
        cidade = request.form.get("cidade")
        estado = request.form.get("estado")
        cep = request.form.get("cep")
        curso_referencia = request.form.get("curso_referencia")
        forma_pagamento = request.form.get("forma_pagamento")
        valor_total_raw = request.form.get("valor_total")
        data_inicio = request.form.get("data_inicio")
        prazo_dias = int(request.form.get("prazo_dias", 60))

        nome_pai = request.form.get("nome_pai", "")
        nome_mae = request.form.get("nome_mae", "")
        data_nascimento = request.form.get("data_nascimento", "")
        sexo = request.form.get("sexo", "")
        naturalidade = request.form.get("naturalidade", "")
        nacionalidade = request.form.get("nacionalidade", "Brasileira")
        estado_civil = request.form.get("estado_civil", "")
        email_alternativo = request.form.get("email_alternativo", "")

        gerar_cobranca = request.form.get("gerar_cobranca") == "1"

        if not data_inicio:
            conn.close()
            return "Data de início não informada.", 400

        try:
            data_inicio_obj = datetime.strptime(data_inicio, "%Y-%m-%d")
        except ValueError:
            conn.close()
            return "Formato de data inválido.", 400

        valor_total = None
        if valor_total_raw:
            try:
                if "," in valor_total_raw:
                    valor_total = float(valor_total_raw.replace(".", "").replace(",", "."))
                else:
                    valor_total = float(valor_total_raw)
            except ValueError:
                conn.close()
                return "Valor total inválido.", 400

        if gerar_cobranca and (not valor_total or valor_total <= 0):
            conn.close()
            return "Informe um valor total válido para gerar a cobrança.", 400

        ra_input = request.form.get("ra", "").strip()
        if ra_input:
            if not ra_input.isdigit() or len(ra_input) != 8:
                conn.close()
                return "RA inválido. Deve conter exatamente 8 números.", 400
            ra = ra_input
            cursor.execute("SELECT id FROM alunos WHERE ra = %s", (ra,))
            if cursor.fetchone():
                conn.close()
                return "RA já existente. Utilize outro número.", 400
        else:
            while True:
                ra = gerar_ra()
                cursor.execute("SELECT id FROM alunos WHERE ra = %s", (ra,))
                if not cursor.fetchone():
                    break

        try:
            senha_para_banco = generate_password_hash(str(senha or ""))
            cursor.execute("""
                INSERT INTO alunos (nome, email, ra, senha)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (nome, email, ra, senha_para_banco))
            aluno_id = cursor.fetchone()["id"]

            cursor.execute("""
                INSERT INTO dados_pessoais
                (aluno_id, cpf, rg, telefone, endereco, cidade, estado, cep,
                 curso_referencia, nome_pai, nome_mae, naturalidade, nacionalidade,
                 data_nascimento, sexo, estado_civil, email_alternativo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (aluno_id, cpf, rg, telefone, endereco, cidade, estado, cep,
                  curso_referencia, nome_pai, nome_mae, naturalidade, nacionalidade,
                  data_nascimento, sexo, estado_civil, email_alternativo))

            if forma_pagamento and valor_total is not None:
                if gerar_cobranca:
                    status_financeiro = "pendente"
                    parcelas_total = 1
                    parcelas_pagas = 0
                elif forma_pagamento == "mercadopago":
                    status_financeiro = "pendente"
                    parcelas_total = 1
                    parcelas_pagas = 0
                elif forma_pagamento in ["avista", "cartao"]:
                    status_financeiro = "pago"
                    parcelas_total = 1
                    parcelas_pagas = 1
                else:
                    status_financeiro = "parcial"
                    parcelas_total = 2
                    parcelas_pagas = 1

                cursor.execute("""
                    INSERT INTO situacao_financeira
                    (aluno_id, forma_pagamento, status, parcelas_total, parcelas_pagas, valor_total)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (aluno_id, forma_pagamento, status_financeiro,
                      parcelas_total, parcelas_pagas, valor_total))

            data_fim_obj = data_inicio_obj + timedelta(days=prazo_dias)
            data_fim = data_fim_obj.strftime("%d/%m/%Y")
            data_inicio_formatada = data_inicio_obj.strftime("%d/%m/%Y")

            disciplinas_selecionadas = request.form.getlist("disciplinas")
            for d_id in disciplinas_selecionadas:
                cursor.execute("""
                    INSERT INTO aluno_disciplina (aluno_id, disciplina_id)
                    VALUES (%s, %s)
                """, (aluno_id, d_id))
                cursor.execute("""
                    INSERT INTO aluno_disciplina_datas
                    (aluno_id, disciplina_id, data_inicio, data_fim_previsto)
                    VALUES (%s, %s, %s, %s)
                """, (aluno_id, d_id, data_inicio_formatada, data_fim))

            conn.commit()
            conn.close()
        except Exception:
            conn.rollback()
            conn.close()
            raise

        # O contrato é padrão e obrigatório: cria automaticamente o registro
        # usando os dados do aluno, matrícula/RA, disciplinas e financeiro já salvos.
        contrato_id = criar_contrato_aluno(aluno_id)

        if gerar_cobranca:
            try:
                cobranca = criar_preferencia_mercadopago(
                    aluno_id=aluno_id,
                    nome=nome,
                    email=email,
                    valor_total=valor_total,
                    contrato_id=contrato_id,
                    base_url=request.host_url.rstrip("/")
                )
                return redirect(f"/mew/alunos?cobranca_id={cobranca['id']}")
            except Exception as e:
                print(f"Erro ao criar cobrança Mercado Pago: {e}")
                return redirect(f"/mew/alunos?erro_mp={str(e)}")

        return redirect("/mew/alunos?sucesso=Aluno+cadastrado+com+sucesso")

    # GET: listagem paginada e sem carregar PDFs/fotos/assinaturas do banco.
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = min(100, max(10, request.args.get("per_page", 50, type=int) or 50))
    offset = (page - 1) * per_page
    cursor.execute("SELECT COUNT(*) AS total FROM alunos")
    total_alunos = int((cursor.fetchone() or {}).get("total") or 0)

    cursor.execute("""
        SELECT a.id, a.nome, a.email, a.ra,
               dp.cpf, dp.telefone, dp.nome_pai, dp.nome_mae, dp.data_nascimento,
               dp.sexo, dp.naturalidade, dp.nacionalidade, dp.estado_civil, dp.email_alternativo,
               sf.forma_pagamento, sf.status AS status_financeiro, sf.valor_total,
               sf.parcelas_total, sf.parcelas_pagas,
               COALESCE(ds.total_disciplinas, 0) AS total_disciplinas,
               COALESCE(ds.disciplinas_datas, '[]'::json) AS disciplinas_datas,
               mp.id AS mp_id, mp.status AS mp_status, mp.checkout_url AS mp_checkout_url,
               mp.sandbox_checkout_url AS mp_sandbox_checkout_url,
               c.id AS contrato_id, c.status AS contrato_status, c.data_envio AS contrato_data_envio
        FROM alunos a
        LEFT JOIN dados_pessoais dp ON dp.aluno_id=a.id
        LEFT JOIN LATERAL (
            SELECT forma_pagamento, status, valor_total, parcelas_total, parcelas_pagas
            FROM situacao_financeira WHERE aluno_id=a.id ORDER BY id DESC LIMIT 1
        ) sf ON TRUE
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS total_disciplinas,
                   json_agg(json_build_object(
                       'disciplina_id', ad.disciplina_id, 'nome', d.nome,
                       'data_inicio', dd.data_inicio, 'data_fim_previsto', dd.data_fim_previsto
                   ) ORDER BY d.nome) AS disciplinas_datas
            FROM aluno_disciplina ad
            LEFT JOIN disciplinas d ON d.id=ad.disciplina_id
            LEFT JOIN aluno_disciplina_datas dd ON dd.aluno_id=ad.aluno_id AND dd.disciplina_id=ad.disciplina_id
            WHERE ad.aluno_id=a.id
        ) ds ON TRUE
        LEFT JOIN LATERAL (
            SELECT id, status, checkout_url, sandbox_checkout_url
            FROM pagamentos_mercadopago WHERE aluno_id=a.id
            ORDER BY CASE WHEN status='pago' THEN 0 ELSE 1 END, id DESC LIMIT 1
        ) mp ON TRUE
        LEFT JOIN LATERAL (
            SELECT id, status, data_envio FROM contratos_alunos
            WHERE aluno_id=a.id ORDER BY id DESC LIMIT 1
        ) c ON TRUE
        ORDER BY a.nome
        LIMIT %s OFFSET %s
    """, (per_page, offset))
    alunos_completo=[]
    for row in cursor.fetchall():
        r=dict(row)
        pagamento_mp = None if not r.get('mp_id') else {
            'id': r.get('mp_id'), 'status': r.get('mp_status'),
            'checkout_url': r.get('mp_checkout_url'), 'sandbox_checkout_url': r.get('mp_sandbox_checkout_url')
        }
        contrato = None if not r.get('contrato_id') else {
            'id': r.get('contrato_id'), 'status': r.get('contrato_status'), 'data_envio': r.get('contrato_data_envio')
        }
        alunos_completo.append({
            'id': r.get('id'), 'nome': r.get('nome'), 'email': r.get('email'), 'ra': r.get('ra'),
            'cpf': r.get('cpf') or '', 'telefone': r.get('telefone') or '',
            'forma_pagamento': r.get('forma_pagamento') or '', 'status_financeiro': r.get('status_financeiro') or '',
            'valor_total': r.get('valor_total') or 0, 'parcelas_total': r.get('parcelas_total') or 0,
            'parcelas_pagas': r.get('parcelas_pagas') or 0, 'total_disciplinas': r.get('total_disciplinas') or 0,
            'disciplinas_datas': r.get('disciplinas_datas') or [], 'nome_pai': r.get('nome_pai') or '',
            'nome_mae': r.get('nome_mae') or '', 'data_nascimento': r.get('data_nascimento') or '',
            'sexo': r.get('sexo') or '', 'naturalidade': r.get('naturalidade') or '',
            'nacionalidade': r.get('nacionalidade') or '', 'estado_civil': r.get('estado_civil') or '',
            'email_alternativo': r.get('email_alternativo') or '', 'pagamento_mp': pagamento_mp, 'contrato': contrato
        })

    cobranca_criada = None
    cobranca_id = request.args.get("cobranca_id")
    if cobranca_id and cobranca_id.isdigit():
        cursor.execute("SELECT id, aluno_id, status, checkout_url, sandbox_checkout_url, external_reference FROM pagamentos_mercadopago WHERE id=%s", (int(cobranca_id),))
        cobranca_criada = cursor.fetchone()
    conn.close()
    total_pages = max(1, (total_alunos + per_page - 1) // per_page)
    return render_template(
        "mew/alunos.html", disciplinas=disciplinas, alunos=alunos_completo,
        cobranca_criada=cobranca_criada, erro_mp=request.args.get("erro_mp"), sucesso=request.args.get("sucesso"),
        page=page, per_page=per_page, total_alunos=total_alunos, total_pages=total_pages
    )

@app.route("/mew/gerar-cobranca/<int:aluno_id>", methods=["POST"])
def mew_gerar_cobranca_aluno(aluno_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.id, a.nome, a.email, sf.valor_total
        FROM alunos a
        LEFT JOIN LATERAL (
            SELECT valor_total FROM situacao_financeira
            WHERE aluno_id = a.id ORDER BY id DESC LIMIT 1
        ) sf ON TRUE
        WHERE a.id = %s
    """, (aluno_id,))
    aluno = cursor.fetchone()
    cursor.execute("SELECT id FROM contratos_alunos WHERE aluno_id = %s ORDER BY id DESC LIMIT 1", (aluno_id,))
    contrato = cursor.fetchone()
    conn.close()

    if not aluno or not aluno["valor_total"]:
        return redirect("/mew/alunos?erro_mp=Aluno+sem+valor+financeiro+cadastrado")

    try:
        cobranca = criar_preferencia_mercadopago(
            aluno_id=aluno_id,
            nome=aluno["nome"],
            email=aluno["email"],
            valor_total=aluno["valor_total"],
            contrato_id=contrato["id"] if contrato else None,
            base_url=request.host_url.rstrip("/")
        )
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE situacao_financeira
            SET status = 'pendente', parcelas_pagas = 0
            WHERE id = (SELECT id FROM situacao_financeira WHERE aluno_id = %s ORDER BY id DESC LIMIT 1)
        """, (aluno_id,))
        conn.commit()
        conn.close()
        return redirect(f"/mew/alunos?cobranca_id={cobranca['id']}")
    except Exception as e:
        return redirect(f"/mew/alunos?erro_mp={str(e)}")


@app.route("/webhook/mercadopago", methods=["POST"])
def webhook_mercadopago():
    dados = request.get_json(silent=True) or {}
    tipo = dados.get("type") or request.args.get("type")
    payment_id = (dados.get("data") or {}).get("id") or request.args.get("data.id") or request.args.get("id")

    if tipo not in (None, "payment") or not payment_id:
        return jsonify({"ok": True}), 200

    # Se a chave secreta do Webhook estiver configurada, valida a assinatura x-signature.
    webhook_secret = os.getenv("MERCADOPAGO_WEBHOOK_SECRET")
    if webhook_secret:
        x_signature = request.headers.get("x-signature")
        x_request_id = request.headers.get("x-request-id")
        data_id_assinatura = request.args.get("data.id")
        if not x_signature or not x_request_id or not data_id_assinatura:
            return jsonify({"ok": False, "erro": "assinatura ausente"}), 401
        try:
            partes = {}
            for parte in x_signature.split(","):
                if "=" in parte:
                    chave, valor = parte.split("=", 1)
                    partes[chave.strip()] = valor.strip()
            ts = partes.get("ts")
            v1 = partes.get("v1")
            manifest = f"id:{str(data_id_assinatura).lower()};request-id:{x_request_id};ts:{ts};"
            import hmac
            esperado = hmac.new(webhook_secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
            if not v1 or not hmac.compare_digest(esperado, v1):
                return jsonify({"ok": False, "erro": "assinatura inválida"}), 401
        except Exception as e:
            print(f"Erro ao validar assinatura Mercado Pago: {e}")
            return jsonify({"ok": False, "erro": "falha na assinatura"}), 401

    try:
        sdk = get_mercadopago_sdk()
        resultado = sdk.payment().get(payment_id)
        pagamento = resultado.get("response", {}) if isinstance(resultado, dict) else {}
        external_reference = pagamento.get("external_reference")
        status_mp = pagamento.get("status") or "unknown"

        if not external_reference:
            return jsonify({"ok": True}), 200

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pagamentos_mercadopago WHERE external_reference = %s", (external_reference,))
        cobranca = cursor.fetchone()
        if not cobranca:
            conn.close()
            return jsonify({"ok": True}), 200

        agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        ja_pago = cobranca.get("status") == "pago"
        status_local = "pago" if (status_mp == "approved" or ja_pago) else "nao_pago"
        data_pagamento = agora if status_mp == "approved" else cobranca.get("data_pagamento")

        cursor.execute("""
            UPDATE pagamentos_mercadopago
            SET payment_id = %s, status = %s, status_mp = %s,
                data_atualizacao = %s, data_pagamento = %s
            WHERE id = %s
        """, (str(payment_id), status_local, status_mp, agora, data_pagamento, cobranca["id"]))

        if status_mp == "approved":
            cursor.execute("""
                UPDATE situacao_financeira
                SET status = 'pago', parcelas_pagas = parcelas_total
                WHERE id = (
                    SELECT id FROM situacao_financeira
                    WHERE aluno_id = %s ORDER BY id DESC LIMIT 1
                )
            """, (cobranca["aluno_id"],))
        elif not ja_pago:
            cursor.execute("""
                UPDATE situacao_financeira
                SET status = 'pendente', parcelas_pagas = 0
                WHERE id = (
                    SELECT id FROM situacao_financeira
                    WHERE aluno_id = %s ORDER BY id DESC LIMIT 1
                )
            """, (cobranca["aluno_id"],))

        conn.commit()
        aluno_id_email = cobranca["aluno_id"]
        conn.close()

        # E-mail transacional. Compras iniciadas pela nova home seguem primeiro para
        # conferência documental; matrículas criadas pelo MEW mantêm o fluxo antigo.
        if status_mp == "approved":
            try:
                solicitacao_publica = _solicitacao_publica_por_cobranca(cobranca["id"])
                if solicitacao_publica:
                    novo_pagamento = _marcar_solicitacao_publica_pago(solicitacao_publica["id"], str(payment_id))
                    if novo_pagamento:
                        enviar_email_pagamento_publico(solicitacao_publica["id"])
                        enviar_alerta_admin_matricula_publica(solicitacao_publica["id"], fase="pagamento")
                else:
                    enviar_boas_vindas_titan(
                        aluno_id_email,
                        referencia=f"mp:{payment_id}:boas_vindas",
                        pagamento_id=str(payment_id)
                    )
            except Exception as email_erro:
                print(f"Aviso: pagamento aprovado, mas o e-mail Titan não foi enviado: {email_erro}")

        return jsonify({"ok": True}), 200
    except Exception as e:
        print(f"Erro webhook Mercado Pago: {e}")
        return jsonify({"ok": False}), 500


@app.route("/pagamento/mercadopago/sucesso")
def pagamento_mercadopago_sucesso():
    token = _token_solicitacao_publica_retorno_mp()
    if token:
        payment_id = request.args.get("payment_id") or request.args.get("collection_id")
        if payment_id:
            try:
                _sincronizar_pagamento_publico(token, str(payment_id))
            except Exception as exc:
                print(f"Aviso: retorno do Mercado Pago aguardando webhook: {exc}")
        return redirect(url_for("matricula_publica_documentos", token=token))
    return render_template_string("""
    <div style='font-family:Arial;max-width:680px;margin:70px auto;text-align:center'>
      <h1 style='color:#15803d'>Pagamento recebido</h1>
      <p>O Mercado Pago informou que o pagamento foi aprovado. O status também será confirmado automaticamente pelo sistema.</p>
      <a href='/dashboard'>Ir para o ambiente do aluno</a>
    </div>""")


@app.route("/pagamento/mercadopago/pendente")
def pagamento_mercadopago_pendente():
    token = _token_solicitacao_publica_retorno_mp()
    if token:
        return redirect(url_for("matricula_publica_documentos", token=token))
    return render_template_string("""
    <div style='font-family:Arial;max-width:680px;margin:70px auto;text-align:center'>
      <h1>Pagamento pendente</h1><p>Assim que o Mercado Pago aprovar, o sistema atualizará o status automaticamente.</p>
      <a href='/'>Voltar</a>
    </div>""")


@app.route("/pagamento/mercadopago/falha")
def pagamento_mercadopago_falha():
    token = _token_solicitacao_publica_retorno_mp()
    if token:
        return redirect(url_for("matricula_publica_contratar", token=token))
    return render_template_string("""
    <div style='font-family:Arial;max-width:680px;margin:70px auto;text-align:center'>
      <h1 style='color:#b91c1c'>Pagamento não concluído</h1><p>Nenhuma baixa financeira foi realizada.</p>
      <a href='/'>Voltar</a>
    </div>""")


@app.route("/mew/disciplinas", methods=["GET", "POST"])
def mew_disciplinas():
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    conn=get_db_connection(); cursor=conn.cursor()
    if request.method == "POST":
        nome_disciplina=(request.form.get("nome_disciplina") or "").strip()
        if not nome_disciplina:
            conn.close(); return "Nome da disciplina é obrigatório.", 400
        try:
            cursor.execute("INSERT INTO disciplinas (nome) VALUES (%s) RETURNING id", (nome_disciplina,))
            disciplina_id=cursor.fetchone()["id"]
            for i in range(1,5):
                titulo=request.form.get(f"titulo_{i}")
                video_url=request.form.get(f"video_{i}")
                pdf_url=request.form.get(f"pdf_{i}")
                arq=request.files.get(f"questoes_xlsx_{i}")
                try:
                    questoes=questoes_xlsx_upload(arq) if arq and arq.filename else parse_questoes_texto(request.form.get(f"questoes_{i}"))
                except Exception as e:
                    raise ValueError(f"Capítulo {i}: {e}")
                if not questoes:
                    raise ValueError(f"Capítulo {i}: informe pelo menos uma questão ou envie uma planilha .xlsx.")
                questoes_json=json.dumps(questoes, ensure_ascii=False)
                cursor.execute("INSERT INTO capitulos (disciplina_id,titulo,video_url,pdf_url) VALUES (%s,%s,%s,%s) RETURNING id",(disciplina_id,titulo,video_url,pdf_url))
                capitulo_id=cursor.fetchone()["id"]
                cursor.execute("INSERT INTO provas (capitulo_id,questoes_json) VALUES (%s,%s)",(capitulo_id,questoes_json))
            conn.commit()
        except Exception as e:
            conn.rollback(); conn.close(); return f"Não foi possível criar a disciplina: {escape(str(e))}", 400
        conn.close(); return redirect("/mew/disciplinas")
    cursor.execute("SELECT id,nome,carga_horaria FROM disciplinas ORDER BY id")
    disciplinas=cursor.fetchall(); conn.close()
    return render_template("mew/disciplinas.html", disciplinas=disciplinas)


@app.route("/mew/editar-disciplina/<int:disciplina_id>", methods=["GET", "POST"])
def mew_editar_disciplina(disciplina_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    conn=get_db_connection(); cursor=conn.cursor()
    if request.method == "POST":
        try:
            cursor.execute("UPDATE disciplinas SET nome=%s WHERE id=%s",((request.form.get("nome_disciplina") or "").strip(),disciplina_id))
            cursor.execute("SELECT id FROM capitulos WHERE disciplina_id=%s ORDER BY id",(disciplina_id,))
            capitulos=cursor.fetchall()
            for i,cap in enumerate(capitulos,start=1):
                titulo=request.form.get(f"titulo_{i}"); video=request.form.get(f"video_{i}"); pdf=request.form.get(f"pdf_{i}")
                arq=request.files.get(f"questoes_xlsx_{i}")
                questoes=questoes_xlsx_upload(arq) if arq and arq.filename else parse_questoes_texto(request.form.get(f"questoes_{i}"))
                if not questoes:
                    raise ValueError(f"Capítulo {i}: informe pelo menos uma questão.")
                cursor.execute("UPDATE capitulos SET titulo=%s,video_url=%s,pdf_url=%s WHERE id=%s",(titulo,video,pdf,cap["id"]))
                cursor.execute("UPDATE provas SET questoes_json=%s WHERE capitulo_id=%s",(json.dumps(questoes,ensure_ascii=False),cap["id"]))
            conn.commit()
        except Exception as e:
            conn.rollback(); conn.close(); return f"Não foi possível salvar: {escape(str(e))}", 400
        conn.close(); return redirect("/mew/disciplinas")
    cursor.execute("SELECT * FROM disciplinas WHERE id=%s",(disciplina_id,)); disciplina=cursor.fetchone()
    cursor.execute("SELECT c.*,p.questoes_json FROM capitulos c LEFT JOIN provas p ON p.capitulo_id=c.id WHERE c.disciplina_id=%s ORDER BY c.id",(disciplina_id,))
    capitulos=[]
    for row in cursor.fetchall():
        r=dict(row); r["questoes_tabela"]=questoes_para_tabela(r.get("questoes_json")); capitulos.append(r)
    conn.close()
    return render_template("mew/editar_disciplina.html",disciplina=disciplina,capitulos=capitulos)


@app.route("/mew/solicitacoes")
def mew_solicitacoes():
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar solicitações de material
    cursor.execute("""
        SELECT sm.*, a.nome as aluno_nome, d.nome as disciplina_nome
        FROM solicitacoes_material sm
        JOIN alunos a ON sm.aluno_id = a.id
        LEFT JOIN disciplinas d ON sm.disciplina_id = d.id
        ORDER BY sm.data_solicitacao DESC
    """)
    solicitacoes_material = cursor.fetchall()

    # Buscar solicitações de declarações
    cursor.execute("""
        SELECT sd.*, a.nome as aluno_nome
        FROM solicitacoes_declaracoes sd
        JOIN alunos a ON sd.aluno_id = a.id
        ORDER BY sd.data_solicitacao DESC
    """)
    solicitacoes_declaracoes = cursor.fetchall()

    # Buscar solicitações de documentos
    cursor.execute("""
        SELECT sd.*, a.nome as aluno_nome, a.email as aluno_email,
               COALESCE((
                   SELECT STRING_AGG(d.nome, ',' ORDER BY d.nome)
                   FROM disciplinas d
                   WHERE d.id = ANY(string_to_array(NULLIF(sd.disciplinas_ids,''), ',')::int[])
               ), '') AS disciplinas_nomes
        FROM solicitacoes_documentos sd
        JOIN alunos a ON sd.aluno_id = a.id
        ORDER BY sd.data_solicitacao DESC
    """)
    solicitacoes_documentos = cursor.fetchall()

    conn.close()

    return render_template(
        "mew/solicitacoes.html",
        solicitacoes_material=solicitacoes_material,
        solicitacoes_declaracoes=solicitacoes_declaracoes,
        solicitacoes_documentos=solicitacoes_documentos
    )

@app.route("/mew/marcar-entregue/<tipo>/<int:id>")
def mew_marcar_entregue(tipo, id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    if tipo == "material":
        cursor.execute("""
            UPDATE solicitacoes_material
            SET entregue = 1
            WHERE id = %s
        """, (id,))
    elif tipo == "declaracao":
        cursor.execute("""
            UPDATE solicitacoes_declaracoes
            SET entregue = 1
            WHERE id = %s
        """, (id,))

    conn.commit()
    conn.close()

    return redirect("/mew/solicitacoes")


@app.route("/mew/deletar-solicitacao/<tipo>/<int:id>")
def mew_deletar_solicitacao(tipo, id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    if tipo == "material":
        cursor.execute("DELETE FROM solicitacoes_material WHERE id = %s", (id,))
    elif tipo == "declaracao":
        cursor.execute("DELETE FROM solicitacoes_declaracoes WHERE id = %s", (id,))

    conn.commit()
    conn.close()

    return redirect("/mew/solicitacoes")

@app.route("/mew/logout")
def mew_logout():
    session.pop("mew_admin", None)
    return redirect("/mew/login")

@app.route("/mew/editar-aluno/<int:aluno_id>", methods=["GET", "POST"])
def mew_editar_aluno(aluno_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    from datetime import datetime, timedelta

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        if request.method == "POST":
            # Dados básicos
            nome = request.form.get("nome")
            email = request.form.get("email")
            senha = request.form.get("senha")
            cpf = request.form.get("cpf")
            rg = request.form.get("rg")
            telefone = request.form.get("telefone")
            endereco = request.form.get("endereco")
            cidade = request.form.get("cidade")
            estado = request.form.get("estado")
            cep = request.form.get("cep")
            curso_referencia = request.form.get("curso_referencia")
            prazo_dias = int(request.form.get("prazo_dias", 60))

            # === NOVOS CAMPOS DE DADOS PESSOAIS ===
            nome_pai = request.form.get("nome_pai", "")
            nome_mae = request.form.get("nome_mae", "")
            data_nascimento = request.form.get("data_nascimento", "")
            sexo = request.form.get("sexo", "")
            naturalidade = request.form.get("naturalidade", "")
            nacionalidade = request.form.get("nacionalidade", "Brasileira")
            estado_civil = request.form.get("estado_civil", "")
            email_alternativo = request.form.get("email_alternativo", "")
            # ======================================

            # Atualizar tabela alunos
            if senha:
                cursor.execute("""
                    UPDATE alunos
                    SET nome = %s, email = %s, senha = %s
                    WHERE id = %s
                """, (nome, email, generate_password_hash(str(senha)), aluno_id))
            else:
                cursor.execute("""
                    UPDATE alunos
                    SET nome = %s, email = %s
                    WHERE id = %s
                """, (nome, email, aluno_id))

            # Verificar se já existem dados pessoais
            cursor.execute("SELECT id FROM dados_pessoais WHERE aluno_id = %s", (aluno_id,))
            dados_existentes = cursor.fetchone()

            if dados_existentes:
                # Atualizar dados existentes (COM TODOS OS CAMPOS)
                cursor.execute("""
                    UPDATE dados_pessoais
                    SET cpf = %s, rg = %s, telefone = %s, endereco = %s,
                        cidade = %s, estado = %s, cep = %s, curso_referencia = %s,
                        nome_pai = %s, nome_mae = %s, naturalidade = %s, nacionalidade = %s,
                        data_nascimento = %s, sexo = %s, estado_civil = %s, email_alternativo = %s
                    WHERE aluno_id = %s
                """, (cpf, rg, telefone, endereco, cidade, estado, cep, curso_referencia,
                      nome_pai, nome_mae, naturalidade, nacionalidade,
                      data_nascimento, sexo, estado_civil, email_alternativo, aluno_id))
            else:
                # Inserir novos dados (COM TODOS OS CAMPOS)
                cursor.execute("""
                    INSERT INTO dados_pessoais
                    (aluno_id, cpf, rg, telefone, endereco, cidade, estado, cep,
                     curso_referencia, nome_pai, nome_mae, naturalidade, nacionalidade,
                     data_nascimento, sexo, estado_civil, email_alternativo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (aluno_id, cpf, rg, telefone, endereco, cidade, estado, cep,
                      curso_referencia, nome_pai, nome_mae, naturalidade, nacionalidade,
                      data_nascimento, sexo, estado_civil, email_alternativo))

            # ===== ATUALIZAR SITUAÇÃO FINANCEIRA =====
            forma_pagamento = request.form.get("forma_pagamento")
            valor_total = request.form.get("valor_total")
            status_financeiro = request.form.get("status_financeiro")
            parcelas_pagas = request.form.get("parcelas_pagas", "1")

            if forma_pagamento and valor_total:
                try:
                    valor_total_float = float(valor_total.replace(",", "."))
                except ValueError:
                    conn.close()
                    return "Valor total inválido.", 400

                # Determinar parcelas totais
                if forma_pagamento == "boleto_pix":
                    parcelas_total = 2
                    # Se pagou as 2 parcelas, status é "pago"
                    if parcelas_pagas == "2":
                        status_financeiro = "pago"
                    elif not status_financeiro:
                        status_financeiro = "parcial"
                else:
                    parcelas_total = 1
                    if not status_financeiro:
                        status_financeiro = "pago"

                # Verificar se já existe situação financeira
                cursor.execute("SELECT id FROM situacao_financeira WHERE aluno_id = %s", (aluno_id,))
                situacao_existente = cursor.fetchone()

                if situacao_existente:
                    # Atualizar
                    cursor.execute("""
                        UPDATE situacao_financeira
                        SET forma_pagamento = %s, status = %s,
                            parcelas_total = %s, parcelas_pagas = %s,
                            valor_total = %s
                        WHERE aluno_id = %s
                    """, (forma_pagamento, status_financeiro,
                          parcelas_total, parcelas_pagas,
                          valor_total_float, aluno_id))
                else:
                    # Inserir
                    cursor.execute("""
                        INSERT INTO situacao_financeira
                        (aluno_id, forma_pagamento, status,
                         parcelas_total, parcelas_pagas, valor_total)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (aluno_id, forma_pagamento, status_financeiro,
                          parcelas_total, parcelas_pagas, valor_total_float))

            # Gerenciar disciplinas
            if request.form.get("gerenciar_disciplinas"):
                disciplinas_selecionadas = request.form.getlist("disciplinas")

                # Buscar disciplinas atuais
                cursor.execute("SELECT disciplina_id FROM aluno_disciplina WHERE aluno_id = %s", (aluno_id,))
                disciplinas_atuais = [str(row['disciplina_id']) for row in cursor.fetchall()]

                # Remover disciplinas desmarcadas
                for d_id in disciplinas_atuais:
                    if d_id not in disciplinas_selecionadas:
                        try:
                            cursor.execute("DELETE FROM aluno_disciplina WHERE aluno_id = %s AND disciplina_id = %s",
                                          (aluno_id, d_id))
                            cursor.execute("DELETE FROM aluno_disciplina_datas WHERE aluno_id = %s AND disciplina_id = %s",
                                          (aluno_id, d_id))
                        except:
                            pass  # Ignorar erros em exclusões

                # Adicionar/atualizar disciplinas selecionadas
                for d_id in disciplinas_selecionadas:
                    # Verificar se já existe matrícula
                    cursor.execute("SELECT id FROM aluno_disciplina WHERE aluno_id = %s AND disciplina_id = %s",
                                  (aluno_id, d_id))
                    existe = cursor.fetchone()

                    if not existe:
                        # Adicionar nova matrícula
                        cursor.execute("""
                            INSERT INTO aluno_disciplina (aluno_id, disciplina_id)
                            VALUES (%s, %s)
                        """, (aluno_id, d_id))

                    # Obter data específica para esta disciplina
                    data_inicio_key = f"data_inicio_{d_id}"
                    data_inicio = request.form.get(data_inicio_key)

                    if data_inicio:
                        try:
                            data_inicio_obj = datetime.strptime(data_inicio, "%Y-%m-%d")
                            data_fim_obj = data_inicio_obj + timedelta(days=prazo_dias)
                            data_fim = data_fim_obj.strftime("%d/%m/%Y")

                            data_inicio_formatada = data_inicio_obj.strftime("%d/%m/%Y")

                            cursor.execute("""
                                INSERT INTO aluno_disciplina_datas
                                (aluno_id, disciplina_id, data_inicio, data_fim_previsto)
                                VALUES (%s, %s, %s, %s)
                                ON CONFLICT (aluno_id, disciplina_id) DO UPDATE SET
                                    data_inicio = EXCLUDED.data_inicio,
                                    data_fim_previsto = EXCLUDED.data_fim_previsto,
                                    prova_final_aberta = 0
                            """, (aluno_id, d_id, data_inicio_formatada, data_fim))
                        except Exception as e:
                            print(f"Erro ao processar data da disciplina {d_id}: {e}")

            conn.commit()
            conn.close()
            return redirect("/mew/alunos")

    except Exception as e:
        if 'conn' in locals():
            try:
                conn.close()
            except:
                pass
        return f"Erro ao processar: {str(e)}", 500

    # GET: Buscar dados do aluno para edição
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM alunos WHERE id = %s", (aluno_id,))
        aluno = cursor.fetchone()

        if not aluno:
            conn.close()
            return "Aluno não encontrado", 404

        cursor.execute("SELECT * FROM dados_pessoais WHERE aluno_id = %s", (aluno_id,))
        dados_pessoais = cursor.fetchone()

        # Buscar situação financeira
        cursor.execute("""
            SELECT * FROM situacao_financeira
            WHERE aluno_id = %s
            ORDER BY id DESC
            LIMIT 1
        """, (aluno_id,))
        situacao_financeira = cursor.fetchone()

        # Buscar todas as disciplinas disponíveis
        cursor.execute("SELECT * FROM disciplinas ORDER BY nome")
        disciplinas = cursor.fetchall()

        # Buscar disciplinas atuais do aluno com suas datas
        cursor.execute("""
            SELECT ad.disciplina_id, d.nome, addd.data_inicio, addd.data_fim_previsto
            FROM aluno_disciplina ad
            LEFT JOIN disciplinas d ON ad.disciplina_id = d.id
            LEFT JOIN aluno_disciplina_datas addd ON ad.aluno_id = addd.aluno_id AND ad.disciplina_id = addd.disciplina_id
            WHERE ad.aluno_id = %s
        """, (aluno_id,))
        disciplinas_aluno = cursor.fetchall()

        # Criar dicionário para fácil acesso às datas por disciplina
        datas_disciplinas = {}
        for d in disciplinas_aluno:
            if d['data_inicio']:
                try:
                    data_obj = datetime.strptime(d['data_inicio'], "%d/%m/%Y")
                    datas_disciplinas[str(d['disciplina_id'])] = data_obj.strftime("%Y-%m-%d")
                except:
                    datas_disciplinas[str(d['disciplina_id'])] = ""

        conn.close()

        return render_template(
            "mew/editar_aluno.html",
            aluno=aluno,
            dados_pessoais=dados_pessoais,
            situacao_financeira=situacao_financeira,
            disciplinas=disciplinas,
            disciplinas_aluno=disciplinas_aluno,
            datas_disciplinas=datas_disciplinas,
            prazo_dias_aluno=60
        )

    except Exception as e:
        if 'conn' in locals():
            try:
                conn.close()
            except:
                pass
        return f"Erro ao carregar dados: {str(e)}", 500


@app.route("/mew/deletar-aluno/<int:aluno_id>")
def mew_deletar_aluno(aluno_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Deletar em cascata (começando pelas tabelas dependentes)
    cursor.execute("DELETE FROM situacao_financeira WHERE aluno_id = %s", (aluno_id,))
    cursor.execute("DELETE FROM dados_pessoais WHERE aluno_id = %s", (aluno_id,))
    cursor.execute("DELETE FROM notas WHERE aluno_id = %s", (aluno_id,))
    cursor.execute("DELETE FROM aluno_disciplina WHERE aluno_id = %s", (aluno_id,))
    cursor.execute("DELETE FROM solicitacoes_material WHERE aluno_id = %s", (aluno_id,))
    cursor.execute("DELETE FROM solicitacoes_declaracoes WHERE aluno_id = %s", (aluno_id,))
    cursor.execute("DELETE FROM alunos WHERE id = %s", (aluno_id,))

    conn.commit()
    conn.close()

    return redirect("/mew/alunos")

@app.route("/solicitar-documentos-modal", methods=["GET"])
def solicitar_documentos_modal():
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return "Não autenticado", 401

    tipo = request.args.get("tipo")
    nome = request.args.get("nome")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT d.id, d.nome
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        WHERE ad.aluno_id = %s
        ORDER BY d.nome
    """, (aluno_id,))

    disciplinas = cursor.fetchall()
    conn.close()

    html = f'''
    <div class="document-form">
        <input type="hidden" id="docTipo" value="{tipo}">
        <input type="hidden" id="docNome" value="{nome}">

        <div class="form-group">
            <label><i class="fas fa-book"></i> Selecione as Disciplinas</label>
            <p style="font-size: 14px; color: var(--gray-600); margin-bottom: 10px;">
                Selecione uma ou mais disciplinas relacionadas ao documento:
            </p>
            <div style="max-height: 250px; overflow-y: auto; border: 1px solid #ddd; border-radius: 8px; padding: 10px;">
    '''

    if disciplinas:
        for d in disciplinas:
            html += f'''
            <div style="margin-bottom: 8px; padding: 5px;">
                <label style="display: flex; align-items: center; cursor: pointer;">
                    <input type="checkbox" class="disciplina-checkbox" value="{d['id']}" style="margin-right: 10px; width: 18px; height: 18px;">
                    <span>{d['nome']}</span>
                </label>
            </div>
            '''
    else:
        html += '''
        <div style="text-align: center; padding: 20px;">
            <i class="fas fa-exclamation-circle" style="font-size: 24px; color: var(--warning);"></i>
            <p>Você não está matriculado em nenhuma disciplina.</p>
        </div>
        '''

    html += '''
            </div>
        </div>

        <div class="form-group" style="margin-top: 20px;">
            <label><i class="fas fa-pencil-alt"></i> Detalhes da Solicitação</label>
            <textarea class="form-control" id="docDetalhes" rows="4"
                      placeholder="Descreva os detalhes da sua solicitação..."></textarea>
        </div>

        <div class="form-group" style="margin-top: 15px;">
            <label><i class="fas fa-copy"></i> Quantidade de Vias</label>
            <select class="form-control" id="docVias">
                <option value="1">1 via</option>
                <option value="2">2 vias</option>
                <option value="3">3 vias</option>
            </select>
        </div>

        <p style="font-size: 13px; color: var(--gray-600); margin-top: 15px; padding: 10px; background: #e8f5e8; border-radius: 5px;">
            <i class="fas fa-info-circle" style="color: var(--success);"></i>
            Sua solicitação será processada em até 5 dias úteis.
        </p>
    </div>
    '''

    return html

@app.route("/solicitar-documento", methods=["POST"])
def solicitar_documento():
    """Processa a solicitação de documento"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "message": "Não autenticado"})

    data = request.json
    tipo = data.get("tipo")
    nome = data.get("nome")
    disciplinas_ids = data.get("disciplinas_ids", [])
    detalhes = data.get("detalhes", "")
    vias = data.get("vias", "1")

    if not tipo or not disciplinas_ids:
        return jsonify({"success": False, "message": "Dados incompletos"})

    # Formatar detalhes com vias
    detalhes_formatado = detalhes
    if vias != "1":
        detalhes_formatado += f" ({vias} vias)"

    conn = get_db_connection()
    cursor = conn.cursor()

    # Inserir solicitação
    data_solicitacao = datetime.now().strftime("%d/%m/%Y %H:%M")
    disciplinas_str = ",".join(map(str, disciplinas_ids))

    cursor.execute("""
        INSERT INTO solicitacoes_documentos
        (aluno_id, tipo_documento, disciplinas_ids, detalhes, data_solicitacao)
        VALUES (%s, %s, %s, %s, %s)
    """, (aluno_id, tipo, disciplinas_str, detalhes_formatado, data_solicitacao))

    conn.commit()
    conn.close()

    return jsonify({"success": True, "message": "Solicitação registrada com sucesso!"})

@app.route("/historico-documentos")
def historico_documentos():
    """Retorna histórico do aluno sem uma consulta adicional por solicitação."""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "message": "Não autenticado"})
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        SELECT sd.*,
               COALESCE((
                   SELECT STRING_AGG(d.nome, ',' ORDER BY d.nome)
                   FROM disciplinas d
                   WHERE d.id = ANY(string_to_array(NULLIF(sd.disciplinas_ids,''), ',')::int[])
               ), 'N/A') AS disciplinas_nomes
        FROM solicitacoes_documentos sd
        WHERE sd.aluno_id = %s
        ORDER BY sd.data_solicitacao DESC
        LIMIT 300
    """, (aluno_id,))
    resultado = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({"success": True, "solicitacoes": resultado})

@app.route("/mew/solicitacoes-documentos")
def mew_solicitacoes_documentos():
    """Painel MEW paginado e sem N+1 para nomes das disciplinas."""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = 50
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS total FROM solicitacoes_documentos")
    total = int((cursor.fetchone() or {}).get("total") or 0)
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page
    cursor.execute("""
        SELECT sd.*, a.nome as aluno_nome, a.email as aluno_email,
               COALESCE((
                   SELECT STRING_AGG(d.nome, ',' ORDER BY d.nome)
                   FROM disciplinas d
                   WHERE d.id = ANY(string_to_array(NULLIF(sd.disciplinas_ids,''), ',')::int[])
               ), '') AS disciplinas_nomes
        FROM solicitacoes_documentos sd
        JOIN alunos a ON sd.aluno_id = a.id
        ORDER BY
            CASE sd.status
                WHEN 'pendente' THEN 1
                WHEN 'processando' THEN 2
                WHEN 'concluido' THEN 3
                ELSE 4
            END,
            sd.data_solicitacao DESC
        LIMIT %s OFFSET %s
    """, (per_page, offset))
    solicitacoes = cursor.fetchall()
    conn.close()
    return render_template("mew/solicitacoes_documentos.html", solicitacoes=solicitacoes,
                           page=page, total_pages=total_pages, total_solicitacoes=total)

@app.route("/mew/responder-documento/<int:id>", methods=["POST"])
def mew_responder_documento(id):
    """MEW responde à solicitação de documento"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})

    data = request.json
    resposta = data.get("resposta", "")
    status = data.get("status", "concluido")
    arquivo_url = data.get("arquivo_url", "")

    conn = get_db_connection()
    cursor = conn.cursor()

    data_resposta = datetime.now().strftime("%d/%m/%Y %H:%M")

    cursor.execute("""
        UPDATE solicitacoes_documentos
        SET status = %s, resposta = %s, arquivo_url = %s, data_resposta = %s
        WHERE id = %s
    """, (status, resposta, arquivo_url, data_resposta, id))

    conn.commit()
    conn.close()

    return jsonify({"success": True, "message": "Resposta registrada"})

@app.route("/mew/deletar-solicitacao-doc/<int:id>")
def mew_deletar_solicitacao_doc(id):
    """MEW deleta solicitação de documento"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM solicitacoes_documentos WHERE id = %s", (id,))

    conn.commit()
    conn.close()

    return redirect("/mew/solicitacoes-documentos")

# ==========================
# AVALIAÇÃO FINAL DISCIPLINAR
# ==========================

@app.route("/avaliacao-final")
def avaliacao_final():
    """Menu principal da avaliação final - AGORA VERIFICA POR ALUNO"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar disciplinas do aluno que têm prova final liberada PARA ELE
    cursor.execute("""
        SELECT d.id, d.nome, lf.data_liberacao,
               (SELECT COUNT(*) FROM notas_finais nf
                WHERE nf.aluno_id = %s AND nf.disciplina_id = d.id) as ja_realizada,
               (SELECT COUNT(*) FROM questoes_finais qf WHERE qf.disciplina_id = d.id) as total_questoes
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        LEFT JOIN liberacao_final lf ON d.id = lf.disciplina_id AND lf.aluno_id = %s
        WHERE ad.aluno_id = %s
        AND lf.liberada = 1
        AND CAST(lf.data_liberacao AS DATE) <= CURRENT_DATE
    """, (aluno_id, aluno_id, aluno_id))

    disciplinas = cursor.fetchall()

    # Buscar resultados anteriores
    cursor.execute("""
        SELECT nf.*, d.nome as disciplina_nome
        FROM notas_finais nf
        JOIN disciplinas d ON nf.disciplina_id = d.id
        WHERE nf.aluno_id = %s
        ORDER BY nf.data_realizacao DESC
    """, (aluno_id,))

    resultados = cursor.fetchall()

    conn.close()

    return render_template(
        "avaliacao_final.html",
        disciplinas=disciplinas,
        resultados=resultados,
        aluno_nome=session.get("aluno_nome")
    )


@app.route("/mew/deletar-disciplina/<int:disciplina_id>")
def mew_deletar_disciplina(disciplina_id):
    """Deleta uma disciplina e remove todas as associações"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Deletar em ordem correta (começando pelas tabelas dependentes)
        # 1. Notas finais relacionadas à disciplina
        cursor.execute("DELETE FROM notas_finais WHERE disciplina_id = %s", (disciplina_id,))

        # 2. Questões finais
        cursor.execute("DELETE FROM questoes_finais WHERE disciplina_id = %s", (disciplina_id,))

        # 3. Provas finais
        cursor.execute("DELETE FROM provas_finais WHERE disciplina_id = %s", (disciplina_id,))

        # 4. Liberações finais
        cursor.execute("DELETE FROM liberacao_final WHERE disciplina_id = %s", (disciplina_id,))

        # 5. Notas dos alunos
        cursor.execute("DELETE FROM notas WHERE disciplina_id = %s", (disciplina_id,))

        # 6. Solicitações de material
        cursor.execute("DELETE FROM solicitacoes_material WHERE disciplina_id = %s", (disciplina_id,))

        # 7. Solicitações de documentos
        cursor.execute("DELETE FROM solicitacoes_documentos WHERE disciplinas_ids LIKE %s",
                      (f'%{disciplina_id}%',))

        # 8. Datas das disciplinas dos alunos
        cursor.execute("DELETE FROM aluno_disciplina_datas WHERE disciplina_id = %s", (disciplina_id,))

        # 9. Associações aluno-disciplina
        cursor.execute("DELETE FROM aluno_disciplina WHERE disciplina_id = %s", (disciplina_id,))

        # 10. Provas dos capítulos (primeiro deletar provas)
        cursor.execute("""
            DELETE FROM provas
            WHERE capitulo_id IN (
                SELECT id FROM capitulos WHERE disciplina_id = %s
            )
        """, (disciplina_id,))

        # 11. Capítulos
        cursor.execute("DELETE FROM capitulos WHERE disciplina_id = %s", (disciplina_id,))

        # 12. Finalmente, a disciplina
        cursor.execute("DELETE FROM disciplinas WHERE id = %s", (disciplina_id,))

        conn.commit()
        conn.close()

        return redirect("/mew/disciplinas?sucesso=Disciplina+deletada+com+sucesso")

    except Exception as e:
        conn.close()
        return f"Erro ao deletar disciplina: {str(e)}", 500


@app.route("/avaliacao-final/prova/<int:disciplina_id>")
def prova_final(disciplina_id):
    """Página da prova final com 30 questões"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    # Verificar se já fez esta prova
    conn = get_db_connection()
    cursor = conn.cursor()

    # Se o Projeto Final estiver liberado, a prova final normal fica bloqueada
    cursor.execute("""
        SELECT id
        FROM projetos_finais
        WHERE aluno_id = %s
          AND disciplina_id = %s
          AND liberado = 1
    """, (aluno_id, disciplina_id))

    if cursor.fetchone():
        conn.close()
        return redirect("/projeto-final?erro=Esta+disciplina+está+em+modalidade+Projeto+Final")

    cursor.execute("SELECT id FROM notas_finais WHERE aluno_id = %s AND disciplina_id = %s",
                   (aluno_id, disciplina_id))
    if cursor.fetchone():
        conn.close()
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Prova já realizada</title>
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                .info-box {
                    background: #eceff1;
                    color: #4a5157;
                    padding: 30px;
                    border-radius: 10px;
                    margin: 20px auto;
                    max-width: 600px;
                    border: 1px solid #c9d0d5;
                }
                .btn {
                    display: inline-block;
                    background: #343a40;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 5px;
                    margin: 10px;
                }
            </style>
        </head>
        <body>
            <div class="info-box">
                <h2>📋 Você já realizou esta prova final</h2>
                <p>Você já realizou a avaliação final desta disciplina.</p>
                <p>Verifique seus resultados no menu de Avaliação Final.</p>
                <a href="/avaliacao-final" class="btn">📊 Ver Resultados</a>
            </div>
        </body>
        </html>
        '''

    # Buscar questões da prova final
    cursor.execute("""
        SELECT * FROM questoes_finais
        WHERE disciplina_id = %s
        ORDER BY RANDOM()
        LIMIT 30
    """, (disciplina_id,))

    questoes = cursor.fetchall()

    if len(questoes) < 30:
        conn.close()
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Prova não disponível</title>
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                .error-box {
                    background: #f8d7da;
                    color: #721c24;
                    padding: 20px;
                    border-radius: 10px;
                    margin: 20px auto;
                    max-width: 500px;
                    border: 1px solid #f5c6cb;
                }
                .btn {
                    display: inline-block;
                    background: #343a40;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 5px;
                    margin-top: 20px;
                }
            </style>
        </head>
        <body>
            <div class="error-box">
                <h2>❌ Prova não disponível</h2>
                <p>A prova final desta disciplina ainda não está disponível ou não possui questões suficientes.</p>
                <a href="/avaliacao-final" class="btn">↩️ Voltar</a>
            </div>
        </body>
        </html>
        '''

    # Buscar informações da disciplina
    cursor.execute("SELECT nome FROM disciplinas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()

    conn.close()

    return render_template(
        "prova_final.html",
        disciplina=disciplina,
        disciplina_id=disciplina_id,
        questoes=questoes,
        total_questoes=len(questoes)
    )

@app.route("/avaliacao-final/correcao/<int:disciplina_id>", methods=["POST"])
def correcao_final(disciplina_id):
    """Corrige a prova final e calcula a média final"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # Segurança: não permite corrigir prova normal se o Projeto Final estiver liberado
    cursor.execute("""
        SELECT id
        FROM projetos_finais
        WHERE aluno_id = %s
          AND disciplina_id = %s
          AND liberado = 1
    """, (aluno_id, disciplina_id))

    if cursor.fetchone():
        conn.close()
        return redirect("/projeto-final?erro=Esta+disciplina+está+em+modalidade+Projeto+Final")

    # Buscar questões
    cursor.execute("SELECT * FROM questoes_finais WHERE disciplina_id = %s", (disciplina_id,))
    todas_questoes = cursor.fetchall()

    # Contar acertos
    acertos = 0
    for questao in todas_questoes:
        resposta_aluno = request.form.get(f"q_{questao['id']}")

        if resposta_aluno is not None:
            if resposta_aluno.strip().upper() == str(questao["resposta_correta"]).strip().upper():
                acertos += 1

    # Calcular nota da prova final (0-10)
    nota_final = round((acertos / 30) * 10, 2)

    # Calcular média das unidades sem duplicar registros legado/canônico.
    media_disciplina = round(_media_notas_logicas(cursor, aluno_id, disciplina_id), 2)

    # Calcular média final: (nota_final + media_disciplina) / 2
    media_final = round((nota_final + media_disciplina) / 2, 2)

    # Determinar status
    status = "aprovado" if media_final >= 7.0 else "reprovado"

    # Salvar resultado
    data_realizacao = datetime.now().strftime("%d/%m/%Y %H:%M")
    cursor.execute("""
        INSERT INTO notas_finais
        (aluno_id, disciplina_id, nota_final, media_disciplina, media_final, status, data_realizacao)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (aluno_id, disciplina_id) DO UPDATE SET
            nota_final=EXCLUDED.nota_final,
            media_disciplina=EXCLUDED.media_disciplina,
            media_final=EXCLUDED.media_final,
            status=EXCLUDED.status,
            data_realizacao=EXCLUDED.data_realizacao
    """, (aluno_id, disciplina_id, nota_final, media_disciplina, media_final, status, data_realizacao))

    conn.commit()
    conn.close()

    # Guardar resultado na sessão para mostrar
    session['resultado_final'] = {
        'disciplina_id': disciplina_id,
        'nota_final': nota_final,
        'media_disciplina': media_disciplina,
        'media_final': media_final,
        'status': status,
        'acertos': acertos,
        'total': 30
    }

    return redirect(f"/avaliacao-final/resultado/{disciplina_id}")

@app.route("/avaliacao-final/resultado/<int:disciplina_id>")
def resultado_final(disciplina_id):
    """Mostra resultado da avaliação final"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    resultado = session.get('resultado_final', {})

    if not resultado or resultado.get('disciplina_id') != disciplina_id:
        # Buscar do banco se não tiver na sessão
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT nf.*, d.nome as disciplina_nome
            FROM notas_finais nf
            JOIN disciplinas d ON nf.disciplina_id = d.id
            WHERE nf.aluno_id = %s AND nf.disciplina_id = %s
        """, (aluno_id, disciplina_id))

        resultado_db = cursor.fetchone()
        conn.close()

        if not resultado_db:
            return redirect("/avaliacao-final")

        resultado = dict(resultado_db)

    return render_template(
        "resultado_final.html",
        resultado=resultado,
        aluno_nome=session.get("aluno_nome")
    )

# ==========================
# PAINEL MEW - AVALIAÇÃO FINAL
# ==========================

@app.route("/mew/avaliacao-final")

def mew_avaliacao_final():
    """Painel do gestor para gerenciar avaliações finais - AGORA POR ALUNO"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    from datetime import datetime, date  # Adicione esta importação

    conn = get_db_connection()
    cursor = conn.cursor()

    # Contar alunos com acesso à prova final
    cursor.execute("SELECT COUNT(DISTINCT aluno_id) as total_alunos FROM liberacao_final WHERE liberada = 1")
    total_alunos_acesso = cursor.fetchone()["total_alunos"] or 0

    # Contar provas realizadas
    cursor.execute("SELECT COUNT(*) as total FROM notas_finais")
    total_provas = cursor.fetchone()["total"] or 0

    # Contar aprovados/reprovados
    cursor.execute("SELECT COUNT(*) as total FROM notas_finais WHERE LOWER(TRIM(COALESCE(status,''))) = 'aprovado'")
    total_aprovados = cursor.fetchone()["total"] or 0
    cursor.execute("SELECT COUNT(*) as total FROM notas_finais WHERE LOWER(TRIM(COALESCE(status,''))) = 'reprovado'")
    total_reprovados = cursor.fetchone()["total"] or 0

    # Buscar todas as disciplinas para o formulário
    cursor.execute("SELECT * FROM disciplinas ORDER BY nome")
    disciplinas = cursor.fetchall()

    # Buscar todos os alunos para o formulário
    cursor.execute("SELECT id, nome, ra FROM alunos ORDER BY nome")
    alunos = cursor.fetchall()

    # Buscar liberações existentes (agora por aluno)
    cursor.execute("""
        SELECT lf.*, a.nome as aluno_nome, a.ra, d.nome as disciplina_nome,
               (SELECT COUNT(*) FROM questoes_finais qf WHERE qf.disciplina_id = lf.disciplina_id) as total_questoes
        FROM liberacao_final lf
        JOIN alunos a ON lf.aluno_id = a.id
        JOIN disciplinas d ON lf.disciplina_id = d.id
        ORDER BY lf.data_liberacao DESC
    """)
    liberacoes = cursor.fetchall()

    # Buscar resultados dos alunos
    cursor.execute("""
        SELECT nf.*, a.nome as aluno_nome, a.ra, d.nome as disciplina_nome
        FROM notas_finais nf
        JOIN alunos a ON nf.aluno_id = a.id
        JOIN disciplinas d ON nf.disciplina_id = d.id
        ORDER BY nf.data_realizacao DESC
    """)
    resultados = cursor.fetchall()

    conn.close()

    return render_template(
        "mew/avaliacao_final.html",
        total_alunos_acesso=total_alunos_acesso,
        total_provas=total_provas,
        total_aprovados=total_aprovados,
        total_reprovados=total_reprovados,
        disciplinas=disciplinas,
        alunos=alunos,
        liberacoes=liberacoes,
        resultados=resultados,
        date=date  # Adicione esta linha para passar o objeto date para o template
    )

@app.route("/mew/liberar-prova-final-aluno", methods=["POST"])
def liberar_prova_final_aluno():
    """Libera a prova final para um ALUNO ESPECÍFICO em uma disciplina"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})

    aluno_id = request.form.get("aluno_id")
    disciplina_id = request.form.get("disciplina_id")
    data_liberacao = request.form.get("data_liberacao")

    if not all([aluno_id, disciplina_id, data_liberacao]):
        return redirect("/mew/avaliacao-final?erro=Dados+incompletos")

    # Verificar se existem 30 questões para esta disciplina
    conn = get_db_connection()
    cursor = conn.cursor()

    # Se houver Projeto Final liberado, não permite liberar também a prova de 30 questões
    cursor.execute("""
        SELECT id
        FROM projetos_finais
        WHERE aluno_id = %s
          AND disciplina_id = %s
          AND liberado = 1
    """, (aluno_id, disciplina_id))

    if cursor.fetchone():
        conn.close()
        return redirect("/mew/avaliacao-final?erro=Projeto+Final+já+está+liberado+para+este+aluno+e+disciplina")

    cursor.execute("SELECT COUNT(*) as total FROM questoes_finais WHERE disciplina_id = %s", (disciplina_id,))
    total_questoes = cursor.fetchone()["total"] or 0

    if total_questoes < 30:
        conn.close()
        return redirect(f"/mew/avaliacao-final?erro=Disciplina+precisa+de+30+questões+({total_questoes}/30)")

    # Verificar se já existe liberação para este aluno nesta disciplina
    cursor.execute("SELECT id FROM liberacao_final WHERE aluno_id = %s AND disciplina_id = %s",
                  (aluno_id, disciplina_id))

    if cursor.fetchone():
        # Atualizar data e liberar
        cursor.execute("""
            UPDATE liberacao_final
            SET data_liberacao = %s, liberada = 1
            WHERE aluno_id = %s AND disciplina_id = %s
        """, (data_liberacao, aluno_id, disciplina_id))
    else:
        # Inserir nova liberação
        cursor.execute("""
            INSERT INTO liberacao_final (aluno_id, disciplina_id, data_liberacao, liberada)
            VALUES (%s, %s, %s, 1)
        """, (aluno_id, disciplina_id, data_liberacao))

    conn.commit()
    conn.close()

    return redirect("/mew/avaliacao-final?sucesso=Prova+liberada+para+o+aluno")

@app.route("/mew/remover-liberacao/<int:liberacao_id>")
def remover_liberacao(liberacao_id):
    """Remove a liberação de uma prova final"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM liberacao_final WHERE id = %s", (liberacao_id,))

    conn.commit()
    conn.close()

    return redirect("/mew/avaliacao-final?sucesso=Liberação+removida")

@app.route("/mew/visualizar-prova-final/<int:disciplina_id>")
def visualizar_prova_final(disciplina_id):
    """Visualiza todas as 30 questões da prova final"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar disciplina
    cursor.execute("SELECT * FROM disciplinas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()

    # Buscar TODAS as questões (sem limite)
    cursor.execute("SELECT * FROM questoes_finais WHERE disciplina_id = %s ORDER BY id", (disciplina_id,))
    questoes = cursor.fetchall()

    # Contar questões
    total_questoes = len(questoes)

    conn.close()

    return render_template(
        "mew/visualizar_prova_final.html",
        disciplina=disciplina,
        questoes=questoes,
        total_questoes=total_questoes
    )

@app.route("/mew/importar-questoes/<int:disciplina_id>", methods=["POST"])
@app.route("/mew/importar-questoes-json/<int:disciplina_id>", methods=["POST"])
def importar_questoes_json(disciplina_id):
    """Importa pela interface normal (Excel/Sheets/XLSX); mantém JSON apenas por compatibilidade antiga."""
    if not session.get("mew_admin"):
        return jsonify({"success":False,"message":"Não autorizado"}),403
    try:
        arquivo=request.files.get("questoes_xlsx")
        if arquivo and arquivo.filename:
            questoes=questoes_xlsx_upload(arquivo)
        else:
            texto=request.form.get("questoes_tabela") or request.form.get("questoes_json") or ""
            questoes=parse_questoes_texto(texto)
        if not questoes:
            return jsonify({"success":False,"message":"Nenhuma questão encontrada."}),400
        conn=get_db_connection(); cursor=conn.cursor()
        try:
            for q in questoes:
                op=q["opcoes"]
                cursor.execute("""INSERT INTO questoes_finais
                    (disciplina_id,pergunta,opcao_a,opcao_b,opcao_c,opcao_d,resposta_correta)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (disciplina_id,q["pergunta"],op["A"],op["B"],op["C"],op["D"],q["resposta_certa"]))
            conn.commit()
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
        return jsonify({"success":True,"message":f"{len(questoes)} questões importadas com sucesso!","count":len(questoes)})
    except Exception as e:
        return jsonify({"success":False,"message":str(e)}),400


@app.route("/mew/exportar-questoes-json/<int:disciplina_id>")
def exportar_questoes_json(disciplina_id):
    """Mantido para integrações antigas; a interface normal usa Excel."""
    if not session.get("mew_admin"):
        return jsonify({"error":"Não autorizado"}),403
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT pergunta,opcao_a,opcao_b,opcao_c,opcao_d,resposta_correta FROM questoes_finais WHERE disciplina_id=%s ORDER BY id",(disciplina_id,))
    questoes=[dict(x) for x in cursor.fetchall()]; conn.close()
    return jsonify({"disciplina_id":disciplina_id,"total_questoes":len(questoes),"questoes":questoes})


@app.route("/mew/exportar-questoes-excel/<int:disciplina_id>")
def exportar_questoes_excel(disciplina_id):
    if not session.get("mew_admin"):
        return "Não autorizado",403
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT d.nome FROM disciplinas d WHERE id=%s",(disciplina_id,)); disc=cursor.fetchone()
    cursor.execute("SELECT pergunta,opcao_a,opcao_b,opcao_c,opcao_d,resposta_correta FROM questoes_finais WHERE disciplina_id=%s ORDER BY id",(disciplina_id,))
    rows=cursor.fetchall(); conn.close()
    wb=Workbook(); ws=wb.active; ws.title="Questões"
    ws.append(["Pergunta","A","B","C","D","Resposta"])
    for r in rows:
        ws.append([r["pergunta"],r["opcao_a"],r["opcao_b"],r["opcao_c"],r["opcao_d"],r["resposta_correta"]])
    out=BytesIO(); wb.save(out); out.seek(0)
    nome=secure_filename((disc or {}).get("nome") or f"disciplina-{disciplina_id}") or f"disciplina-{disciplina_id}"
    return send_file(out,as_attachment=True,download_name=f"questoes-{nome}.xlsx",mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/situacao-academica")
def situacao_academica():
    """Situação acadêmica baseada diretamente no PostgreSQL, sem recalcular valores administrativos."""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT nome, ra FROM alunos WHERE id = %s", (aluno_id,))
        aluno = cursor.fetchone()
        if not aluno:
            flash("Aluno não encontrado.", "error")
            return redirect(url_for("dashboard"))

        cursor.execute("""
            SELECT d.id, d.nome,
                   addd.frequencia, addd.progresso_manual, addd.data_inicio,
                   nf.nota_final, nf.media_disciplina, nf.media_final,
                   nf.status AS status_final, nf.data_realizacao
            FROM disciplinas d
            JOIN aluno_disciplina ad ON d.id = ad.disciplina_id AND ad.aluno_id = %s
            LEFT JOIN aluno_disciplina_datas addd
              ON addd.aluno_id = ad.aluno_id AND addd.disciplina_id = d.id
            LEFT JOIN notas_finais nf
              ON nf.aluno_id = ad.aluno_id AND nf.disciplina_id = d.id
            ORDER BY d.nome
        """, (aluno_id,))
        disciplinas_raw = cursor.fetchall()

        situacao_disciplinas = []
        notas_finais = []
        for row in disciplinas_raw:
            d = dict(row)
            capitulos = _capitulos_ordenados(cursor, d["id"])
            unidades = _notas_logicas_disciplina(cursor, aluno_id, d["id"], capitulos)
            for unidade in unidades:
                # Campos compatíveis com o HTML antigo, mas sem expor ID interno como número da unidade.
                unidade["capitulo"] = unidade["ordem"]

            valores = [u["nota"] for u in unidades if u["nota"] is not None]
            feitos = len(valores)
            total = len(unidades)
            media_capitulos = round(sum(valores) / feitos, 2) if feitos else 0.0

            status_norm = _normalizar_status_academico(d.get("status_final"))
            tem_final = d.get("nota_final") is not None or d.get("media_final") is not None or bool(status_norm in {"aprovado", "reprovado"})
            if status_norm == "aprovado":
                situacao = "Aprovado"
            elif status_norm == "reprovado":
                situacao = "Reprovado"
            elif total > 0 and feitos >= total:
                situacao = "Aguardando final"
            elif feitos > 0 or int(d.get("progresso_manual") or 0) > 0 or d.get("data_inicio"):
                situacao = "Em andamento"
            else:
                situacao = "Não iniciada"

            media_final = float(d["media_final"]) if d.get("media_final") is not None else None
            nota_final_obj = None
            if tem_final:
                nota_final_obj = {
                    "nota_final": float(d["nota_final"]) if d.get("nota_final") is not None else None,
                    "media_disciplina": float(d["media_disciplina"]) if d.get("media_disciplina") is not None else media_capitulos,
                    "media_final": media_final,
                    "status": status_norm or "cursando",
                    "data_realizacao": d.get("data_realizacao") or "",
                }
                notas_finais.append({"disciplina_id": d["id"], **nota_final_obj})

            frequencia = float(d["frequencia"]) if d.get("frequencia") is not None else None
            situacao_disciplinas.append({
                "id": d["id"],
                "nome": d["nome"],
                "notas_capitulos": unidades,
                "nota_final": nota_final_obj,
                "media_capitulos": media_capitulos,
                "media_final": round(media_final, 2) if media_final is not None else None,
                "frequencia": frequencia,
                "progresso": int(d.get("progresso_manual")) if d.get("progresso_manual") is not None else None,
                "status": status_norm or "cursando",
                "situacao": situacao,
                "capitulos_feitos": feitos,
                "capitulos_total": total,
            })

        total_disciplinas = len(situacao_disciplinas)
        disciplinas_aprovadas = sum(1 for d in situacao_disciplinas if d["situacao"] == "Aprovado")
        disciplinas_reprovadas = sum(1 for d in situacao_disciplinas if d["situacao"] == "Reprovado")
        disciplinas_cursando = sum(1 for d in situacao_disciplinas if d["situacao"] in {"Em andamento", "Não iniciada"})
        disciplinas_aguardando_final = sum(1 for d in situacao_disciplinas if d["situacao"] == "Aguardando final")
        finais = [d["media_final"] for d in situacao_disciplinas if d["media_final"] is not None]
        media_geral = round(sum(finais) / len(finais), 2) if finais else 0
    finally:
        conn.close()

    return render_template(
        "situacao_academica.html",
        aluno_nome=aluno["nome"], aluno_ra=aluno["ra"],
        situacao_disciplinas=situacao_disciplinas,
        total_disciplinas=total_disciplinas,
        disciplinas_aprovadas=disciplinas_aprovadas,
        disciplinas_reprovadas=disciplinas_reprovadas,
        disciplinas_cursando=disciplinas_cursando,
        disciplinas_aguardando_final=disciplinas_aguardando_final,
        media_geral=media_geral,
        notas_finais=notas_finais,
        now=datetime.now(),
    )
# ==========================
# ADICIONE ESTA FUNÇÃO PARA VERIFICAR DISPONIBILIDADE
# ==========================
@app.route("/validar-documento/<codigo>")
def ver_resultado_validacao(codigo):
    """Mostra o resultado da validação de um documento específico"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Buscar documento pelo código
        cursor.execute("""
            SELECT codigo, aluno_nome, aluno_ra, tipo, data_geracao, data_validade, hash_documento
            FROM documentos_autenticados
            WHERE codigo = %s
        """, (codigo.upper(),))

        documento = cursor.fetchone()
        conn.close()

        if documento:
            doc_dict = dict(documento)

            # Verificar validade
            from datetime import datetime
            hoje = datetime.now()

            if doc_dict.get('data_validade'):
                try:
                    data_validade = datetime.strptime(doc_dict['data_validade'], "%d/%m/%Y")
                    status = "válido" if hoje <= data_validade else "expirado"
                except:
                    status = "válido"
            else:
                status = "válido"

            return render_template(
                "resultado_validacao_completo.html",
                valido=True,
                codigo=codigo.upper(),
                documento=doc_dict,
                status=status
            )
        else:
            return render_template(
                "resultado_validacao.html",
                valido=False,
                codigo=codigo.upper(),
                mensagem="Documento não encontrado no sistema."
            )

    except Exception as e:
        print(f"Erro na validação: {e}")
        return render_template(
            "resultado_validacao.html",
            valido=False,
            codigo=codigo,
            mensagem="Erro ao validar documento."
        )

# ==========================
# VALIDAÇÃO PÚBLICA DE DOCUMENTOS
# ==========================

@app.route("/validar-documento", methods=["GET", "POST"])
def validar_documento_publico():
    """Página pública para validação de documentos - SIMPLIFICADA"""

    # Se for POST, processar a validação via AJAX
    if request.method == "POST":
        data = request.get_json()
        codigo = data.get('codigo', '').strip().upper()

        if not codigo:
            return jsonify({"success": False, "message": "Código não fornecido"})

        conn = get_db_connection()
        cursor = conn.cursor()

        # Verificar se o código existe na tabela documentos_autenticados
        cursor.execute("SELECT id FROM documentos_autenticados WHERE codigo = %s", (codigo,))
        documento = cursor.fetchone()
        conn.close()

        if documento:
            # Código válido - retornar URL de redirecionamento
            return jsonify({
                "success": True,
                "url": f"/ver-documento/{codigo}"
            })
        else:
            # Código inválido
            return jsonify({
                "success": False,
                "message": "Código não encontrado. Verifique se digitou corretamente."
            })

    # Se for GET, mostrar a página de validação
    return render_template("validar_documento.html")


@app.route("/api/validar-qrcode", methods=['POST'])
def api_validar_qrcode():
    """
    API para validar documento via QR Code (usado pelo app)
    """
    try:
        data = request.get_json()
        qr_data = data.get('qr_data')

        if not qr_data:
            return jsonify({"success": False, "message": "Dados do QR Code não fornecidos"})

        # Extrair informações do QR Code
        try:
            info = json.loads(qr_data)
            codigo = info.get('codigo')
            hash_recebido = info.get('hash')
        except:
            # Se não for JSON, tentar como código direto
            codigo = qr_data
            hash_recebido = None

        if not codigo:
            return jsonify({"success": False, "message": "Código não encontrado no QR Code"})

        # Buscar documento
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT codigo, aluno_nome, aluno_ra, tipo, data_emissao, data_validade, hash_documento
            FROM documentos_autenticados
            WHERE codigo = %s
        """, (codigo.upper(),))

        documento = cursor.fetchone()
        conn.close()

        if not documento:
            return jsonify({
                "success": False,
                "message": "Documento não encontrado",
                "codigo": codigo
            })

        # Verificar hash se fornecido
        hash_valido = True
        if hash_recebido and documento['hash_documento']:
            hash_valido = (hash_recebido == documento['hash_documento'])

        # Verificar validade
        from datetime import datetime
        hoje = datetime.now()
        data_validade = datetime.strptime(documento['data_validade'], "%d/%m/%Y")
        valido = hoje <= data_validade

        return jsonify({
            "success": True,
            "valido": valido,
            "hash_valido": hash_valido,
            "documento": {
                "codigo": documento['codigo'],
                "aluno_nome": documento['aluno_nome'],
                "aluno_ra": documento['aluno_ra'],
                "tipo": documento['tipo'],
                "data_emissao": documento['data_emissao'],
                "data_validade": documento['data_validade']
            },
            "mensagem": "Documento válido" if valido else "Documento expirado"
        })

    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"})

def buscar_documento_db(codigo):
    """Busca um documento autenticado sem depender de índices de tupla."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT codigo, aluno_nome, aluno_ra, tipo, conteudo_html, data_geracao
            FROM documentos_autenticados
            WHERE codigo = %s OR codigo_autenticacao = %s
            ORDER BY id DESC LIMIT 1
        """, (codigo, codigo))
        documento = cursor.fetchone()
        return dict(documento) if documento else None
    except Exception as e:
        print(f"Erro ao buscar documento: {e}")
        return None
    finally:
        conn.close()



@app.route("/mew/gerar-documento")
def mew_gerar_documento():
    """Página para gerar documentos autenticados"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar alunos para o formulário
    cursor.execute("SELECT id, nome, ra FROM alunos ORDER BY nome")
    alunos = cursor.fetchall()

    # Buscar disciplinas para o formulário
    cursor.execute("SELECT * FROM disciplinas ORDER BY nome")
    disciplinas = cursor.fetchall()

    conn.close()

    return render_template(
        "mew/gerar_documento.html",
        alunos=alunos,
        disciplinas=disciplinas
    )

@app.route("/disciplinas-isoladas")
def disciplinas_isoladas_page():
    """Página de landing page para disciplinas isoladas"""
    return render_template("disciplinas_isoladas.html")


@app.route("/mew/aluno/<int:aluno_id>/disciplinas", methods=["GET", "POST"])
def mew_gerenciar_disciplinas(aluno_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        acao = request.form.get("acao")
        disciplina_id = request.form.get("disciplina_id")

        # 🔄 EDITAR DATA
        if acao == "editar_data":
            data_inicio = request.form.get("data_inicio")

            from datetime import datetime, timedelta
            data_inicio_obj = datetime.strptime(data_inicio, "%Y-%m-%d")
            data_fim = (data_inicio_obj + timedelta(days=60)).strftime("%d/%m/%Y")
            data_inicio_fmt = data_inicio_obj.strftime("%d/%m/%Y")

            cursor.execute("""
                UPDATE aluno_disciplina_datas
                SET data_inicio = %s, data_fim_previsto = %s
                WHERE aluno_id = %s AND disciplina_id = %s
            """, (data_inicio_fmt, data_fim, aluno_id, disciplina_id))

        # ➕ ADICIONAR DISCIPLINA
        elif acao == "adicionar":
            data_inicio = request.form.get("data_inicio")

            from datetime import datetime, timedelta
            data_inicio_obj = datetime.strptime(data_inicio, "%Y-%m-%d")
            data_fim = (data_inicio_obj + timedelta(days=60)).strftime("%d/%m/%Y")

            cursor.execute("""
                INSERT INTO aluno_disciplina (aluno_id, disciplina_id)
                VALUES (%s, %s)
                ON CONFLICT (aluno_id, disciplina_id) DO NOTHING
            """, (aluno_id, disciplina_id))

            cursor.execute("""
                INSERT INTO aluno_disciplina_datas
                (aluno_id, disciplina_id, data_inicio, data_fim_previsto)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (aluno_id, disciplina_id) DO UPDATE SET
                    data_inicio = EXCLUDED.data_inicio,
                    data_fim_previsto = EXCLUDED.data_fim_previsto,
                    prova_final_aberta = 0
            """, (aluno_id, disciplina_id,
                  data_inicio_obj.strftime("%d/%m/%Y"),
                  data_fim))

        # ❌ REMOVER DISCIPLINA
        elif acao == "remover":
            cursor.execute("""
                DELETE FROM aluno_disciplina
                WHERE aluno_id = %s AND disciplina_id = %s
            """, (aluno_id, disciplina_id))

            cursor.execute("""
                DELETE FROM aluno_disciplina_datas
                WHERE aluno_id = %s AND disciplina_id = %s
            """, (aluno_id, disciplina_id))

        conn.commit()

    # 🔎 DADOS PARA O GET
    cursor.execute("SELECT id, nome FROM alunos WHERE id = %s", (aluno_id,))
    aluno = cursor.fetchone()

    cursor.execute("""
        SELECT d.id, d.nome,
            addd.data_inicio,
            CASE
                WHEN addd.data_inicio IS NOT NULL
                THEN substr(addd.data_inicio, 7, 4) || '-' ||
                    substr(addd.data_inicio, 4, 2) || '-' ||
                    substr(addd.data_inicio, 1, 2)
            END AS data_inicio_input
        FROM disciplinas d
        LEFT JOIN aluno_disciplina_datas addd
            ON d.id = addd.disciplina_id
            AND addd.aluno_id = %s
        ORDER BY d.nome
    """, (aluno_id,))
    disciplinas = cursor.fetchall()

    conn.close()

    return render_template(
        "mew/gerenciar_disciplinas.html",
        aluno=aluno,
        disciplinas=disciplinas
    )

# ==========================
# MEW - GERENCIAR NOTAS ACADÊMICAS
# ==========================

@app.route("/mew/gerenciar-notas")
def mew_gerenciar_notas():
    """Página inicial para gerenciar notas acadêmicas"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar todos os alunos
    cursor.execute("SELECT id, nome, ra FROM alunos ORDER BY nome")
    alunos = cursor.fetchall()

    conn.close()

    return render_template("mew/gerenciar_notas.html", alunos=alunos)

@app.route("/mew/gerenciar-notas/aluno/<int:aluno_id>")
def mew_notas_aluno(aluno_id):
    """Mostra disciplinas de um aluno específico"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar informações do aluno
    cursor.execute("SELECT id, nome, ra FROM alunos WHERE id = %s", (aluno_id,))
    aluno = cursor.fetchone()

    if not aluno:
        conn.close()
        return "Aluno não encontrado", 404

    # Buscar disciplinas e contar unidades pelo mapeamento real de capítulos.
    cursor.execute("""
        SELECT d.id, d.nome
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        WHERE ad.aluno_id = %s
        ORDER BY d.nome
    """, (aluno_id,))
    disciplinas = []
    for row in cursor.fetchall():
        d = dict(row)
        unidades = _notas_logicas_disciplina(cursor, aluno_id, d["id"])
        d["total_capitulos"] = len(unidades)
        d["provas_feitas"] = sum(1 for u in unidades if u["nota"] is not None)
        disciplinas.append(d)

    conn.close()

    return render_template(
        "mew/notas_disciplinas.html",
        aluno=aluno,
        disciplinas=disciplinas
    )

@app.route("/mew/gerenciar-notas/disciplina/<int:aluno_id>/<int:disciplina_id>")
def mew_notas_disciplina(aluno_id, disciplina_id):
    """Mostra e gerencia notas de uma disciplina específica."""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome, ra FROM alunos WHERE id = %s", (aluno_id,))
    aluno = cursor.fetchone()
    cursor.execute("SELECT id, nome FROM disciplinas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()
    if not aluno or not disciplina:
        conn.close()
        return "Aluno ou disciplina não encontrados", 404

    capitulos = _capitulos_ordenados(cursor, disciplina_id)
    notas_logicas = _notas_logicas_disciplina(cursor, aluno_id, disciplina_id, capitulos)
    notas_existentes = {n["capitulo_id"]: n["nota"] for n in notas_logicas if n["nota"] is not None}

    cursor.execute("""
        SELECT nota_final, media_disciplina, media_final, status
        FROM notas_finais
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))
    nota_final = cursor.fetchone()
    if nota_final:
        nota_final = dict(nota_final)
        nota_final["status"] = _normalizar_status_academico(nota_final.get("status"))

    cursor.execute("""
        SELECT data_inicio, prova_final_aberta, frequencia, progresso_manual
        FROM aluno_disciplina_datas
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))
    datas_info = cursor.fetchone()
    datas_info = dict(datas_info) if datas_info else {}

    total_capitulos = len(capitulos)
    provas_feitas = len([n for n in notas_logicas if n["nota"] is not None])
    if datas_info.get("progresso_manual") is not None:
        progresso_atual = max(0, min(100, int(datas_info.get("progresso_manual") or 0)))
    elif total_capitulos:
        bruto = round((provas_feitas / total_capitulos) * 100)
        progresso_atual = 100 if bruto >= 100 else 75 if bruto >= 75 else 50 if bruto >= 50 else 25 if bruto > 0 else 0
    else:
        progresso_atual = 0

    frequencia_atual = datas_info.get("frequencia")
    conn.close()
    return render_template(
        "mew/notas_editar.html",
        aluno=aluno,
        disciplina=disciplina,
        capitulos=capitulos,
        notas_existentes=notas_existentes,
        nota_final=nota_final,
        datas_info=datas_info,
        progresso_atual=progresso_atual,
        frequencia_atual=frequencia_atual,
        total_capitulos=total_capitulos,
        provas_feitas=provas_feitas,
    )


@app.route("/mew/gerenciar-notas/salvar", methods=["POST"])
def mew_salvar_notas():
    """Persiste notas, prova final, frequência e progresso sem apagar avaliações."""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"}), 403

    aluno_id = request.form.get("aluno_id")
    disciplina_id = request.form.get("disciplina_id")
    acao = request.form.get("acao")
    if not all([aluno_id, disciplina_id, acao]):
        return jsonify({"success": False, "message": "Dados incompletos"}), 400

    try:
        aluno_id = int(aluno_id)
        disciplina_id = int(disciplina_id)
    except Exception:
        return jsonify({"success": False, "message": "Aluno ou disciplina inválidos"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if acao == "salvar_nota":
            capitulo = request.form.get("capitulo")
            nota_raw = request.form.get("nota")
            if capitulo in (None, "") or nota_raw in (None, ""):
                raise ValueError("Capítulo ou nota não informados")
            nota = float(str(nota_raw).replace(",", "."))
            if nota < 0 or nota > 10:
                raise ValueError("A nota deve estar entre 0 e 10")
            capitulo_id, ordem = _salvar_nota_logica(cursor, aluno_id, disciplina_id, capitulo, nota)

            media_disciplina = _media_notas_logicas(cursor, aluno_id, disciplina_id)
            cursor.execute("SELECT nota_final FROM notas_finais WHERE aluno_id=%s AND disciplina_id=%s", (aluno_id, disciplina_id))
            final_row = cursor.fetchone()
            media_final = None
            status = None
            if final_row and final_row.get("nota_final") is not None:
                media_final = round((float(final_row["nota_final"]) + media_disciplina) / 2, 2)
                status = "aprovado" if media_final >= 7 else "reprovado"
                cursor.execute("""
                    UPDATE notas_finais
                    SET media_disciplina=%s, media_final=%s, status=%s
                    WHERE aluno_id=%s AND disciplina_id=%s
                """, (round(media_disciplina, 2), media_final, status, aluno_id, disciplina_id))
            message = f"Nota da Unidade {ordem} salva no banco"
            payload_extra = {
                "capitulo_id": capitulo_id, "ordem": ordem, "nota": nota,
                "media_disciplina": round(media_disciplina, 2),
                "media_final": media_final, "status": status,
            }

        elif acao == "excluir_nota":
            capitulo = request.form.get("capitulo")
            if capitulo in (None, ""):
                raise ValueError("Capítulo não informado")
            _excluir_nota_logica(cursor, aluno_id, disciplina_id, capitulo)
            media_disciplina = _media_notas_logicas(cursor, aluno_id, disciplina_id)
            cursor.execute("SELECT nota_final FROM notas_finais WHERE aluno_id=%s AND disciplina_id=%s", (aluno_id, disciplina_id))
            final_row = cursor.fetchone()
            media_final = None
            status = None
            if final_row and final_row.get("nota_final") is not None:
                media_final = round((float(final_row["nota_final"]) + media_disciplina) / 2, 2)
                status = "aprovado" if media_final >= 7 else "reprovado"
                cursor.execute("""
                    UPDATE notas_finais SET media_disciplina=%s, media_final=%s, status=%s
                    WHERE aluno_id=%s AND disciplina_id=%s
                """, (round(media_disciplina, 2), media_final, status, aluno_id, disciplina_id))
            message = "Nota excluída do banco"
            payload_extra = {
                "media_disciplina": round(media_disciplina, 2),
                "media_final": media_final, "status": status,
            }

        elif acao == "salvar_final":
            nota_final_raw = request.form.get("nota_final")
            if nota_final_raw in (None, ""):
                raise ValueError("Informe a nota da prova final")
            nota_final_val = float(str(nota_final_raw).replace(",", "."))
            if nota_final_val < 0 or nota_final_val > 10:
                raise ValueError("A nota final deve estar entre 0 e 10")

            media_auto = round(_media_notas_logicas(cursor, aluno_id, disciplina_id), 2)
            md_raw = request.form.get("media_disciplina")
            mf_raw = request.form.get("media_final")
            media_disciplina = float(str(md_raw).replace(",", ".")) if md_raw not in (None, "") else media_auto
            media_final = float(str(mf_raw).replace(",", ".")) if mf_raw not in (None, "") else round((nota_final_val + media_disciplina) / 2, 2)
            if not (0 <= media_disciplina <= 10 and 0 <= media_final <= 10):
                raise ValueError("As médias devem estar entre 0 e 10")
            status = _normalizar_status_academico(request.form.get("status"))
            if status not in {"aprovado", "reprovado", "cursando"}:
                status = "aprovado" if media_final >= 7 else "reprovado"
            agora = datetime.now().strftime("%d/%m/%Y %H:%M")
            cursor.execute("""
                INSERT INTO notas_finais
                (aluno_id, disciplina_id, nota_final, media_disciplina, media_final, status, data_realizacao)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (aluno_id, disciplina_id) DO UPDATE SET
                    nota_final=EXCLUDED.nota_final,
                    media_disciplina=EXCLUDED.media_disciplina,
                    media_final=EXCLUDED.media_final,
                    status=EXCLUDED.status,
                    data_realizacao=EXCLUDED.data_realizacao
            """, (aluno_id, disciplina_id, nota_final_val, media_disciplina, media_final, status, agora))
            message = "Nota final, médias e situação salvas no banco"
            payload_extra = {"nota_final": nota_final_val, "media_disciplina": media_disciplina, "media_final": media_final, "status": status}

        elif acao == "excluir_final":
            cursor.execute("DELETE FROM notas_finais WHERE aluno_id=%s AND disciplina_id=%s", (aluno_id, disciplina_id))
            message = "Nota final excluída do banco"
            payload_extra = {}

        elif acao == "atualizar_progresso":
            progresso_raw = request.form.get("progresso")
            frequencia_raw = request.form.get("frequencia")
            data_inicio = request.form.get("data_inicio") or None
            prova_final_aberta = 1 if str(request.form.get("prova_final_aberta", "0")) == "1" else 0
            if progresso_raw in (None, ""):
                raise ValueError("Progresso não informado")
            progresso = int(float(progresso_raw))
            if progresso < 0 or progresso > 100:
                raise ValueError("Progresso deve estar entre 0 e 100")
            frequencia = None
            if frequencia_raw not in (None, ""):
                frequencia = float(str(frequencia_raw).replace(",", "."))
                if frequencia < 0 or frequencia > 100:
                    raise ValueError("Frequência deve estar entre 0 e 100")

            # Importante: alterar progresso/frequência NUNCA apaga nota.
            cursor.execute("""
                INSERT INTO aluno_disciplina_datas
                    (aluno_id, disciplina_id, data_inicio, prova_final_aberta, frequencia, progresso_manual)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (aluno_id, disciplina_id) DO UPDATE SET
                    data_inicio=COALESCE(EXCLUDED.data_inicio, aluno_disciplina_datas.data_inicio),
                    prova_final_aberta=EXCLUDED.prova_final_aberta,
                    frequencia=COALESCE(EXCLUDED.frequencia, aluno_disciplina_datas.frequencia),
                    progresso_manual=EXCLUDED.progresso_manual
            """, (aluno_id, disciplina_id, data_inicio, prova_final_aberta, frequencia, progresso))
            message = "Progresso e frequência salvos no banco"
            payload_extra = {"progresso": progresso, "frequencia": frequencia}
        else:
            raise ValueError("Ação inválida")

        conn.commit()
        resposta = {"success": True, "message": message}
        resposta.update(payload_extra)
        return jsonify(resposta)
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 400
    finally:
        conn.close()

@app.route('/mew/buscar-dados-aluno/<int:aluno_id>')
def buscar_dados_aluno(aluno_id):
    try:
        # Buscar dados completos do aluno
        aluno_completo = buscar_dados_pessoais_completos(aluno_id)

        if not aluno_completo:
            return jsonify({'success': False, 'message': 'Aluno não encontrado'})

        return jsonify({
            'success': True,
            'aluno': {
                'id': aluno_completo['id'],
                'nome': aluno_completo['nome'],
                'ra': aluno_completo['ra'],
                'email': aluno_completo['email'],
                'cpf': aluno_completo.get('cpf', ''),
                'cpf_formatado': aluno_completo.get('cpf_formatado', ''),
                'rg': aluno_completo.get('rg', ''),
                'telefone': aluno_completo.get('telefone', ''),
                'telefone_formatado': aluno_completo.get('telefone_formatado', ''),
                'endereco': aluno_completo.get('endereco', ''),
                'cidade': aluno_completo.get('cidade', ''),
                'estado': aluno_completo.get('estado', ''),
                'cep': aluno_completo.get('cep', ''),
                'endereco_completo': aluno_completo.get('endereco_completo', ''),
                'curso_referencia': aluno_completo.get('curso_referencia', 'Disciplinas Isoladas'),
                'filiacao': aluno_completo.get('filiacao', ''),
                'naturalidade': aluno_completo.get('naturalidade', ''),
                'nacionalidade': aluno_completo.get('nacionalidade', 'Brasileira')
            }
        })
    except Exception as e:
        import traceback
        print(f"Erro em buscar_dados_aluno: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': f'Erro: {str(e)}'})

@app.route('/mew/buscar-disciplinas-aluno/<int:aluno_id>')
def buscar_disciplinas_aluno_route(aluno_id):
    try:
        # Buscar disciplinas do aluno usando a nova função
        disciplinas = buscar_disciplinas_por_aluno_id(aluno_id)

        if disciplinas is None:
            return jsonify({'success': False, 'message': 'Erro ao buscar disciplinas'})

        return jsonify({
            'success': True,
            'disciplinas': disciplinas,
            'total': len(disciplinas)
        })
    except Exception as e:
        import traceback
        print(f"Erro em buscar_disciplinas_aluno_route: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': f'Erro: {str(e)}'})


@app.route('/mew/gerar-documento-processar', methods=['POST'])
def gerar_documento_processar():
    try:
        import hashlib
        import secrets

        data = request.get_json()
        aluno_id = data.get('aluno_id')
        tipo_documento = data.get('tipo_documento')
        conteudo_html = data.get('conteudo_html')
        observacoes = data.get('observacoes', '')

        if not aluno_id or not tipo_documento:
            return jsonify({'success': False, 'message': 'Dados incompletos'})

        # Buscar aluno
        aluno_completo = buscar_dados_pessoais_completos(aluno_id)
        if not aluno_completo:
            return jsonify({'success': False, 'message': 'Aluno não encontrado'})

        # Gerar código único
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        codigo = f"HIST-{aluno_completo['ra']}-{timestamp}-{secrets.token_hex(4).upper()}"

        # Gerar hash
        hash_documento = hashlib.sha256(f"{aluno_completo['ra']}{timestamp}{conteudo_html}".encode()).hexdigest()

        # Salvar no banco
        documento_id = salvar_documento_autenticado({
            'codigo_autenticacao': codigo,
            'aluno_id': aluno_id,
            'tipo_documento': tipo_documento,
            'hash_documento': hash_documento,
            'conteudo_html': conteudo_html,
            'data_emissao': datetime.now(),
            'observacoes': observacoes,
            'aluno_nome': aluno_completo['nome'],
            'aluno_ra': aluno_completo['ra']
        })

        if not documento_id:
            return jsonify({'success': False, 'message': 'Erro ao salvar documento'})

        return jsonify({
            'success': True,
            'codigo': codigo,
            'hash': hash_documento,
            'url_validacao': f'/validar-documento/{codigo}',
            'documento_id': documento_id,
            'aluno_nome': aluno_completo['nome'],
            'aluno_ra': aluno_completo['ra']
        })

    except Exception as e:
        import traceback
        print(f"Erro em gerar_documento_processar: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': f'Erro: {str(e)}'})

def buscar_dados_pessoais_completos(aluno_id):
    """Busca dados pessoais completos do aluno - VERSÃO COMPLETA"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT a.*,
               dp.cpf, dp.rg, dp.telefone, dp.endereco, dp.cidade, dp.estado, dp.cep,
               dp.curso_referencia, dp.nome_pai, dp.nome_mae, dp.naturalidade,
               dp.nacionalidade, dp.data_nascimento, dp.sexo, dp.estado_civil
        FROM alunos a
        LEFT JOIN dados_pessoais dp ON a.id = dp.aluno_id
        WHERE a.id = %s
    """, (aluno_id,))

    aluno_row = cursor.fetchone()

    if not aluno_row:
        conn.close()
        return None

    aluno = dict(aluno_row)

    # Formatar dados
    aluno['cpf_formatado'] = formatar_cpf(aluno.get('cpf', '')) if aluno.get('cpf') else ''
    aluno['telefone_formatado'] = formatar_telefone(aluno.get('telefone', '')) if aluno.get('telefone') else ''

    # Endereço completo
    endereco_parts = []
    if aluno.get('endereco'):
        endereco_parts.append(aluno['endereco'])
    if aluno.get('cidade'):
        endereco_parts.append(aluno['cidade'])
    if aluno.get('estado'):
        endereco_parts.append(f"- {aluno['estado']}")
    if aluno.get('cep'):
        endereco_parts.append(f"CEP: {aluno['cep']}")

    aluno['endereco_completo'] = ', '.join(endereco_parts)

    # Campos padrão se não existirem
    aluno['naturalidade'] = aluno.get('naturalidade', '')
    aluno['nacionalidade'] = aluno.get('nacionalidade', 'Brasileira')
    aluno['data_nascimento'] = aluno.get('data_nascimento', '')
    aluno['sexo'] = aluno.get('sexo', '')
    aluno['estado_civil'] = aluno.get('estado_civil', '')
    aluno['curso'] = aluno.get('curso_referencia', 'Disciplinas Isoladas')

    conn.close()
    return aluno



def buscar_aluno_por_id(aluno_id):
    """Busca um aluno pelo ID - VERSÃO COMPLETA"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT a.*, dp.cpf, dp.rg, dp.telefone, dp.endereco, dp.cidade, dp.estado, dp.cep,
               dp.curso_referencia
        FROM alunos a
        LEFT JOIN dados_pessoais dp ON a.id = dp.aluno_id
        WHERE a.id = %s
    """, (aluno_id,))

    aluno_row = cursor.fetchone()
    conn.close()

    if not aluno_row:
        return None

    aluno = dict(aluno_row)

    # Formatar dados
    aluno['cpf_formatado'] = formatar_cpf(aluno.get('cpf', ''))
    aluno['telefone_formatado'] = formatar_telefone(aluno.get('telefone', ''))
    aluno['endereco_completo'] = f"{aluno.get('endereco', '')}, {aluno.get('cidade', '')} - {aluno.get('estado', '')}, CEP: {aluno.get('cep', '')}"

    # Adicionar campos padrão para template
    aluno['filiacao'] = aluno.get('filiacao', '')
    aluno['naturalidade'] = aluno.get('naturalidade', '')
    aluno['nacionalidade'] = aluno.get('nacionalidade', 'Brasileira')
    aluno['data_nascimento'] = aluno.get('data_nascimento', '')
    aluno['sexo'] = aluno.get('sexo', '')
    aluno['curso'] = aluno.get('curso_referencia', 'Disciplinas Isoladas')

    return aluno


def buscar_disciplinas_por_aluno_id(aluno_id):
    """Busca disciplinas e notas usando os capítulos reais; lê legado 1..N sem duplicar."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT d.id, d.nome, d.carga_horaria,
               addd.data_inicio, addd.data_fim_previsto, addd.frequencia, addd.progresso_manual,
               docente_info.docente_nome, docente_info.docente_titulacao, docente_info.ano_semestre,
               nf.nota_final, nf.media_disciplina, nf.media_final, nf.status AS status_final
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id AND ad.aluno_id = %s
        LEFT JOIN aluno_disciplina_datas addd
          ON ad.aluno_id = addd.aluno_id AND ad.disciplina_id = addd.disciplina_id
        LEFT JOIN LATERAL (
            SELECT doc.nome AS docente_nome, doc.titulacao AS docente_titulacao, dd.ano_semestre
            FROM disciplina_docente dd
            JOIN docentes doc ON dd.docente_id = doc.id
            WHERE dd.disciplina_id = d.id AND COALESCE(doc.ativo, 1) = 1
            ORDER BY dd.id DESC LIMIT 1
        ) docente_info ON TRUE
        LEFT JOIN notas_finais nf
          ON ad.aluno_id = nf.aluno_id AND d.id = nf.disciplina_id
        ORDER BY d.nome
    """, (aluno_id,))
    disciplinas_raw = cursor.fetchall()
    disciplinas = []

    for row in disciplinas_raw:
        disc = dict(row)
        carga_horaria = int(disc.get("carga_horaria") or 80)
        if disc.get("docente_nome"):
            docente_display = disc["docente_nome"]
            if disc.get("docente_titulacao"):
                docente_display += f" ({disc['docente_titulacao']})"
        else:
            docente_display = _docente_documental_disciplina(cursor, disc["id"], disc["nome"])

        periodo = disc.get("ano_semestre") or ""
        if not periodo and disc.get("data_inicio"):
            data_obj = _parse_data_sigeu(disc.get("data_inicio"))
            if data_obj:
                periodo = f"{data_obj.year}.{'1' if data_obj.month <= 6 else '2'}"
        if not periodo:
            periodo = f"{datetime.now().year}.1"

        unidades = _notas_logicas_disciplina(cursor, aluno_id, disc["id"])
        notas = [u["nota"] for u in unidades]
        while len(notas) < 4:
            notas.append(None)
        nota_exibicao = disc.get("media_final") if disc.get("media_final") is not None else disc.get("nota_final")
        nota_exibicao = round(float(nota_exibicao), 2) if nota_exibicao is not None else None
        status_norm = _normalizar_status_academico(disc.get("status_final"))
        status_display = "APROVADO" if status_norm == "aprovado" else "REPROVADO" if status_norm == "reprovado" else "CURSANDO"
        semestre = str(periodo).split(".")[-1] if "." in str(periodo) else "1"

        disciplinas.append({
            "id": disc["id"], "nome": disc["nome"], "periodo": periodo, "semestre": semestre,
            "carga": carga_horaria, "carga_horaria": carga_horaria, "docente": docente_display,
            "nota": nota_exibicao, "status": status_display,
            "nota1": notas[0], "nota2": notas[1], "nota3": notas[2], "nota4": notas[3],
            "notas_unidades": unidades,
            "nota_final": disc.get("nota_final"), "media_disciplina": disc.get("media_disciplina"),
            "media_final": disc.get("media_final"), "frequencia": disc.get("frequencia"),
            "progresso_manual": disc.get("progresso_manual"),
            "data_inicio": disc.get("data_inicio"), "data_fim_previsto": disc.get("data_fim_previsto"),
        })
    conn.commit()
    conn.close()
    return disciplinas


def buscar_dados_pessoais_completos(aluno_id):
    """Busca dados pessoais completos do aluno - VERSÃO COMPLETA"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT a.*,
               dp.cpf, dp.rg, dp.telefone, dp.endereco, dp.cidade, dp.estado, dp.cep,
               dp.curso_referencia
        FROM alunos a
        LEFT JOIN dados_pessoais dp ON a.id = dp.aluno_id
        WHERE a.id = %s
    """, (aluno_id,))

    aluno_row = cursor.fetchone()

    if not aluno_row:
        conn.close()
        return None

    aluno = dict(aluno_row)

    # Formatar dados
    aluno['cpf_formatado'] = formatar_cpf(aluno.get('cpf', '')) if aluno.get('cpf') else ''
    aluno['telefone_formatado'] = formatar_telefone(aluno.get('telefone', '')) if aluno.get('telefone') else ''
    aluno['endereco_completo'] = f"{aluno.get('endereco', '')}, {aluno.get('cidade', '')} - {aluno.get('estado', '')}, CEP: {aluno.get('cep', '')}"

    conn.close()
    return aluno

def formatar_cpf(cpf):
    """Formata CPF: 000.000.000-00"""
    cpf = ''.join(filter(str.isdigit, cpf))
    if len(cpf) == 11:
        return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
    return cpf

def formatar_telefone(tel):
    """Formata telefone: (00) 00000-0000"""
    tel = ''.join(filter(str.isdigit, tel))
    if len(tel) == 11:
        return f"({tel[:2]}) {tel[2:7]}-{tel[7:]}"
    elif len(tel) == 10:
        return f"({tel[:2]}) {tel[2:6]}-{tel[6:]}"
    return tel

def salvar_documento_autenticado(documento_data):
    """Salva documento autenticado. A estrutura é criada exclusivamente pela migração."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        data_emissao = documento_data.get('data_emissao')
        if hasattr(data_emissao, 'strftime'):
            data_emissao = data_emissao.strftime('%d/%m/%Y %H:%M')
        cursor.execute("""
            INSERT INTO documentos_autenticados
            (codigo_autenticacao, codigo, aluno_id, tipo_documento, tipo, hash_documento,
             conteudo_html, data_emissao, data_geracao, observacoes, aluno_nome, aluno_ra)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            documento_data.get('codigo_autenticacao'),
            documento_data.get('codigo') or documento_data.get('codigo_autenticacao'),
            documento_data.get('aluno_id'),
            documento_data.get('tipo_documento'),
            documento_data.get('tipo') or documento_data.get('tipo_documento'),
            documento_data.get('hash_documento'),
            documento_data.get('conteudo_html'),
            data_emissao,
            documento_data.get('data_geracao') or data_emissao,
            documento_data.get('observacoes', ''),
            documento_data.get('aluno_nome', ''),
            documento_data.get('aluno_ra', ''),
        ))
        documento_id = cursor.fetchone()['id']
        conn.commit()
        return documento_id
    except Exception as e:
        conn.rollback()
        print(f"Erro em salvar_documento_autenticado: {e}")
        return None
    finally:
        conn.close()


@app.route("/mew/visualizar-documento/<codigo>")
def mew_visualizar_documento(codigo):
    """Visualiza tanto documentos antigos (codigo_autenticacao) quanto novos (codigo)."""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM documentos_autenticados WHERE codigo=%s OR codigo_autenticacao=%s ORDER BY id DESC LIMIT 1", (codigo, codigo))
    documento = cursor.fetchone(); conn.close()
    if not documento:
        return "Documento não encontrado", 404
    doc = dict(documento)
    cod = doc.get("codigo") or doc.get("codigo_autenticacao") or codigo
    conteudo = doc.get("conteudo_html") or "<p>Conteúdo indisponível.</p>"
    return f"""<!DOCTYPE html><html><head><meta charset='UTF-8'><title>Documento {cod}</title><style>body{{margin:0;font-family:Arial}}.barra{{background:#3f464b;color:white;padding:12px;text-align:center}}.btn{{position:fixed;right:20px;bottom:20px;background:#3f464b;color:white;padding:10px 15px;border:0;border-radius:5px;z-index:999}}@media print{{.barra,.btn{{display:none}}}}</style></head><body><div class='barra'>Documento autenticado • Código: {cod}</div>{conteudo}<button class='btn' onclick='window.print()'>Imprimir / PDF</button></body></html>"""


def gerar_codigo_simples():
    """Gera código simples de 10 caracteres: FACP-XXXX"""
    letras_numeros = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codigo = "FACP-" + ''.join(random.choice(letras_numeros) for _ in range(8))
    return codigo

def salvar_documento_simples(codigo, aluno_nome, aluno_ra, tipo, conteudo_html):
    """Salva documento de forma simples"""
    conn = get_db_connection()
    cursor = conn.cursor()

    data_geracao = datetime.now().strftime('%d/%m/%Y')

    cursor.execute("""
        INSERT INTO documentos_autenticados
        (codigo, aluno_nome, aluno_ra, tipo, conteudo_html, data_geracao)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (codigo, aluno_nome, aluno_ra, tipo, conteudo_html, data_geracao))

    conn.commit()
    conn.close()

    return True

def buscar_documento_por_codigo(codigo):
    """Busca documento pelo código"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM documentos_autenticados WHERE codigo = %s", (codigo,))
    documento = cursor.fetchone()
    conn.close()

    if documento:
        return {
            'codigo': documento['codigo'],
            'aluno_nome': documento['aluno_nome'],
            'aluno_ra': documento['aluno_ra'],
            'tipo': documento['tipo'],
            'conteudo_html': documento['conteudo_html'],
            'data_geracao': documento['data_geracao']
        }
    return None


# ==========================
# FUNÇÕES PARA HISTÓRICO AUTENTICADO - SIMPLES!
# ==========================

def obter_configuracao_ano():
    """Obtém o ano configurado para os documentos ou usa o ano atual"""
    # Você pode criar uma tabela no banco para configurações se quiser
    # Por enquanto, vamos usar um arquivo de configuração ou variável de ambiente
    ano_configurado = os.environ.get("HISTORICO_ANO", None)

    if ano_configurado:
        return ano_configurado

    # Se não tiver configuração, use o ano atual
    from datetime import datetime
    return str(datetime.now().year)

def calcular_ira_aluno_completo(aluno_id):
    """Calcular IRA do aluno baseado nas disciplinas aprovadas - VERSÃO CORRIGIDA (ponderada)"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar todas as disciplinas do aluno com status final
    cursor.execute("""
        SELECT
            d.carga_horaria,
            nf.media_final,
            nf.status
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        LEFT JOIN notas_finais nf ON ad.aluno_id = nf.aluno_id AND d.id = nf.disciplina_id
        WHERE ad.aluno_id = %s
    """, (aluno_id,))

    disciplinas = cursor.fetchall()

    # Mapeamento de nota para conceito (baseado na média final 0-100)
    def nota_para_conceito_valor(nota):
        """Converte nota de 0-100 para valor do conceito"""
        if nota >= 90: return ("A", 4.0)
        elif nota >= 80: return ("B", 3.0)
        elif nota >= 70: return ("C", 2.0)
        elif nota >= 60: return ("D", 1.0)
        else: return ("F", 0.0)

    # Calcular IRA ponderado pela carga horária
    soma_pontos = 0
    soma_carga = 0
    disciplinas_aprovadas = 0
    carga_total_aprovada = 0

    for disc in disciplinas:
        carga = disc['carga_horaria'] if disc['carga_horaria'] else 80

        if disc['status'] == 'aprovado' and disc['media_final'] is not None:
            nota = disc['media_final']
            # Converter nota para valor do conceito
            _, valor_conceito = nota_para_conceito_valor(nota)

            # Soma ponderada: valor_conceito * carga_horária
            soma_pontos += valor_conceito * carga
            soma_carga += carga
            disciplinas_aprovadas += 1
            carga_total_aprovada += carga

    # IRA = Soma(conceito_valor * carga_horária) / Soma(carga_horária)
    ira = soma_pontos / soma_carga if soma_carga > 0 else 0

    conn.close()

    return {
        'ira': round(ira, 2),
        'disciplinas_aprovadas': disciplinas_aprovadas,
        'carga_total_aprovada': carga_total_aprovada
    }

def calcular_ira_aluno_completo(aluno_id):
    """Calcular IRA do aluno baseado nas disciplinas aprovadas - VERSÃO PONDERADA"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar todas as disciplinas do aluno com status final
    cursor.execute("""
        SELECT
            d.carga_horaria,
            nf.media_final,
            nf.status
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        LEFT JOIN notas_finais nf ON ad.aluno_id = nf.aluno_id AND d.id = nf.disciplina_id
        WHERE ad.aluno_id = %s
    """, (aluno_id,))

    disciplinas = cursor.fetchall()

    # Mapeamento de nota para conceito (baseado na média final 0-100)
    def nota_para_conceito_valor(nota):
        """Converte nota de 0-100 para valor do conceito"""
        if nota >= 90: return ("A", 4.0)
        elif nota >= 80: return ("B", 3.0)
        elif nota >= 70: return ("C", 2.0)
        elif nota >= 60: return ("D", 1.0)
        else: return ("F", 0.0)

    # Calcular IRA ponderado pela carga horária
    soma_pontos = 0
    soma_carga = 0
    disciplinas_aprovadas = 0
    carga_total_aprovada = 0

    for disc in disciplinas:
        carga = disc['carga_horaria'] if disc['carga_horaria'] else 80

        if disc['status'] == 'aprovado' and disc['media_final'] is not None:
            nota = disc['media_final']
            # Converter nota para valor do conceito
            _, valor_conceito = nota_para_conceito_valor(nota)

            # Soma ponderada: valor_conceito * carga_horária
            soma_pontos += valor_conceito * carga
            soma_carga += carga
            disciplinas_aprovadas += 1
            carga_total_aprovada += carga

    # IRA = Soma(conceito_valor * carga_horária) / Soma(carga_horária)
    ira = soma_pontos / soma_carga if soma_carga > 0 else 0

    conn.close()

    return {
        'ira': round(ira, 2),
        'disciplinas_aprovadas': disciplinas_aprovadas,
        'carga_total_aprovada': carga_total_aprovada
    }

def obter_configuracao_ano():
    """Obtém o ano configurado para os documentos ou usa o ano atual"""
    ano_configurado = os.environ.get("HISTORICO_ANO", None)

    if ano_configurado:
        return ano_configurado

    from datetime import datetime
    return str(datetime.now().year)



def gerar_historico_automatico(aluno_id, disciplinas, dados_aluno, qr_code_base64, codigo, hash_documento, ano_manual=None, ira_manual='N/I', total_disciplinas_manual='0', frequencia_manual='N/I'):
    """Gera HTML do histórico escolar com QR CODE JÁ INCLUSO"""

    conn = get_db_connection()
    cursor = conn.cursor()

    # Carrega em uma única consulta tudo que será usado por disciplina no histórico.
    # Evita 3 consultas extras para cada linha do documento.
    disciplina_ids = [int(d['id']) for d in disciplinas if d.get('id') is not None]
    info_por_disciplina = {}
    if disciplina_ids:
        cursor.execute("""
            SELECT d.id, d.carga_horaria,
                   doc.nome AS docente_nome, doc.titulacao,
                   adt.data_inicio,
                   nf.media_final
            FROM disciplinas d
            LEFT JOIN LATERAL (
                SELECT dd.docente_id
                FROM disciplina_docente dd
                WHERE dd.disciplina_id = d.id
                ORDER BY dd.ano_semestre DESC, dd.id DESC
                LIMIT 1
            ) dd_ultimo ON TRUE
            LEFT JOIN docentes doc ON doc.id = dd_ultimo.docente_id
            LEFT JOIN aluno_disciplina_datas adt
                   ON adt.aluno_id = %s AND adt.disciplina_id = d.id
            LEFT JOIN notas_finais nf
                   ON nf.aluno_id = %s AND nf.disciplina_id = d.id
            WHERE d.id = ANY(%s)
        """, (aluno_id, aluno_id, disciplina_ids))
        info_por_disciplina = {int(row['id']): dict(row) for row in cursor.fetchall()}

    carga_total_aprovada = 0
    carga_total_cursada = 0
    for d in disciplinas:
        info = info_por_disciplina.get(int(d['id']), {})
        carga = int(info.get('carga_horaria') or 80)
        carga_total_cursada += carga
        if d.get('status', '').upper() == 'APROVADO':
            carga_total_aprovada += carga

    # Data atual
    from datetime import datetime
    data_atual = datetime.now().strftime("%d/%m/%Y")

    # Obter ano configurável
    ano_historico = ano_manual if ano_manual else obter_configuracao_ano()

    # ===== USAR VALORES MANUAIS DO FORMULÁRIO =====
    ira_display = ira_manual

    # Converter total_disciplinas_manual para número
    try:
        total_disciplinas_valor = int(total_disciplinas_manual)
    except:
        total_disciplinas_valor = 0

    ira_info = {
        'disciplinas_aprovadas': total_disciplinas_valor,
        'carga_total_aprovada': carga_total_aprovada
    }
    # ==============================================

    # Buscar dados adicionais do aluno
    cursor.execute("""
        SELECT nome_pai, nome_mae, naturalidade, nacionalidade,
               data_nascimento, sexo, estado_civil, curso_referencia
        FROM dados_pessoais
        WHERE aluno_id = %s
    """, (aluno_id,))

    dados_adicionais = cursor.fetchone()

    # Formatar filiação
    if dados_adicionais:
        pai = dados_adicionais['nome_pai'] if dados_adicionais['nome_pai'] else ''
        mae = dados_adicionais['nome_mae'] if dados_adicionais['nome_mae'] else ''
        if pai and mae:
            filiacao = f"{pai} e {mae}"
        elif pai:
            filiacao = pai
        elif mae:
            filiacao = mae
        else:
            filiacao = ""

        naturalidade = dados_adicionais['naturalidade'] if dados_adicionais['naturalidade'] else ''
        nacionalidade = dados_adicionais['nacionalidade'] if dados_adicionais['nacionalidade'] else 'Brasileira'
        data_nascimento = dados_adicionais['data_nascimento'] if dados_adicionais['data_nascimento'] else ''
        sexo = dados_adicionais['sexo'] if dados_adicionais['sexo'] else ''
        estado_civil = dados_adicionais['estado_civil'] if dados_adicionais['estado_civil'] else ''
        curso_referencia = dados_adicionais['curso_referencia'] if dados_adicionais['curso_referencia'] else 'Disciplinas Isoladas'
    else:
        filiacao = ""
        naturalidade = ""
        nacionalidade = "Brasileira"
        data_nascimento = ""
        sexo = ""
        estado_civil = ""
        curso_referencia = dados_aluno.get('curso_referencia', 'Disciplinas Isoladas')

    # Converter abreviações de sexo
    if sexo.upper() in ['M', 'MASC', 'MASCULINO']:
        sexo_display = 'MASCULINO'
    elif sexo.upper() in ['F', 'FEM', 'FEMININO']:
        sexo_display = 'FEMININO'
    else:
        sexo_display = sexo

    # Gerar linhas da tabela com os dados já carregados acima.
    linhas = ""
    for d in disciplinas:
        info_disc = info_por_disciplina.get(int(d['id']), {})
        carga_horaria = info_disc.get('carga_horaria') or 80

        if info_disc.get('docente_nome'):
            docente = info_disc['docente_nome']
            if info_disc.get('titulacao'):
                docente += f" ({info_disc['titulacao']})"
        else:
            docente = 'Docente Titular'

        data_inicio_disc = info_disc.get('data_inicio')
        if data_inicio_disc:
            try:
                data_obj = datetime.strptime(data_inicio_disc, "%d/%m/%Y")
                ano = data_obj.year
                mes = data_obj.month
                semestre = "1" if mes <= 6 else "2"
                periodo = f"{ano}.{semestre}"
            except Exception:
                periodo = f"{datetime.now().year}.1"
        else:
            periodo = f"{datetime.now().year}.1"

        media_final = info_disc.get('media_final')
        nota_display = f"{float(media_final):.2f}" if media_final is not None else "N/I"

        # Determinar status
        status_display = d.get('status', 'CURSANDO')

        # Determinar semestre
        semestre = periodo.split('.')[-1] if '.' in periodo else "1"

        # Frequência administrativa é a fonte de verdade. O valor manual, quando informado,
        # é persistido antes da geração; sem valor manual usamos o que já está no banco.
        if str(frequencia_manual or "").strip().upper() not in {"", "N/I", "NI", "NONE"}:
            frequencia = frequencia_manual
        elif d.get("frequencia") is not None:
            frequencia = f"{float(d.get('frequencia')):.0f}%"
        else:
            frequencia = "N/I"

        linhas += f"""
            <tr>
                <td style="border: 1px solid #000; padding: 4px; text-align: center;">{periodo}</td>
                <td style="border: 1px solid #000; padding: 4px; text-align: left;">{d.get('nome', 'Disciplina')}</td>
                <td style="border: 1px solid #000; padding: 4px; text-align: center;">{semestre}</td>
                <td style="border: 1px solid #000; padding: 4px; text-align: center;">{carga_horaria}H</td>
                <td style="border: 1px solid #000; padding: 4px; text-align: left;">{docente}</td>
                <td style="border: 1px solid #000; padding: 4px; text-align: center;">{nota_display}</td>
                <td style="border: 1px solid #000; padding: 4px; text-align: center;">{frequencia}</td>
                <td style="border: 1px solid #000; padding: 4px; text-align: center;">{status_display}</td>
            </tr>
        """

    # Gerar link de validação
    base_url = "https://campusvirtualfacop.com.br"
    link_validacao = f"{base_url}/validar-documento/{codigo}"
    data_emissao = datetime.now().strftime("%d/%m/%Y %H:%M")
    data_validade = (datetime.now() + timedelta(days=365*5)).strftime("%d/%m/%Y")

    # HTML institucional final: preto e branco, sem padrões geométricos, mantendo os dados completos.
    # O campo de banco continua se chamando curso_referencia para compatibilidade, mas é exibido como Unidade Curricular.
    html = f'''<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><title>HISTÓRICO ACADÊMICO - {dados_aluno.get('nome','')}</title>
<style>
@page {{ size:A4; margin:14mm; }} *{{box-sizing:border-box}}
body{{margin:0;background:#fff;font-family:Arial,Helvetica,sans-serif;color:#000;font-size:9.3pt;line-height:1.35}}
.doc{{background:#fff}}
.cab{{border-bottom:2px solid #000;padding-bottom:9px;margin-bottom:14px;display:flex;justify-content:space-between;gap:15px;align-items:flex-start}}
.brand{{font-size:15pt;font-weight:700}} .sub{{font-size:8.5pt;margin-top:3px}}
.cert{{font-size:7.7pt;text-align:right;max-width:52%;line-height:1.35}} .cert b{{font-size:9pt}}
h1{{text-align:center;font-size:19pt;margin:16px 0 14px}}
.dados-tabela{{width:100%;border-collapse:collapse;margin-bottom:13px;font-size:8.8pt}}
.dados-tabela td{{border:1px solid #000;padding:6px 8px;width:50%;vertical-align:top}}
table{{width:100%;border-collapse:collapse;font-size:8.1pt}} thead{{display:table-header-group}} tr{{page-break-inside:avoid}}
th,td{{border:1px solid #000;padding:4px;vertical-align:top}} th{{background:#fff;color:#000;text-transform:uppercase;font-size:7.3pt}}
.resumo{{margin-top:12px;border:1px solid #000;padding:8px;display:flex;gap:16px;flex-wrap:wrap}}
.assinatura{{margin:10mm auto 5mm;text-align:center;max-width:90mm}} .assinatura strong{{display:block;font-size:10pt}} .assinatura span{{display:block;font-size:8pt;margin-top:2px}} .assinatura small{{display:block;font-size:7pt;margin-top:3px}}
.auth{{margin-top:10px;border-top:1px solid #000;padding-top:8px;display:grid;grid-template-columns:76px 1fr;gap:10px;align-items:center}}
.auth img{{width:72px;height:72px}} .hash{{font-family:monospace;font-size:6.5pt;word-break:break-all;margin-top:4px}}
.obs{{margin-top:11px;border:1px solid #000;padding:8px;font-size:7.6pt}}
.rodape{{margin-top:9px;border-top:1px solid #000;padding-top:6px;font-size:6.5pt;text-align:center}}
</style></head><body><div class="doc">
<div class="cab"><div><div class="brand">GRUPO EDUCACIONAL UNIFICADO</div><div class="sub">SIGEU Educacional • Sistema Integrado de Gestão Educacional</div></div><div class="cert"><b>FACOP CERTIFICADORA</b><br>Faculdade do Centro Oeste Paulista LTDA<br>CNPJ 04.344.730/0001-60 • Portaria MEC nº 887 de 26/07/2017</div></div>
<h1>HISTÓRICO ACADÊMICO</h1>
<table class="dados-tabela">
<tr><td><b>Aluno:</b> {dados_aluno.get('nome','')}</td><td><b>RA:</b> {dados_aluno.get('ra','')}</td></tr>
<tr><td><b>CPF:</b> {dados_aluno.get('cpf_formatado','')}</td><td><b>Ano de referência:</b> {ano_historico}</td></tr>
<tr><td><b>Filiação:</b> {filiacao or 'N/I'}</td><td><b>Data de nascimento:</b> {data_nascimento or 'N/I'}</td></tr>
<tr><td><b>Naturalidade:</b> {naturalidade or 'N/I'}</td><td><b>Nacionalidade:</b> {nacionalidade or 'N/I'}</td></tr>
<tr><td><b>Sexo:</b> {sexo_display or 'N/I'}</td><td><b>Estado civil:</b> {estado_civil or 'N/I'}</td></tr>
<tr><td colspan="2"><b>Unidade Curricular:</b> {curso_referencia or 'Disciplinas / Unidades Curriculares'}</td></tr>
</table>
<table><thead><tr><th>Período</th><th>Componente Curricular</th><th>Sem.</th><th>C.H.</th><th>Docente/Titulação</th><th>Nota Final</th><th>Frequência</th><th>Resultado</th></tr></thead><tbody>{linhas}
<tr><td colspan="3"><b>Carga Horária Total Aprovada</b></td><td><b>{carga_total_aprovada}H</b></td><td colspan="2"><b>Carga Horária Total Cursada</b></td><td colspan="2"><b>{carga_total_cursada}H</b></td></tr></tbody></table>
<div class="resumo"><span><b>IRA:</b> {ira_display}</span><span><b>Disciplinas aprovadas:</b> {ira_info['disciplinas_aprovadas']}</span><span><b>Carga aprovada:</b> {carga_total_aprovada}H</span><span><b>Carga cursada:</b> {carga_total_cursada}H</span></div>
<div class="obs"><b>Observação:</b> documento emitido para registro dos componentes curriculares cursados. A FACOP CERTIFICADORA atua na certificação documental conforme a parceria educacional registrada. Os dados acadêmicos e pessoais acima são mantidos conforme cadastro do estudante.</div>
<div class="assinatura"><strong>Tatiane R. L. Costa</strong><span>Documento assinado eletronicamente</span><small>Assinatura validada pela certificação institucional.</small></div>
<div class="auth"><img src="{qr_code_base64}" alt="QR Code"><div><b>Código:</b> {codigo}<br><b>Emissão:</b> {data_emissao}<br><b>Validade:</b> {data_validade}<div class="hash">SHA-256: {hash_documento}</div></div></div>
<div class="rodape">GRUPO EDUCACIONAL UNIFICADO • SIGEU Educacional • FACOP CERTIFICADORA • Validação eletrônica pelo QR Code e código acima.</div>
</div></body></html>'''

    conn.close()
    return html

@app.route("/ver-documento/<codigo>")
def ver_documento_completo(codigo):
    """
    Mostra o documento completo com QR Code e informações de autenticação
    VERSÃO CORRIGIDA
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM documentos_autenticados WHERE codigo = %s", (codigo.upper(),))
        documento = cursor.fetchone()
        conn.close()

        if not documento:
            return '''
            <html>
            <head>
                <title>Documento não encontrado</title>
                <style>
                    body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f5f5f5; }
                    .error-box {
                        background: white;
                        padding: 30px;
                        border-radius: 10px;
                        max-width: 500px;
                        margin: 0 auto;
                        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                        border-left: 4px solid #dc3545;
                    }
                    .btn {
                        display: inline-block;
                        padding: 10px 20px;
                        background: #343a40;
                        color: white;
                        text-decoration: none;
                        border-radius: 5px;
                        margin-top: 20px;
                    }
                </style>
            </head>
            <body>
                <div class="error-box">
                    <h2>❌ Documento não encontrado</h2>
                    <p>Código: <strong>{}</strong></p>
                    <p>Este documento não foi encontrado no sistema ou foi removido.</p>
                    <a href="/validar-documento" class="btn">← Validar outro documento</a>
                </div>
            </body>
            </html>
            '''.format(codigo)

        # Converter para dicionário para facilitar o acesso
        doc_dict = dict(documento)

        # Retornar o HTML salvo no banco diretamente
        return doc_dict.get('conteudo_html', '<p>Erro: Conteúdo não encontrado</p>')

    except Exception as e:
        return f"Erro ao carregar documento: {str(e)}"

# ==========================
# ROTA PARA LISTAR DOCUMENTOS (MEW)
# ==========================

@app.route("/mew/listar-documentos")
def mew_listar_documentos():
    """Lista metadados dos documentos sem carregar HTML, QR ou arquivos pesados."""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    try:
        pagina = max(1, int(request.args.get("pagina", 1)))
    except (TypeError, ValueError):
        pagina = 1
    por_pagina = min(100, max(20, int(os.getenv("DOCS_PAGE_SIZE", "50"))))
    offset = (pagina - 1) * por_pagina
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) AS total FROM documentos_autenticados")
        total = int((cursor.fetchone() or {}).get("total") or 0)
        cursor.execute("""
            SELECT id,
                   COALESCE(codigo, codigo_autenticacao) AS codigo_autenticacao,
                   COALESCE(tipo, tipo_documento) AS tipo_documento,
                   aluno_id, aluno_nome, aluno_ra,
                   data_geracao, data_emissao, data_validade, disciplina_id,
                   CASE
                     WHEN data_validade IS NULL OR data_validade='' THEN 'válido'
                     ELSE 'válido'
                   END AS status
            FROM documentos_autenticados
            ORDER BY id DESC
            LIMIT %s OFFSET %s
        """, (por_pagina, offset))
        documentos = cursor.fetchall()
    finally:
        conn.close()
    total_paginas = max(1, (total + por_pagina - 1) // por_pagina)
    return render_template(
        "mew/listar_documentos.html", documentos=documentos,
        pagina=pagina, total_paginas=total_paginas, total_documentos=total
    )


# ==========================
# ROTA PARA DELETAR DOCUMENTO (MEW)
# ==========================

@app.route("/mew/deletar-documento/<codigo>")
def deletar_documento(codigo):
    """Deleta o registro e, quando existir, o objeto privado correspondente no R2."""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, arquivo_r2_key FROM documentos_autenticados WHERE codigo=%s OR codigo_autenticacao=%s ORDER BY id DESC",
            (codigo, codigo),
        )
        encontrados = cursor.fetchall()
        ids = [r["id"] for r in encontrados]
        if ids:
            cursor.execute("DELETE FROM documentos_enviados WHERE documento_original_id = ANY(%s)", (ids,))
        cursor.execute("DELETE FROM documentos_autenticados WHERE codigo=%s OR codigo_autenticacao=%s", (codigo, codigo))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()

    for row in encontrados:
        key = row.get("arquivo_r2_key")
        if key:
            try:
                delete_object(key)
            except Exception as exc:
                app.logger.warning("Documento %s excluído do banco, mas falhou a remoção do R2 (%s): %s", row.get("id"), key, exc)

    return redirect("/mew/listar-documentos?sucesso=Documento+removido")

# ==========================
# MEW - GERENCIAR INFORMAÇÕES DAS DISCIPLINAS
# ==========================

@app.route("/mew/info-disciplinas")
def mew_info_disciplinas():
    """Página principal para gerenciar informações das disciplinas"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    return render_template("mew/info_disciplinas.html")

@app.route("/mew/docentes", methods=["GET", "POST"])
def mew_docentes():
    """Gerenciar docentes"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        nome = request.form.get("nome")
        titulacao = request.form.get("titulacao", "")
        email = request.form.get("email", "")
        telefone = request.form.get("telefone", "")

        if not nome:
            conn.close()
            return redirect("/mew/docentes?erro=Nome+obrigatório")

        cursor.execute("""
            INSERT INTO docentes (nome, titulacao, email, telefone)
            VALUES (%s, %s, %s, %s)
        """, (nome, titulacao, email, telefone))

        conn.commit()
        conn.close()
        return redirect("/mew/docentes?sucesso=Docente+cadastrado")

    # GET: Listar docentes
    cursor.execute("SELECT * FROM docentes ORDER BY nome")
    docentes = cursor.fetchall()

    conn.close()

    return render_template("mew/docentes.html", docentes=docentes)

@app.route("/mew/editar-docente/<int:docente_id>", methods=["GET", "POST"])
def mew_editar_docente(docente_id):
    """Editar informações de um docente"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        nome = request.form.get("nome")
        titulacao = request.form.get("titulacao", "")
        email = request.form.get("email", "")
        telefone = request.form.get("telefone", "")
        ativo = 1 if request.form.get("ativo") in {"1", "on", "true", "True"} else 0

        cursor.execute("""
            UPDATE docentes
            SET nome = %s, titulacao = %s, email = %s, telefone = %s, ativo = %s
            WHERE id = %s
        """, (nome, titulacao, email, telefone, ativo, docente_id))

        conn.commit()
        conn.close()
        return redirect("/mew/docentes?sucesso=Docente+atualizado")

    # GET: Buscar docente
    cursor.execute("SELECT * FROM docentes WHERE id = %s", (docente_id,))
    docente = cursor.fetchone()

    if not docente:
        conn.close()
        return redirect("/mew/docentes?erro=Docente+não+encontrado")

    cursor.execute("""
        SELECT d.nome, dd.ano_semestre
        FROM disciplinas d
        JOIN disciplina_docente dd ON d.id = dd.disciplina_id
        WHERE dd.docente_id = %s
        ORDER BY dd.ano_semestre DESC NULLS LAST, d.nome
    """, (docente_id,))
    disciplinas_docente = cursor.fetchall()

    conn.close()

    return render_template(
        "mew/editar_docente.html",
        docente=docente,
        disciplinas_docente=disciplinas_docente
    )

@app.route("/mew/deletar-docente/<int:docente_id>")
def mew_deletar_docente(docente_id):
    """Deletar docente (apenas se não estiver associado a disciplinas)"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Verificar se docente está associado a alguma disciplina
    cursor.execute("SELECT id FROM disciplina_docente WHERE docente_id = %s LIMIT 1", (docente_id,))
    if cursor.fetchone():
        conn.close()
        return redirect("/mew/docentes?erro=Docente+está+associado+a+disciplinas")

    cursor.execute("DELETE FROM docentes WHERE id = %s", (docente_id,))

    conn.commit()
    conn.close()

    return redirect("/mew/docentes?sucesso=Docente+removido")

@app.route("/mew/atribuir-info-disciplina", methods=["GET", "POST"])
def mew_atribuir_info_disciplina():
    """Atribuir informações a uma disciplina (carga horária, docente, semestre)"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        disciplina_id = request.form.get("disciplina_id")
        carga_horaria = request.form.get("carga_horaria", "80")
        docente_id = request.form.get("docente_id")
        ano_semestre = request.form.get("ano_semestre")

        if not disciplina_id:
            conn.close()
            return redirect("/mew/atribuir-info-disciplina?erro=Selecione+uma+disciplina")

        # Atualizar carga horária da disciplina
        cursor.execute("""
            UPDATE disciplinas
            SET carga_horaria = %s
            WHERE id = %s
        """, (carga_horaria, disciplina_id))

        # Uma disciplina tem um responsável atual para os documentos. Ao trocar, removemos
        # a associação anterior para que o sistema não continue exibindo o docente antigo.
        cursor.execute("DELETE FROM disciplina_docente WHERE disciplina_id = %s", (disciplina_id,))
        if docente_id and docente_id != "0":
            cursor.execute("""
                INSERT INTO disciplina_docente (disciplina_id, docente_id, ano_semestre)
                VALUES (%s, %s, %s)
            """, (disciplina_id, docente_id, ano_semestre))
            cursor.execute("SELECT nome FROM docentes WHERE id = %s", (docente_id,))
            docente_row = cursor.fetchone()
            cursor.execute("UPDATE disciplinas SET docente_documental = %s WHERE id = %s",
                           ((docente_row or {}).get("nome"), disciplina_id))
        else:
            cursor.execute("UPDATE disciplinas SET docente_documental = NULL WHERE id = %s", (disciplina_id,))

        conn.commit()
        conn.close()
        return redirect("/mew/atribuir-info-disciplina?sucesso=Informações+salvas")

    # GET: Mostrar formulário

    # Buscar disciplinas
    cursor.execute("SELECT id, nome, carga_horaria FROM disciplinas ORDER BY nome")
    disciplinas = cursor.fetchall()

    # Buscar docentes ativos
    cursor.execute("SELECT id, nome FROM docentes WHERE ativo = 1 ORDER BY nome")
    docentes = cursor.fetchall()

    # Gerar lista de anos/semestres
    from datetime import datetime
    ano_atual = datetime.now().year
    semestres = []
    for ano in range(2020, ano_atual + 3):  # De 2020 até 2 anos no futuro
        semestres.append(f"{ano}.1")
        semestres.append(f"{ano}.2")

    conn.close()

    return render_template(
        "mew/atribuir_info_disciplina.html",
        disciplinas=disciplinas,
        docentes=docentes,
        semestres=semestres
    )

@app.route("/mew/buscar-info-disciplina/<int:disciplina_id>")
def buscar_info_disciplina(disciplina_id):
    """Buscar informações de uma disciplina específica"""
    if not session.get("mew_admin"):
        return jsonify({"error": "Não autorizado"})

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar informações da disciplina
    cursor.execute("SELECT id, nome, carga_horaria FROM disciplinas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()

    if not disciplina:
        conn.close()
        return jsonify({"error": "Disciplina não encontrada"})

    # Buscar docente associado (mais recente)
    cursor.execute("""
        SELECT d.id, d.nome, dd.ano_semestre
        FROM docentes d
        JOIN disciplina_docente dd ON d.id = dd.docente_id
        WHERE dd.disciplina_id = %s
        ORDER BY dd.ano_semestre DESC
        LIMIT 1
    """, (disciplina_id,))

    docente_info = cursor.fetchone()

    conn.close()

    return jsonify({
        "success": True,
        "disciplina": dict(disciplina) if disciplina else None,
        "docente": dict(docente_info) if docente_info else None,
        "carga_horaria": disciplina["carga_horaria"] if disciplina and disciplina["carga_horaria"] else 80
    })

@app.route("/mew/listar-info-disciplinas")
def mew_listar_info_disciplinas():
    """Listar todas as disciplinas com suas informações"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar todas as disciplinas com suas informações
    cursor.execute("""
        SELECT
            d.id,
            d.nome,
            d.carga_horaria,
            doc.nome as docente_nome,
            doc.titulacao as docente_titulacao,
            dd.ano_semestre,
            (SELECT COUNT(*) FROM aluno_disciplina ad WHERE ad.disciplina_id = d.id) as total_alunos
        FROM disciplinas d
        LEFT JOIN disciplina_docente dd ON d.id = dd.disciplina_id
        LEFT JOIN docentes doc ON dd.docente_id = doc.id
        ORDER BY d.nome
    """)

    disciplinas = cursor.fetchall()

    conn.close()

    return render_template("mew/listar_info_disciplinas.html", disciplinas=disciplinas)

@app.route("/mew/rendimento-academico")
def mew_rendimento_academico():
    """Gerenciar rendimento acadêmico dos alunos"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar alunos
    cursor.execute("SELECT id, nome, ra FROM alunos ORDER BY nome")
    alunos = cursor.fetchall()

    # Buscar disciplinas
    cursor.execute("SELECT id, nome FROM disciplinas ORDER BY nome")
    disciplinas = cursor.fetchall()

    conn.close()

    return render_template(
        "mew/rendimento_academico.html",
        alunos=alunos,
        disciplinas=disciplinas
    )

@app.route("/mew/salvar-rendimento", methods=["POST"])
def mew_salvar_rendimento():
    """Salvar ou atualizar rendimento acadêmico"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})

    aluno_id = request.form.get("aluno_id")
    disciplina_id = request.form.get("disciplina_id")
    nota_final = request.form.get("nota_final")
    carga_horaria = request.form.get("carga_horaria", "80")
    conceito = request.form.get("conceito")

    if not all([aluno_id, disciplina_id, nota_final]):
        return jsonify({"success": False, "message": "Dados incompletos"})

    try:
        nota_final = float(nota_final.replace(",", "."))
        carga_horaria = int(carga_horaria)

        # Determinar conceito se não fornecido
        if not conceito:
            if nota_final >= 90:
                conceito = "A"
            elif nota_final >= 80:
                conceito = "B"
            elif nota_final >= 70:
                conceito = "C"
            elif nota_final >= 60:
                conceito = "D"
            else:
                conceito = "F"

        # Calcular peso (baseado na carga horária)
        peso = carga_horaria / 80.0

        conn = get_db_connection()
        cursor = conn.cursor()

        # Verificar se já existe
        cursor.execute("""
            SELECT id FROM rendimento_academico
            WHERE aluno_id = %s AND disciplina_id = %s
        """, (aluno_id, disciplina_id))

        if cursor.fetchone():
            # Atualizar
            cursor.execute("""
                UPDATE rendimento_academico
                SET nota_final = %s, carga_horaria = %s, conceito = %s, peso = %s
                WHERE aluno_id = %s AND disciplina_id = %s
            """, (nota_final, carga_horaria, conceito, peso, aluno_id, disciplina_id))
        else:
            # Inserir
            cursor.execute("""
                INSERT INTO rendimento_academico
                (aluno_id, disciplina_id, nota_final, carga_horaria, conceito, peso)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (aluno_id, disciplina_id, nota_final, carga_horaria, conceito, peso))

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Rendimento salvo com sucesso",
            "conceito": conceito,
            "peso": peso
        })

    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"})

@app.route("/mew/buscar-rendimento/<int:aluno_id>/<int:disciplina_id>")
def buscar_rendimento(aluno_id, disciplina_id):
    """Buscar rendimento acadêmico de um aluno em uma disciplina"""
    if not session.get("mew_admin"):
        return jsonify({"error": "Não autorizado"})

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ra.*, a.nome as aluno_nome, d.nome as disciplina_nome
        FROM rendimento_academico ra
        JOIN alunos a ON ra.aluno_id = a.id
        JOIN disciplinas d ON ra.disciplina_id = d.id
        WHERE ra.aluno_id = %s AND ra.disciplina_id = %s
    """, (aluno_id, disciplina_id))

    rendimento = cursor.fetchone()

    conn.close()

    if rendimento:
        return jsonify({"success": True, "rendimento": dict(rendimento)})
    else:
        return jsonify({"success": False, "message": "Rendimento não encontrado"})

@app.route("/mew/ira-aluno/<int:aluno_id>")
def calcular_ira_aluno_completo(aluno_id):
    """Calcular IRA do aluno baseado nas disciplinas aprovadas - VERSÃO CORRIGIDA (ponderada)"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar todas as disciplinas do aluno com status final
    cursor.execute("""
        SELECT
            d.carga_horaria,
            nf.media_final,
            nf.status
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        LEFT JOIN notas_finais nf ON ad.aluno_id = nf.aluno_id AND d.id = nf.disciplina_id
        WHERE ad.aluno_id = %s
    """, (aluno_id,))

    disciplinas = cursor.fetchall()

    # Mapeamento de nota para conceito (baseado na média final 0-100)
    def nota_para_conceito_valor(nota):
        """Converte nota de 0-100 para valor do conceito"""
        if nota >= 90: return ("A", 4.0)
        elif nota >= 80: return ("B", 3.0)
        elif nota >= 70: return ("C", 2.0)
        elif nota >= 60: return ("D", 1.0)
        else: return ("F", 0.0)

    # Calcular IRA ponderado pela carga horária
    soma_pontos = 0
    soma_carga = 0
    disciplinas_aprovadas = 0
    carga_total_aprovada = 0

    for disc in disciplinas:
        carga = disc['carga_horaria'] if disc['carga_horaria'] else 80

        if disc['status'] == 'aprovado' and disc['media_final'] is not None:
            nota = disc['media_final']
            # Converter nota para valor do conceito
            _, valor_conceito = nota_para_conceito_valor(nota)

            # Soma ponderada: valor_conceito * carga_horária
            soma_pontos += valor_conceito * carga
            soma_carga += carga
            disciplinas_aprovadas += 1
            carga_total_aprovada += carga

    # IRA = Soma(conceito_valor * carga_horária) / Soma(carga_horária)
    ira = soma_pontos / soma_carga if soma_carga > 0 else 0

    conn.close()

    return {
        'ira': round(ira, 2),
        'disciplinas_aprovadas': disciplinas_aprovadas,
        'carga_total_aprovada': carga_total_aprovada
    }

@app.route("/mew/api/estatisticas-info-disciplinas")
def api_estatisticas_info_disciplinas():
    """API para estatísticas das informações das disciplinas"""
    if not session.get("mew_admin"):
        return jsonify({"error": "Não autorizado"})

    conn = get_db_connection()
    cursor = conn.cursor()

    # Total de disciplinas
    cursor.execute("SELECT COUNT(*) as total FROM disciplinas")
    total_disciplinas = cursor.fetchone()["total"] or 0

    # Total de docentes
    cursor.execute("SELECT COUNT(*) as total FROM docentes WHERE ativo = 1")
    total_docentes = cursor.fetchone()["total"] or 0

    # Disciplinas com informações completas (carga horária + docente)
    cursor.execute("""
        SELECT COUNT(DISTINCT d.id) as total
        FROM disciplinas d
        LEFT JOIN disciplina_docente dd ON d.id = dd.disciplina_id
        WHERE (d.carga_horaria IS NOT NULL AND d.carga_horaria != 80)
           OR dd.docente_id IS NOT NULL
    """)
    disciplinas_com_info = cursor.fetchone()["total"] or 0

    conn.close()

    return jsonify({
        "success": True,
        "total_disciplinas": total_disciplinas,
        "total_docentes": total_docentes,
        "disciplinas_com_info": disciplinas_com_info
    })

def calcular_ira_aluno_completo(aluno_id):
    """Calcular IRA do aluno baseado nas disciplinas aprovadas"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar todas as disciplinas do aluno com status final
    cursor.execute("""
        SELECT
            d.carga_horaria,
            nf.media_final,
            nf.status
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        LEFT JOIN notas_finais nf ON ad.aluno_id = nf.aluno_id AND d.id = nf.disciplina_id
        WHERE ad.aluno_id = %s
    """, (aluno_id,))

    disciplinas = cursor.fetchall()

    # Mapeamento de conceitos
    def nota_para_conceito(nota):
        # As notas do SIGEU estão na escala 0-10, não 0-100.
        if nota >= 9: return ("A", 4.0)
        elif nota >= 8: return ("B", 3.0)
        elif nota >= 7: return ("C", 2.0)
        elif nota >= 6: return ("D", 1.0)
        else: return ("F", 0.0)

    # Calcular IRA
    soma_pontos = 0
    soma_carga = 0
    disciplinas_aprovadas = 0

    for disc in disciplinas:
        carga = disc['carga_horaria'] if disc['carga_horaria'] else 80

        if _normalizar_status_academico(disc.get('status')) == 'aprovado' and disc['media_final'] is not None:
            nota = disc['media_final']
            conceito, valor = nota_para_conceito(nota)
            soma_pontos += valor * carga
            soma_carga += carga
            disciplinas_aprovadas += 1

    ira = soma_pontos / soma_carga if soma_carga > 0 else 0

    conn.close()

    return {
        'ira': round(ira, 2),
        'disciplinas_aprovadas': disciplinas_aprovadas,
        'carga_total_aprovada': soma_carga
    }

def obter_configuracao_ano():
    """Obtém o ano configurado para os documentos ou usa o ano atual"""
    # Você pode criar uma tabela no banco para configurações se quiser
    # Por enquanto, vamos usar um arquivo de configuração ou variável de ambiente
    ano_configurado = os.environ.get("HISTORICO_ANO", None)

    if ano_configurado:
        return ano_configurado

    # Se não tiver configuração, use o ano atual
    from datetime import datetime
    return str(datetime.now().year)

@app.route("/suporte")
def pagina_whatsapp():
    return render_template("suporte.html")


@app.route("/api/validar-codigo", methods=["POST"])
def api_validar_codigo():
    """API simples para validar código - chamada pelo formulário"""
    try:
        data = request.get_json()
        codigo = data.get('codigo', '').strip().upper()

        if not codigo:
            return jsonify({"success": False, "message": "Código não fornecido"})

        # Conectar ao banco
        conn = get_db_connection()
        cursor = conn.cursor()

        # Buscar o código na tabela documentos_autenticados
        cursor.execute("""
            SELECT id, codigo
            FROM documentos_autenticados
            WHERE codigo = %s
        """, (codigo,))

        documento = cursor.fetchone()
        conn.close()

        if documento:
            # Código encontrado!
            return jsonify({
                "success": True,
                "url": f"/validar-documento/{codigo}"  # Redireciona para a página do documento
            })
        else:
            # Código não encontrado
            return jsonify({
                "success": False,
                "message": "❌ Código não encontrado. Verifique e tente novamente."
            })

    except Exception as e:
        print(f"Erro na API de validação: {e}")
        return jsonify({
            "success": False,
            "message": "Erro ao validar. Tente novamente."
        })

@app.route('/mew/gerar-declaracao-conclusao', methods=['POST'])
def gerar_declaracao_conclusao_route():
    """
    Gera declaração de conclusão de disciplina com QR Code
    """
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})

    try:
        data = request.get_json()
        aluno_id = data.get('aluno_id')
        disciplina_id = data.get('disciplina_id')
        ano_manual = data.get('ano_historico')

        if not aluno_id or not disciplina_id:
            return jsonify({"success": False, "message": "Aluno ou disciplina não selecionados"})

        # Buscar dados do aluno
        aluno_completo = buscar_dados_pessoais_completos(aluno_id)
        if not aluno_completo:
            return jsonify({"success": False, "message": "Aluno não encontrado"})

        # Buscar dados da disciplina específica
        disciplinas = buscar_disciplinas_por_aluno_id(aluno_id)
        disciplina_selecionada = None
        for d in disciplinas:
            if d['id'] == disciplina_id:
                disciplina_selecionada = d
                break

        if not disciplina_selecionada:
            return jsonify({"success": False, "message": "Disciplina não encontrada para este aluno"})

        # Verificar se o aluno concluiu a disciplina (tem nota final)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM notas_finais
            WHERE aluno_id = %s AND disciplina_id = %s
        """, (aluno_id, disciplina_id))

        if not cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "message": "Aluno ainda não concluiu esta disciplina"})
        conn.close()

        # Gerar código único
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        codigo = f"DECL-{aluno_completo['ra']}-{disciplina_id}-{timestamp}-{secrets.token_hex(4).upper()}"

        # Gerar hash do documento
        hash_documento = gerar_hash_documento(
            f"declaracao_{aluno_id}_{disciplina_id}",
            aluno_completo['ra'],
            timestamp
        )

        # Gerar link de validação
        base_url = request.host_url.rstrip('/')
        link_validacao = gerar_link_validacao(codigo, base_url)

        # GERAR QR CODE com o link e montar a declaração institucional final.
        dados_qr = link_validacao
        qr_code_base64 = gerar_qrcode_base64(dados_qr)
        status_documental = _status_disciplina_documentos(aluno_id, disciplina_id)
        if not status_documental.get("id"):
            return jsonify({"success": False, "message": "Não foi possível carregar os dados documentais da disciplina"})
        if str(status_documental.get("status_final") or "").strip().lower() != "aprovado":
            return jsonify({"success": False, "message": "A declaração de conclusão só pode ser gerada para disciplina aprovada."})
        html_com_qr = _html_declaracao_integrada(
            aluno_completo,
            status_documental,
            codigo,
            qr_code_base64,
            hash_documento
        )

        # Criar metadados
        metadados = criar_metadados_documento(aluno_id, 'declaracao_conclusao', codigo, hash_documento)

        # Data atual
        data_emissao = datetime.now().strftime("%d/%m/%Y %H:%M")
        data_validade = (datetime.now() + timedelta(days=365*5)).strftime("%d/%m/%Y")

        # Salvar no banco
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO documentos_autenticados
            (codigo, aluno_id, aluno_nome, aluno_ra, tipo, conteudo_html, data_geracao,
             qr_code, hash_documento, data_emissao, data_validade, metadados, disciplina_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            codigo,
            aluno_id,
            aluno_completo['nome'],
            aluno_completo['ra'],
            'declaracao_conclusao',
            html_com_qr,
            data_emissao,
            qr_code_base64,
            hash_documento,
            data_emissao,
            data_validade,
            metadados,
            disciplina_id
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "codigo": codigo,
            "hash": hash_documento,
            "qr_code": qr_code_base64,
            "aluno_nome": aluno_completo['nome'],
            "aluno_ra": aluno_completo['ra'],
            "disciplina_nome": disciplina_selecionada['nome'],
            "url_validacao": link_validacao,
            "url_visualizar": f"/ver-documento/{codigo}",
            "data_emissao": data_emissao,
            "data_validade": data_validade
        })

    except Exception as e:
        import traceback
        print(f"Erro: {e}")
        print(traceback.format_exc())
        return jsonify({"success": False, "message": f"Erro: {str(e)}"})

@app.route('/mew/buscar-documentos-aluno/<int:aluno_id>')
def buscar_documentos_aluno(aluno_id):
    """Busca metadados dos documentos em uma consulta, sem N+1 e sem HTML pesado."""
    if not session.get("mew_admin"):
        return jsonify({"success":False,"message":"Não autorizado"}),403
    tipo=request.args.get('tipo','')
    conn=get_db_connection(); cursor=conn.cursor()
    query="""SELECT da.id,COALESCE(da.codigo,da.codigo_autenticacao) AS codigo,
                    COALESCE(da.tipo,da.tipo_documento) AS tipo,da.aluno_nome,da.aluno_ra,
                    da.data_emissao,da.disciplina_id,d.nome AS disciplina_nome
             FROM documentos_autenticados da
             LEFT JOIN disciplinas d ON d.id=da.disciplina_id
             WHERE da.aluno_id=%s"""
    params=[aluno_id]
    if tipo:
        query += " AND COALESCE(da.tipo,da.tipo_documento)=%s"; params.append(tipo)
    query += " ORDER BY da.id DESC LIMIT 300"
    cursor.execute(query,params); documentos=[dict(x) for x in cursor.fetchall()]; conn.close()
    return jsonify({"success":True,"documentos":documentos})


@app.route("/mew/gerenciar-documentos")
def mew_gerenciar_documentos():
    """Gerenciamento sem carregar conteúdo HTML, QR, blobs ou arquivos."""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT id,nome,ra FROM alunos ORDER BY nome"); alunos=cursor.fetchall()
    cursor.execute("SELECT COUNT(*) AS total FROM documentos_autenticados"); total_documentos=cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM documentos_enviados WHERE status='enviado'"); documentos_enviados=cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM documentos_enviados WHERE status='visualizado'"); documentos_visualizados=cursor.fetchone()["total"]
    cursor.execute("""
        SELECT da.id,COALESCE(da.codigo,da.codigo_autenticacao) AS codigo,
               COALESCE(da.tipo,da.tipo_documento) AS tipo,da.aluno_id,da.aluno_nome,da.aluno_ra,
               da.disciplina_id,da.data_emissao,da.data_geracao,d.nome AS disciplina_nome,
               de.id AS envio_id,de.status AS status_envio,de.data_envio,de.data_visualizacao,de.mensagem
        FROM documentos_autenticados da
        LEFT JOIN disciplinas d ON d.id=da.disciplina_id
        LEFT JOIN LATERAL (
            SELECT id,status,data_envio,data_visualizacao,mensagem
            FROM documentos_enviados WHERE documento_original_id=da.id ORDER BY id DESC LIMIT 1
        ) de ON TRUE
        ORDER BY da.id DESC LIMIT 500
    """)
    documentos=[]
    for row in cursor.fetchall():
        d=dict(row); d['envios']=[]
        if d.get('envio_id'):
            d['envios'].append({'id':d['envio_id'],'status':d.get('status_envio'),'data_envio':d.get('data_envio'),'data_visualizacao':d.get('data_visualizacao'),'mensagem':d.get('mensagem')})
        documentos.append(d)
    conn.close()
    categorias=[{'id':'historico','nome':'Histórico Escolar'},{'id':'declaracao_conclusao','nome':'Declaração de Conclusão'},{'id':'plano_ensino','nome':'Plano de Ensino'},{'id':'outros','nome':'Outros Documentos'}]
    return render_template("mew/gerenciar_documentos.html",alunos=alunos,documentos=documentos,categorias=categorias,total_documentos=total_documentos,documentos_enviados=documentos_enviados,documentos_visualizados=documentos_visualizados)


@app.route('/mew/gerar-historico-automatico', methods=['POST'])
def gerar_historico_automatico_route():
    """
    Gera histórico escolar automaticamente com QR Code e hash
    """
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})

    try:
        data = request.get_json()
        aluno_id = data.get('aluno_id')
        ano_manual = data.get('ano_historico')

        # Histórico é calculado pelo banco. O único valor administrativo opcional
        # que pode ser atribuído aqui é a frequência; quando informado, ele é
        # persistido antes da geração e passa a ser a fonte de verdade.
        frequencia = data.get('frequencia', 'N/I')

        if not aluno_id:
            return jsonify({"success": False, "message": "Aluno não selecionado"})

        # Buscar dados do aluno
        aluno_completo = buscar_dados_pessoais_completos(aluno_id)
        if not aluno_completo:
            return jsonify({"success": False, "message": "Aluno não encontrado"})

        # Buscar disciplinas do aluno
        disciplinas = buscar_disciplinas_por_aluno_id(aluno_id)
        if not disciplinas:
            return jsonify({"success": False, "message": "Aluno não tem disciplinas"})

        # Se a frequência foi digitada no MEW, ela deixa de ser apenas texto do documento:
        # passa a ser gravada no vínculo acadêmico e reaproveitada por todas as telas/documentos.
        freq_texto = str(frequencia or "").strip().replace("%", "").replace(",", ".")
        if freq_texto and freq_texto.upper() not in {"N/I", "NI", "NONE"}:
            freq_valor = float(freq_texto)
            if not 0 <= freq_valor <= 100:
                return jsonify({"success": False, "message": "Frequência deve estar entre 0 e 100%"})
            conn_freq = get_db_connection()
            cur_freq = conn_freq.cursor()
            try:
                for disc in disciplinas:
                    cur_freq.execute("""
                        INSERT INTO aluno_disciplina_datas (aluno_id, disciplina_id, frequencia)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (aluno_id, disciplina_id) DO UPDATE SET frequencia = EXCLUDED.frequencia
                    """, (aluno_id, disc["id"], freq_valor))
                    disc["frequencia"] = freq_valor
                conn_freq.commit()
                frequencia = f"{freq_valor:.0f}%"
            except Exception:
                conn_freq.rollback()
                raise
            finally:
                conn_freq.close()

        # Gerar código único
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        codigo = f"HIST-{aluno_completo['ra']}-{timestamp}-{secrets.token_hex(4).upper()}"

        # Gerar hash do documento
        hash_documento = gerar_hash_documento(
            f"historico_{aluno_id}_{timestamp}",
            aluno_completo['ra'],
            timestamp
        )

        # Gerar link de validação
        base_url = request.host_url.rstrip('/')
        link_validacao = f"{base_url}/validar-documento/{codigo}"

        # GERAR QR CODE
        dados_qr = link_validacao
        qr_code_base64 = gerar_qrcode_base64(dados_qr)

        html = gerar_historico_automatico(
            aluno_id, disciplinas, aluno_completo, qr_code_base64,
            codigo, hash_documento, ano_manual, frequencia_manual=frequencia
        )

        # Criar metadados
        metadados = criar_metadados_documento(aluno_id, 'historico', codigo, hash_documento)

        # Data atual
        data_emissao = datetime.now().strftime("%d/%m/%Y %H:%M")
        data_validade = (datetime.now() + timedelta(days=365*5)).strftime("%d/%m/%Y")

        # Salvar no banco
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO documentos_autenticados
            (codigo, aluno_id, aluno_nome, aluno_ra, tipo, conteudo_html, data_geracao,
             qr_code, hash_documento, data_emissao, data_validade, metadados)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            codigo,
            aluno_id,
            aluno_completo['nome'],
            aluno_completo['ra'],
            'historico',
            html,
            data_emissao,
            qr_code_base64,
            hash_documento,
            data_emissao,
            data_validade,
            metadados
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "codigo": codigo,
            "hash": hash_documento,
            "qr_code": qr_code_base64,
            "aluno_nome": aluno_completo['nome'],
            "aluno_ra": aluno_completo['ra'],
            "url_validacao": link_validacao,
            "url_visualizar": f"/ver-documento/{codigo}",
            "data_emissao": data_emissao,
            "data_validade": data_validade
        })

    except Exception as e:
        import traceback
        print(f"Erro: {e}")
        print(traceback.format_exc())
        return jsonify({"success": False, "message": f"Erro: {str(e)}"})


@app.route("/mew/enviar-documento-aluno/<int:documento_id>", methods=["POST"])
def mew_enviar_documento_aluno(documento_id):
    """Envia um documento para a área do aluno"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})

    try:
        data = request.get_json()
        mensagem_personalizada = data.get('mensagem', '')
        aluno_id = data.get('aluno_id')  # 👈 RECEBER O ALUNO_ID DO FORMULÁRIO

        if not aluno_id:
            return jsonify({"success": False, "message": "Selecione um aluno para enviar o documento"})

        conn = get_db_connection()
        cursor = conn.cursor()

        # Buscar documento original
        cursor.execute("""
            SELECT id,COALESCE(codigo,codigo_autenticacao) AS codigo,COALESCE(tipo,tipo_documento) AS tipo,disciplina_id
            FROM documentos_autenticados WHERE id = %s
        """, (documento_id,))

        documento_row = cursor.fetchone()

        if not documento_row:
            conn.close()
            return jsonify({"success": False, "message": "Documento não encontrado"})

        # Converter para dicionário
        documento = dict(documento_row)

        # Verificar se o aluno existe
        cursor.execute("SELECT id, nome, ra FROM alunos WHERE id = %s", (aluno_id,))
        aluno = cursor.fetchone()
        if not aluno:
            conn.close()
            return jsonify({"success": False, "message": "Aluno não encontrado no sistema"})

        # Buscar nome da disciplina se houver
        disciplina_nome = None
        if documento.get('disciplina_id'):
            cursor.execute("SELECT nome FROM disciplinas WHERE id = %s", (documento['disciplina_id'],))
            disc = cursor.fetchone()
            disciplina_nome = disc['nome'] if disc else None

        # Determinar título do documento baseado no tipo
        if documento['tipo'] == 'historico':
            titulo = "Histórico Escolar"
        elif documento['tipo'] == 'declaracao_conclusao':
            titulo = f"Declaração de Conclusão - {disciplina_nome}" if disciplina_nome else "Declaração de Conclusão"
        elif documento['tipo'] == 'plano_ensino':
            titulo = f"Plano de Ensino - {disciplina_nome}" if disciplina_nome else "Plano de Ensino"
        else:
            titulo = "Documento Acadêmico"

        # Gerar mensagem padrão
        mensagem_padrao = gerar_mensagem_padrao(
            documento['tipo'],
            aluno['nome'],
            disciplina_nome
        )

        # Usar mensagem personalizada se fornecida, senão usar padrão
        mensagem_final = mensagem_personalizada if mensagem_personalizada.strip() else mensagem_padrao

        # Inserir registro de envio
        data_envio = datetime.now().strftime("%d/%m/%Y %H:%M")

        cursor.execute("""
            INSERT INTO documentos_enviados
            (documento_original_id, aluno_id, codigo, tipo, titulo, disciplina_id, data_envio, mensagem, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'enviado')
            RETURNING id
        """, (
            documento_id,
            aluno_id,  # 👈 USA O ALUNO_ID DO FORMULÁRIO
            documento['codigo'],
            documento['tipo'],
            titulo,
            documento.get('disciplina_id'),
            data_envio,
            mensagem_final
        ))

        envio_id = cursor.fetchone()["id"]
        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": f"Documento enviado para {aluno['nome']} com sucesso!",
            "envio_id": envio_id,
            "data_envio": data_envio
        })

    except Exception as e:
        import traceback
        print(f"Erro ao enviar documento: {e}")
        print(traceback.format_exc())
        if 'conn' in locals():
            conn.close()
        return jsonify({"success": False, "message": f"Erro ao enviar documento: {str(e)}"})

@app.route("/meus-documentos")
def meus_documentos():
    """Página do aluno para ver documentos recebidos"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar documentos enviados para este aluno
    cursor.execute("""
        SELECT
            de.*,
            da.conteudo_html,
            d.nome as disciplina_nome
        FROM documentos_enviados de
        JOIN documentos_autenticados da ON de.documento_original_id = da.id
        LEFT JOIN disciplinas d ON de.disciplina_id = d.id
        WHERE de.aluno_id = %s
        ORDER BY de.data_envio DESC
    """, (aluno_id,))

    documentos = cursor.fetchall()

    # Contar não visualizados
    nao_visualizados = sum(1 for d in documentos if d['status'] == 'enviado')

    conn.close()

    return render_template(
        "aluno/meus_documentos.html",
        documentos=documentos,
        nao_visualizados=nao_visualizados,
        aluno_nome=session.get("aluno_nome")
    )

@app.route("/visualizar-documento/<int:envio_id>")
def visualizar_documento(envio_id):
    """Aluno visualiza um documento específico"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar documento e verificar se pertence ao aluno
    cursor.execute("""
        SELECT de.*, da.conteudo_html, da.codigo, a.nome as aluno_nome
        FROM documentos_enviados de
        JOIN documentos_autenticados da ON de.documento_original_id = da.id
        JOIN alunos a ON de.aluno_id = a.id
        WHERE de.id = %s AND de.aluno_id = %s
    """, (envio_id, aluno_id))

    documento = cursor.fetchone()

    if not documento:
        conn.close()
        return "Documento não encontrado ou acesso negado", 404

    # Atualizar status para visualizado se ainda não foi
    if documento['status'] == 'enviado':
        data_visualizacao = datetime.now().strftime("%d/%m/%Y %H:%M")
        cursor.execute("""
            UPDATE documentos_enviados
            SET status = 'visualizado', data_visualizacao = %s
            WHERE id = %s
        """, (data_visualizacao, envio_id))
        conn.commit()

    conn.close()

    # Adicionar cabeçalho informativo
    html_completo = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>{documento['titulo']}</title>
        <style>
            body {{ margin: 0; padding: 0; font-family: Arial, sans-serif; }}
            .info-header {{
                background: #3f464b;
                color: white;
                padding: 15px;
                text-align: center;
                font-size: 14px;
            }}
            .info-header .badge {{
                background: #ffd700;
                color: #3f464b;
                padding: 5px 15px;
                border-radius: 20px;
                font-weight: bold;
                margin-left: 10px;
            }}
            .document-container {{
                max-width: 210mm;
                margin: 0 auto;
                background: white;
            }}
            .back-btn {{
                position: fixed;
                bottom: 20px;
                right: 20px;
                background: #3f464b;
                color: white;
                padding: 10px 20px;
                border-radius: 5px;
                text-decoration: none;
                font-size: 14px;
                z-index: 1000;
                box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            }}
            .back-btn:hover {{
                background: #262b2f;
            }}
            @media print {{
                .info-header, .back-btn {{ display: none; }}
            }}
        </style>
    </head>
    <body>
        <div class="info-header">
            📄 Documento disponibilizado pela SiGEu Educa • Facop CTP
            <span class="badge">Código: {documento['codigo']}</span>
        </div>

        <div class="document-container">
            {documento['conteudo_html']}
        </div>

        <a href="/meus-documentos" class="back-btn">← Voltar para Meus Documentos</a>

        <script>
            // Registrar download quando imprimir/baixar PDF
            document.addEventListener('keydown', function(e) {{
                if ((e.ctrlKey || e.metaKey) && e.key === 'p') {{
                    // Usuário vai imprimir/baixar
                    fetch('/registrar-download-documento/{envio_id}', {{method: 'POST'}});
                }}
            }});
        </script>
    </body>
    </html>
    """

    return html_completo

@app.route("/registrar-download-documento/<int:envio_id>", methods=["POST"])
def registrar_download_documento(envio_id):
    """Registra quando o aluno baixa/printa o documento"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False})

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE documentos_enviados
        SET status = 'baixado'
        WHERE id = %s AND aluno_id = %s
    """, (envio_id, aluno_id))

    conn.commit()
    conn.close()

    return jsonify({"success": True})

@app.route("/mew/filtrar-documentos")
def mew_filtrar_documentos():
    if not session.get("mew_admin"):
        return jsonify({"error":"Não autorizado"}),403
    aluno_id=request.args.get('aluno_id',''); categoria=request.args.get('categoria',''); status=request.args.get('status','')
    query="""SELECT da.id,COALESCE(da.codigo,da.codigo_autenticacao) AS codigo,
                    COALESCE(da.tipo,da.tipo_documento) AS tipo,da.aluno_id,da.aluno_nome,da.aluno_ra,
                    da.disciplina_id,da.data_emissao,d.nome AS disciplina_nome,
                    de.id AS envio_id,de.status AS status_envio,de.data_envio,de.data_visualizacao
             FROM documentos_autenticados da
             LEFT JOIN disciplinas d ON d.id=da.disciplina_id
             LEFT JOIN LATERAL (SELECT id,status,data_envio,data_visualizacao FROM documentos_enviados
                 WHERE documento_original_id=da.id ORDER BY id DESC LIMIT 1) de ON TRUE
             WHERE 1=1"""
    params=[]
    if aluno_id: query += " AND da.aluno_id=%s"; params.append(aluno_id)
    if categoria and categoria!='todos': query += " AND COALESCE(da.tipo,da.tipo_documento)=%s"; params.append(categoria)
    if status=='enviados': query += " AND de.id IS NOT NULL"
    elif status=='nao_enviados': query += " AND de.id IS NULL"
    elif status=='visualizados': query += " AND de.status='visualizado'"
    query += " ORDER BY da.id DESC LIMIT 500"
    conn=get_db_connection(); cursor=conn.cursor(); cursor.execute(query,params); resultado=[dict(x) for x in cursor.fetchall()]; conn.close()
    return jsonify({"success":True,"documentos":resultado})



@app.route("/mew/excluir-documento/<int:documento_id>", methods=["GET", "POST", "DELETE"])
def mew_excluir_documento(documento_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute("SELECT arquivo_r2_key FROM documentos_autenticados WHERE id=%s",(documento_id,)); row=cursor.fetchone()
        if not row:
            conn.close()
            if request.method in ("POST", "DELETE"):
                return jsonify({"success": False, "message": "Documento não encontrado"}), 404
            return redirect("/mew/gerenciar-documentos?erro=Documento+não+encontrado")
        cursor.execute("DELETE FROM documentos_enviados WHERE documento_original_id=%s",(documento_id,))
        cursor.execute("DELETE FROM documentos_autenticados WHERE id=%s",(documento_id,)); conn.commit(); conn.close()
        if row.get('arquivo_r2_key'):
            try: delete_object(row['arquivo_r2_key'])
            except Exception as e: app.logger.warning("Falha ao excluir arquivo R2 do documento %s: %s",documento_id,e)
        if request.method in ("POST", "DELETE"):
            return jsonify({"success": True, "message": "Documento excluído com sucesso"})
        return redirect("/mew/gerenciar-documentos?sucesso=Documento+excluído+com+sucesso")
    except Exception as e:
        try: conn.rollback(); conn.close()
        except Exception: pass
        if request.method in ("POST", "DELETE"):
            return jsonify({"success": False, "message": str(e)}), 500
        return redirect(f"/mew/gerenciar-documentos?erro=Erro+ao+excluir:+{str(e)}")


@app.route("/registrar-visualizacao-documento/<int:envio_id>", methods=["POST"])
def registrar_visualizacao_documento(envio_id):
    """Registra quando o aluno visualiza o documento (marcar como lido)"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False})

    conn = get_db_connection()
    cursor = conn.cursor()

    data_visualizacao = datetime.now().strftime("%d/%m/%Y %H:%M")

    cursor.execute("""
        UPDATE documentos_enviados
        SET status = 'visualizado', data_visualizacao = %s
        WHERE id = %s AND aluno_id = %s AND status = 'enviado'
    """, (data_visualizacao, envio_id, aluno_id))

    conn.commit()
    conn.close()

    return jsonify({"success": True})

@app.route("/meus-documentos-api")
def meus_documentos_api():
    """API para retornar documentos do aluno em formato JSON"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "message": "Não autenticado"})

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar documentos enviados para este aluno
    cursor.execute("""
        SELECT
            de.id,
            de.documento_original_id,
            de.codigo,
            de.tipo,
            de.titulo,
            de.disciplina_id,
            de.data_envio,
            de.status,
            de.mensagem,
            d.nome as disciplina_nome
        FROM documentos_enviados de
        LEFT JOIN disciplinas d ON de.disciplina_id = d.id
        WHERE de.aluno_id = %s
        ORDER BY de.data_envio DESC
    """, (aluno_id,))

    documentos = cursor.fetchall()
    conn.close()

    # Converter para lista de dicionários
    resultado = []
    for doc in documentos:
        doc_dict = dict(doc)
        resultado.append(doc_dict)

    return jsonify({"success": True, "documentos": resultado})


def gerar_mensagem_padrao(tipo_documento, aluno_nome, disciplina_nome=None):
    """Gera mensagem padrão para envio de documentos"""

    if tipo_documento == 'historico':
        return f"""Olá {aluno_nome},

Seu Histórico Escolar foi gerado com sucesso! 📄

Este documento oficial contém todas as disciplinas cursadas, notas e carga horária.
Ele possui autenticação digital com QR Code e pode ser validado no site da instituição.

Para visualizar e baixar seu histórico:
1. Clique no botão "Visualizar Documento" abaixo
2. Use a opção de impressão do navegador (Ctrl+P) para salvar como PDF
3. Guarde o código de autenticação para validação futura

Qualquer dúvida, estamos à disposição.

Atenciosamente,
Secretaria Acadêmica SiGEu Educacional"""

    elif tipo_documento == 'declaracao_conclusao':
        return f"""Olá {aluno_nome},

Sua Declaração de Conclusão da disciplina {disciplina_nome} está disponível! 🎓

Este documento oficial comprova sua conclusão da disciplina com aproveitamento.
Ele possui autenticação digital com QR Code e pode ser validado no site da instituição.

Para visualizar e baixar sua declaração:
1. Clique no botão "Visualizar Documento" abaixo
2. Use a opção de impressão do navegador (Ctrl+P) para salvar como PDF
3. Guarde o código de autenticação para validação futura

Parabéns pela conquista!

Atenciosamente,
Secretaria Acadêmica SiGEu Educ • Facop CTF"""

    elif tipo_documento == 'plano_ensino':
        return f"""Olá {aluno_nome},

O Plano de Ensino da disciplina {disciplina_nome} foi disponibilizado! 📚

Este documento contém a ementa, objetivos, conteúdo programático, metodologia e critérios de avaliação.
Ele possui autenticação digital com QR Code e pode ser validado no site da instituição.

Para visualizar e baixar o plano de ensino:
1. Clique no botão "Visualizar Documento" abaixo
2. Use a opção de impressão do navegador (Ctrl+P) para salvar como PDF
3. Guarde o código de autenticação para validação futura

Bons estudos!

Atenciosamente,
Coordenação Acadêmica • SIGEU Educacional • FACOP CERTIFICADORA"""

    else:
        return f"""Olá {aluno_nome},

Um novo documento acadêmico foi disponibilizado para você! 📋

Este documento possui autenticação digital com QR Code e pode ser validado no site da instituição.

Para visualizar e baixar:
1. Clique no botão "Visualizar Documento" abaixo
2. Use a opção de impressão do navegador (Ctrl+P) para salvar como PDF
3. Guarde o código de autenticação para validação futura

Atenciosamente,
Secretaria Acadêmica • SIGEU Educacional • FACOP CERTIFICADORA"""

# ============================================
# MEW - GERAR PLANOS DE ENSINO COM IA
# ============================================

@app.route("/mew/gerar-plano-ensino")
def mew_gerar_plano_ensino():
    """Gera plano já vinculado a uma disciplina real do cadastro."""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome, COALESCE(carga_horaria,80) AS carga_horaria FROM disciplinas ORDER BY nome")
    disciplinas = cursor.fetchall()
    conn.close()
    hoje = datetime.now().strftime("%Y-%m-%d")
    selected_disciplina_id = request.args.get("disciplina_id", type=int)
    return render_template("mew/gerar_plano_ensino.html", hoje=hoje, disciplinas=disciplinas, selected_disciplina_id=selected_disciplina_id)


@app.route("/mew/processar-plano-ensino", methods=["POST"])
def mew_processar_plano_ensino():
    """Gera o plano a partir apenas da disciplina cadastrada + sugestão de ementa."""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"}), 403

    dados_recebidos = request.get_json(silent=True) or {}

    try:
        disciplina_id = int(dados_recebidos.get("disciplina_id"))
    except Exception:
        return jsonify({"success": False, "message": "Selecione uma disciplina cadastrada."}), 400

    ementa_sugerida = (dados_recebidos.get("ementa") or "").strip()
    if not ementa_sugerida:
        return jsonify({"success": False, "message": "Informe uma sugestão de ementa."}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, nome, COALESCE(carga_horaria,80) AS carga_horaria FROM disciplinas WHERE id=%s",
        (disciplina_id,)
    )
    disc = cursor.fetchone()
    if not disc:
        conn.close()
        return jsonify({"success": False, "message": "Disciplina não encontrada."}), 404

    # Docente é dado institucional: usa o cadastro real e, na ausência, o responsável institucional padrão.
    docente = _docente_documental_disciplina(cursor, disciplina_id, disc["nome"])
    conn.commit()
    conn.close()

    if not os.getenv("OPENAI_API_KEY"):
        return jsonify({"success": False, "message": "OPENAI_API_KEY não configurada no Render."}), 500

    # ÚNICOS DADOS DE CONTEÚDO: título real da disciplina + sugestão de ementa.
    # Carga vem do banco. Todo o conteúdo pedagógico variável e bibliografias vêm da IA.
    dados_ia = {
        "disciplina": disc["nome"],
        "ementa": ementa_sugerida,
        "carga_horaria": f"{int(disc['carga_horaria'] or 80)} horas"
    }

    try:
        from api_planos import consultar_openai_para_plano
        conteudo_ia = consultar_openai_para_plano(dados_ia)
        if not conteudo_ia:
            raise ValueError("A IA retornou conteúdo vazio.")

        disciplina = disc["nome"].upper()
        carga_horaria = f"{int(disc['carga_horaria'] or 80)} horas"

        # Modalidade e pré-requisitos são gerados pela IA; metodologia e avaliação continuam institucionais/fixas.
        dados_html = dict(conteudo_ia)
        modalidade = (dados_html.pop("modalidade", None) or "EaD").strip()
        numero_unidades = max(6, min(8, int(conteudo_ia.get("numero_unidades") or 6)))
        dados_html.pop("numero_unidades", None)

        data_formatada = datetime.now().strftime("%d/%m/%Y")
        codigo = gerar_codigo_simples()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        hash_documento = gerar_hash_documento(
            f"plano_ensino_{disciplina_id}_{timestamp}", "ADMIN", timestamp
        )
        base_url = request.host_url.rstrip("/")
        qr_code_base64 = gerar_qrcode_base64(f"{base_url}/validar-documento/{codigo}")
        metadados = criar_metadados_documento(None, "plano_ensino", codigo, hash_documento)
        data_emissao = datetime.now().strftime("%d/%m/%Y %H:%M")
        data_validade = (datetime.now() + timedelta(days=365 * 5)).strftime("%d/%m/%Y")

        html_completo = gerar_html_plano_ensino(
            disciplina=disciplina,
            codigo=codigo,
            hash_completa=hash_documento,
            carga_horaria=carga_horaria,
            modalidade=modalidade,
            docente=docente,
            data_formatada=data_formatada,
            qr_code_base64=qr_code_base64,
            numero_unidades=numero_unidades,
            **dados_html
        )

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM documentos_autenticados
            WHERE COALESCE(tipo,tipo_documento)='plano_ensino' AND disciplina_id=%s
            ORDER BY id DESC LIMIT 1
        """, (disciplina_id,))
        existente = cursor.fetchone()
        if existente:
            documento_id = existente["id"]
            cursor.execute("""
                UPDATE documentos_autenticados
                SET codigo=%s, codigo_autenticacao=%s, aluno_id=NULL, aluno_nome='ADMIN - MEW', aluno_ra='ADMIN',
                    tipo='plano_ensino', tipo_documento='plano_ensino', conteudo_html=%s, data_geracao=%s,
                    qr_code=%s, hash_documento=%s, data_emissao=%s, data_validade=%s, metadados=%s,
                    disciplina_id=%s
                WHERE id=%s
            """, (codigo, codigo, html_completo, data_emissao, qr_code_base64, hash_documento,
                  data_emissao, data_validade, metadados, disciplina_id, documento_id))
            # Mantém um único plano institucional corrente por disciplina.
            cursor.execute("""
                DELETE FROM documentos_autenticados
                WHERE COALESCE(tipo,tipo_documento)='plano_ensino' AND disciplina_id=%s AND id<>%s
                  AND id NOT IN (SELECT COALESCE(documento_original_id,0) FROM documentos_enviados)
            """, (disciplina_id, documento_id))
        else:
            cursor.execute("""
                INSERT INTO documentos_autenticados
                (codigo,codigo_autenticacao,aluno_id,aluno_nome,aluno_ra,tipo,tipo_documento,conteudo_html,data_geracao,
                 qr_code,hash_documento,data_emissao,data_validade,metadados,disciplina_id)
                VALUES (%s,%s,NULL,'ADMIN - MEW','ADMIN','plano_ensino','plano_ensino',%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (codigo,codigo,html_completo,data_emissao,qr_code_base64,hash_documento,
                  data_emissao,data_validade,metadados,disciplina_id))
            documento_id = cursor.fetchone()["id"]
        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "id": documento_id,
            "codigo": codigo,
            "hash": hash_documento,
            "disciplina": disciplina,
            "disciplina_id": disciplina_id,
            "docente": docente,
            "carga_horaria": carga_horaria,
            "modalidade": modalidade,
            "numero_unidades": numero_unidades,
            "bibliografia_gerada_por_ia": True,
            "url_visualizar": f"/ver-documento/{codigo}",
            "data_emissao": data_emissao,
            "data_validade": data_validade
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Erro ao gerar plano: {str(e)}"}), 500


@app.route("/mew/planos-ensino")
def mew_planos_ensino():
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("""
        WITH atuais AS (
          SELECT DISTINCT ON (da.disciplina_id)
                 da.id,COALESCE(da.codigo,da.codigo_autenticacao) AS codigo,da.data_geracao,da.data_emissao,
                 da.hash_documento,da.disciplina_id
          FROM documentos_autenticados da
          WHERE COALESCE(da.tipo,da.tipo_documento)='plano_ensino' AND da.disciplina_id IS NOT NULL
          ORDER BY da.disciplina_id,da.id DESC
        ), nao_vinculados AS (
          SELECT da.id,COALESCE(da.codigo,da.codigo_autenticacao) AS codigo,da.data_geracao,da.data_emissao,
                 da.hash_documento,da.disciplina_id
          FROM documentos_autenticados da
          WHERE COALESCE(da.tipo,da.tipo_documento)='plano_ensino' AND da.disciplina_id IS NULL
        ), lista AS (
          SELECT * FROM atuais UNION ALL SELECT * FROM nao_vinculados
        )
        SELECT lista.id,lista.codigo,lista.data_geracao,lista.data_emissao,lista.hash_documento,lista.disciplina_id,
               d.nome AS disciplina,COALESCE(e.total_envios,0) AS total_envios
        FROM lista
        LEFT JOIN disciplinas d ON d.id=lista.disciplina_id
        LEFT JOIN LATERAL (SELECT COUNT(*) AS total_envios FROM documentos_enviados WHERE documento_original_id=lista.id) e ON TRUE
        ORDER BY lista.id DESC LIMIT 500
    """); planos=cursor.fetchall()
    cursor.execute("SELECT id,nome FROM disciplinas ORDER BY nome"); disciplinas=cursor.fetchall(); conn.close()
    return render_template("mew/planos_ensino.html",planos=planos,total_planos=len(planos),disciplinas=disciplinas)




def gerar_html_plano_ensino(disciplina, codigo, hash_completa, carga_horaria,
                             modalidade, docente, data_formatada, qr_code_base64, **kwargs):
    """Gera o HTML completo do plano de ensino com QR Code"""

    from api_planos import METODOLOGIA_FIXA, SISTEMA_AVALIACAO_FIXO

    # Extrair campos do kwargs (vindos da IA)
    objetivo_geral = kwargs.get('objetivo_geral', '')
    objetivos_especificos = kwargs.get('objetivos_especificos', '')
    ementa = kwargs.get('ementa_expandida', '')
    conteudo_programatico = kwargs.get('conteudo_programatico', '')
    habilidades = kwargs.get('habilidades', '')
    enquadramento_curricular = kwargs.get('enquadramento_curricular', '')

    # Bibliografia gerada automaticamente pela IA
    bibliografia_basica = kwargs.get('bibliografia_basica', '')
    bibliografia_complementar = kwargs.get('bibliografia_complementar', '')

    # Processar bibliografia básica (converter texto simples em HTML)
    if bibliografia_basica:
        bibliografia_basica = bibliografia_basica.replace('\n', '<br>')

# Processar bibliografia complementar
    if bibliografia_complementar:
        bibliografia_complementar = bibliografia_complementar.replace('\n', '<br>')

    # Garantir que enquadramento tenha formatação adequada
    if enquadramento_curricular and '<br>' not in enquadramento_curricular:
        enquadramento_curricular = enquadramento_curricular.replace('\n', '<br>')

    # Campos opcionais
    encontros_sincronos = kwargs.get('encontros_sincronos', 'Conforme cronograma')
    plataforma = kwargs.get('plataforma', 'AVA - Ambiente Virtual de Aprendizagem')
    pre_requisitos = kwargs.get('pre_requisitos', 'Não há pré-requisitos formais.')

    # HTML do plano (mesmo template do sistema original)
    html = f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, print-scale=1">
    <title>Plano de Ensino - {disciplina} | GRUPO EDUCACIONAL UNIFICADO • SIGEU EDUCACIONAL • FACOP CERTIFICADORA</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
        @page {{ size: A4; margin: 0; }}
        /* ESTILO PROFISSIONAL INSTITUCIONAL - DOCUMENTO ACADÊMICO */
        /* PADRÃO DE CORES: AZUL MARINHO (#3f464b), CINZA, DETALHES DE SEGURANÇA */
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            background: #c9c9c9; /* Fundo cinza claro externo, igual declaração */
            font-family: "Arial Nova", "Arial", "Calibri", "Segoe UI", sans-serif; /* Fonte igual declaração */
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 40px 20px;
            margin: 0;
            position: relative;
        }}

        .page {{
            max-width: 1100px;
            width: 100%;
            background-color: #fefefe; /* Fundo branco igual declaração */
            background-image: none; /* Remove gradientes complexos, deixa fundo sólido como declaração */
            box-shadow: 0 0 20px rgba(0,0,0,0.3); /* Sombra igual declaração */
            border-radius: 0; /* Remove bordas arredondadas, igual declaração */
            padding: 15mm 20mm 25mm 20mm; /* Padding igual declaração */
            position: relative;
            border: 0.5pt solid #3f464b; /* Borda fina azul marinho, igual cantoneiras */
            border-top: 8px solid #3f464b; /* Linha superior mais grossa azul marinho */
            border-bottom: 8px solid #3f464b; /* Linha inferior mais grossa azul marinho */
            margin-bottom: 30px;
            page-break-after: always;
        }}

        .page:last-child {{
            margin-bottom: 0;
            page-break-after: auto;
        }}

        /* MARCA D'ÁGUA - IGUAL DECLARAÇÃO */
        .watermark {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 1;
            opacity: 0.03; /* Opacidade sutil como na declaração */
        }}
        .watermark-text {{
            position: absolute;
            font-size: 72pt; /* Tamanho grande como na declaração */
            font-family: "Arial Black", "Arial", sans-serif;
            color: rgba(26, 35, 126, 0.03); /* Azul marinho com baixa opacidade */
            text-transform: uppercase;
            letter-spacing: 15px;
            white-space: nowrap;
            pointer-events: none;
            z-index: 1;
            font-weight: 900;
            transform: rotate(-45deg); /* Rotação como na declaração */
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%) rotate(-45deg);
        }}

        .page-number {{
            position: absolute;
            bottom: 6mm; /* Posição igual cantoneira */
            left: 6mm;
            font-size: 8pt;
            color: #3f464b;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 2px;
            background: rgba(255,255,255,0.9);
            padding: 2mm 4mm;
            border: 0.5pt solid #3f464b;
            z-index: 20;
        }}

        .plano-content {{
            position: relative;
            z-index: 5;
        }}

        /* CABEÇALHO INSTITUCIONAL - IGUAL DECLARAÇÃO */
        .header-institution {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            border-bottom: 1.5pt solid #3f464b; /* Linha azul marinho */
            padding-bottom: 4mm;
            margin-bottom: 10mm;
        }}

        .logo-area {{
            display: flex;
            align-items: center;
            gap: 5mm;
        }}

        .logo-img {{
            width: 25mm; /* Tamanho igual declaração */
            height: auto;
            object-fit: contain;
            opacity: 0.9;
        }}

        .institution-name h1 {{
            font-family: "Arial Black", "Arial", sans-serif; /* Fonte igual declaração */
            font-size: 14pt;
            color: #3f464b; /* Azul marinho */
            text-transform: uppercase;
            letter-spacing: 1.5px;
            line-height: 1.2;
            margin-top: 8mm;
        }}

        .institution-name h2 {{
            font-family: "Arial", sans-serif;
            font-size: 8pt;
            color: #444; /* Cinza igual declaração */
            margin-top: 2mm;
            line-height: 1.3;
            border-left: none; /* Remove borda verde */
            padding-left: 0;
            background: none; /* Remove fundo verde */
        }}

        .meta-identifiers {{
            text-align: right;
            font-family: "Courier New", monospace; /* Fonte monoespaçada */
            font-size: 7pt;
            color: #3f464b;
            background: rgba(26,35,126,0.03); /* Fundo sutil azul */
            padding: 2mm 4mm;
            border: 0.5pt solid #3f464b;
            font-weight: 500;
        }}

        .meta-identifiers span {{
            display: block;
            margin-top: 2mm;
            background: #3f464b; /* Fundo azul marinho */
            color: #fefefe;
            padding: 1mm 2mm;
            border-radius: 0;
            letter-spacing: 1.1px;
            font-weight: bold;
        }}

        /* TÍTULO DO PLANO - IGUAL DECLARAÇÃO */
        .plano-title {{
            text-align: center;
            margin: 1mm 0 10mm 0;
            position: relative;
            z-index: 5;
        }}

        .plano-title h3 {{
            font-family: "Arial Black", "Arial", sans-serif;
            font-size: 18pt;
            color: #3f464b;
            text-transform: uppercase;
            letter-spacing: 4px;
            margin-bottom: 3mm;
            position: relative;
            display: inline-block;
            padding: 0 15mm;
            border-bottom: none; /* Remove borda inferior */
            text-shadow: none; /* Remove sombra */
        }}

        /* LINHAS DECORATIVAS LATERAIS DO TÍTULO - IGUAL DECLARAÇÃO */
        .plano-title h3::before,
        .plano-title h3::after {{
            content: "";
            position: absolute;
            top: 50%;
            width: 10mm;
            height: 1pt;
            background: #3f464b;
        }}

        .plano-title h3::before {{
            left: 0;
        }}

        .plano-title h3::after {{
            right: 0;
        }}

        /* TABELAS NO ESTILO CERTIFICADO - IGUAL DECLARAÇÃO */
        .info-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 8mm 0;
            border: 1pt solid #3f464b; /* Borda azul marinho */
            background: white;
            font-size: 10.5pt; /* Tamanho de fonte igual declaração */
        }}

        .info-table th {{
            background: #3f464b; /* Fundo azul marinho */
            color: white; /* Texto branco */
            font-weight: bold;
            text-align: left;
            vertical-align: top;
            width: 25%;
            padding: 4px 8px;
            border: 1pt solid #3f464b;
            font-size: 10pt;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .info-table td {{
            width: 75%;
            padding: 4px 8px;
            border: 1pt solid #3f464b;
            vertical-align: top;
            text-align: justify;
            background: white;
            color: #1a1a1a; /* Cor de texto principal */
            font-size: 10.5pt;
            line-height: 1.6;
        }}

        .info-table th[colspan="2"] {{
            background: #3f464b; /* Fundo azul marinho */
            color: white;
            text-align: center;
            font-size: 11pt;
            padding: 4px;
        }}

        /* EMENTA */
        .ementa-topicos {{
            text-align: justify;
            line-height: 1.6;
            color: #1a1a1a;
        }}

        /* CONTEÚDO PROGRAMÁTICO */
        .conteudo-programatico {{
            font-family: inherit;
            text-align: justify;
            white-space: pre-line;
            color: #1a1a1a;
        }}

        .conteudo-programatico strong {{
            font-size: 11pt;
            color: #3f464b; /* Azul marinho */
            border-bottom: 0.5pt solid #3f464b; /* Linha azul */
            padding-bottom: 1px;
            margin-bottom: 2px;
            display: inline-block;
        }}

        /* BIBLIOGRAFIA */
        .bibliografia-item {{
            margin-bottom: 2pt;
            padding-left: 0pt;
            text-indent: 0pt;
            text-align: justify;
            line-height: 1.3;
            color: #1a1a1a;
        }}

        /* FÓRMULAS - ESTILO DE DESTAQUE IGUAL DECLARAÇÃO */
        .formula {{
            font-family: 'Courier New', monospace;
            background: #f5f5f5;
            padding: 8pt 12pt;
            border-left: 4px solid #3f464b; /* Borda azul marinho */
            margin: 10pt 0;
            text-align: justify;
            border-radius: 0 6px 6px 0;
            color: #1a1a1a;
            font-weight: 500;
        }}

        /* ÁREA DE AUTENTICAÇÃO - IGUAL DECLARAÇÃO */
        .signature-area {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: flex-end;
            margin-top: 15mm;
            padding-top: 28px;
            border-top: 2px solid #3f464b; /* Linha azul marinho */
            position: relative;
        }}

        .signature-block {{
            flex: 1.2;
            padding-right: 20px;
        }}

        .digital-signature {{
            font-family: 'Courier New', monospace;
            background: #3f464b; /* Fundo azul marinho */
            padding: 16px 18px;
            border-radius: 0; /* Sem bordas arredondadas */
            color: #f1f3f4;
            box-shadow: 0 4px 10px rgba(0,0,0,0.1);
            border-left: 8px solid #262b2f; /* Tom mais escuro de azul */
            font-size: 13px;
            word-break: break-all;
        }}

        .hash-label {{
            font-size: 11px;
            text-transform: uppercase;
            color: #e0e0e0;
            letter-spacing: 2px;
            font-weight: 600;
        }}

        .hash-value {{
            font-size: 11px;
            font-weight: 500;
            margin-top: 5px;
            word-break: break-all;
            color: #ffffff;
            background: #262b2f; /* Tom mais escuro */
            padding: 8px 12px;
            border-radius: 0;
            border: 0.5px solid #7a8389;
            font-family: monospace;
            letter-spacing: 1px;
            line-height: 1.5;
        }}

        .stamp-date {{
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            justify-content: flex-end;
            flex: 0.8;
        }}

        .secretary-signature {{
            background: #f8f9fa; /* Fundo cinza claro */
            padding: 16px 24px;
            border-radius: 0;
            border-bottom: 5px solid #3f464b; /* Linha inferior azul marinho */
            text-align: right;
            width: 100%;
            box-shadow: -2px 6px 12px rgba(0,0,0,0.05);
        }}

        .secretary-name {{
            font-family: "Arial Black", "Arial", sans-serif;
            font-size: 22px;
            font-weight: 700;
            color: #3f464b; /* Azul marinho */
            font-style: italic;
            border-bottom: 1px solid #ccc;
            padding-bottom: 6px;
        }}

        .secretary-title {{
            font-size: 15px;
            color: #555;
            margin-top: 6px;
            font-weight: 600;
            text-transform: uppercase;
        }}

        .signature-line {{
            display: flex;
            align-items: center;
            justify-content: flex-end;
            margin-top: 16px;
            gap: 15px;
        }}

        .date-today {{
            font-size: 16px;
            background: #3f464b; /* Fundo azul marinho */
            color: white;
            padding: 8px 20px;
            border-radius: 0; /* Sem arredondamento */
            font-weight: 600;
            letter-spacing: 1px;
            margin-top: 14px;
            display: inline-block;
        }}

        /* QR CODE - IGUAL DECLARAÇÃO */
        .qr-code-box {{
            margin-top: 30px;
            padding: 20px;
            background: #fafafa;
            border: 0.5pt solid #ccc;
            display: flex;
            align-items: center;
            gap: 20px;
        }}

        .qr-code-image {{
            width: 120px;
            height: 120px;
            object-fit: contain;
        }}

        .qr-code-info {{
            flex: 1;
        }}

        .qr-code-info p {{
            margin: 5px 0;
            font-size: 10pt;
            color: #1a1a1a;
        }}

        .qr-code-info strong {{
            color: #3f464b; /* Azul marinho */
        }}

        /* RODAPÉ DE VALIDAÇÃO - IGUAL DECLARAÇÃO */
        .footer-validation {{
            margin-top: 35px;
            font-size: 6.5pt;
            color: #666;
            display: flex;
            justify-content: space-between;
            border-top: 0.3pt solid #ddd;
            padding-top: 3mm;
            text-transform: uppercase;
            font-weight: 400;
        }}

        /* BOTÕES */
        .botoes {{
            text-align: center;
            margin: 30pt 0 10pt;
            padding: 10pt;
            max-width: 1100px;
            width: 100%;
        }}

        .btn {{
            display: inline-block;
            padding: 12px 28px;
            margin: 0 8px;
            background: #3f464b; /* Azul marinho */
            color: white;
            text-decoration: none;
            border-radius: 0; /* Botões retos */
            font-weight: 700;
            border: none;
            cursor: pointer;
            font-size: 14px;
            letter-spacing: 1px;
            text-transform: uppercase;
            border: 1px solid #262b2f;
            transition: all 0.2s;
        }}

        .btn:hover {{
            background: #262b2f; /* Tom mais escuro */
            transform: scale(1.02);
            box-shadow: 0 8px 16px rgba(0,0,0,0.2);
        }}

        .borda-seguranca {{
    position: absolute;
    top: 8mm;
    left: 8mm;
    right: 8mm;
    bottom: 8mm;
    border: 0.5pt solid #3f464b;
    pointer-events: none;
    z-index: 2;
}}

.borda-seguranca::before {{
    content: "";
    position: absolute;
    top: 2mm;
    left: 2mm;
    right: 2mm;
    bottom: 2mm;
    border: 0.3pt dashed #3f464b;
    opacity: 0.5;
}}

/* CANTONEIRAS DE SEGURANÇA */
.cantoneira {{
    position: absolute;
    width: 15mm;
    height: 15mm;
    border: 2pt solid #3f464b;
    z-index: 100;
}}

.cantoneira.top-left {{
    top: 6mm;
    left: 6mm;
    border-right: none;
    border-bottom: none;
}}

.cantoneira.top-right {{
    top: 6mm;
    right: 6mm;
    border-left: none;
    border-bottom: none;
}}

.cantoneira.bottom-left {{
    bottom: 6mm;
    left: 6mm;
    border-right: none;
    border-top: none;
}}

.cantoneira.bottom-right {{
    bottom: 6mm;
    right: 6mm;
    border-left: none;
    border-top: none;
}}

/* MARCA D'ÁGUA PRINCIPAL - IGUAL DECLARAÇÃO */
.marca-dagua-principal {{
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%) rotate(-45deg);
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 72pt;
    color: rgba(26, 35, 126, 0.03);
    text-transform: uppercase;
    letter-spacing: 15px;
    white-space: nowrap;
    pointer-events: none;
    z-index: 1;
    font-weight: 900;
}}

/* MARCA D'ÁGUA SECUNDÁRIA - PATTERN GEOMÉTRICO */
.marca-dagua-pattern {{
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-image:
        repeating-linear-gradient(45deg, transparent, transparent 35px, rgba(26,35,126,0.015) 35px, rgba(26,35,126,0.015) 70px),
        repeating-linear-gradient(-45deg, transparent, transparent 35px, rgba(26,35,126,0.015) 35px, rgba(26,35,126,0.015) 70px);
    pointer-events: none;
    z-index: 1;
}}

/* MICROTEXTO DE SEGURANÇA NA BORDA */
.microtexto-borda {{
    position: absolute;
    font-family: "Arial", sans-serif;
    font-size: 5pt;
    color: rgba(26,35,126,0.3);
    letter-spacing: 1px;
    text-transform: uppercase;
    white-space: nowrap;
    z-index: 20;
}}

.microtexto-borda.top {{
    top: 5mm;
    left: 50%;
    transform: translateX(-50%);
}}

.microtexto-borda.bottom {{
    bottom: 5mm;
    left: 50%;
    transform: translateX(-50%);
}}

.microtexto-borda.left {{
    left: 3mm;
    top: 50%;
    transform: translateY(-50%) rotate(-90deg);
    transform-origin: center;
}}

.microtexto-borda.right {{
    right: 3mm;
    top: 50%;
    transform: translateY(-50%) rotate(90deg);
    transform-origin: center;
}}

/* FAIXA SUPERIOR IDENTIFICADORA */
.faixa-identificadora {{
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 4mm;
    background: repeating-linear-gradient(
        90deg,
        #3f464b 0px,
        #3f464b 5mm,
        #ffffff 5mm,
        #ffffff 10mm,
        #3f464b 10mm,
        #3f464b 15mm
    );
    z-index: 10;
}}

/* MICROTEXTOS DE SEGURANÇA ESPALHADOS */
.microtexto-seguranca {{
    position: absolute;
    font-family: "Arial", sans-serif;
    font-size: 5pt;
    color: rgba(0,0,0,0.15);
    z-index: 2;
    letter-spacing: 0.5px;
}}

.micro-1 {{ top: 30mm; left: 10mm; transform: rotate(90deg); }}
.micro-2 {{ top: 50mm; right: 10mm; transform: rotate(-90deg); }}
.micro-3 {{ bottom: 80mm; left: 12mm; }}
.micro-4 {{ bottom: 100mm; right: 50mm; }}



        /* PADRÃO FINAL DOS DOCUMENTOS: ACADÊMICO, BRANCO E PRETO */
        body {{
            background: #fff !important;
            color: #000 !important;
            padding: 20px 0 !important;
        }}
        .page {{
            background: #fff !important;
            background-image: none !important;
            box-shadow: none !important;
            border: 1px solid #000 !important;
            border-top: 2px solid #000 !important;
            border-bottom: 2px solid #000 !important;
        }}
        .marca-dagua-principal,
        .marca-dagua-pattern,
        .watermark,
        .watermark-text,
        .cantoneira,
        .faixa-identificadora,
        .microtexto-seguranca,
        .microtexto-borda {{ display: none !important; }}
        .borda-seguranca {{ border: 1px solid #000 !important; }}
        .borda-seguranca::before {{ display: none !important; }}
        .logo-img {{ filter: grayscale(1) contrast(1.15) !important; opacity: 1 !important; }}
        .header-institution,
        .signature-area,
        .footer-validation {{ border-color: #000 !important; }}
        .institution-name h1,
        .institution-name h2,
        .meta-identifiers,
        .plano-title h3,
        .conteudo-programatico strong,
        .secretary-name,
        .secretary-title,
        .qr-code-info strong,
        .page-number {{ color: #000 !important; }}
        .meta-identifiers,
        .meta-identifiers span,
        .info-table,
        .info-table th,
        .info-table td,
        .formula,
        .digital-signature,
        .hash-value,
        .secretary-signature,
        .date-today,
        .qr-code-box,
        .page-number {{
            background: #fff !important;
            color: #000 !important;
            border-color: #000 !important;
            box-shadow: none !important;
        }}
        .info-table th,
        .info-table th[colspan="2"] {{
            background: #fff !important;
            color: #000 !important;
            border: 1px solid #000 !important;
        }}
        .info-table td {{ border: 1px solid #000 !important; }}
        .plano-title h3::before,
        .plano-title h3::after {{ background: #000 !important; }}
        .digital-signature {{ border-left: 4px solid #000 !important; }}
        .hash-label {{ color: #000 !important; }}
        .hash-value {{ border: 1px solid #000 !important; }}
        .formula {{ border-left: 4px solid #000 !important; border-radius: 0 !important; }}
        .secretary-signature {{ border-bottom: 0 !important; text-align: center; box-shadow: none !important; }}
        .secretary-name {{ font-family: Arial, sans-serif !important; font-size: 9pt !important; font-style: normal !important; border-bottom: 0 !important; padding-bottom: 3px !important; }}
        .secretary-title {{ font-size: 10pt !important; color: #000 !important; }}
        .signature-electronic {{ font-size: 8pt; color: #000; border-top: 0 !important; padding-top: 5px; display: block; width: 100%; }}
        .signature-area {{ display:block !important; margin-top:8mm !important; padding-top:0 !important; border-top:0 !important; }}
        .digital-signature {{ padding:7px 9px !important; font-size:8pt !important; border-left:3px solid #000 !important; }}
        .hash-label {{ font-size:7pt !important; letter-spacing:1px !important; }}
        .hash-value {{ font-size:6.7pt !important; line-height:1.2 !important; padding:4px 6px !important; margin-top:4px !important; }}
        .academic-signature {{ max-width:95mm; margin:8mm auto 0; text-align:center; font-size:8pt; }}
        .academic-signature strong {{ display:block; font-size:9.5pt; }}
        .academic-signature span {{ display:block; margin-top:2px; }}
        .academic-signature small {{ display:block; margin-top:3px; font-size:6.8pt; }}
        .info-table * {{ color:#000 !important; background:#fff !important; border-color:#000 !important; box-shadow:none !important; text-shadow:none !important; }}
        .date-today {{ border: 1px solid #000 !important; padding: 6px 12px !important; }}
        .btn {{ background:#fff !important; color:#000 !important; border:1px solid #000 !important; }}

        /* IMPRESSÃO */
        @media print {{
            body {{
                background: white;
                padding: 0;
                display: block !important;
            }}
            .page {{
                box-shadow: none;
                border: 1px solid #000;
                border-top: 2px solid #000;
                border-bottom: 2px solid #000;
                background: white;
                width: 210mm !important;
                max-width: 210mm !important;
                padding: 15mm 20mm 25mm 20mm;
                margin: 0 !important;
                page-break-after: auto !important;
                break-after: auto !important;
            }}
            .page + .page {{
                page-break-before: always !important;
                break-before: page !important;
            }}
            .footer-validation {{ display: none !important; }}
            .botoes {{ display: none !important; }}
            .info-table p {{ margin: 0 0 4pt !important; line-height: 1.28 !important; }}
            .info-table ul {{ margin: 2pt 0 4pt 16pt !important; line-height: 1.28 !important; }}
            .watermark {{
                opacity: 0.03;
                print-color-adjust: exact;
            }}
            .digital-signature {{
                background: #fff !important;
                color: #000 !important;
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
            }}
            .btn {{
                display: none;
            }}
            .info-table th {{
                background: #fff !important;
                color: #000 !important;
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
            }}
            .info-table th[colspan="2"] {{
                background: #fff !important;
                color: #000 !important;
            }}
        }}
    </style>
</head>
<body>
    <!-- PÁGINA 1 - IDENTIFICAÇÃO, OBJETIVOS, EMENTA -->
    <div class="page">
    <!-- ELEMENTOS DE SEGURANÇA E BORDA -->
<div class="borda-seguranca"></div>
<div class="cantoneira top-left"></div>
<div class="cantoneira top-right"></div>
<div class="cantoneira bottom-left"></div>
<div class="cantoneira bottom-right"></div>

<!-- MICROTEXTOS DE BORDA -->
<div class="microtexto-borda top">DOCUMENTO OFICIAL - GRUPO EDUCACIONAL UNIFICADO | SiGEU Educacional | FACOP CERTIFICADORA - PLANO DE ENSINO</div>
<div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98</div>
<div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
<div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>

<!-- MARCAS D'ÁGUA -->
<div class="marca-dagua-principal">FACOP CERTIFICADORA</div>
<div class="marca-dagua-pattern"></div>

<!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
<div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
<div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
<div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO - GRUPO EDUCACIONAL UNIFICADO | SiGEU Educacional | FACOP CERTIFICADORA</div>
<div class="microtexto-seguranca micro-4">AUTENTICIDADE VERIFICÁVEL</div>

<!-- FAIXA IDENTIFICADORA -->
<div class="faixa-identificadora"></div>
        <div class="page-number">PÁGINA 1/4</div>
        <div class="plano-content">
            <!-- CABEÇALHO INSTITUCIONAL -->
            <div class="header-institution">
                <div class="logo-area">
                    <img src="/static/img/logo_declaracao.png" alt="Logo institucional" class="logo-img" onerror="this.style.display='none'">
                    <div class="institution-name">
                        <h1>GRUPO EDUCACIONAL UNIFICADO</h1>
                        <h2>SIGEU Educacional • Sistema Integrado de Gestão Educacional</h2>
                    </div>
                </div>
                <div class="meta-identifiers">
    <div style="font-size:10px; margin-top: 2px;"><b>FACOP CERTIFICADORA</b><br>Faculdade do Centro Oeste Paulista<br>CNPJ 04.344.730/0001-60 • Portaria MEC nº 887/2017</div>
    <span>PLANO-{disciplina.replace(' ', '-')} • GERAL</span>
</div>
            </div>

            <!-- TÍTULO PRINCIPAL -->
            <div class="plano-title">
                <h3>PLANO DE ENSINO</h3>
            </div>

            <!-- 1) IDENTIFICAÇÃO -->
            <table class="info-table">
                <tr><th colspan="2">1) IDENTIFICAÇÃO DA DISCIPLINA</th></tr>
                <tr><th>Disciplina</th><td><strong>{disciplina}</strong></td></tr>
                <tr><th>Carga horária</th><td>{carga_horaria}</td></tr>
                <tr><th>Modalidade</th><td>{modalidade}</td></tr>
                <tr><th>Encontros Síncronos</th><td>{encontros_sincronos}</td></tr>
                <tr><th>Plataforma</th><td>{plataforma}</td></tr>
                <tr><th>Pré-requisitos</th><td>{pre_requisitos}</td></tr>
                <tr><th>Docente</th><td>{docente}</td></tr>
                <tr><th>Data</th><td>{data_formatada}</td></tr>
            </table>

            <!-- 2) OBJETIVOS -->
            <table class="info-table">
                <tr><th colspan="2">2) OBJETIVOS</th></tr>
                <tr><th>Geral</th><td>{objetivo_geral}</td></tr>
                <tr><th>Específicos</th><td>{objetivos_especificos}</td></tr>
            </table>

            <!-- 3) EMENTA -->
            <table class="info-table">
                <tr><th colspan="2">3) EMENTA</th></tr>
                <tr><td colspan="2" class="ementa-topicos">{ementa}</td></tr>
            </table>
        </div>
    </div>

    <!-- PÁGINA 2 - CONTEÚDO PROGRAMÁTICO -->
    <div class="page">
    <!-- ELEMENTOS DE SEGURANÇA E BORDA -->
<div class="borda-seguranca"></div>
<div class="cantoneira top-left"></div>
<div class="cantoneira top-right"></div>
<div class="cantoneira bottom-left"></div>
<div class="cantoneira bottom-right"></div>

<!-- MICROTEXTOS DE BORDA -->
<div class="microtexto-borda top">DOCUMENTO OFICIAL - GRUPO EDUCACIONAL UNIFICADO | SiGEU Educacional | FACOP CERTIFICADORA - PLANO DE ENSINO</div>
<div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98</div>
<div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
<div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>

<!-- MARCAS D'ÁGUA -->
<div class="marca-dagua-principal">FACOP CERTIFICADORA</div>
<div class="marca-dagua-pattern"></div>

<!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
<div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
<div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
<div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO - GRUPO EDUCACIONAL UNIFICADO | SiGEU Educacional | FACOP CERTIFICADORA</div>
<div class="microtexto-seguranca micro-4">AUTENTICIDADE VERIFICÁVEL</div>

<!-- FAIXA IDENTIFICADORA -->
<div class="faixa-identificadora"></div>
        <div class="page-number">PÁGINA 2/4</div>
        <div class="plano-content">
            <!-- CABEÇALHO INSTITUCIONAL -->
            <div class="header-institution">
                <div class="logo-area">
                    <img src="/static/img/logo_declaracao.png" alt="Logo institucional" class="logo-img" onerror="this.style.display='none'">
                    <div class="institution-name">
                        <h1>GRUPO EDUCACIONAL UNIFICADO</h1>
                        <h2>SIGEU Educacional • Sistema Integrado de Gestão Educacional</h2>
                    </div>
                </div>
                <div class="meta-identifiers">
                    <div style="font-size:10px; margin-top: 2px;"><b>FACOP CERTIFICADORA</b><br>Faculdade do Centro Oeste Paulista<br>CNPJ 04.344.730/0001-60 • Portaria MEC nº 887/2017</div>
                    <span>{codigo}</span>
                </div>
            </div>

            <!-- TÍTULO PRINCIPAL -->
            <div class="plano-title">
                <h3>PLANO DE ENSINO</h3>
            </div>

            <!-- 4) CONTEÚDO PROGRAMÁTICO -->
            <table class="info-table">
                <tr><th colspan="2">4) CONTEÚDO PROGRAMÁTICO</th></tr>
                <tr><td colspan="2" class="conteudo-programatico">{conteudo_programatico.replace('\\n', '<br>').replace('•', '&bull;')}</td></tr>
            </table>
        </div>
    </div>

    <!-- PÁGINA 3 - METODOLOGIA, AVALIAÇÃO, BIBLIOGRAFIA, AUTENTICAÇÃO -->
    <div class="page">
    <!-- ELEMENTOS DE SEGURANÇA E BORDA -->
<div class="borda-seguranca"></div>
<div class="cantoneira top-left"></div>
<div class="cantoneira top-right"></div>
<div class="cantoneira bottom-left"></div>
<div class="cantoneira bottom-right"></div>

<!-- MICROTEXTOS DE BORDA -->
<div class="microtexto-borda top">DOCUMENTO OFICIAL - GRUPO EDUCACIONAL UNIFICADO | SiGEU Educacional | FACOP CERTIFICADORA - PLANO DE ENSINO</div>
<div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98</div>
<div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
<div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>

<!-- MARCAS D'ÁGUA -->
<div class="marca-dagua-principal">FACOP CERTIFICADORA</div>
<div class="marca-dagua-pattern"></div>

<!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
<div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
<div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
<div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO - GRUPO EDUCACIONAL UNIFICADO | SiGEU Educacional | FACOP CERTIFICADORA</div>
<div class="microtexto-seguranca micro-4">AUTENTICIDADE VERIFICÁVEL</div>

<!-- FAIXA IDENTIFICADORA -->
<div class="faixa-identificadora"></div>
        <div class="page-number">PÁGINA 3/4</div>
        <div class="plano-content">
            <!-- CABEÇALHO INSTITUCIONAL -->
            <div class="header-institution">
                <div class="logo-area">
                    <img src="/static/img/logo_declaracao.png" alt="Logo institucional" class="logo-img" onerror="this.style.display='none'">
                    <div class="institution-name">
                        <h1>GRUPO EDUCACIONAL UNIFICADO</h1>
                        <h2>SIGEU Educacional • Sistema Integrado de Gestão Educacional</h2>
                    </div>
                </div>
                <div class="meta-identifiers">
                    <div style="font-size:10px; margin-top: 2px;"><b>FACOP CERTIFICADORA</b><br>Faculdade do Centro Oeste Paulista<br>CNPJ 04.344.730/0001-60 • Portaria MEC nº 887/2017</div>
                    <span>{codigo}</span>
                </div>
            </div>

            <!-- TÍTULO PRINCIPAL -->
            <div class="plano-title">
                <h3>PLANO DE ENSINO</h3>
            </div>

            <!-- 5) METODOLOGIA -->
            <table class="info-table">
                <tr><th colspan="2">5) METODOLOGIA</th></tr>
                <tr><td colspan="2" style="text-align: justify;">{METODOLOGIA_FIXA}</td></tr>
            </table>

            <!-- 6) AVALIAÇÃO -->
            <table class="info-table">
                <tr><th colspan="2">6) CRITÉRIOS DE AVALIAÇÃO</th></tr>
                <tr><td colspan="2">{SISTEMA_AVALIACAO_FIXO}</td></tr>
            </table>

        </div>
    </div>

    <!-- PÁGINA 4 - BIBLIOGRAFIA E AUTENTICAÇÃO -->
    <div class="page">
        <div class="borda-seguranca"></div>
        <div class="page-number">PÁGINA 4/4</div>
        <div class="plano-content">
            <div class="header-institution">
                <div class="logo-area">
                    <img src="/static/img/logo_declaracao.png" alt="Logo institucional" class="logo-img" onerror="this.style.display='none'">
                    <div class="institution-name">
                        <h1>GRUPO EDUCACIONAL UNIFICADO</h1>
                        <h2>SIGEU Educacional • Sistema Integrado de Gestão Educacional</h2>
                    </div>
                </div>
                <div class="meta-identifiers">
                    <div style="font-size:10px; margin-top: 2px;"><b>FACOP CERTIFICADORA</b><br>Faculdade do Centro Oeste Paulista<br>CNPJ 04.344.730/0001-60 • Portaria MEC nº 887/2017</div>
                    <span>{codigo}</span>
                </div>
            </div>
            <div class="plano-title"><h3>PLANO DE ENSINO</h3></div>

            <!-- 7) BIBLIOGRAFIA -->
            <table class="info-table">
                <tr><th colspan="2">7) BIBLIOGRAFIA</th></tr>
                <tr><th>Básica</th><td>
                    {bibliografia_basica}
                </td></tr>
                <tr><th>Complementar</th><td>
                    {bibliografia_complementar}
                </td></tr>
            </table>

            <!-- QR CODE DE AUTENTICAÇÃO -->
            <div class="qr-code-box">
                <img src="{qr_code_base64}" class="qr-code-image" alt="QR Code">
                <div class="qr-code-info">
                    <p><strong>DOCUMENTO AUTENTICADO ELETRONICAMENTE</strong></p>
                    <p><strong>Código:</strong> {codigo}</p>
                    <p><strong>Hash:</strong> {hash_completa[:30]}...</p>
                    <p><strong>Data de Emissão:</strong> {data_formatada}</p>
                    <p><strong>Validade:</strong> 5 anos</p>
                </div>
            </div>

            <!-- REGISTRO E RESPONSÁVEL ACADÊMICO -->
            <div class="signature-area">
                <div class="academic-signature">
                    <strong>Tatiane R. L. Costa</strong>
                    <span>Documento assinado eletronicamente</span>
                    <small>Assinatura validada pela certificação institucional.</small>
                </div>
            </div>

            <!-- RODAPÉ DE VALIDAÇÃO -->
            <div class="footer-validation">
                <span>Protocolo: {codigo}</span>
                <span style="font-family: monospace;">HASH: {hash_completa[:16]}...{hash_completa[-16:]}</span>
                <span>verificação: https://campusvirtualfacop.com.br/validar-documento</span>
            </div>
        </div>
    </div>

    <div class="botoes no-print">
        <button onclick="window.print()" class="btn">IMPRIMIR PDF (4 PÁGINAS)</button>
        <a href="/mew/gerar-plano-ensino" class="btn">NOVO PLANO</a>
        <a href="/mew/planos-ensino" class="btn">LISTAR PLANOS</a>
    </div>
</body>
</html>'''

    return html

@app.route("/mew/excluir-documentos-lote", methods=["POST"])
def mew_excluir_documentos_lote():
    """Exclui múltiplos documentos e limpa os objetos R2 somente após o commit do banco."""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"}), 403

    conn = None
    try:
        data = request.get_json(silent=True) or {}
        documento_ids = []
        for value in data.get("documento_ids", []):
            try:
                documento_ids.append(int(value))
            except (TypeError, ValueError):
                continue
        documento_ids = sorted(set(documento_ids))
        if not documento_ids:
            return jsonify({"success": False, "message": "Nenhum documento selecionado"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, arquivo_r2_key FROM documentos_autenticados WHERE id = ANY(%s)", (documento_ids,))
        arquivos = cursor.fetchall()
        cursor.execute("DELETE FROM documentos_enviados WHERE documento_original_id = ANY(%s)", (documento_ids,))
        cursor.execute("DELETE FROM documentos_autenticados WHERE id = ANY(%s)", (documento_ids,))
        excluidos = cursor.rowcount
        conn.commit()
        conn.close(); conn = None

        falhas_r2 = 0
        for row in arquivos:
            key = row.get("arquivo_r2_key")
            if not key:
                continue
            try:
                delete_object(key)
            except Exception as exc:
                falhas_r2 += 1
                app.logger.warning("Falha ao remover R2 do documento %s: %s", row.get("id"), exc)

        mensagem = f"{excluidos} documento(s) excluído(s)"
        if falhas_r2:
            mensagem += f"; {falhas_r2} objeto(s) do R2 ficaram pendentes de limpeza"
        return jsonify({"success": True, "message": mensagem, "excluidos": excluidos, "falhas_r2": falhas_r2})
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback(); conn.close()
            except Exception:
                pass
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/mew/enviar-plano-aluno/<int:documento_id>", methods=["POST"])
def mew_enviar_plano_aluno(documento_id):
    """Envia um plano de ensino para um aluno específico"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})

    try:
        data = request.get_json()
        aluno_id = data.get('aluno_id')
        mensagem_personalizada = data.get('mensagem', '')

        if not aluno_id:
            return jsonify({"success": False, "message": "Selecione um aluno"})

        conn = get_db_connection()
        cursor = conn.cursor()

        # Buscar documento original (plano de ensino)
        cursor.execute("""
            SELECT id,COALESCE(codigo,codigo_autenticacao) AS codigo,disciplina_id
            FROM documentos_autenticados WHERE id = %s AND COALESCE(tipo,tipo_documento) = 'plano_ensino'
        """, (documento_id,))

        documento = cursor.fetchone()
        if not documento:
            conn.close()
            return jsonify({"success": False, "message": "Plano de ensino não encontrado"})

        # Buscar dados do aluno
        cursor.execute("SELECT id, nome, ra FROM alunos WHERE id = %s", (aluno_id,))
        aluno = cursor.fetchone()
        if not aluno:
            conn.close()
            return jsonify({"success": False, "message": "Aluno não encontrado"})

        # Buscar nome da disciplina (do documento original)
        cursor.execute("SELECT nome FROM disciplinas WHERE id = %s", (documento['disciplina_id'],))
        disciplina = cursor.fetchone()
        disciplina_nome = disciplina['nome'] if disciplina else "Disciplina"

        # Gerar mensagem padrão
        mensagem_padrao = f"""Olá {aluno['nome']},

O Plano de Ensino da disciplina **{disciplina_nome}** foi disponibilizado! 📚

Este documento contém a ementa, objetivos, conteúdo programático, metodologia e critérios de avaliação.
Ele possui autenticação digital com QR Code e pode ser validado no site da instituição.

Para visualizar e baixar o plano de ensino:
1. Clique no botão "Visualizar Documento" abaixo
2. Use a opção de impressão do navegador (Ctrl+P) para salvar como PDF
3. Guarde o código de autenticação para validação futura

Bons estudos!

Atenciosamente,
Coordenação Acadêmica SiGEU Educacional"""

        mensagem_final = mensagem_personalizada if mensagem_personalizada.strip() else mensagem_padrao

        # Inserir registro de envio
        data_envio = datetime.now().strftime("%d/%m/%Y %H:%M")

        cursor.execute("""
            INSERT INTO documentos_enviados
            (documento_original_id, aluno_id, codigo, tipo, titulo, disciplina_id, data_envio, mensagem, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'enviado')
            RETURNING id
        """, (
            documento_id,
            aluno_id,
            documento['codigo'],
            'plano_ensino',
            f"Plano de Ensino - {disciplina_nome}",
            documento['disciplina_id'],
            data_envio,
            mensagem_final
        ))

        envio_id = cursor.fetchone()["id"]
        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": f"Plano de ensino enviado para {aluno['nome']}",
            "envio_id": envio_id,
            "data_envio": data_envio
        })

    except Exception as e:
        import traceback
        print(f"Erro: {e}")
        print(traceback.format_exc())
        if 'conn' in locals():
            conn.close()
        return jsonify({"success": False, "message": f"Erro: {str(e)}"})

@app.route("/mew/testar-chave-api")
def testar_chave_api():
    """Confere somente a presença da chave; nunca revela prefixo, tamanho ou conteúdo."""
    if not session.get("mew_admin"):
        return "Não autorizado", 403
    return "API Key configurada no ambiente" if os.getenv("OPENAI_API_KEY") else "API Key NÃO configurada no ambiente"

# ============================================
# ROTAS PARA DISCIPLINAS ALTERNATIVAS (ALUNO)
# ============================================

@app.route("/disciplina-alternativa/<int:disciplina_id>")
def disciplina_alternativa(disciplina_id):
    """Página da disciplina alternativa para o aluno"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # Verificar se o aluno está matriculado
    cursor.execute("""
        SELECT * FROM aluno_disciplina_alternativa
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))

    if not cursor.fetchone():
        conn.close()
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Acesso Negado</title>
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                .error-box {
                    background: #f8d7da;
                    color: #721c24;
                    padding: 30px;
                    border-radius: 10px;
                    margin: 20px auto;
                    max-width: 600px;
                    border: 1px solid #f5c6cb;
                }
                .btn {
                    display: inline-block;
                    background: #343a40;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 5px;
                    margin-top: 20px;
                }
            </style>
        </head>
        <body>
            <div class="error-box">
                <h2>❌ Acesso Negado</h2>
                <p>Você não está matriculado nesta disciplina alternativa.</p>
                <a href="/dashboard" class="btn">🏠 Voltar ao Dashboard</a>
            </div>
        </body>
        </html>
        '''

    # Buscar dados da disciplina
    cursor.execute("SELECT * FROM disciplinas_alternativas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()

    if not disciplina:
        conn.close()
        return "Disciplina não encontrada", 404

    # Buscar anexos do aluno nesta disciplina
    cursor.execute("""
        SELECT * FROM anexos_disciplina_alternativa
        WHERE aluno_id = %s AND disciplina_id = %s
        ORDER BY data_envio DESC
    """, (aluno_id, disciplina_id))

    anexos = cursor.fetchall()

    # Buscar nota final
    cursor.execute("""
        SELECT * FROM notas_finais_alternativas
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))

    nota_final = cursor.fetchone()

    conn.close()

    return render_template(
        "disciplina_alternativa.html",
        disciplina=disciplina,
        anexos=anexos,
        nota_final=nota_final,
        aluno_nome=session.get("aluno_nome"),
        aluno_ra=session.get("aluno_ra")
    )

@app.route("/enviar-anexo", methods=["POST"])
def enviar_anexo():
    """Envia anexo da disciplina alternativa diretamente ao R2."""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    disciplina_id = request.form.get("disciplina_id")
    descricao = (request.form.get("descricao") or "").strip()
    arquivo = request.files.get("anexo")
    if not disciplina_id or not arquivo or not arquivo.filename:
        return jsonify({"success": False, "message": "Disciplina ou arquivo não informado"}), 400
    if not r2_is_configured():
        return jsonify({"success": False, "message": "Cloudflare R2 ainda não foi configurado."}), 503

    nome_original = secure_filename(arquivo.filename) or "anexo"
    extensao = nome_original.rsplit(".", 1)[1].lower() if "." in nome_original else ""
    extensoes_permitidas = {"pdf", "doc", "docx", "jpg", "jpeg", "png", "zip"}
    if extensao not in extensoes_permitidas:
        return jsonify({"success": False, "message": "Formato não permitido. Use PDF, DOC, DOCX, JPG, PNG ou ZIP."}), 400
    mime = arquivo.mimetype or guess_content_type(nome_original)
    key = make_key("disciplinas-alternativas", nome_original, disciplina_id, aluno_id)
    try:
        r2_upload_fileobj(arquivo.stream, key, mime, {"aluno_id": aluno_id, "disciplina_id": disciplina_id})
        conn = get_db_connection(); cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO anexos_disciplina_alternativa
                (aluno_id, disciplina_id, nome_arquivo, url_arquivo, descricao, data_envio, status, r2_key, content_type)
                VALUES (%s,%s,%s,NULL,%s,%s,'pendente',%s,%s)
                RETURNING id
            """, (aluno_id, disciplina_id, arquivo.filename, descricao,
                  datetime.now().strftime("%d/%m/%Y %H:%M"), key, mime))
            anexo_id = cursor.fetchone()["id"]
            conn.commit()
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
        return jsonify({"success": True, "message": "Arquivo enviado com sucesso! Aguarde a correção do professor.", "anexo_id": anexo_id})
    except Exception as e:
        try: delete_object(key)
        except Exception: pass
        return jsonify({"success": False, "message": f"Erro ao enviar arquivo: {e}"}), 500


@app.route("/excluir-anexo/<int:anexo_id>", methods=["POST"])
def excluir_anexo(anexo_id):
    """Exclui anexo pendente do aluno e remove o objeto do R2 quando aplicável."""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT r2_key, url_arquivo, status FROM anexos_disciplina_alternativa WHERE id=%s AND aluno_id=%s", (anexo_id, aluno_id))
    anexo = cursor.fetchone()
    if not anexo:
        conn.close(); return jsonify({"success": False, "message": "Anexo não encontrado"}), 404
    if anexo.get("status") != "pendente":
        conn.close(); return jsonify({"success": False, "message": "Não é possível excluir anexo já corrigido"}), 409
    cursor.execute("DELETE FROM anexos_disciplina_alternativa WHERE id=%s", (anexo_id,))
    conn.commit(); conn.close()
    if anexo.get("r2_key"):
        try: delete_object(anexo["r2_key"])
        except Exception as e: app.logger.warning("Falha ao excluir objeto R2 do anexo %s: %s", anexo_id, e)
    elif anexo.get("url_arquivo"):
        # Compatibilidade com anexos antigos salvos localmente.
        try:
            caminho = anexo["url_arquivo"].lstrip("/")
            if os.path.exists(caminho): os.remove(caminho)
        except Exception: pass
    return jsonify({"success": True, "message": "Anexo excluído com sucesso"})




# ============================================
# ROTAS PARA DISCIPLINAS ALTERNATIVAS (MEW)
# ============================================

@app.route("/mew/disciplinas-alternativas")
def mew_disciplinas_alternativas():
    """Lista todas as disciplinas alternativas"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT da.*,
               (SELECT COUNT(*) FROM aluno_disciplina_alternativa WHERE disciplina_id = da.id) as total_alunos,
               (SELECT COUNT(*) FROM anexos_disciplina_alternativa WHERE disciplina_id = da.id) as total_anexos
        FROM disciplinas_alternativas da
        ORDER BY da.data_criacao DESC
    """)

    disciplinas = cursor.fetchall()
    conn.close()

    return render_template("mew/disciplinas_alternativas.html", disciplinas=disciplinas)

@app.route("/mew/criar-disciplina-alternativa", methods=["GET", "POST"])
def mew_criar_disciplina_alternativa():
    """Cria uma nova disciplina alternativa"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    if request.method == "POST":
        nome = request.form.get("nome")
        mural = request.form.get("mural")

        if not nome:
            flash("Nome da disciplina é obrigatório", "error")
            return redirect("/mew/criar-disciplina-alternativa")

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO disciplinas_alternativas (nome, mural, data_criacao, ativa)
            VALUES (%s, %s, %s, 1)
            RETURNING id
        """, (nome, mural, datetime.now().strftime("%d/%m/%Y %H:%M")))

        disciplina_id = cursor.fetchone()["id"]
        conn.commit()
        conn.close()

        flash(f"Disciplina '{nome}' criada com sucesso!", "success")
        return redirect(f"/mew/editar-disciplina-alternativa/{disciplina_id}")

    return render_template("mew/criar_disciplina_alternativa.html")

@app.route("/mew/editar-disciplina-alternativa/<int:disciplina_id>", methods=["GET", "POST"])
def mew_editar_disciplina_alternativa(disciplina_id):
    """Edita uma disciplina alternativa"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        nome = request.form.get("nome")
        mural = request.form.get("mural")
        ativa = request.form.get("ativa", "0")

        cursor.execute("""
            UPDATE disciplinas_alternativas
            SET nome = %s, mural = %s, ativa = %s
            WHERE id = %s
        """, (nome, mural, ativa, disciplina_id))

        conn.commit()
        flash("Disciplina atualizada com sucesso!", "success")

    # GET: Buscar dados
    cursor.execute("SELECT * FROM disciplinas_alternativas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()

    # Buscar alunos matriculados
    cursor.execute("""
        SELECT a.id, a.nome, a.ra, ada.data_matricula
        FROM alunos a
        JOIN aluno_disciplina_alternativa ada ON a.id = ada.aluno_id
        WHERE ada.disciplina_id = %s
        ORDER BY a.nome
    """, (disciplina_id,))

    alunos_matriculados = cursor.fetchall()

    # Buscar todos os alunos para matricular
    cursor.execute("SELECT id, nome, ra FROM alunos ORDER BY nome")
    todos_alunos = cursor.fetchall()

    # Buscar anexos
    cursor.execute("""
        SELECT a.*, al.nome as aluno_nome, al.ra as aluno_ra
        FROM anexos_disciplina_alternativa a
        JOIN alunos al ON a.aluno_id = al.id
        WHERE a.disciplina_id = %s
        ORDER BY a.data_envio DESC
    """, (disciplina_id,))

    anexos = cursor.fetchall()

    conn.close()

    return render_template(
        "mew/editar_disciplina_alternativa.html",
        disciplina=disciplina,
        alunos_matriculados=alunos_matriculados,
        todos_alunos=todos_alunos,
        anexos=anexos
    )

@app.route("/mew/matricular-aluno-alternativa", methods=["POST"])
def mew_matricular_aluno_alternativa():
    """Matricula um aluno em uma disciplina alternativa"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})

    disciplina_id = request.form.get("disciplina_id")
    aluno_id = request.form.get("aluno_id")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO aluno_disciplina_alternativa (aluno_id, disciplina_id, data_matricula)
            VALUES (%s, %s, %s)
        """, (aluno_id, disciplina_id, datetime.now().strftime("%d/%m/%Y")))

        conn.commit()
        conn.close()

        return jsonify({"success": True, "message": "Aluno matriculado com sucesso"})
    except:
        conn.close()
        return jsonify({"success": False, "message": "Aluno já matriculado nesta disciplina"})

@app.route("/mew/remover-matricula-alternativa", methods=["POST"])
def mew_remover_matricula_alternativa():
    """Remove matrícula de um aluno em disciplina alternativa"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})

    disciplina_id = request.form.get("disciplina_id")
    aluno_id = request.form.get("aluno_id")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM aluno_disciplina_alternativa
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))

    conn.commit()
    conn.close()

    return jsonify({"success": True, "message": "Matrícula removida"})

@app.route("/mew/corrigir-anexo/<int:anexo_id>", methods=["POST"])
def mew_corrigir_anexo(anexo_id):
    """Corrige um anexo, calcula média e SALVA NA TABELA notas_finais (disciplinas normais)"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})

    data = request.get_json()
    nota = data.get("nota")
    feedback = data.get("feedback", "")
    status = data.get("status", "corrigido")

    if nota is None:
        return jsonify({"success": False, "message": "Nota não informada"})

    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. BUSCAR DADOS DO ANEXO
    cursor.execute("SELECT disciplina_id, aluno_id FROM anexos_disciplina_alternativa WHERE id = %s", (anexo_id,))
    anexo = cursor.fetchone()

    if not anexo:
        conn.close()
        return jsonify({"success": False, "message": "Anexo não encontrado"})

    disciplina_id = anexo['disciplina_id']
    aluno_id = anexo['aluno_id']

    # 2. ATUALIZAR ANEXO (a coluna feedback é garantida por migrate.py)
    cursor.execute("""
        UPDATE anexos_disciplina_alternativa
        SET nota = %s, feedback = %s, status = %s, data_correcao = %s
        WHERE id = %s
    """, (nota, feedback, status, datetime.now().strftime("%d/%m/%Y %H:%M"), anexo_id))

    # 3. CALCULAR MÉDIA DO ALUNO NESTA DISCIPLINA
    cursor.execute("""
        SELECT AVG(nota) as media
        FROM anexos_disciplina_alternativa
        WHERE aluno_id = %s AND disciplina_id = %s AND nota IS NOT NULL
    """, (aluno_id, disciplina_id))

    resultado = cursor.fetchone()
    media = resultado['media'] if resultado and resultado['media'] else 0
    nota_final = round(media, 2)

    # 4. BUSCAR O NOME DA DISCIPLINA ALTERNATIVA
    cursor.execute("SELECT nome FROM disciplinas_alternativas WHERE id = %s", (disciplina_id,))
    disciplina_alt = cursor.fetchone()
    nome_disciplina = disciplina_alt['nome'] if disciplina_alt else f"Disciplina Alternativa {disciplina_id}"

    # 5. VERIFICAR SE JÁ EXISTE UMA DISCIPLINA NORMAL COM ESTE NOME
    cursor.execute("SELECT id FROM disciplinas WHERE nome = %s", (nome_disciplina,))
    disciplina_normal = cursor.fetchone()

    if disciplina_normal:
        # Já existe - usar o ID existente
        disciplina_normal_id = disciplina_normal['id']
    else:
        # Criar nova disciplina normal
        cursor.execute("INSERT INTO disciplinas (nome) VALUES (%s) RETURNING id", (nome_disciplina,))
        disciplina_normal_id = cursor.fetchone()["id"]

        # Criar 4 capítulos vazios para esta disciplina (para fins de estrutura)
        for i in range(1, 5):
            cursor.execute("""
                INSERT INTO capitulos (disciplina_id, titulo, video_url, pdf_url)
                VALUES (%s, %s, '', '')
                RETURNING id
            """, (disciplina_normal_id, f"Capítulo {i}"))

            capitulo_id = cursor.fetchone()["id"]
            # Criar prova vazia
            cursor.execute("""
                INSERT INTO provas (capitulo_id, questoes_json)
                VALUES (%s, '[]')
            """, (capitulo_id,))

    # 6. SALVAR NA TABELA notas_finais (disciplinas normais)
    # Calcular média das provas dos capítulos (vai ser 0, já que não tem)
    cursor.execute("""
        SELECT AVG(nota) as media_capitulos
        FROM notas
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_normal_id))

    media_capitulos = cursor.fetchone()
    media_capitulos_valor = media_capitulos['media_capitulos'] if media_capitulos and media_capitulos['media_capitulos'] else 0

    # A média final é a nota da disciplina alternativa
    media_final = nota_final
    status_final = "aprovado" if media_final >= 7 else "reprovado" if media_final > 0 else "cursando"

    # Salvar/Atualizar nota final na tabela de disciplinas normais
    cursor.execute("""
        INSERT INTO notas_finais
        (aluno_id, disciplina_id, nota_final, media_disciplina, media_final, status, data_realizacao)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (aluno_id, disciplina_id) DO UPDATE SET
            nota_final = EXCLUDED.nota_final,
            media_disciplina = EXCLUDED.media_disciplina,
            media_final = EXCLUDED.media_final,
            status = EXCLUDED.status,
            data_realizacao = EXCLUDED.data_realizacao
    """, (aluno_id, disciplina_normal_id, nota_final, media_capitulos_valor, media_final, status_final,
          datetime.now().strftime("%d/%m/%Y %H:%M")))

    # 7. TAMBÉM SALVAR NA TABELA DE NOTAS FINAIS ALTERNATIVAS
    cursor.execute("""
        DELETE FROM notas_finais_alternativas
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))
    cursor.execute("""
        INSERT INTO notas_finais_alternativas
        (aluno_id, disciplina_id, nota_final, status, data_realizacao)
        VALUES (%s, %s, %s, %s, %s)
    """, (aluno_id, disciplina_id, nota_final, status_final, datetime.now().strftime("%d/%m/%Y %H:%M")))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "message": f"Correção salva! Nota: {nota_final} - {status_final.upper()}",
        "media": media,
        "nota_final": nota_final,
        "status_final": status_final,
        "disciplina_normal_id": disciplina_normal_id
    })

@app.route("/mew/excluir-disciplina-alternativa/<int:disciplina_id>")
def mew_excluir_disciplina_alternativa(disciplina_id):
    """Exclui a disciplina e seus vínculos sem deixar objetos órfãos no R2."""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT r2_key,url_arquivo FROM anexos_disciplina_alternativa WHERE disciplina_id=%s",(disciplina_id,))
    anexos=cursor.fetchall()
    cursor.execute("DELETE FROM notas_finais_alternativas WHERE disciplina_id=%s",(disciplina_id,))
    cursor.execute("DELETE FROM anexos_disciplina_alternativa WHERE disciplina_id=%s",(disciplina_id,))
    cursor.execute("DELETE FROM aluno_disciplina_alternativa WHERE disciplina_id=%s",(disciplina_id,))
    cursor.execute("DELETE FROM disciplinas_alternativas WHERE id=%s",(disciplina_id,))
    conn.commit(); conn.close()
    for anexo in anexos:
        if anexo.get("r2_key"):
            try: delete_object(anexo["r2_key"])
            except Exception as e: app.logger.warning("Objeto R2 órfão após exclusão da disciplina %s: %s",disciplina_id,e)
        elif anexo.get("url_arquivo"):
            try:
                caminho=anexo["url_arquivo"].lstrip("/")
                if os.path.exists(caminho): os.remove(caminho)
            except Exception: pass
    return redirect("/mew/disciplinas-alternativas?sucesso=Disciplina+excluída")




def _redirect_arquivo_r2_ou_legacy(r2_key=None, legacy_path=None, download_name=None, inline=True):
    """Entrega arquivo privado por URL assinada e mantém compatibilidade com uploads antigos."""
    if r2_key:
        return redirect(r2_presigned_url(r2_key, download_name=download_name, inline=inline))
    if legacy_path:
        caminho = str(legacy_path).replace("\\", "/").lstrip("/")
        if caminho.startswith("static/"):
            caminho = caminho[len("static/"):]
        return redirect(url_for("static", filename=caminho))
    return "Arquivo não encontrado.", 404


@app.route("/anexo-disciplina-alternativa/<int:anexo_id>")
def baixar_anexo_disciplina_alternativa(anexo_id):
    aluno_id = session.get("aluno_id")
    admin = bool(session.get("mew_admin"))
    if not aluno_id and not admin:
        return redirect(url_for("login"))
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT id,aluno_id,nome_arquivo,r2_key,url_arquivo FROM anexos_disciplina_alternativa WHERE id=%s",(anexo_id,))
    anexo=cursor.fetchone(); conn.close()
    if not anexo:
        return "Anexo não encontrado.",404
    if not admin and int(anexo["aluno_id"]) != int(aluno_id):
        return "Acesso não autorizado.",403
    return _redirect_arquivo_r2_ou_legacy(anexo.get("r2_key"),anexo.get("url_arquivo"),anexo.get("nome_arquivo"),inline=True)


@app.route("/projeto-final/arquivo-aluno/<int:projeto_id>")
def baixar_projeto_final_aluno(projeto_id):
    aluno_id=session.get("aluno_id"); admin=bool(session.get("mew_admin"))
    if not aluno_id and not admin:
        return redirect(url_for("login"))
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT id,aluno_id,nome_arquivo,arquivo_r2_key,arquivo_path FROM projetos_finais WHERE id=%s",(projeto_id,))
    projeto=cursor.fetchone(); conn.close()
    if not projeto: return "Projeto não encontrado.",404
    if not admin and int(projeto["aluno_id"]) != int(aluno_id): return "Acesso não autorizado.",403
    return _redirect_arquivo_r2_ou_legacy(projeto.get("arquivo_r2_key"),projeto.get("arquivo_path"),projeto.get("nome_arquivo"),inline=True)


@app.route("/projeto-final/arquivo-atividade/<int:projeto_id>")
def baixar_atividade_projeto_final(projeto_id):
    aluno_id=session.get("aluno_id"); admin=bool(session.get("mew_admin"))
    if not aluno_id and not admin:
        return redirect(url_for("login"))
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT id,aluno_id,nome_arquivo_atividade,arquivo_atividade_r2_key,arquivo_atividade_path FROM projetos_finais WHERE id=%s",(projeto_id,))
    projeto=cursor.fetchone(); conn.close()
    if not projeto: return "Projeto não encontrado.",404
    if not admin and int(projeto["aluno_id"]) != int(aluno_id): return "Acesso não autorizado.",403
    return _redirect_arquivo_r2_ou_legacy(projeto.get("arquivo_atividade_r2_key"),projeto.get("arquivo_atividade_path"),projeto.get("nome_arquivo_atividade"),inline=True)


@app.route("/documento-anexo/<codigo>")
def baixar_documento_anexo(codigo):
    # O código é o próprio token de validação do documento; nunca expõe a chave R2.
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("""SELECT arquivo_r2_key,arquivo_nome,arquivo_mime
                      FROM documentos_autenticados
                      WHERE codigo=%s OR codigo_autenticacao=%s ORDER BY id DESC LIMIT 1""",(codigo,codigo))
    doc=cursor.fetchone(); conn.close()
    if not doc or not doc.get("arquivo_r2_key"):
        return "Arquivo não encontrado ou documento antigo ainda não migrado.",404
    return redirect(r2_presigned_url(doc["arquivo_r2_key"],download_name=doc.get("arquivo_nome") or "documento",inline=True))


@app.route("/contrato-pendente", methods=["GET", "POST"])
def contrato_pendente():
    aluno_id=session.get("aluno_id")
    if not aluno_id: return redirect(url_for("login"))
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT status FROM situacao_financeira WHERE aluno_id=%s ORDER BY id DESC LIMIT 1",(aluno_id,)); financeiro=cursor.fetchone()
    if financeiro and financeiro.get("status")!="pago": conn.close(); return redirect(url_for("aguardando_pagamento"))
    cursor.execute("""SELECT c.id,c.status,c.data_envio,a.nome,a.ra,a.email,dp.cpf
                      FROM contratos_alunos c JOIN alunos a ON a.id=c.aluno_id
                      LEFT JOIN dados_pessoais dp ON dp.aluno_id=a.id
                      WHERE c.aluno_id=%s AND c.status='pendente' ORDER BY c.id DESC LIMIT 1""",(aluno_id,))
    contrato=cursor.fetchone()
    if not contrato: conn.close(); return redirect(url_for("dashboard"))
    if request.method=="POST":
        if not r2_is_configured(): conn.close(); return "Cloudflare R2 ainda não foi configurado no Render.",503
        assinatura=request.form.get("assinatura",""); foto=request.form.get("foto_assinatura","")
        if request.form.get("aceite_contrato")!="1": conn.close(); return "É obrigatório declarar a leitura e o aceite do contrato.",400
        if request.form.get("aceite_foto")!="1": conn.close(); return "É obrigatória a autorização específica para o registro fotográfico desta assinatura.",400
        try:
            assinatura_raw, assinatura_mime=decode_data_url(assinatura)
            foto_raw, foto_mime=decode_data_url(foto)
        except Exception:
            conn.close(); return "Assinatura ou fotografia inválida.",400
        if assinatura_mime not in {"image/png","image/jpeg"} or not (0<len(assinatura_raw)<=1_500_000): conn.close(); return "Assinatura eletrônica inválida ou muito grande.",400
        if foto_mime not in {"image/jpeg","image/png","image/webp"} or not (0<len(foto_raw)<=3_000_000): conn.close(); return "Fotografia de confirmação inválida ou muito grande.",400
        agora=agora_brasilia(); data_assinatura=agora.strftime("%d/%m/%Y %H:%M:%S"); ip_assinatura=obter_ip_cliente(); user_agent=(request.headers.get("User-Agent") or "")[:1000]
        aceite=TEXTO_ACEITE_CONTRATO+"\n\nAUTORIZAÇÃO DO REGISTRO FOTOGRÁFICO:\n"+TEXTO_ACEITE_FOTO
        sha_ass=hashlib.sha256(assinatura_raw).hexdigest(); sha_foto=hashlib.sha256(foto_raw).hexdigest()
        dados_hash=json.dumps({"contrato_id":contrato["id"],"aluno_id":aluno_id,"nome":contrato.get("nome") or "","ra":contrato.get("ra") or "","cpf":contrato.get("cpf") or "","data_assinatura":data_assinatura,"assinatura_sha256":sha_ass,"foto_sha256":sha_foto,"ip":ip_assinatura,"user_agent":user_agent,"aceite":aceite,"versao":VERSAO_CONTRATO},ensure_ascii=False,sort_keys=True,separators=(",",":"))
        hash_assinado=hashlib.sha256(dados_hash.encode()).hexdigest().upper()
        key_ass=make_key("contratos/assinaturas",f"assinatura{extension_for_mime(assinatura_mime)}",contrato["id"])
        key_foto=make_key("contratos/fotos",f"foto{extension_for_mime(foto_mime)}",contrato["id"])
        enviados=[]
        try:
            r2_upload_bytes(assinatura_raw,key_ass,assinatura_mime,{"contrato_id":contrato["id"],"sha256":sha_ass}); enviados.append(key_ass)
            r2_upload_bytes(foto_raw,key_foto,foto_mime,{"contrato_id":contrato["id"],"sha256":sha_foto}); enviados.append(key_foto)
            cursor.execute("""UPDATE contratos_alunos SET status='assinado', assinatura_base64=NULL,
                foto_assinatura_base64=NULL, assinatura_r2_key=%s, assinatura_mime=%s,
                foto_assinatura_r2_key=%s, foto_assinatura_mime=%s, arquivo_assinado_path=%s,
                data_assinatura=%s, ip_assinatura=%s, user_agent_assinatura=%s, aceite_contrato=TRUE,
                aceite_foto=TRUE, texto_aceite=%s, versao_contrato=%s, hash_assinado=%s
                WHERE id=%s AND status='pendente'""",
                (key_ass,assinatura_mime,key_foto,foto_mime,f"/contrato/pdf/{contrato['id']}",data_assinatura,ip_assinatura,user_agent,aceite,VERSAO_CONTRATO,hash_assinado,contrato["id"]))
            if cursor.rowcount!=1: raise RuntimeError("Este contrato já foi assinado ou não está mais disponível.")
            conn.commit()
        except Exception as e:
            conn.rollback()
            for key in enviados:
                try: delete_object(key)
                except Exception: pass
            conn.close(); return escape(str(e)),409
        conn.close()
        # Retira imediatamente as grandes strings base64 do alcance do processo.
        del assinatura_raw, foto_raw, assinatura, foto
        try: gerar_pdf_contrato_assinado(contrato["id"],salvar=True)
        except Exception as e: app.logger.exception("Falha ao gerar PDF do contrato %s: %s",contrato["id"],e)
        return redirect(url_for("visualizar_contrato_registro",contrato_id=contrato["id"]))
    conn.close()
    return render_template("assinar_contrato.html",contrato=contrato,texto_aceite_contrato=TEXTO_ACEITE_CONTRATO,texto_aceite_foto=TEXTO_ACEITE_FOTO)



@app.route("/mew/contratos", methods=["GET", "POST"])
def mew_contratos():
    if not session.get("mew_admin"):
        return "Não autorizado", 403
    if request.method == "POST":
        aluno_id = request.form.get("aluno_id", type=int)
        if aluno_id:
            criar_contrato_aluno(aluno_id)
        return redirect("/mew/contratos")

    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = 100
    offset = (page - 1) * per_page
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT a.id, a.nome, a.ra
            FROM alunos a
            WHERE NOT EXISTS (
                SELECT 1 FROM contratos_alunos c WHERE c.aluno_id = a.id
            )
            ORDER BY a.nome
        """)
        alunos_sem_contrato = cursor.fetchall()

        cursor.execute("SELECT COUNT(*) AS total FROM contratos_alunos")
        total = int((cursor.fetchone() or {}).get("total") or 0)
        total_pages = max(1, (total + per_page - 1) // per_page)
        if page > total_pages:
            page = total_pages
            offset = (page - 1) * per_page

        # Listagem leve: nunca carrega assinatura, foto, HTML ou PDF.
        cursor.execute("""
            SELECT c.id, c.status, c.data_envio, c.data_assinatura, a.nome, a.ra
            FROM contratos_alunos c
            JOIN alunos a ON a.id = c.aluno_id
            ORDER BY c.id DESC
            LIMIT %s OFFSET %s
        """, (per_page, offset))
        contratos = cursor.fetchall()
    finally:
        conn.close()

    return render_template_string("""<!DOCTYPE html>
<html lang='pt-br'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>MEW - Contratos Automáticos</title>
<style>
:root{--grafite:#353b40;--preto:#1f2428;--cinza:#e9ecef;--borda:#c9d0d5;--laranja:#c86d20}
*{box-sizing:border-box}body{font-family:Arial,sans-serif;margin:0;background:var(--cinza);color:var(--preto)}
.wrap{width:min(1180px,calc(100% - 28px));margin:22px auto}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:14px}
a{color:var(--preto)}.box{background:#fff;padding:20px;border:1px solid var(--borda);border-top:4px solid var(--grafite);margin-bottom:16px;box-shadow:0 2px 8px #0000000d}
h1,h2{margin-top:0}.muted{color:#667078;font-size:13px}select,button{padding:11px;border:1px solid #adb5ba;width:100%;font:inherit}button{background:var(--grafite);color:#fff;border:0;font-weight:700;cursor:pointer;margin-top:8px}button:hover{background:#252a2e}
.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:690px}th,td{padding:10px;border-bottom:1px solid #dfe4e7;text-align:left}th{background:#eceff1;font-size:12px;text-transform:uppercase;letter-spacing:.03em}.pendente{color:#9a6500;font-weight:700}.assinado{color:#28733d;font-weight:700}
.pager{display:flex;align-items:center;justify-content:center;gap:8px;flex-wrap:wrap;margin-top:16px}.pager a,.pager span{padding:8px 11px;border:1px solid var(--borda);background:#fff;text-decoration:none}.pager .current{background:var(--grafite);color:#fff;border-color:var(--grafite)}
@media(max-width:640px){.wrap{width:min(100% - 16px,1180px);margin:10px auto}.box{padding:14px}.top{align-items:flex-start;flex-direction:column}.top h1{font-size:20px}.pager a,.pager span{padding:8px}}
</style></head>
<body><div class='wrap'>
<div class='top'><div><h1>Contratos automáticos</h1><div class='muted'>Gestão acadêmica • documentos contratuais</div></div><a href='/mew/dashboard'>← Dashboard administrativo</a></div>
<div class='box'><h2>Cadastros antigos sem contrato</h2><p class='muted'>O contrato padrão continua sendo criado automaticamente no cadastro do aluno.</p>
{% if alunos_sem_contrato %}<form method='POST'><label for='aluno_id'><strong>Gerar contrato para:</strong></label><select id='aluno_id' name='aluno_id' required><option value=''>Selecione...</option>{% for aluno in alunos_sem_contrato %}<option value='{{ aluno.id }}'>{{ aluno.nome }} — RA {{ aluno.ra }}</option>{% endfor %}</select><button type='submit'>GERAR CONTRATO PADRÃO</button></form>{% else %}<p>Todos os alunos possuem contrato registrado.</p>{% endif %}</div>
<div class='box'><h2>Contratos</h2><p class='muted'>{{ total }} registro(s). A listagem carrega somente metadados; PDFs, foto e assinatura são buscados apenas ao abrir o contrato.</p><div class='table-wrap'><table><tr><th>Aluno</th><th>RA</th><th>Status</th><th>Envio</th><th>Documento</th></tr>{% for c in contratos %}<tr><td>{{ c.nome }}</td><td>{{ c.ra }}</td><td class='{{ c.status }}'>{{ c.status|upper }}</td><td>{{ c.data_envio or '—' }}</td><td><a href='/contrato/registro/{{ c.id }}' target='_blank' rel='noopener'>Abrir contrato</a></td></tr>{% else %}<tr><td colspan='5'>Nenhum contrato encontrado.</td></tr>{% endfor %}</table></div>
{% if total_pages > 1 %}<div class='pager'>{% if page > 1 %}<a href='?page={{ page-1 }}'>← Anterior</a>{% endif %}<span class='current'>Página {{ page }} de {{ total_pages }}</span>{% if page < total_pages %}<a href='?page={{ page+1 }}'>Próxima →</a>{% endif %}</div>{% endif %}</div>
</div></body></html>""", contratos=contratos, alunos_sem_contrato=alunos_sem_contrato,
        total=total, page=page, total_pages=total_pages)


@app.before_request
def controlar_acesso_aluno_pagamento_contrato():
    """Fluxo do aluno: pagamento aprovado -> assinatura do contrato -> acesso à plataforma."""
    if request.path.startswith("/mew") or request.path.startswith("/static"):
        return

    rotas_liberadas = {
        "login",
        "logout",
        "index",
        "aguardando_pagamento",
        "pagamento_mercadopago_sucesso",
        "pagamento_mercadopago_pendente",
        "pagamento_mercadopago_falha",
        "webhook_mercadopago",
        "contrato_pendente",
        "visualizar_contrato_registro",
        "visualizar_contrato_aluno"
    }

    if request.endpoint in rotas_liberadas:
        return

    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT status
        FROM situacao_financeira
        WHERE aluno_id = %s
        ORDER BY id DESC
        LIMIT 1
    """, (aluno_id,))
    financeiro = cursor.fetchone()

    # Se existe situação financeira, somente libera após status PAGO.
    if financeiro and financeiro.get("status") != "pago":
        conn.close()
        return redirect(url_for("aguardando_pagamento"))

    cursor.execute("""
        SELECT id
        FROM contratos_alunos
        WHERE aluno_id = %s AND status = 'pendente'
        ORDER BY id DESC
        LIMIT 1
    """, (aluno_id,))
    contrato = cursor.fetchone()
    conn.close()

    if contrato:
        return redirect(url_for("contrato_pendente"))


@app.route("/aguardando-pagamento")
def aguardando_pagamento():
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sf.status, sf.valor_total,
               pm.checkout_url, pm.sandbox_checkout_url, pm.status AS status_mp_local
        FROM situacao_financeira sf
        LEFT JOIN LATERAL (
            SELECT checkout_url, sandbox_checkout_url, status
            FROM pagamentos_mercadopago
            WHERE aluno_id = sf.aluno_id
            ORDER BY id DESC
            LIMIT 1
        ) pm ON TRUE
        WHERE sf.aluno_id = %s
        ORDER BY sf.id DESC
        LIMIT 1
    """, (aluno_id,))
    financeiro = cursor.fetchone()
    conn.close()

    if not financeiro or financeiro.get("status") == "pago":
        return redirect(url_for("dashboard"))

    checkout = financeiro.get("checkout_url") or financeiro.get("sandbox_checkout_url")

    return render_template_string("""
    <!DOCTYPE html><html lang="pt-br"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Aguardando pagamento | SIGEU Educacional</title>
    <style>
      body{font-family:Arial;background:#f3f4f6;margin:0;padding:30px;color:#111827}
      .card{max-width:650px;margin:70px auto;background:#fff;padding:35px;border-radius:14px;box-shadow:0 10px 30px #0001;text-align:center}
      .btn{display:inline-block;margin:10px;padding:13px 22px;border-radius:8px;background:#343a40;color:#fff;text-decoration:none;font-weight:700}
      .sair{background:#374151}
    </style></head><body><div class="card">
      <h1>⏳ Pagamento pendente</h1>
      <p>O acesso às disciplinas será liberado automaticamente após a confirmação do pagamento.</p>
      <p>Depois da aprovação, você realizará a assinatura do contrato com sua matrícula/RA já vinculada.</p>
      {% if checkout %}<a class="btn" href="{{ checkout }}" target="_blank">ABRIR PAGAMENTO</a>{% endif %}
      <a class="btn" href="/dashboard">VERIFICAR NOVAMENTE</a>
      <a class="btn sair" href="/logout">SAIR</a>
    </div></body></html>
    """, checkout=checkout)

@app.route("/mew/anexar-documento", methods=["GET", "POST"])
def mew_anexar_documento():
    """Anexa arquivo privado ao R2 e mantém apenas metadados/HTML leve no PostgreSQL."""
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    if request.method == "GET":
        return """
        <!DOCTYPE html><html lang="pt-br"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Anexar Documento - SIGEU</title><style>
        body{font-family:Arial,sans-serif;background:#e9ecef;color:#1f2428;margin:0;padding:32px}.box{max-width:700px;margin:auto;background:#fff;border:1px solid #c9ced2;border-top:5px solid #353b40;padding:28px;box-shadow:0 8px 22px #0001}h1{margin-top:0}label{display:block;font-weight:700;margin-top:16px}input,select,textarea{width:100%;box-sizing:border-box;padding:11px;margin-top:6px;border:1px solid #aeb5ba}.info{background:#f5f6f7;border-left:4px solid #c86d20;padding:12px;margin:16px 0}.btn{width:100%;margin-top:20px;padding:13px;border:0;background:#353b40;color:#fff;font-weight:700;cursor:pointer}.btn:hover{background:#1f2428}a{color:#1f2428}
        </style></head><body><div class="box"><h1>📎 Anexar documento</h1>
        <div class="info">O arquivo será armazenado no Cloudflare R2. O PostgreSQL guardará apenas código, hash e metadados.</div>
        <form method="POST" enctype="multipart/form-data">
        <label>Tipo *</label><select name="tipo" required><option value="">Selecione...</option><option value="plano_aula">Plano de Aula</option><option value="certificado">Certificado</option><option value="diploma">Diploma</option><option value="atestado">Atestado</option><option value="comprovante">Comprovante</option><option value="outro">Outro</option></select>
        <label>Título</label><input name="titulo" type="text" placeholder="Ex.: Documento complementar">
        <label>Descrição</label><textarea name="descricao" rows="3"></textarea>
        <label>Arquivo *</label><input name="arquivo" type="file" required>
        <button class="btn" type="submit">🔐 ANEXAR E AUTENTICAR</button></form>
        <p style="text-align:center;margin-top:20px"><a href="/mew/dashboard">← Voltar ao MEW</a></p></div></body></html>
        """

    tipo=(request.form.get("tipo") or "").strip()
    titulo=(request.form.get("titulo") or f"Documento {tipo}").strip()
    descricao=(request.form.get("descricao") or "").strip()
    arquivo=request.files.get("arquivo")
    if not tipo or not arquivo or not arquivo.filename:
        return "Tipo e arquivo são obrigatórios.",400
    if not r2_is_configured():
        return "Cloudflare R2 ainda não foi configurado no Render.",503

    original=secure_filename(arquivo.filename) or "documento"
    mime=arquivo.mimetype or guess_content_type(original)
    sha256=_hash_e_rebobinar(arquivo.stream)
    agora=datetime.now(); timestamp=agora.strftime("%Y%m%d%H%M%S")
    codigo=f"DOC-{timestamp}-{secrets.token_hex(4).upper()}"
    key=make_key("documentos-anexos",original,tipo,codigo)
    r2_upload_fileobj(arquivo.stream,key,mime,{"codigo":codigo,"sha256":sha256,"tipo":tipo})

    base_url=request.host_url.rstrip('/')
    link_validacao=f"{base_url}/validar-documento/{codigo}"
    qr_code_base64=gerar_qrcode_base64(link_validacao)
    data_emissao=agora.strftime("%d/%m/%Y %H:%M")
    data_validade=(agora+timedelta(days=365*5)).strftime("%d/%m/%Y")
    hash_documento=hashlib.sha256(f"{codigo}|{tipo}|{sha256}".encode()).hexdigest()
    titulo_html=escape(titulo); descricao_html=escape(descricao); nome_html=escape(arquivo.filename)
    html_conteudo=f"""<!doctype html><html lang='pt-br'><head><meta charset='utf-8'><style>
    body{{font-family:Arial,sans-serif;background:#d8dcdf;color:#1f2428;margin:0;padding:28px}}.folha{{max-width:820px;min-height:900px;margin:auto;background:#fff;border:1px solid #aeb5ba;padding:40px;box-shadow:0 10px 30px #0002}}h1{{border-bottom:3px solid #353b40;padding-bottom:12px}}.arquivo{{margin:28px 0;padding:22px;background:#f3f4f5;border-left:5px solid #c86d20}}.btn{{display:inline-block;background:#353b40;color:#fff;padding:12px 18px;text-decoration:none;font-weight:bold}}.auth{{margin-top:42px;border-top:1px solid #aaa;padding-top:18px;display:flex;gap:18px;align-items:center}}.auth img{{width:105px;height:105px}}.hash{{font-family:monospace;font-size:8pt;word-break:break-all}}
    </style></head><body><div class='folha'><h1>{titulo_html}</h1><p>{descricao_html}</p><div class='arquivo'><b>Arquivo:</b> {nome_html}<br><b>Tipo:</b> {escape(mime)}<br><b>Tamanho:</b> armazenado externamente no R2<br><br><a class='btn' href='/documento-anexo/{codigo}' target='_blank'>ABRIR ARQUIVO</a></div><div class='auth'><img src='{qr_code_base64}'><div><b>Código:</b> {codigo}<br><b>Emissão:</b> {data_emissao}<br><b>Validade:</b> {data_validade}<div class='hash'>SHA-256: {sha256}</div></div></div></div></body></html>"""
    metadados=json.dumps({"titulo":titulo,"descricao":descricao,"arquivo":arquivo.filename,"mime":mime,"sha256_arquivo":sha256,"storage":"r2"},ensure_ascii=False)

    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO documentos_autenticados
            (codigo,codigo_autenticacao,aluno_id,aluno_nome,aluno_ra,tipo,tipo_documento,conteudo_html,data_geracao,
             qr_code,hash_documento,data_emissao,data_validade,metadados,arquivo_r2_key,arquivo_nome,arquivo_mime)
            VALUES(%s,%s,NULL,'ADMIN - MEW','ADMIN',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,(codigo,codigo,f"anexo_{tipo}",f"anexo_{tipo}",html_conteudo,data_emissao,qr_code_base64,hash_documento,data_emissao,data_validade,metadados,key,arquivo.filename,mime))
        conn.commit()
    except Exception:
        conn.rollback()
        try: delete_object(key)
        except Exception: pass
        raise
    finally:
        conn.close()
    return f"""<!doctype html><html><body style='font-family:Arial;background:#eceff1;padding:40px'><div style='max-width:650px;margin:auto;background:#fff;padding:30px;border-top:5px solid #353b40'><h2>✅ Documento autenticado</h2><p><b>Código:</b> {codigo}</p><p>O arquivo foi enviado ao Cloudflare R2 e não ficou gravado no PostgreSQL.</p><p><a href='/ver-documento/{codigo}' target='_blank'>Visualizar documento</a></p><p><a href='/mew/anexar-documento'>Anexar outro</a> · <a href='/mew/dashboard'>Voltar ao MEW</a></p></div></body></html>"""




# ==========================================================
# PROJETO FINAL
# ==========================================================

@app.route("/projeto-final")
def projeto_final():
    aluno_id = session.get("aluno_id")

    if not aluno_id:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            pf.*,
            d.nome AS disciplina_nome
        FROM projetos_finais pf
        JOIN disciplinas d ON d.id = pf.disciplina_id
        WHERE pf.aluno_id = %s
          AND pf.liberado = 1
        ORDER BY d.nome
    """, (aluno_id,))

    projetos = cursor.fetchall()
    conn.close()

    return render_template(
        "projeto_final.html",
        projetos=projetos
    )


@app.route("/projeto-final/enviar/<int:disciplina_id>", methods=["POST"])
def enviar_projeto_final(disciplina_id):
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))
    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename:
        return redirect("/projeto-final?erro=Selecione+um+arquivo")
    extensoes_permitidas = {"pdf", "doc", "docx", "zip"}
    nome_original = arquivo.filename
    extensao = nome_original.rsplit(".", 1)[1].lower() if "." in nome_original else ""
    if extensao not in extensoes_permitidas:
        return redirect("/projeto-final?erro=Formato+não+permitido.+Use+PDF,+DOC,+DOCX+ou+ZIP")
    if not r2_is_configured():
        return redirect("/projeto-final?erro=Armazenamento+Cloudflare+R2+não+configurado")

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""SELECT id,corrigido,arquivo_r2_key FROM projetos_finais
                      WHERE aluno_id=%s AND disciplina_id=%s AND liberado=1""", (aluno_id, disciplina_id))
    projeto = cursor.fetchone()
    if not projeto:
        conn.close(); return redirect("/projeto-final?erro=Projeto+Final+não+liberado")
    if projeto.get("corrigido"):
        conn.close(); return redirect("/projeto-final?erro=Este+projeto+já+foi+corrigido")

    mime = arquivo.mimetype or guess_content_type(nome_original)
    key = make_key("projetos-finais/alunos", nome_original, aluno_id, disciplina_id)
    antigo = projeto.get("arquivo_r2_key")
    try:
        r2_upload_fileobj(arquivo.stream, key, mime, {"aluno_id": aluno_id, "disciplina_id": disciplina_id})
        cursor.execute("""
            UPDATE projetos_finais
            SET arquivo_r2_key=%s, arquivo_path=NULL, nome_arquivo=%s, data_envio=%s,
                nota=NULL, corrigido=0, data_correcao=NULL
            WHERE id=%s
        """, (key, nome_original, datetime.now().strftime("%d/%m/%Y %H:%M"), projeto["id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        try: delete_object(key)
        except Exception: pass
        conn.close(); raise
    conn.close()
    if antigo and antigo != key:
        try: delete_object(antigo)
        except Exception: pass
    return redirect("/projeto-final?sucesso=Projeto+enviado+com+sucesso")



@app.route("/mew/arquivo-final")
def arquivo_final():
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT id,nome,ra FROM alunos ORDER BY nome"); alunos=cursor.fetchall()
    cursor.execute("SELECT id,nome FROM disciplinas ORDER BY nome"); disciplinas=cursor.fetchall()
    cursor.execute("""
        SELECT pf.id,pf.aluno_id,pf.disciplina_id,pf.liberado,pf.titulo_atividade,pf.conteudo_atividade,
               pf.arquivo_atividade_path,pf.nome_arquivo_atividade,pf.arquivo_path,pf.nome_arquivo,
               pf.data_envio,pf.nota,pf.corrigido,pf.data_correcao,pf.data_liberacao,
               pf.arquivo_r2_key,pf.arquivo_atividade_r2_key,
               a.nome AS aluno_nome,a.ra AS aluno_ra,d.nome AS disciplina_nome
        FROM projetos_finais pf
        JOIN alunos a ON a.id=pf.aluno_id JOIN disciplinas d ON d.id=pf.disciplina_id
        ORDER BY CASE WHEN COALESCE(pf.arquivo_r2_key,pf.arquivo_path) IS NOT NULL AND pf.corrigido=0 THEN 0 ELSE 1 END, pf.id DESC
    """)
    projetos=cursor.fetchall(); conn.close()
    return render_template("mew/arquivo_final.html",alunos=alunos,disciplinas=disciplinas,projetos=projetos)



@app.route("/mew/liberar-projeto-final", methods=["POST"])
def liberar_projeto_final():
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    aluno_id=request.form.get("aluno_id"); disciplina_id=request.form.get("disciplina_id")
    titulo=(request.form.get("titulo_atividade") or "Projeto Final").strip()
    conteudo=(request.form.get("conteudo_atividade") or "").strip()
    arquivo=request.files.get("arquivo_atividade")
    if not aluno_id or not disciplina_id:
        return redirect("/mew/arquivo-final?erro=Selecione+aluno+e+disciplina")
    if not conteudo and (not arquivo or not arquivo.filename):
        return redirect("/mew/arquivo-final?erro=Escreva+as+orientações+ou+anexe+o+arquivo+da+atividade")
    if arquivo and arquivo.filename and not r2_is_configured():
        return redirect("/mew/arquivo-final?erro=Armazenamento+Cloudflare+R2+não+configurado")

    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT id,arquivo_atividade_r2_key,nome_arquivo_atividade FROM projetos_finais WHERE aluno_id=%s AND disciplina_id=%s",(aluno_id,disciplina_id))
    existente=cursor.fetchone(); key_antigo=existente.get("arquivo_atividade_r2_key") if existente else None
    key=key_antigo; nome=existente.get("nome_arquivo_atividade") if existente else None; novo_key=None
    if arquivo and arquivo.filename:
        ext=arquivo.filename.rsplit('.',1)[1].lower() if '.' in arquivo.filename else ''
        if ext not in {"pdf","doc","docx"}:
            conn.close(); return redirect("/mew/arquivo-final?erro=Arquivo+da+atividade+deve+ser+PDF,+DOC+ou+DOCX")
        nome=arquivo.filename; novo_key=make_key("projetos-finais/atividades",nome,aluno_id,disciplina_id); key=novo_key
        try: r2_upload_fileobj(arquivo.stream,key,arquivo.mimetype or guess_content_type(nome),{"aluno_id":aluno_id,"disciplina_id":disciplina_id})
        except Exception:
            conn.close(); raise
    agora=datetime.now().strftime("%d/%m/%Y %H:%M")
    try:
        if existente:
            cursor.execute("""UPDATE projetos_finais SET liberado=1,titulo_atividade=%s,conteudo_atividade=%s,
                arquivo_atividade_r2_key=%s,arquivo_atividade_path=CASE WHEN %s IS NOT NULL THEN NULL ELSE arquivo_atividade_path END,
                nome_arquivo_atividade=%s,data_liberacao=%s WHERE id=%s""",
                (titulo,conteudo,key,novo_key,nome,agora,existente["id"]))
        else:
            cursor.execute("""INSERT INTO projetos_finais(aluno_id,disciplina_id,liberado,titulo_atividade,conteudo_atividade,
                arquivo_atividade_r2_key,nome_arquivo_atividade,data_liberacao) VALUES(%s,%s,1,%s,%s,%s,%s,%s)""",
                (aluno_id,disciplina_id,titulo,conteudo,key,nome,agora))
        cursor.execute("UPDATE liberacao_final SET liberada=0 WHERE aluno_id=%s AND disciplina_id=%s",(aluno_id,disciplina_id))
        cursor.execute("UPDATE aluno_disciplina_datas SET prova_final_aberta=0 WHERE aluno_id=%s AND disciplina_id=%s",(aluno_id,disciplina_id))
        conn.commit()
    except Exception:
        conn.rollback()
        if novo_key:
            try: delete_object(novo_key)
            except Exception: pass
        conn.close(); raise
    conn.close()
    if novo_key and key_antigo and key_antigo != novo_key:
        try: delete_object(key_antigo)
        except Exception: pass
    return redirect("/mew/arquivo-final?sucesso=Projeto+Final+liberado+e+prova+final+normal+bloqueada")



@app.route("/mew/editar-projeto-final/<int:projeto_id>", methods=["POST"])
def editar_projeto_final(projeto_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    titulo=(request.form.get("titulo_atividade") or "Projeto Final").strip()
    conteudo=(request.form.get("conteudo_atividade") or "").strip()
    arquivo=request.files.get("arquivo_atividade")
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT id,aluno_id,disciplina_id,arquivo_atividade_r2_key,nome_arquivo_atividade FROM projetos_finais WHERE id=%s",(projeto_id,))
    projeto=cursor.fetchone()
    if not projeto:
        conn.close(); return redirect("/mew/arquivo-final?erro=Projeto+não+encontrado")
    key=projeto.get("arquivo_atividade_r2_key"); nome=projeto.get("nome_arquivo_atividade"); novo=None
    if arquivo and arquivo.filename:
        if not r2_is_configured():
            conn.close(); return redirect("/mew/arquivo-final?erro=Armazenamento+Cloudflare+R2+não+configurado")
        ext=arquivo.filename.rsplit('.',1)[1].lower() if '.' in arquivo.filename else ''
        if ext not in {"pdf","doc","docx"}:
            conn.close(); return redirect("/mew/arquivo-final?erro=Arquivo+da+atividade+deve+ser+PDF,+DOC+ou+DOCX")
        nome=arquivo.filename; novo=make_key("projetos-finais/atividades",nome,projeto["aluno_id"],projeto["disciplina_id"])
        r2_upload_fileobj(arquivo.stream,novo,arquivo.mimetype or guess_content_type(nome),{"projeto_id":projeto_id})
        key=novo
    try:
        cursor.execute("""UPDATE projetos_finais SET titulo_atividade=%s,conteudo_atividade=%s,
            arquivo_atividade_r2_key=%s, arquivo_atividade_path=CASE WHEN %s IS NOT NULL THEN NULL ELSE arquivo_atividade_path END,
            nome_arquivo_atividade=%s WHERE id=%s""",(titulo,conteudo,key,novo,nome,projeto_id))
        conn.commit()
    except Exception:
        conn.rollback()
        if novo:
            try: delete_object(novo)
            except Exception: pass
        conn.close(); raise
    conn.close()
    if novo and projeto.get("arquivo_atividade_r2_key") and projeto["arquivo_atividade_r2_key"] != novo:
        try: delete_object(projeto["arquivo_atividade_r2_key"])
        except Exception: pass
    return redirect("/mew/arquivo-final?sucesso=Conteúdo+da+atividade+atualizado")



@app.route("/mew/remover-projeto-final/<int:projeto_id>")
def remover_projeto_final(projeto_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE projetos_finais
        SET liberado = 0
        WHERE id = %s
    """, (projeto_id,))

    conn.commit()
    conn.close()

    return redirect(
        "/mew/arquivo-final?sucesso=Liberação+removida"
    )


@app.route(
    "/mew/corrigir-projeto-final/<int:projeto_id>",
    methods=["POST"]
)
def corrigir_projeto_final(projeto_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    try:
        nota = float(request.form.get("nota", "").replace(",", "."))
    except (ValueError, AttributeError):
        return redirect(
            "/mew/arquivo-final?erro=Nota+inválida"
        )

    if nota < 0 or nota > 10:
        return redirect(
            "/mew/arquivo-final?erro=A+nota+deve+estar+entre+0+e+10"
        )

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, aluno_id, disciplina_id, arquivo_r2_key, arquivo_path
        FROM projetos_finais
        WHERE id = %s
    """, (projeto_id,))

    projeto = cursor.fetchone()

    if not projeto:
        conn.close()
        return redirect(
            "/mew/arquivo-final?erro=Projeto+não+encontrado"
        )

    if not (projeto.get("arquivo_r2_key") or projeto.get("arquivo_path")):
        conn.close()
        return redirect(
            "/mew/arquivo-final?erro=O+aluno+ainda+não+enviou+o+arquivo"
        )

    aluno_id = projeto["aluno_id"]
    disciplina_id = projeto["disciplina_id"]

    media_disciplina = round(_media_notas_logicas(cursor, aluno_id, disciplina_id), 2)

    nota_final = round(nota, 2)
    media_final = round((nota_final + media_disciplina) / 2, 2)
    status = "aprovado" if media_final >= 7.0 else "reprovado"
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")

    cursor.execute("""
        UPDATE projetos_finais
        SET nota = %s,
            corrigido = 1,
            data_correcao = %s
        WHERE id = %s
    """, (
        nota_final,
        agora,
        projeto_id
    ))

    cursor.execute("""
        SELECT id
        FROM notas_finais
        WHERE aluno_id = %s
          AND disciplina_id = %s
    """, (
        aluno_id,
        disciplina_id
    ))

    nota_existente = cursor.fetchone()

    if nota_existente:
        cursor.execute("""
            UPDATE notas_finais
            SET nota_final = %s,
                media_disciplina = %s,
                media_final = %s,
                status = %s,
                data_realizacao = %s
            WHERE aluno_id = %s
              AND disciplina_id = %s
        """, (
            nota_final,
            media_disciplina,
            media_final,
            status,
            agora,
            aluno_id,
            disciplina_id
        ))
    else:
        cursor.execute("""
            INSERT INTO notas_finais
            (
                aluno_id,
                disciplina_id,
                nota_final,
                media_disciplina,
                media_final,
                status,
                data_realizacao
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            aluno_id,
            disciplina_id,
            nota_final,
            media_disciplina,
            media_final,
            status,
            agora
        ))

    conn.commit()
    conn.close()

    return redirect(
        "/mew/arquivo-final?sucesso=Nota+lançada.+Projeto+Final+substituiu+a+Prova+Final"
    )

# ============================================================
# SIGEU - DOCUMENTOS ACADÊMICOS INTEGRADOS + TITAN SMTP
# ============================================================

def init_documentos_integrados_db():
    """Cria/atualiza as estruturas do novo fluxo sem exigir SQL manual."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Colunas acadêmicas usadas automaticamente pelos documentos.
    cursor.execute("ALTER TABLE disciplinas ADD COLUMN IF NOT EXISTS carga_horaria INTEGER DEFAULT 80")
    cursor.execute("ALTER TABLE disciplinas ADD COLUMN IF NOT EXISTS docente_documental TEXT")

    # Estrutura de docentes, caso a instalação antiga ainda não a tenha criado.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS docentes (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            titulacao TEXT,
            email TEXT,
            telefone TEXT,
            ativo INTEGER DEFAULT 1
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS disciplina_docente (
            id SERIAL PRIMARY KEY,
            disciplina_id INTEGER NOT NULL,
            docente_id INTEGER NOT NULL,
            ano_semestre TEXT,
            FOREIGN KEY (disciplina_id) REFERENCES disciplinas(id),
            FOREIGN KEY (docente_id) REFERENCES docentes(id)
        )
    """)

    # Garante uma estrutura compatível com os documentos já existentes.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documentos_autenticados (
            id SERIAL PRIMARY KEY,
            codigo TEXT UNIQUE,
            aluno_id INTEGER,
            aluno_nome TEXT,
            aluno_ra TEXT,
            tipo TEXT,
            conteudo_html TEXT,
            data_geracao TEXT,
            qr_code TEXT,
            hash_documento TEXT,
            data_emissao TEXT,
            data_validade TEXT,
            metadados TEXT,
            disciplina_id INTEGER
        )
    """)
    for sql in [
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS codigo TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS codigo_autenticacao TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS aluno_id INTEGER",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS aluno_nome TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS aluno_ra TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS tipo TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS tipo_documento TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS observacoes TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS conteudo_html TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS data_geracao TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS qr_code TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS hash_documento TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS data_emissao TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS data_validade TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS metadados TEXT",
        "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS disciplina_id INTEGER",
    ]:
        cursor.execute(sql)

    # Mantém compatibilidade com a área manual "Meus Documentos" já existente.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documentos_enviados (
            id SERIAL PRIMARY KEY,
            documento_original_id INTEGER,
            aluno_id INTEGER NOT NULL,
            codigo TEXT,
            tipo TEXT,
            titulo TEXT,
            disciplina_id INTEGER,
            data_envio TEXT,
            mensagem TEXT,
            status TEXT DEFAULT 'enviado',
            data_visualizacao TEXT,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id)
        )
    """)

    cursor.execute("ALTER TABLE docentes ADD COLUMN IF NOT EXISTS titulacao TEXT")
    cursor.execute("ALTER TABLE docentes ADD COLUMN IF NOT EXISTS email TEXT")
    cursor.execute("ALTER TABLE docentes ADD COLUMN IF NOT EXISTS telefone TEXT")
    cursor.execute("ALTER TABLE docentes ADD COLUMN IF NOT EXISTS ativo INTEGER DEFAULT 1")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS solicitacoes_documentos_integrados (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER NOT NULL,
            tipo_solicitacao TEXT NOT NULL,
            tipos_documentos TEXT NOT NULL,
            disciplinas_ids TEXT NOT NULL,
            detalhes TEXT,
            data_solicitacao TEXT,
            status TEXT DEFAULT 'pendente',
            mensagem_status TEXT,
            codigo_pacote TEXT,
            pdf_previa BYTEA,
            pdf_final BYTEA,
            nome_arquivo TEXT,
            hash_pdf TEXT,
            componentes_json TEXT,
            data_preparacao TEXT,
            data_aprovacao TEXT,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_solic_doc_int_aluno ON solicitacoes_documentos_integrados(aluno_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_solic_doc_int_status ON solicitacoes_documentos_integrados(status)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS emails_transacionais (
            id SERIAL PRIMARY KEY,
            aluno_id INTEGER,
            tipo TEXT,
            referencia TEXT UNIQUE,
            destinatario TEXT,
            data_envio TEXT,
            status TEXT,
            erro TEXT
        )
    """)

    conn.commit()
    conn.close()


def _parse_data_sigeu(valor):
    if not valor:
        return None
    texto = str(valor).strip().split(" ")[0]
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato)
        except Exception:
            pass
    return None


def _docente_documental_disciplina(cursor, disciplina_id, disciplina_nome):
    """Retorna o docente real da disciplina; se faltar vínculo, sorteia um docente ativo e grava o vínculo."""
    cursor.execute("""
        SELECT doc.id, doc.nome, doc.titulacao
        FROM disciplina_docente dd
        JOIN docentes doc ON doc.id = dd.docente_id
        WHERE dd.disciplina_id = %s AND COALESCE(doc.ativo, 1) = 1
        ORDER BY dd.id DESC
        LIMIT 1
    """, (disciplina_id,))
    row = cursor.fetchone()
    if row and row.get("nome"):
        nome = row["nome"]
        if row.get("titulacao"):
            nome += f" ({row['titulacao']})"
        cursor.execute("UPDATE disciplinas SET docente_documental=%s WHERE id=%s", (nome, disciplina_id))
        return nome

    # Se não houver vínculo ativo, sorteia somente entre os docentes reais/ativos cadastrados no MEW.
    cursor.execute("""
        SELECT id, nome, titulacao
        FROM docentes
        WHERE COALESCE(ativo, 1) = 1 AND NULLIF(TRIM(nome), '') IS NOT NULL
        ORDER BY RANDOM()
        LIMIT 1
    """)
    sorteado = cursor.fetchone()
    if sorteado and sorteado.get("nome"):
        docente_id = sorteado["id"]
        nome = sorteado["nome"]
        if sorteado.get("titulacao"):
            nome += f" ({sorteado['titulacao']})"
        cursor.execute(
            "INSERT INTO disciplina_docente(disciplina_id,docente_id,ano_semestre) VALUES(%s,%s,%s)",
            (disciplina_id, docente_id, datetime.now().strftime("%Y.%m"))
        )
        cursor.execute("UPDATE disciplinas SET docente_documental=%s WHERE id=%s", (nome, disciplina_id))
        return nome

    # Só chega aqui se o cadastro de docentes estiver realmente vazio.
    nome = "Docente não cadastrado"
    cursor.execute("UPDATE disciplinas SET docente_documental=%s WHERE id=%s", (nome, disciplina_id))
    return nome

def _status_disciplina_documentos(aluno_id, disciplina_id, cursor=None):
    """Verifica 20 dias + capítulos + avaliações + final/projeto + aprovação."""
    fechar = cursor is None
    if fechar:
        conn = get_db_connection()
        cursor = conn.cursor()
    else:
        conn = None

    cursor.execute("""
        SELECT d.id, d.nome, COALESCE(d.carga_horaria, 80) AS carga_horaria,
               addd.data_inicio, addd.data_fim_previsto, addd.frequencia, addd.progresso_manual,
               nf.nota_final, nf.media_disciplina, nf.media_final, nf.status AS status_final,
               nf.data_realizacao
        FROM disciplinas d
        JOIN aluno_disciplina ad ON ad.disciplina_id = d.id AND ad.aluno_id = %s
        LEFT JOIN aluno_disciplina_datas addd
          ON addd.aluno_id = ad.aluno_id AND addd.disciplina_id = d.id
        LEFT JOIN notas_finais nf
          ON nf.aluno_id = ad.aluno_id AND nf.disciplina_id = d.id
        WHERE d.id = %s
    """, (aluno_id, disciplina_id))
    d = cursor.fetchone()
    if not d:
        if fechar:
            conn.close()
        return {"elegivel": False, "motivo": "Disciplina não pertence à matrícula do aluno."}

    cursor.execute("SELECT COUNT(*) AS total FROM capitulos WHERE disciplina_id = %s", (disciplina_id,))
    total_capitulos = int((cursor.fetchone() or {}).get("total") or 0)

    unidades_documentais = _notas_logicas_disciplina(cursor, aluno_id, disciplina_id)
    capitulos_avaliados = sum(1 for u in unidades_documentais if u["nota"] is not None)

    cursor.execute("""
        SELECT corrigido, nota, arquivo_r2_key, arquivo_path, data_envio
        FROM projetos_finais
        WHERE aluno_id = %s AND disciplina_id = %s
        LIMIT 1
    """, (aluno_id, disciplina_id))
    projeto = cursor.fetchone()

    data_inicio = _parse_data_sigeu(d.get("data_inicio"))
    dias_cursados = (datetime.now().date() - data_inicio.date()).days if data_inicio else 0
    faltam_dias = max(0, 20 - dias_cursados)

    motivos = []
    progresso_manual = d.get("progresso_manual")
    conclusao_administrativa = progresso_manual is not None and int(progresso_manual or 0) >= 100
    if not conclusao_administrativa:
        if not data_inicio:
            motivos.append("data de início da disciplina não cadastrada")
        elif faltam_dias > 0:
            motivos.append(f"faltam {faltam_dias} dia(s) para completar o prazo mínimo de 20 dias")

        if total_capitulos <= 0:
            motivos.append("disciplina sem capítulos cadastrados")
        elif capitulos_avaliados < total_capitulos:
            motivos.append(f"faltam {total_capitulos - capitulos_avaliados} avaliação(ões) de capítulo")

    final_concluido = False
    final_tipo = "Prova Final"
    if projeto:
        final_tipo = "Projeto Final"
        arquivo_enviado = bool(projeto.get("arquivo_r2_key") or projeto.get("arquivo_path"))
        final_concluido = arquivo_enviado and bool(projeto.get("corrigido")) and projeto.get("nota") is not None
        if not arquivo_enviado:
            motivos.append("Projeto Final ainda não foi enviado")
        elif not final_concluido:
            motivos.append("Projeto Final ainda não foi corrigido e lançado")
    else:
        final_concluido = d.get("media_final") is not None or d.get("nota_final") is not None
        if not final_concluido:
            motivos.append("avaliação final ainda não foi concluída/lançada")

    aprovado = str(d.get("status_final") or "").lower() == "aprovado"
    if final_concluido and not aprovado:
        motivos.append("disciplina ainda não consta como aprovada")

    if d.get("frequencia") is not None:
        frequencia = max(0.0, min(100.0, float(d.get("frequencia"))))
    else:
        # Compatibilidade para matrículas antigas que ainda não receberam frequência administrativa.
        atividades_total = max(total_capitulos, 0) + 1
        atividades_feitas = min(capitulos_avaliados, max(total_capitulos, 0)) + (1 if final_concluido else 0)
        frequencia = round((atividades_feitas / atividades_total) * 100, 2) if atividades_total else 0.0

    docente = _docente_documental_disciplina(cursor, disciplina_id, d["nome"])

    resultado = {
        "id": d["id"],
        "nome": d["nome"],
        "carga_horaria": int(d.get("carga_horaria") or 80),
        "data_inicio": d.get("data_inicio") or "",
        "data_fim_previsto": d.get("data_fim_previsto") or "",
        "nota_final": d.get("nota_final"),
        "media_disciplina": d.get("media_disciplina"),
        "media_final": d.get("media_final"),
        "status_final": d.get("status_final") or "",
        "data_realizacao": d.get("data_realizacao") or "",
        "total_capitulos": total_capitulos,
        "capitulos_avaliados": capitulos_avaliados,
        "final_tipo": final_tipo,
        "final_concluido": final_concluido,
        "frequencia": frequencia,
        "docente": docente,
        "elegivel": len(motivos) == 0,
        "motivo": "; ".join(motivos) if motivos else "Elegível para emissão documental."
    }

    if fechar:
        conn.commit()
        conn.close()
    return resultado


def _disciplinas_documentos_aluno(aluno_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT d.id
        FROM disciplinas d
        JOIN aluno_disciplina ad ON ad.disciplina_id = d.id
        WHERE ad.aluno_id = %s
        ORDER BY d.nome
    """, (aluno_id,))
    ids = [r["id"] for r in cursor.fetchall()]
    resultado = [_status_disciplina_documentos(aluno_id, did, cursor) for did in ids]
    conn.commit()
    conn.close()
    return resultado


def _calcular_ira_automatico(disciplinas):
    """IRA em escala 0-10, ponderado pela carga horária real."""
    soma = 0.0
    carga = 0
    aprovadas = 0
    for d in disciplinas:
        nota = d.get("media_final")
        if nota is None:
            nota = d.get("nota_final")
        if nota is None:
            continue
        ch = int(d.get("carga_horaria") or 80)
        soma += float(nota) * ch
        carga += ch
        if str(d.get("status_final") or "").lower() == "aprovado":
            aprovadas += 1
    return {
        "ira": round(soma / carga, 2) if carga else 0.0,
        "carga_total": carga,
        "disciplinas_aprovadas": aprovadas,
        "total_disciplinas": len(disciplinas)
    }


def _dados_aluno_documentos(aluno_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.id, a.nome, a.email, a.ra,
               dp.cpf, dp.rg, dp.telefone, dp.endereco, dp.cidade, dp.estado, dp.cep,
               dp.curso_referencia, dp.nome_pai, dp.nome_mae, dp.naturalidade,
               dp.nacionalidade, dp.data_nascimento, dp.sexo, dp.estado_civil
        FROM alunos a
        LEFT JOIN dados_pessoais dp ON dp.aluno_id = a.id
        WHERE a.id = %s
    """, (aluno_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    dados = dict(row)
    cpf = re.sub(r"\D", "", dados.get("cpf") or "")
    dados["cpf_formatado"] = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if len(cpf) == 11 else dados.get("cpf", "")
    return dados


def _html_historico_integrado(aluno, disciplinas, codigo, qr_code, hash_documento):
    """Histórico acadêmico institucional em preto e branco, preservando os dados cadastrais."""
    resumo = _calcular_ira_automatico(disciplinas)

    def _v(valor, padrao="N/I"):
        valor = "" if valor is None else str(valor).strip()
        return escape(valor or padrao)

    pai = (aluno.get("nome_pai") or "").strip()
    mae = (aluno.get("nome_mae") or "").strip()
    filiacao = " e ".join([x for x in (pai, mae) if x]) or "N/I"
    unidade_curricular = aluno.get("curso_referencia") or "Disciplinas / Unidades Curriculares"

    linhas = []
    for d in disciplinas:
        nota = d.get("media_final")
        if nota is None:
            nota = d.get("nota_final")
        nota_txt = f"{float(nota):.2f}" if nota is not None else "N/I"
        status_txt = str(d.get("status_final") or "").upper() or "N/I"
        inicio = d.get("data_inicio") or "N/I"
        linhas.append(f"""
        <tr>
          <td>{escape(d.get('nome') or '')}</td>
          <td>{int(d.get('carga_horaria') or 80)}h</td>
          <td>{escape(d.get('docente') or 'Docente responsável')}</td>
          <td>{nota_txt}</td>
          <td>{float(d.get('frequencia') or 0):.0f}%</td>
          <td>{escape(status_txt)}</td>
          <td>{escape(str(inicio))}</td>
        </tr>""")

    return f"""<!doctype html><html lang='pt-br'><head><meta charset='utf-8'>
    <title>Histórico Acadêmico - {escape(aluno.get('nome') or '')}</title>
    <style>
    @page {{ size:A4; margin:14mm; }}
    *{{box-sizing:border-box}}
    body{{font-family:Arial,Helvetica,sans-serif;color:#000;background:#fff;font-size:9.5pt;line-height:1.35;margin:0}}
    .doc{{width:100%;background:#fff}}
    .cab{{border-bottom:2px solid #000;padding:0 0 9px;margin-bottom:13px;display:flex;justify-content:space-between;gap:16px;align-items:flex-start}}
    .brand{{font-size:15pt;font-weight:700;letter-spacing:.3px}} .sub{{font-size:8.5pt;margin-top:3px}}
    .cert{{font-size:7.8pt;text-align:right;line-height:1.35;max-width:52%}} .cert b{{font-size:9pt}}
    h1{{font-size:19pt;text-align:center;margin:16px 0 14px;letter-spacing:.5px}}
    .dados-tabela{{width:100%;border-collapse:collapse;margin-bottom:13px;font-size:9pt}}
    .dados-tabela td{{border:1px solid #000;padding:6px 8px;width:50%;vertical-align:top}}
    table{{width:100%;border-collapse:collapse;font-size:8.4pt;page-break-inside:auto}}
    thead{{display:table-header-group}} tr{{page-break-inside:avoid}}
    th,td{{border:1px solid #000;padding:5px;vertical-align:top}} th{{background:#fff;color:#000;text-transform:uppercase;font-size:7.7pt;text-align:left}}
    .resumo{{margin-top:12px;border:1px solid #000;padding:8px;display:flex;gap:18px;flex-wrap:wrap}}
    .assinatura{{margin:10mm auto 5mm;text-align:center;max-width:90mm}} .assinatura strong{{display:block;font-size:10pt}} .assinatura span{{display:block;font-size:8pt;margin-top:2px}} .assinatura small{{display:block;font-size:7pt;margin-top:3px}}
    .auth{{margin-top:14px;border-top:1px solid #000;padding-top:10px;display:grid;grid-template-columns:82px 1fr;gap:12px;align-items:center}}
    .auth img{{width:78px;height:78px}} .hash{{font-family:monospace;font-size:6.7pt;word-break:break-all;margin-top:4px}}
    .rodape{{margin-top:10px;border-top:1px solid #000;padding-top:6px;font-size:6.7pt;text-align:center}}
    @media print{{body,.doc{{background:#fff}}}}
    </style></head><body><div class='doc'>
      <div class='cab'>
        <div><div class='brand'>GRUPO EDUCACIONAL UNIFICADO</div><div class='sub'>SIGEU Educacional • Sistema Integrado de Gestão Educacional</div></div>
        <div class='cert'><b>FACOP CERTIFICADORA</b><br>Faculdade do Centro Oeste Paulista LTDA<br>CNPJ 04.344.730/0001-60 • Portaria MEC nº 887 de 26/07/2017</div>
      </div>
      <h1>HISTÓRICO ACADÊMICO</h1>
      <table class='dados-tabela'>
        <tr><td><b>Aluno:</b> {_v(aluno.get('nome'))}</td><td><b>RA:</b> {_v(aluno.get('ra'))}</td></tr>
        <tr><td><b>CPF:</b> {_v(aluno.get('cpf_formatado'))}</td><td><b>RG:</b> {_v(aluno.get('rg'))}</td></tr>
        <tr><td><b>Data de nascimento:</b> {_v(aluno.get('data_nascimento'))}</td><td><b>Nacionalidade:</b> {_v(aluno.get('nacionalidade'), 'Brasileira')}</td></tr>
        <tr><td><b>Naturalidade:</b> {_v(aluno.get('naturalidade'))}</td><td><b>Estado civil:</b> {_v(aluno.get('estado_civil'))}</td></tr>
        <tr><td colspan='2'><b>Filiação:</b> {_v(filiacao)}</td></tr>
        <tr><td colspan='2'><b>Unidade Curricular:</b> {_v(unidade_curricular, 'Disciplinas / Unidades Curriculares')}</td></tr>
      </table>
      <table><thead><tr><th>Componente Curricular</th><th>CH</th><th>Docente</th><th>Média</th><th>Frequência</th><th>Situação</th><th>Início</th></tr></thead>
      <tbody>{''.join(linhas)}</tbody></table>
      <div class='resumo'><span><b>IRA:</b> {resumo['ira']:.2f}/10</span><span><b>Disciplinas:</b> {resumo['total_disciplinas']}</span><span><b>Aprovadas:</b> {resumo['disciplinas_aprovadas']}</span><span><b>Carga horária:</b> {resumo['carga_total']}h</span></div>
      <div class='assinatura'><strong>Tatiane R. L. Costa</strong><span>Documento assinado eletronicamente</span><small>Assinatura validada pela certificação institucional.</small></div>
      <div class='auth'><img src='{qr_code}' alt='QR Code'><div><b>Código:</b> {escape(codigo)}<br><b>Emissão:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}<div class='hash'>SHA-256: {escape(hash_documento)}</div></div></div>
      <div class='rodape'>Documento eletrônico autenticado. Validação pelo código, QR Code e hash de integridade.</div>
    </div></body></html>"""


def _html_declaracao_integrada(aluno, d, codigo, qr_code, hash_documento):
    """Declaração institucional em P&B com assinatura eletrônica tipográfica, sem assinatura simulada."""
    nota = d.get("media_final") if d.get("media_final") is not None else d.get("nota_final")
    nota_txt = f"{float(nota):.2f}" if nota is not None else "N/I"
    data_conclusao = d.get("data_realizacao") or datetime.now().strftime("%d/%m/%Y")
    data_conclusao = str(data_conclusao).split(" ")[0]
    unidade_curricular = aluno.get("curso_referencia") or "Disciplinas / Unidades Curriculares"

    return f"""<!doctype html><html lang='pt-br'><head><meta charset='utf-8'>
    <title>Declaração de Conclusão - {escape(d.get('nome') or '')}</title>
    <style>
    @page{{size:A4;margin:14mm}}
    *{{box-sizing:border-box}} body{{font-family:Arial,Helvetica,sans-serif;color:#000;background:#fff;line-height:1.55;margin:0;font-size:10.5pt}}
    .doc{{border:1px solid #000;padding:12mm 11mm;background:#fff;min-height:0}}
    .cab{{border-bottom:2px solid #000;padding-bottom:9px;display:flex;justify-content:space-between;gap:16px;align-items:flex-start}}
    .brand{{font-size:14pt;font-weight:700}} .sub{{font-size:8.5pt;margin-top:3px}}
    .cert{{font-size:7.7pt;text-align:right;max-width:52%;line-height:1.35}} .cert b{{font-size:9pt}}
    h1{{text-align:center;font-size:20pt;margin:14mm 0 10mm;line-height:1.2}}
    p{{text-align:justify;font-size:11.5pt;margin:0 0 11px}}
    .dados{{border:1px solid #000;margin:12px 0;padding:8px 10px}}
    .dados div{{margin:3px 0}}
    .assinatura{{margin:12mm auto 8mm;text-align:center;max-width:88mm}}
    .assinatura strong{{display:block;font-size:11pt}} .assinatura span{{display:block;font-size:8.5pt;margin-top:2px}}
    .assinatura small{{display:block;font-size:6.8pt;margin-top:5px}}
    .auth{{margin-top:14px;border-top:1px solid #000;padding-top:10px;display:grid;grid-template-columns:82px 1fr;gap:12px;align-items:center}}
    .auth img{{width:78px;height:78px}} .hash{{font-family:monospace;font-size:6.5pt;word-break:break-all;margin-top:4px}}
    .rodape{{margin-top:7px;border-top:1px solid #000;padding-top:5px;font-size:6.6pt;text-align:center}}
    @media print{{body,.doc{{background:#fff}}}}
    </style></head><body><div class='doc'>
      <div class='cab'>
        <div><div class='brand'>GRUPO EDUCACIONAL UNIFICADO</div><div class='sub'>SIGEU Educacional • Sistema Integrado de Gestão Educacional</div></div>
        <div class='cert'><b>FACOP CERTIFICADORA</b><br>Faculdade do Centro Oeste Paulista LTDA<br>CNPJ 04.344.730/0001-60 • Portaria MEC nº 887 de 26/07/2017</div>
      </div>
      <h1>DECLARAÇÃO DE CONCLUSÃO DE DISCIPLINA</h1>
      <p>O <b>GRUPO EDUCACIONAL UNIFICADO</b>, por meio do <b>SIGEU Educacional</b>, declara, para os devidos fins, que <b>{escape(aluno.get('nome') or '')}</b>, CPF {escape(aluno.get('cpf_formatado') or '')}, matrícula/RA <b>{escape(aluno.get('ra') or '')}</b>, concluiu com aproveitamento o componente curricular <b>{escape(d.get('nome') or '')}</b>, com carga horária de <b>{int(d.get('carga_horaria') or 80)} horas</b>, frequência acadêmica registrada de <b>{float(d.get('frequencia') or 0):.0f}%</b> e média final <b>{nota_txt}</b>.</p>
      <p>A conclusão foi registrada em {escape(data_conclusao)}. O docente/responsável acadêmico registrado para o componente é <b>{escape(d.get('docente') or 'N/I')}</b>.</p>
      <p>A certificação documental, quando aplicável, é realizada pela <b>FACOP CERTIFICADORA</b> — Faculdade do Centro Oeste Paulista LTDA, CNPJ 04.344.730/0001-60, credenciada pela Portaria MEC nº 887 de 26/07/2017, no âmbito da parceria educacional registrada no sistema.</p>
      <div class='dados'><div><b>Unidade Curricular:</b> {escape(str(unidade_curricular))}</div><div><b>Situação:</b> APROVADO</div><div class='wide'><b>Documento:</b> emissão acadêmica eletrônica autenticada por código, QR Code e hash.</div></div>
      <div class='assinatura'><strong>Tatiane R. L. Costa</strong><span>Documento assinado eletronicamente</span><small>Assinatura validada pela certificação institucional.</small></div>
      <div class='auth'><img src='{qr_code}' alt='QR Code'><div><b>Código:</b> {escape(codigo)}<br><b>Emissão:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}<div class='hash'>SHA-256: {escape(hash_documento)}</div></div></div>
      <div class='rodape'>GRUPO EDUCACIONAL UNIFICADO • SIGEU Educacional • FACOP CERTIFICADORA</div>
    </div></body></html>"""

def _html_para_pdf(html_texto, base_url=None):
    return render_html_to_pdf_bytes(html_texto, base_url or request.host_url)


def _mesclar_pdfs(lista_pdfs):
    temporarios=[]
    try:
        entradas=[]
        for pdf_bytes in lista_pdfs:
            tmp=tempfile.NamedTemporaryFile(suffix=".pdf",delete=False); tmp.write(pdf_bytes); tmp.close()
            temporarios.append(tmp.name); entradas.append(tmp.name)
        out=tempfile.NamedTemporaryFile(suffix=".pdf",delete=False); out.close(); temporarios.append(out.name)
        merge_pdf_files(entradas,out.name)
        with open(out.name,"rb") as fh: return fh.read()
    finally:
        for caminho in temporarios:
            try: os.remove(caminho)
            except Exception: pass


def _salvar_componente_autenticado(cursor, aluno, tipo, html_texto, codigo, hash_doc, disciplina_id=None, qr_code=None):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    validade = (datetime.now() + timedelta(days=365 * 5)).strftime("%d/%m/%Y")
    cursor.execute("""
        INSERT INTO documentos_autenticados
        (codigo, aluno_id, aluno_nome, aluno_ra, tipo, conteudo_html, data_geracao,
         qr_code, hash_documento, data_emissao, data_validade, metadados, disciplina_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """, (
        codigo, aluno["id"], aluno.get("nome"), aluno.get("ra"), tipo, html_texto, agora,
        qr_code, hash_doc, agora, validade,
        json.dumps({"origem": "solicitacao_integrada", "versao": "1.0"}, ensure_ascii=False),
        disciplina_id
    ))
    return cursor.fetchone()["id"]


def _gerar_previa_solicitacao_integrada(solicitacao_id):
    """Gera o pacote em arquivos temporários e envia um único PDF ao R2."""
    if not r2_is_configured():
        return False, "Cloudflare R2 ainda não foi configurado no Render."
    conn=get_db_connection(); cursor=conn.cursor(); temporarios=[]
    try:
        cursor.execute("SELECT id,aluno_id,tipo_solicitacao,tipos_documentos,disciplinas_ids FROM solicitacoes_documentos_integrados WHERE id=%s",(solicitacao_id,)); sol=cursor.fetchone()
        if not sol: raise ValueError("Solicitação não encontrada.")
        aluno=_dados_aluno_documentos(sol["aluno_id"])
        if not aluno: raise ValueError("Aluno não encontrado.")
        ids=[int(x) for x in str(sol.get("disciplinas_ids") or "").split(",") if x.strip().isdigit()]
        if not ids: raise ValueError("Nenhuma disciplina foi selecionada.")
        disciplinas=[]
        for did in ids:
            st=_status_disciplina_documentos(sol["aluno_id"],did,cursor)
            if not st.get("elegivel"): raise ValueError(f"{st.get('nome','Disciplina')}: {st.get('motivo')}")
            disciplinas.append(st)
        tipos=json.loads(sol.get("tipos_documentos") or "[]")
        if not tipos: raise ValueError("Nenhum tipo de documento solicitado.")
        componentes=[]; timestamp=datetime.now().strftime("%Y%m%d%H%M%S"); base_url=request.host_url.rstrip("/")

        def add_html(html_texto):
            tmp=tempfile.NamedTemporaryFile(suffix=".pdf",delete=False); tmp.close(); temporarios.append(tmp.name)
            render_html_to_pdf_file(html_texto,tmp.name,base_url)

        if "historico" in tipos:
            codigo=f"HIST-{aluno['ra']}-{timestamp}-{secrets.token_hex(3).upper()}"; hash_doc=gerar_hash_documento("historico-integrado-"+str(solicitacao_id),aluno["ra"],timestamp); qr=gerar_qrcode_base64(f"{base_url}/validar-documento/{codigo}"); html_h=_html_historico_integrado(aluno,disciplinas,codigo,qr,hash_doc); doc_id=_salvar_componente_autenticado(cursor,aluno,"historico",html_h,codigo,hash_doc,None,qr); add_html(html_h); componentes.append({"id":doc_id,"tipo":"historico","codigo":codigo})
        if "conclusao" in tipos:
            for d in disciplinas:
                codigo=f"DECL-{aluno['ra']}-{d['id']}-{timestamp}-{secrets.token_hex(2).upper()}"; hash_doc=gerar_hash_documento(f"declaracao-{solicitacao_id}-{d['id']}",aluno["ra"],timestamp); qr=gerar_qrcode_base64(f"{base_url}/validar-documento/{codigo}"); html_d=_html_declaracao_integrada(aluno,d,codigo,qr,hash_doc); doc_id=_salvar_componente_autenticado(cursor,aluno,"declaracao_conclusao",html_d,codigo,hash_doc,d["id"],qr); add_html(html_d); componentes.append({"id":doc_id,"tipo":"declaracao_conclusao","disciplina_id":d["id"],"codigo":codigo})
        if "plano_ensino" in tipos:
            for d in disciplinas:
                cursor.execute("SELECT id,codigo,conteudo_html,hash_documento FROM documentos_autenticados WHERE tipo='plano_ensino' AND disciplina_id=%s ORDER BY id DESC LIMIT 1",(d["id"],)); plano=cursor.fetchone()
                if not plano or not plano.get("conteudo_html"): raise ValueError(f"Plano de Ensino ainda não foi gerado/vinculado à disciplina {d['nome']}.")
                add_html(plano["conteudo_html"]); componentes.append({"id":plano["id"],"tipo":"plano_ensino","disciplina_id":d["id"],"codigo":plano.get("codigo")})
        if not temporarios: raise ValueError("Nenhum documento pôde ser gerado.")
        merged=tempfile.NamedTemporaryFile(suffix=".pdf",delete=False); merged.close(); temporarios.append(merged.name)
        merge_pdf_files(temporarios[:-1],merged.name)
        h=hashlib.sha256()
        with open(merged.name,"rb") as fh:
            while True:
                chunk=fh.read(1024*1024)
                if not chunk: break
                h.update(chunk)
        hash_pdf=h.hexdigest().upper(); codigo_pacote=f"PAC-{aluno['ra']}-{timestamp}-{secrets.token_hex(3).upper()}"; nome_arquivo=f"SIGEU_documentos_{aluno['ra']}_{timestamp}.pdf"; key=make_key("documentos-integrados",nome_arquivo,solicitacao_id)
        with open(merged.name,"rb") as fh: r2_upload_fileobj(fh,key,"application/pdf",{"solicitacao_id":solicitacao_id,"hash":hash_pdf})
        agora=datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        cursor.execute("""UPDATE solicitacoes_documentos_integrados SET status='aguardando_aprovacao',mensagem_status=%s,codigo_pacote=%s,
            arquivo_r2_key=%s,pdf_previa=NULL,pdf_final=NULL,nome_arquivo=%s,hash_pdf=%s,componentes_json=%s,data_preparacao=%s WHERE id=%s""",
            ("Prévia automática pronta para conferência do MEW.",codigo_pacote,key,nome_arquivo,hash_pdf,json.dumps(componentes,ensure_ascii=False),agora,solicitacao_id))
        conn.commit(); return True,None
    except Exception as e:
        conn.rollback()
        try:
            cursor.execute("UPDATE solicitacoes_documentos_integrados SET status='erro',mensagem_status=%s WHERE id=%s",(str(e),solicitacao_id)); conn.commit()
        except Exception: conn.rollback()
        return False,str(e)
    finally:
        conn.close()
        for caminho in temporarios:
            try: os.remove(caminho)
            except Exception: pass



@app.route("/solicitar-documentos-integrados-modal", methods=["GET"])
def solicitar_documentos_integrados_modal():
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return "Não autenticado", 401

    tipo = request.args.get("tipo", "integrado")
    nome = request.args.get("nome", "Documentos Acadêmicos")
    opcoes = {
        "integrado": ["historico", "conclusao", "plano_ensino"],
        "historico": ["historico"],
        "conclusao": ["conclusao"],
        "plano_ensino": ["plano_ensino"],
    }
    if tipo not in opcoes:
        return "Tipo de solicitação inválido", 400

    disciplinas = _disciplinas_documentos_aluno(aluno_id)
    elegiveis = [d for d in disciplinas if d.get("elegivel")]

    itens = []
    for d in disciplinas:
        disabled = "" if d.get("elegivel") else "disabled"
        cor = "#176b3a" if d.get("elegivel") else "#a12727"
        itens.append(f"""
        <label style='display:block;padding:11px;border-bottom:1px solid #eee;cursor:pointer'>
          <input type='checkbox' class='disciplina-checkbox' value='{d['id']}' {disabled} style='margin-right:9px'>
          <b>{escape(d['nome'])}</b> — {d['carga_horaria']}h
          <div style='font-size:12px;color:{cor};margin:4px 0 0 26px'>{escape(d['motivo'])}</div>
        </label>""")

    tipos_json = json.dumps(opcoes[tipo], ensure_ascii=False)
    return f"""
    <div class='document-form'>
      <input type='hidden' id='docTipo' value='{escape(tipo)}'>
      <input type='hidden' id='docNome' value='{escape(nome)}'>
      <input type='hidden' id='docTiposIntegrados' value='{escape(tipos_json)}'>
      <div style='background:#eef6ff;border-left:4px solid #3f464b;padding:12px;margin-bottom:14px'>
        <b>Regra automática:</b> somente disciplinas com no mínimo 20 dias, todos os capítulos/avaliações concluídos e avaliação final ou Projeto Final concluído, corrigido e com nota podem ser solicitadas.
      </div>
      <div class='form-group'><label><b>Selecione uma, várias ou todas as disciplinas elegíveis</b></label>
        <div style='max-height:310px;overflow:auto;border:1px solid #ddd;border-radius:8px'>{''.join(itens) if itens else '<p style="padding:18px">Nenhuma disciplina matriculada.</p>'}</div>
      </div>
      <div style='margin-top:10px'><button type='button' onclick="document.querySelectorAll('.disciplina-checkbox:not(:disabled)').forEach(x=>x.checked=true)" class='btn btn-secondary'>Selecionar todas elegíveis ({len(elegiveis)})</button></div>
      <div class='form-group' style='margin-top:16px'><label>Observação (opcional)</label><textarea id='docDetalhes' class='form-control' rows='3' placeholder='Observação para a Secretaria/MEW'></textarea></div>
      <input type='hidden' id='docVias' value='1'>
      <button type='button' class='btn btn-primary' style='width:100%;margin-top:10px' onclick='enviarSolicitacao()'>Enviar solicitação</button>
    </div>"""


@app.route("/solicitar-documentos-integrados", methods=["POST"])
def solicitar_documentos_integrados():
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "message": "Não autenticado"}), 401

    data = request.get_json(silent=True) or {}
    tipo = data.get("tipo", "integrado")
    ids = [int(x) for x in data.get("disciplinas_ids", []) if str(x).isdigit()]
    detalhes = (data.get("detalhes") or "").strip()
    opcoes = {
        "integrado": ["historico", "conclusao", "plano_ensino"],
        "historico": ["historico"],
        "conclusao": ["conclusao"],
        "plano_ensino": ["plano_ensino"],
    }
    if tipo not in opcoes or not ids:
        return jsonify({"success": False, "message": "Selecione ao menos uma disciplina."}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        motivos = []
        for did in ids:
            status = _status_disciplina_documentos(aluno_id, did, cursor)
            if not status.get("elegivel"):
                motivos.append(f"{status.get('nome','Disciplina')}: {status.get('motivo')}")
        if motivos:
            conn.rollback()
            return jsonify({"success": False, "message": " | ".join(motivos)}), 400

        agora = datetime.now().strftime("%d/%m/%Y %H:%M")
        cursor.execute("""
            INSERT INTO solicitacoes_documentos_integrados
            (aluno_id, tipo_solicitacao, tipos_documentos, disciplinas_ids, detalhes,
             data_solicitacao, status, mensagem_status)
            VALUES (%s,%s,%s,%s,%s,%s,'pendente','Aguardando geração da prévia e conferência do MEW.')
            RETURNING id
        """, (aluno_id, tipo, json.dumps(opcoes[tipo]), ",".join(map(str, ids)), detalhes, agora))
        sid = cursor.fetchone()["id"]
        conn.commit()
        return jsonify({"success": True, "message": "Solicitação registrada. O MEW fará a conferência antes da liberação.", "id": sid})
    finally:
        conn.close()


@app.route("/historico-documentos-integrados")
def historico_documentos_integrados():
    aluno_id=session.get("aluno_id")
    if not aluno_id: return jsonify({"success":False,"message":"Não autenticado"}),401
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("""SELECT s.id,s.tipo_solicitacao,s.tipos_documentos,s.disciplinas_ids,s.status,s.mensagem_status,s.codigo_pacote,s.nome_arquivo,s.hash_pdf,s.data_solicitacao,s.data_preparacao,s.data_aprovacao,s.arquivo_r2_key,
        COALESCE((SELECT STRING_AGG(d.nome,', ' ORDER BY d.nome) FROM disciplinas d WHERE d.id=ANY(string_to_array(NULLIF(s.disciplinas_ids,''),',')::int[])),'') AS disciplinas_nomes
        FROM solicitacoes_documentos_integrados s WHERE s.aluno_id=%s ORDER BY s.id DESC LIMIT 200""",(aluno_id,)); rows=[]
    for row in cursor.fetchall():
        r=dict(row); r["arquivo_url"]=f"/documentos-integrados/{r['id']}/pdf" if r.get("status")=="aprovado" and r.get("arquivo_r2_key") else None; rows.append(r)
    conn.close(); return jsonify({"success":True,"solicitacoes":rows})



@app.route("/meus-documentos-integrados-api")
def meus_documentos_integrados_api():
    aluno_id=session.get("aluno_id")
    if not aluno_id: return jsonify({"success":False,"documentos":[]}),401
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("""SELECT s.id,s.tipo_solicitacao,s.codigo_pacote,s.nome_arquivo,s.data_aprovacao,s.hash_pdf,s.disciplinas_ids,s.mensagem_status,s.arquivo_r2_key,
        COALESCE((SELECT STRING_AGG(d.nome,', ' ORDER BY d.nome) FROM disciplinas d WHERE d.id=ANY(string_to_array(NULLIF(s.disciplinas_ids,''),',')::int[])),'') AS disciplinas_nomes
        FROM solicitacoes_documentos_integrados s WHERE s.aluno_id=%s AND s.status='aprovado' AND (s.arquivo_r2_key IS NOT NULL OR s.pdf_final IS NOT NULL) ORDER BY s.id DESC LIMIT 200""",(aluno_id,)); docs=[]
    for row in cursor.fetchall():
        titulo="Pacote Integrado: Histórico + Declaração + Plano" if row["tipo_solicitacao"]=="integrado" else {"historico":"Histórico Escolar","conclusao":"Declaração de Conclusão","plano_ensino":"Plano de Ensino"}.get(row["tipo_solicitacao"],"Documentos Acadêmicos")
        docs.append({"id":row["id"],"tipo":"pacote_integrado","titulo":titulo,"disciplina_nome":row.get("disciplinas_nomes") or "","data_envio":row.get("data_aprovacao") or "","mensagem":"Documento conferido e aprovado pela Secretaria/MEW.","status":"enviado","url":f"/documentos-integrados/{row['id']}/pdf"})
    conn.close(); return jsonify({"success":True,"documentos":docs})



@app.route("/documentos-integrados/<int:solicitacao_id>/pdf")
def aluno_pdf_documentos_integrados(solicitacao_id):
    aluno_id=session.get("aluno_id")
    if not aluno_id: return redirect(url_for("login"))
    conn=get_db_connection(); cursor=conn.cursor(); cursor.execute("SELECT arquivo_r2_key,pdf_final,nome_arquivo FROM solicitacoes_documentos_integrados WHERE id=%s AND aluno_id=%s AND status='aprovado'",(solicitacao_id,aluno_id)); row=cursor.fetchone(); conn.close()
    if not row: return "Documento ainda não disponível.",404
    if row.get("arquivo_r2_key"): return redirect(r2_presigned_url(row["arquivo_r2_key"],download_name=row.get("nome_arquivo") or f"documentos_{solicitacao_id}.pdf"))
    if row.get("pdf_final") is not None: return send_file(BytesIO(bytes(row["pdf_final"])),mimetype="application/pdf",as_attachment=False,download_name=row.get("nome_arquivo") or f"documentos_{solicitacao_id}.pdf")
    return "Documento ainda não disponível.",404



@app.route("/mew/documentos-integrados")
def mew_documentos_integrados():
    if not session.get("mew_admin"): return redirect("/mew/login")
    page=max(1,request.args.get("page",1,type=int) or 1); per_page=50; offset=(page-1)*per_page
    conn=get_db_connection(); cursor=conn.cursor(); cursor.execute("SELECT COUNT(*) AS total FROM solicitacoes_documentos_integrados"); total=int((cursor.fetchone() or {}).get("total") or 0)
    cursor.execute("""SELECT s.id,s.aluno_id,s.tipo_solicitacao,s.tipos_documentos,s.disciplinas_ids,s.status,s.mensagem_status,s.codigo_pacote,s.nome_arquivo,s.hash_pdf,s.data_solicitacao,s.data_preparacao,s.data_aprovacao,s.arquivo_r2_key,a.nome AS aluno_nome,a.ra AS aluno_ra,
        COALESCE((SELECT STRING_AGG(d.nome,', ' ORDER BY d.nome) FROM disciplinas d WHERE d.id=ANY(string_to_array(NULLIF(s.disciplinas_ids,''),',')::int[])),'') AS disciplinas_nomes
        FROM solicitacoes_documentos_integrados s JOIN alunos a ON a.id=s.aluno_id
        ORDER BY CASE s.status WHEN 'pendente' THEN 1 WHEN 'erro' THEN 2 WHEN 'aguardando_aprovacao' THEN 3 ELSE 4 END,s.id DESC LIMIT %s OFFSET %s""",(per_page,offset)); rows=cursor.fetchall(); conn.close()
    return render_template("mew/documentos_integrados.html",solicitacoes=rows,page=page,total_pages=max(1,(total+per_page-1)//per_page),total_solicitacoes=total)



@app.route("/mew/documentos-integrados/<int:solicitacao_id>/conferir")
def mew_conferir_documentos_integrados(solicitacao_id):
    if not session.get("mew_admin"): return redirect("/mew/login")
    def buscar():
        conn=get_db_connection(); cur=conn.cursor(); cur.execute("""SELECT s.id,s.aluno_id,s.tipo_solicitacao,s.tipos_documentos,s.disciplinas_ids,s.status,s.mensagem_status,s.codigo_pacote,s.nome_arquivo,s.hash_pdf,s.data_solicitacao,s.data_preparacao,s.data_aprovacao,s.arquivo_r2_key,(s.pdf_previa IS NOT NULL) AS tem_pdf_previa,a.nome AS aluno_nome,a.ra AS aluno_ra FROM solicitacoes_documentos_integrados s JOIN alunos a ON a.id=s.aluno_id WHERE s.id=%s""",(solicitacao_id,)); row=cur.fetchone(); conn.close(); return row
    sol=buscar()
    if not sol: return "Solicitação não encontrada",404
    if sol.get("status") in ("pendente","erro") or not (sol.get("arquivo_r2_key") or sol.get("tem_pdf_previa")):
        _gerar_previa_solicitacao_integrada(solicitacao_id); sol=buscar()
    return render_template("mew/conferir_documentos_integrados.html",solicitacao=sol)



@app.route("/mew/documentos-integrados/<int:solicitacao_id>/pdf-previa")
def mew_pdf_previa_integrada(solicitacao_id):
    if not session.get("mew_admin"): return "Não autorizado",403
    conn=get_db_connection(); cursor=conn.cursor(); cursor.execute("SELECT arquivo_r2_key,pdf_previa,nome_arquivo FROM solicitacoes_documentos_integrados WHERE id=%s",(solicitacao_id,)); row=cursor.fetchone(); conn.close()
    if not row: return "Prévia não disponível",404
    nome="PREVIA_"+(row.get("nome_arquivo") or f"documentos_{solicitacao_id}.pdf")
    if row.get("arquivo_r2_key"): return redirect(r2_presigned_url(row["arquivo_r2_key"],download_name=nome))
    if row.get("pdf_previa") is not None: return send_file(BytesIO(bytes(row["pdf_previa"])),mimetype="application/pdf",as_attachment=False,download_name=nome)
    return "Prévia não disponível",404



@app.route("/mew/documentos-integrados/<int:solicitacao_id>/regerar", methods=["POST"])
def mew_regerar_documentos_integrados(solicitacao_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    ok, erro = _gerar_previa_solicitacao_integrada(solicitacao_id)
    if ok:
        return redirect(f"/mew/documentos-integrados/{solicitacao_id}/conferir?sucesso=Prévia+regenerada")
    return redirect(f"/mew/documentos-integrados/{solicitacao_id}/conferir?erro={url_quote(erro or 'Erro')}")


@app.route("/mew/documentos-integrados/<int:solicitacao_id>/excluir", methods=["POST"])
def mew_excluir_documentos_integrados(solicitacao_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, status, arquivo_r2_key, componentes_json
        FROM solicitacoes_documentos_integrados
        WHERE id=%s
    """, (solicitacao_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return redirect("/mew/documentos-integrados?erro=Solicitação+não+encontrada")

    if row.get("status") == "aprovado":
        conn.close()
        return redirect("/mew/documentos-integrados?erro=Documento+já+aprovado.+A+exclusão+foi+bloqueada+para+preservar+o+registro+liberado+ao+aluno")

    # Remove apenas os documentos temporários criados por esta solicitação.
    # O Plano de Ensino institucional já existente NÃO é apagado.
    ids_componentes = []
    try:
        componentes = json.loads(row.get("componentes_json") or "[]")
        ids_componentes = [
            int(c.get("id")) for c in componentes
            if c.get("id") and c.get("tipo") in ("historico", "declaracao_conclusao")
        ]
    except Exception:
        ids_componentes = []

    try:
        if ids_componentes:
            cursor.execute(
                "DELETE FROM documentos_autenticados WHERE id = ANY(%s)",
                (ids_componentes,)
            )
        cursor.execute(
            "DELETE FROM solicitacoes_documentos_integrados WHERE id=%s",
            (solicitacao_id,)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        return redirect("/mew/documentos-integrados?erro=Não+foi+possível+excluir+a+solicitação")
    finally:
        if not conn.closed:
            conn.close()

    key = row.get("arquivo_r2_key")
    if key:
        try:
            delete_object(key)
        except Exception:
            pass

    return redirect("/mew/documentos-integrados?sucesso=Solicitação+excluída.+Agora+é+possível+gerar+uma+nova")


@app.route("/mew/documentos-integrados/<int:solicitacao_id>/aprovar", methods=["POST"])
def mew_aprovar_documentos_integrados(solicitacao_id):
    if not session.get("mew_admin"): return redirect("/mew/login")
    conn=get_db_connection(); cursor=conn.cursor(); cursor.execute("SELECT arquivo_r2_key,pdf_previa,nome_arquivo FROM solicitacoes_documentos_integrados WHERE id=%s",(solicitacao_id,)); row=cursor.fetchone()
    if not row: conn.close(); return redirect(f"/mew/documentos-integrados/{solicitacao_id}/conferir?erro=Solicitação+não+encontrada")
    key=row.get("arquivo_r2_key")
    # Legado: antes de aprovar, tira a prévia do BYTEA e manda para o R2.
    if not key and row.get("pdf_previa") is not None:
        if not r2_is_configured(): conn.close(); return redirect(f"/mew/documentos-integrados/{solicitacao_id}/conferir?erro=Configure+o+Cloudflare+R2")
        key=make_key("documentos-integrados",row.get("nome_arquivo") or f"documentos_{solicitacao_id}.pdf",solicitacao_id); r2_upload_bytes(bytes(row["pdf_previa"]),key,"application/pdf")
    if not key: conn.close(); return redirect(f"/mew/documentos-integrados/{solicitacao_id}/conferir?erro=Gere+a+prévia+antes+de+aprovar")
    agora=datetime.now().strftime("%d/%m/%Y %H:%M:%S"); cursor.execute("""UPDATE solicitacoes_documentos_integrados SET arquivo_r2_key=%s,pdf_previa=NULL,pdf_final=NULL,status='aprovado',data_aprovacao=%s,mensagem_status='Conferido e aprovado pelo MEW. Disponível na plataforma do aluno.' WHERE id=%s""",(key,agora,solicitacao_id)); conn.commit(); conn.close(); return redirect("/mew/documentos-integrados?sucesso=Documento+aprovado+e+liberado+ao+aluno")



@app.route("/mew/plano-ensino/<int:documento_id>/vincular", methods=["POST"])
def mew_vincular_plano_disciplina(documento_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    disciplina_id = request.form.get("disciplina_id", type=int)
    if not disciplina_id:
        return redirect("/mew/planos-ensino?erro=Selecione+uma+disciplina")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE documentos_autenticados SET disciplina_id=%s WHERE id=%s AND tipo='plano_ensino'", (disciplina_id, documento_id))
    conn.commit(); conn.close()
    return redirect("/mew/planos-ensino?sucesso=Plano+vinculado+à+disciplina")


def url_quote(texto):
    from urllib.parse import quote_plus
    return quote_plus(str(texto))


# --------------------------- TITAN EMAIL ---------------------------
def _recibo_pagamento_html(aluno, valor, pagamento_id, data_pagamento, disciplinas):
    lista = "".join(f"<li>{escape(x)}</li>" for x in disciplinas) or "<li>Serviços educacionais contratados</li>"
    valor_txt = f"R$ {float(valor or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"""<!doctype html><html><head><meta charset='utf-8'><style>@page{{size:A4;margin:18mm}}body{{font-family:Arial;color:#222629}}h1{{color:#3f464b}}.alerta{{background:#fff7db;border-left:4px solid #b8860b;padding:10px}}table{{width:100%;border-collapse:collapse}}td{{border-bottom:1px solid #ddd;padding:8px}}</style></head><body>
    <h1>RECIBO ELETRÔNICO / COMPROVANTE DE PAGAMENTO</h1>
    <p><b>SIGEU Educacional</b></p><div class='alerta'><b>Importante:</b> este recibo comprova o pagamento no sistema acadêmico e não substitui NFS-e ou outro documento fiscal oficial quando legalmente exigido.</div>
    <table><tr><td>Aluno</td><td>{escape(aluno.get('nome',''))}</td></tr><tr><td>Matrícula/RA</td><td>{escape(aluno.get('ra',''))}</td></tr><tr><td>Valor</td><td>{valor_txt}</td></tr><tr><td>Pagamento</td><td>{escape(str(pagamento_id or ''))}</td></tr><tr><td>Data</td><td>{escape(data_pagamento or '')}</td></tr></table>
    <h3>Serviços/disciplinas vinculados</h3><ul>{lista}</ul><p>Emitido automaticamente pelo SIGEU.</p></body></html>"""


def enviar_boas_vindas_titan(aluno_id, referencia, pagamento_id=None):
    """Envia e-mail apenas se as variáveis TITAN_* estiverem configuradas."""
    host = os.getenv("TITAN_SMTP_HOST", "smtp.titan.email").strip()
    port = int(os.getenv("TITAN_SMTP_PORT", "465"))
    usuario = os.getenv("TITAN_SMTP_USER", "").strip()
    senha_smtp = os.getenv("TITAN_SMTP_PASSWORD", "")
    from_name = os.getenv("TITAN_FROM_NAME", "SIGEU Educacional")
    login_url = os.getenv("SIGEU_LOGIN_URL", "https://campusvirtualfacop.com.br/login")
    if not usuario or not senha_smtp:
        return False, "Titan SMTP ainda não configurado."

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id FROM emails_transacionais WHERE referencia=%s", (referencia,))
    if cursor.fetchone():
        conn.close(); return True, "E-mail já enviado anteriormente."

    cursor.execute("""
        SELECT a.id,a.nome,a.email,a.ra,dp.cpf,
               sf.valor_total
        FROM alunos a
        LEFT JOIN dados_pessoais dp ON dp.aluno_id=a.id
        LEFT JOIN situacao_financeira sf ON sf.id=(SELECT id FROM situacao_financeira WHERE aluno_id=a.id ORDER BY id DESC LIMIT 1)
        WHERE a.id=%s
    """, (aluno_id,))
    aluno = cursor.fetchone()
    if not aluno or not aluno.get("email"):
        conn.close(); return False, "Aluno sem e-mail cadastrado."

    cursor.execute("""SELECT d.nome FROM disciplinas d JOIN aluno_disciplina ad ON ad.disciplina_id=d.id WHERE ad.aluno_id=%s ORDER BY d.nome""", (aluno_id,))
    disciplinas = [r["nome"] for r in cursor.fetchall()]
    conn.close()

    data_pagamento = datetime.now().strftime("%d/%m/%Y %H:%M")
    recibo_html = _recibo_pagamento_html(aluno, aluno.get("valor_total"), pagamento_id, data_pagamento, disciplinas)
    recibo_pdf = render_html_to_pdf_bytes(recibo_html, request.host_url if request else None)

    from email.message import EmailMessage
    from email.utils import formataddr
    import smtplib
    msg = EmailMessage()
    msg["Subject"] = f"Bem-vindo ao SIGEU Educacional | Matrícula {aluno['ra']}"
    msg["From"] = formataddr((from_name, usuario))
    msg["To"] = aluno["email"]
    msg.set_content(f"Bem-vindo ao SIGEU. Matrícula: {aluno['ra']}. Senha inicial: seu CPF, somente números. Acesse: {login_url}")
    corpo = f"""<html><body style='font-family:Arial;color:#1f2937'><h2>Bem-vindo ao SIGEU Educacional</h2><p>Olá, <b>{escape(aluno['nome'])}</b>.</p><p>Seu pagamento foi confirmado e sua matrícula está ativa.</p><div style='background:#f2f6fb;padding:16px;border-left:4px solid #3f464b'><b>Matrícula/RA:</b> {escape(aluno['ra'])}<br><b>Senha inicial:</b> seu CPF, somente números</div><p>Ao primeiro acesso, o sistema apresentará o contrato educacional para assinatura eletrônica. Após a assinatura, as disciplinas já vinculadas à matrícula ficarão disponíveis.</p><p><a href='{login_url}'>Acessar a Plataforma Acadêmica</a></p><p>Segue em anexo o recibo eletrônico/comprovante do pagamento. Ele não substitui NFS-e quando esta for legalmente exigida.</p><p>Atenciosamente,<br><b>SIGEU Educacional</b></p></body></html>"""
    msg.add_alternative(corpo, subtype="html")
    msg.add_attachment(recibo_pdf, maintype="application", subtype="pdf", filename=f"recibo_matricula_{aluno['ra']}.pdf")

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                smtp.login(usuario, senha_smtp); smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo(); smtp.starttls(); smtp.ehlo(); smtp.login(usuario, senha_smtp); smtp.send_message(msg)
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("""INSERT INTO emails_transacionais(aluno_id,tipo,referencia,destinatario,data_envio,status) VALUES(%s,'boas_vindas',%s,%s,%s,'enviado') ON CONFLICT (referencia) DO NOTHING""", (aluno_id, referencia, aluno["email"], datetime.now().strftime("%d/%m/%Y %H:%M:%S")))
        conn.commit(); conn.close()
        return True, "E-mail enviado."
    except Exception as e:
        print(f"Erro Titan SMTP: {e}")
        return False, str(e)


def _dados_contrato_render(contrato_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            c.id AS contrato_id,
            c.status AS contrato_status,
            c.assinatura_base64,
            c.foto_assinatura_base64,
            c.assinatura_r2_key, c.assinatura_mime,
            c.foto_assinatura_r2_key, c.foto_assinatura_mime,
            c.ip_assinatura,
            c.user_agent_assinatura,
            c.aceite_contrato,
            c.aceite_foto,
            c.texto_aceite,
            c.versao_contrato,
            c.hash_assinado,
            c.data_envio,
            c.data_assinatura,
            a.id,
            a.nome,
            a.ra,
            a.email,
            dp.cpf,
            dp.rg,
            dp.telefone,
            dp.endereco,
            dp.cidade,
            dp.estado,
            dp.cep,
            dp.curso_referencia,
            dp.data_nascimento,
            sf.forma_pagamento,
            sf.valor_total,
            sf.parcelas_total,
            sf.parcelas_pagas,
            sf.status AS status_financeiro
        FROM contratos_alunos c
        JOIN alunos a ON a.id = c.aluno_id
        LEFT JOIN dados_pessoais dp ON dp.aluno_id = a.id
        LEFT JOIN situacao_financeira sf ON sf.id = (
            SELECT sf2.id FROM situacao_financeira sf2
            WHERE sf2.aluno_id = a.id
            ORDER BY sf2.id DESC LIMIT 1
        )
        WHERE c.id = %s
    """, (contrato_id,))
    aluno = cursor.fetchone()

    if not aluno:
        conn.close()
        return None

    cursor.execute("""
        SELECT d.nome,
               COALESCE(d.carga_horaria, 80) AS carga_horaria,
               addd.data_inicio,
               addd.data_fim_previsto
        FROM disciplinas d
        JOIN aluno_disciplina ad ON ad.disciplina_id = d.id
        LEFT JOIN aluno_disciplina_datas addd
          ON addd.aluno_id = ad.aluno_id AND addd.disciplina_id = d.id
        WHERE ad.aluno_id = %s
        ORDER BY d.nome
    """, (aluno["id"],))
    disciplinas_db = cursor.fetchall()
    conn.close()

    disciplinas = [d["nome"] for d in disciplinas_db]
    carga_horaria_total = sum(int(d["carga_horaria"] or 0) for d in disciplinas_db)

    valor = float(aluno["valor_total"] or 0)
    parcelas = int(aluno["parcelas_total"] or 1)
    valor_parcela = valor / parcelas if parcelas > 0 else valor

    cpf = re.sub(r"\D", "", aluno["cpf"] or "")
    cpf_formatado = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if len(cpf) == 11 else (aluno["cpf"] or "")

    cep = re.sub(r"\D", "", aluno["cep"] or "")
    cep_formatado = f"{cep[:5]}-{cep[5:]}" if len(cep) == 8 else (aluno["cep"] or "")

    formas_pagamento = {
        "avista": "À vista",
        "cartao": "Cartão",
        "boleto_pix": "Boleto / PIX",
        "mercadopago": "Mercado Pago - PIX / Cartão"
    }
    forma_pagamento = formas_pagamento.get(aluno["forma_pagamento"], aluno["forma_pagamento"] or "Não informado")

    agora = agora_brasilia()
    codigo_contrato = f"CT-{contrato_id:08d}-{aluno['ra'] or aluno['id']}"

    hash_base = "|".join([
        str(contrato_id), str(aluno["id"]), str(aluno["ra"] or ""), cpf,
        str(aluno["data_envio"] or "")
    ])
    hash_pre_assinatura = hashlib.sha256(hash_base.encode("utf-8")).hexdigest().upper()
    hash_contrato = aluno.get("hash_assinado") or hash_pre_assinatura

    datas_validas = [d for d in disciplinas_db if d.get("data_inicio")]
    data_inicio_txt = datas_validas[0]["data_inicio"] if datas_validas else ""
    data_fim_txt = max((d.get("data_fim_previsto") or "" for d in disciplinas_db), default="")

    ano_semestre = f"{agora.year}/{1 if agora.month <= 6 else 2}"
    if data_inicio_txt:
        try:
            di = datetime.strptime(data_inicio_txt, "%d/%m/%Y")
            ano_semestre = f"{di.year}/{1 if di.month <= 6 else 2}"
        except Exception:
            pass

    meses = ["janeiro","fevereiro","março","abril","maio","junho","julho","agosto","setembro","outubro","novembro","dezembro"]
    data_extenso = f"{agora.day} de {meses[agora.month-1]} de {agora.year}"

    return {
        "contrato_id": contrato_id,
        "aluno_id": aluno["id"],
        "nome_contratante": aluno["nome"],
        "nome_academico": aluno["nome"],
        "cpf_formatado": cpf_formatado,
        "rg": aluno["rg"] or "",
        "email": aluno["email"] or "",
        "telefone": aluno["telefone"] or "",
        "endereco": aluno["endereco"] or "",
        "bairro": "",
        "cidade": aluno["cidade"] or "",
        "uf": aluno["estado"] or "",
        "cep_formatado": cep_formatado,
        "ra": aluno["ra"] or "",
        "curso": aluno["curso_referencia"] or "",
        "disciplinas": disciplinas,
        "carga_horaria": f"{carga_horaria_total} horas",
        "valor_total": f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "valor_parcelado": f"{valor_parcela:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "forma_pagamento": forma_pagamento,
        "tempo_minimo": "30 dias",
        "tempo_maximo": data_fim_txt or "Conforme prazo acadêmico contratado",
        "modalidade": "Ambiente Virtual de Aprendizagem / conforme a atividade contratada",
        "ano_semestre": ano_semestre,
        "codigo_contrato": codigo_contrato,
        "hash_contrato": hash_contrato,
        "hash_contrato_curto": hash_contrato[:16],
        "hash_assinado": aluno.get("hash_assinado") or "",
        "data_assinatura": aluno["data_assinatura"] or "PENDENTE DE ASSINATURA",
        "timestamp_iso": (aluno["data_assinatura"] or aluno["data_envio"] or agora.strftime("%d/%m/%Y %H:%M:%S")),
        "data_geracao": aluno["data_envio"] or agora.strftime("%d/%m/%Y %H:%M:%S"),
        "data_extenso": data_extenso,
        "numero_processo": f"SIGEU-{contrato_id:08d}",
        "contrato_status": aluno["contrato_status"],
        "assinado": aluno["contrato_status"] == "assinado",
        "assinatura_base64": (r2_presigned_url(aluno.get("assinatura_r2_key")) if aluno.get("assinatura_r2_key") else (aluno.get("assinatura_base64") or "")),
        "foto_assinatura_base64": (r2_presigned_url(aluno.get("foto_assinatura_r2_key")) if aluno.get("foto_assinatura_r2_key") else (aluno.get("foto_assinatura_base64") or "")),
        "ip_assinatura": aluno.get("ip_assinatura") or "",
        "user_agent_assinatura": aluno.get("user_agent_assinatura") or "",
        "aceite_contrato": bool(aluno.get("aceite_contrato")),
        "aceite_foto": bool(aluno.get("aceite_foto")),
        "texto_aceite": aluno.get("texto_aceite") or "",
        "versao_contrato": aluno.get("versao_contrato") or VERSAO_CONTRATO
    }


@app.route("/contrato/registro/<int:contrato_id>")
def visualizar_contrato_registro(contrato_id):
    dados = _dados_contrato_render(contrato_id)
    if not dados:
        return "Contrato não encontrado.", 404

    if not session.get("mew_admin") and session.get("aluno_id") != dados["aluno_id"]:
        return "Acesso não autorizado.", 403

    return render_template("contrato_padrao.html", **dados)


@app.route("/contrato/<int:aluno_id>")
def visualizar_contrato_aluno(aluno_id):
    if not session.get("mew_admin") and session.get("aluno_id") != aluno_id:
        return "Acesso não autorizado.", 403

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id FROM contratos_alunos
        WHERE aluno_id = %s
        ORDER BY id DESC
        LIMIT 1
    """, (aluno_id,))
    contrato = cursor.fetchone()
    conn.close()

    if not contrato:
        return "Contrato não encontrado para este aluno.", 404

    return redirect(url_for("visualizar_contrato_registro", contrato_id=contrato["id"]))



def gerar_pdf_contrato_assinado(contrato_id, salvar=True):
    """Renderiza em arquivo temporário e armazena o PDF definitivo no R2."""
    if not r2_is_configured(): raise R2NotConfigured("Cloudflare R2 não configurado.")
    dados=_dados_contrato_render(contrato_id)
    if not dados: raise ValueError("Contrato não encontrado.")
    if not dados.get("assinado"): raise ValueError("O contrato ainda não foi assinado.")
    html_final=render_template("contrato_padrao.html",**dados)
    key=make_key("contratos/pdfs",f"Contrato_SIGEU_{dados.get('ra') or contrato_id}.pdf",contrato_id)
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        render_html_to_pdf_file(html_final,tmp.name,request.url_root)
        with open(tmp.name,"rb") as fh: r2_upload_fileobj(fh,key,"application/pdf",{"contrato_id":contrato_id,"hash":dados.get("hash_assinado","")})
    if salvar:
        conn=get_db_connection(); cursor=conn.cursor(); cursor.execute("UPDATE contratos_alunos SET pdf_assinado_r2_key=%s,pdf_assinado=NULL,arquivo_assinado_path=%s WHERE id=%s",(key,f"/contrato/pdf/{contrato_id}",contrato_id)); conn.commit(); conn.close()
    return key,dados



@app.route("/contrato/pdf/<int:contrato_id>")
def contrato_pdf_assinado(contrato_id):
    dados=_dados_contrato_render(contrato_id)
    if not dados: return "Contrato não encontrado.",404
    if not session.get("mew_admin") and session.get("aluno_id")!=dados["aluno_id"]: return "Acesso não autorizado.",403
    if not dados.get("assinado"): return "O contrato ainda não foi assinado.",409
    conn=get_db_connection(); cursor=conn.cursor(); cursor.execute("SELECT pdf_assinado_r2_key,pdf_assinado FROM contratos_alunos WHERE id=%s",(contrato_id,)); reg=cursor.fetchone(); conn.close()
    if reg and reg.get("pdf_assinado_r2_key"):
        return redirect(r2_presigned_url(reg["pdf_assinado_r2_key"],download_name=f"Contrato_SIGEU_{dados.get('ra') or contrato_id}.pdf"))
    # Compatibilidade com contratos antigos ainda não migrados.
    if reg and reg.get("pdf_assinado") is not None:
        return send_file(BytesIO(bytes(reg["pdf_assinado"])),mimetype="application/pdf",as_attachment=False,download_name=f"Contrato_SIGEU_{dados.get('ra') or contrato_id}.pdf")
    key,_=gerar_pdf_contrato_assinado(contrato_id,salvar=True)
    return redirect(r2_presigned_url(key,download_name=f"Contrato_SIGEU_{dados.get('ra') or contrato_id}.pdf"))


# ============================================
# MATRÍCULA PÚBLICA POR DISCIPLINA
# Home -> confirmação acadêmica -> prévia do plano -> contratação -> Mercado Pago
# -> documentos privados no R2 -> conferência MEW -> liberação.
# ============================================

_PUBLIC_AI_HITS = {}
_PUBLIC_AI_HITS_LOCK = threading.Lock()

def _consumir_limite_ia_publica():
    """Proteção simples contra abuso da IA pública: até 20 chamadas por IP a cada 10 min."""
    ip = (request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For") or request.remote_addr or "desconhecido").split(",")[0].strip()
    agora = time.time()
    janela = 600
    limite = 20
    with _PUBLIC_AI_HITS_LOCK:
        recentes = [t for t in _PUBLIC_AI_HITS.get(ip, []) if agora - t < janela]
        if len(recentes) >= limite:
            _PUBLIC_AI_HITS[ip] = recentes
            return False
        recentes.append(agora)
        _PUBLIC_AI_HITS[ip] = recentes
    return True

def _limpar_texto_publico(valor, max_len=4000):
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", str(valor or ""))
    texto = texto.replace("<", " ").replace(">", " ")
    return re.sub(r"[ \t]+", " ", texto).strip()[:max_len]

def _sanitizar_conteudo_plano_publico(conteudo):
    """Preserva a estrutura JSON do plano e neutraliza HTML/control chars em cada texto."""
    def limpar(valor):
        if isinstance(valor, dict):
            return {str(k): limpar(v) for k, v in valor.items()}
        if isinstance(valor, (list, tuple)):
            return [limpar(v) for v in valor]
        texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", str(valor or ""))
        texto = re.sub(r"<[^>]*>", " ", texto)
        return re.sub(r"[ \t]+", " ", texto).strip()[:12000]
    return limpar(conteudo or {})

def _preco_disciplina_publica(carga_horaria):
    try:
        carga = int(carga_horaria)
    except Exception:
        return None
    if carga not in (60, 80, 120):
        return None
    bruto = (os.getenv(f"DISCIPLINA_PRECO_{carga}") or "").strip()
    if not bruto:
        return None
    try:
        if "," in bruto:
            bruto = bruto.replace(".", "").replace(",", ".")
        return round(float(bruto), 2)
    except Exception:
        return None


def _moeda_br(valor):
    if valor is None:
        return "Valor não configurado"
    return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pedido_token_publico(sol):
    return str((sol or {}).get("pedido_token") or (sol or {}).get("token") or "").strip()


def _itens_pedido_publico(sol=None, token=None):
    if sol is None:
        sol = _get_solicitacao_publica(token=token)
    if not sol:
        return []
    pedido = _pedido_token_publico(sol)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM solicitacoes_matricula_publica WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s ORDER BY COALESCE(item_ordem,id),id",
            (pedido,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def _preco_total_pedido_publico(itens):
    total = 0.0
    faltantes = []
    for item in itens or []:
        preco = _preco_disciplina_publica(item.get("carga_horaria"))
        if not preco or preco <= 0:
            faltantes.append(int(item.get("carga_horaria") or 0))
        else:
            total += float(preco)
    return (round(total, 2) if not faltantes else None), sorted(set(faltantes))


def _formatar_docente_publico(nome, titulacao=None):
    """Formata um docente REAL já cadastrado no MEW."""
    nome = _limpar_texto_publico(nome, 180)
    titulo = _limpar_texto_publico(titulacao, 120).lower()
    if not nome:
        return "Docente não cadastrado"
    if re.match(r"^prof(?:a|essor|essora)?\.?\s", nome, re.I):
        return nome
    if "dout" in titulo or titulo in {"dr", "dr."}:
        prefixo = "Prof. Dr."
    elif "mestr" in titulo or titulo in {"me", "me."}:
        prefixo = "Prof. Me."
    elif "espec" in titulo or "pós" in titulo or "pos" in titulo:
        prefixo = "Prof. Esp."
    else:
        prefixo = "Prof."
    return f"{prefixo} {nome}"


def _selecionar_docente_publico(cursor, pedido_token, disciplina_nome=None, carga_horaria=None, aluno_id=None):
    """Seleciona automaticamente entre docentes reais/ativos, evitando repetição no mesmo pedido."""
    # Se a disciplina já existe e tem docente, preserva a associação institucional existente.
    if disciplina_nome:
        cursor.execute(
            """SELECT doc.id,doc.nome,doc.titulacao
               FROM disciplinas d
               JOIN LATERAL (
                   SELECT dd.docente_id FROM disciplina_docente dd
                   WHERE dd.disciplina_id=d.id ORDER BY dd.id DESC LIMIT 1
               ) ult ON TRUE
               JOIN docentes doc ON doc.id=ult.docente_id
               WHERE LOWER(TRIM(d.nome))=LOWER(TRIM(%s))
                 AND COALESCE(d.carga_horaria,80)=%s
                 AND COALESCE(doc.ativo,1)=1
               ORDER BY d.id LIMIT 1""",
            (disciplina_nome, int(carga_horaria or 80)),
        )
        existente = cursor.fetchone()
        if existente:
            return existente["id"], _formatar_docente_publico(existente["nome"], existente.get("titulacao"))

    excluidos = set()
    if pedido_token:
        cursor.execute(
            "SELECT DISTINCT docente_id FROM solicitacoes_matricula_publica WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s AND docente_id IS NOT NULL",
            (pedido_token,),
        )
        excluidos.update(int(r["docente_id"]) for r in cursor.fetchall() if r.get("docente_id"))
    if aluno_id:
        cursor.execute(
            """SELECT DISTINCT dd.docente_id
               FROM aluno_disciplina ad
               JOIN disciplina_docente dd ON dd.disciplina_id=ad.disciplina_id
               WHERE ad.aluno_id=%s""",
            (aluno_id,),
        )
        excluidos.update(int(r["docente_id"]) for r in cursor.fetchall() if r.get("docente_id"))

    cursor.execute(
        """SELECT d.id,d.nome,d.titulacao
           FROM docentes d
           WHERE COALESCE(d.ativo,1)=1 AND NULLIF(TRIM(d.nome),'') IS NOT NULL
           ORDER BY RANDOM()"""
    )
    candidatos = cursor.fetchall()
    escolhido = next((r for r in candidatos if int(r["id"]) not in excluidos), None)
    if escolhido is None and candidatos:
        escolhido = candidatos[0]
    if not escolhido:
        return None, "Docente não cadastrado"
    return escolhido["id"], _formatar_docente_publico(escolhido["nome"], escolhido.get("titulacao"))


def _resumo_pedido_publico(token):
    sol = _get_solicitacao_publica(token=token)
    if not sol:
        return None, [], None, []
    itens = _itens_pedido_publico(sol=sol)
    total, faltantes = _preco_total_pedido_publico(itens)
    return sol, itens, total, faltantes


def _termo_contratacao_publica():
    return """Ao prosseguir, declaro que li e concordo com as condições da contratação das unidades curriculares selecionadas neste pedido. Estou ciente de que a matrícula administrativa, a organização, a execução e o acompanhamento acadêmico e operacional dos serviços contratados são realizados pelo GRUPO EDUCACIONAL UNIFICADO, por meio do SIGEU Educacional. A FACULDADE DO CENTRO OESTE PAULISTA LTDA. (FACOP) atua como FACOP CERTIFICADORA nos termos da parceria aplicável, realizando certificação e/ou emissão dos documentos acadêmicos que lhe couberem, quando aplicável e após o cumprimento dos requisitos acadêmicos, documentais e legais.

A contratação somente produz liberação acadêmica após a confirmação do pagamento e a conferência da documentação enviada. O prazo informado para conferência documental é de até 3 horas após o envio completo, e o início das unidades curriculares será disponibilizado em até 24 horas após a aprovação e liberação acadêmica.

Declaro que os dados pessoais e documentos apresentados são verdadeiros. Ao marcar a caixa de aceite e prosseguir para o pagamento, manifesto minha concordância livre, expressa e inequívoca com estas condições. O contrato acadêmico individual e seus registros eletrônicos serão disponibilizados no fluxo do SIGEU conforme a liberação da matrícula."""


def _get_solicitacao_publica(token=None, solicitacao_id=None):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if solicitacao_id is not None:
            cur.execute("SELECT * FROM solicitacoes_matricula_publica WHERE id=%s", (int(solicitacao_id),))
        else:
            cur.execute("SELECT * FROM solicitacoes_matricula_publica WHERE token=%s", (str(token or ""),))
        return cur.fetchone()
    finally:
        conn.close()


def _solicitacao_publica_por_cobranca(cobranca_id):
    if not cobranca_id:
        return None
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM solicitacoes_matricula_publica WHERE cobranca_id=%s ORDER BY id DESC LIMIT 1", (int(cobranca_id),))
        return cur.fetchone()
    finally:
        conn.close()


def _normalizar_disciplina_publica_ia(disciplina, curso_area=None):
    from openai import OpenAI
    disciplina = _limpar_texto_publico(disciplina, 180)
    curso_area = _limpar_texto_publico(curso_area, 180)
    if len(disciplina) < 2:
        raise ValueError("Informe o nome da disciplina.")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY não configurada no Render.")
    modelo = os.getenv("OPENAI_DISCIPLINA_MODEL") or os.getenv("OPENAI_PLANOS_MODEL", "gpt-5.6-terra")
    client = OpenAI(api_key=api_key)
    if curso_area:
        pedido = f'''Padronize academicamente o nome de uma unidade curricular brasileira sem alterar o seu campo de conhecimento.
Entrada do interessado: "{disciplina}". Curso/área informado: "{curso_area}".
Retorne SOMENTE JSON válido com: nome_sugerido, curso_area, departamento_sugerido, ementa_base.
- nome_sugerido: nomenclatura acadêmica curta e convencional em português.
- curso_area: preserve o curso/área informado, apenas corrigindo grafia quando necessário.
- departamento_sugerido: rótulo amplo e técnico, por exemplo "Departamento de Ciências e Engenharias".
- ementa_base: 4 a 6 frases objetivas cobrindo o núcleo da disciplina, suficiente para gerar um plano de ensino.
Não cite instituição, MEC, reconhecimento, autorização ou certificação. Não invente fatos administrativos.'''
    else:
        pedido = f'''Padronize academicamente o título da disciplina brasileira escrita como "{disciplina}".
Retorne SOMENTE JSON válido com as chaves nome_sugerido e pergunta_curso.
A pergunta_curso deve ser exatamente no sentido de: "Sua disciplina é [nome]. De qual curso ou área ela faz parte?".
Não acrescente instituição, grau, modalidade ou carga horária.'''
    resp = client.responses.create(
        model=modelo,
        input=[
            {"role": "system", "content": "Responda somente JSON válido, sem markdown. Normalize nomenclatura acadêmica com cautela e nunca invente dados institucionais."},
            {"role": "user", "content": pedido},
        ],
    )
    texto = (getattr(resp, "output_text", "") or "").strip()
    if texto.startswith("```"):
        texto = texto.strip("`").strip()
        if texto.lower().startswith("json"):
            texto = texto[4:].strip()
    ini, fim = texto.find("{"), texto.rfind("}")
    if ini >= 0 and fim > ini:
        texto = texto[ini:fim + 1]
    dados = json.loads(texto)
    nome = _limpar_texto_publico(dados.get("nome_sugerido") or disciplina, 180)
    dados["nome_sugerido"] = nome
    if dados.get("curso_area") is not None:
        dados["curso_area"] = _limpar_texto_publico(dados.get("curso_area"), 180)
    if dados.get("departamento_sugerido") is not None:
        dados["departamento_sugerido"] = _limpar_texto_publico(dados.get("departamento_sugerido"), 180)
    if dados.get("ementa_base") is not None:
        dados["ementa_base"] = _limpar_texto_publico(dados.get("ementa_base"), 4000)
    return dados


def _buscar_disciplina_catalogo(nome):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id,nome,COALESCE(carga_horaria,80) AS carga_horaria FROM disciplinas WHERE LOWER(TRIM(nome))=LOWER(TRIM(%s)) LIMIT 1",
            (nome,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def _plano_catalogo_atual(disciplina_id):
    if not disciplina_id:
        return None
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id,COALESCE(codigo,codigo_autenticacao) AS codigo,conteudo_html,data_emissao
            FROM documentos_autenticados
            WHERE disciplina_id=%s AND COALESCE(tipo,tipo_documento)='plano_ensino'
            ORDER BY id DESC LIMIT 1
        """, (disciplina_id,))
        return cur.fetchone()
    finally:
        conn.close()


@app.route("/api/publico/sugerir-disciplina", methods=["POST"])
def api_publico_sugerir_disciplina():
    if not _consumir_limite_ia_publica():
        return jsonify({"success": False, "message": "Muitas consultas em pouco tempo. Aguarde alguns minutos e tente novamente."}), 429
    try:
        dados = request.get_json(silent=True) or {}
        resultado = _normalizar_disciplina_publica_ia(dados.get("disciplina"))
        match = _buscar_disciplina_catalogo(resultado["nome_sugerido"])
        plano = _plano_catalogo_atual(match.get("id") if match else None)
        return jsonify({
            "success": True,
            "nome_sugerido": resultado["nome_sugerido"],
            "pergunta_curso": resultado.get("pergunta_curso") or f"Sua disciplina é {resultado['nome_sugerido']}. De qual curso ou área ela faz parte?",
            "catalogo": dict(match) if match else None,
            "plano_existente": bool(plano),
            "mensagem_plano": ("Esta disciplina já possui plano de ensino institucional. Se a ementa disponível atender ao que você procura, usaremos esse plano. Se desejar outra abordagem, informe abaixo os conteúdos esperados ou específicos." if plano else "A disciplina será consultada na base institucional e, quando necessário, terá o plano preparado conforme a ementa informada."),
        })
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 400


@app.route("/api/publico/gerar-previa-plano", methods=["POST"])
def api_publico_gerar_previa_plano():
    if not _consumir_limite_ia_publica():
        return jsonify({"success": False, "message": "Muitas gerações em pouco tempo. Aguarde alguns minutos e tente novamente."}), 429
    try:
        dados = request.get_json(silent=True) or {}
        disciplina_informada = (dados.get("disciplina") or "").strip()
        curso_area = (dados.get("curso_area") or "").strip()
        conteudos_esperados = _limpar_texto_publico(dados.get("conteudos_esperados"), 3500)
        pedido_recebido = _limpar_texto_publico(dados.get("pedido_token"), 160)
        carga = int(dados.get("carga_horaria") or 0)
        if carga not in (60, 80, 120):
            return jsonify({"success": False, "message": "Escolha 60, 80 ou 120 horas."}), 400
        if not disciplina_informada or not curso_area:
            return jsonify({"success": False, "message": "Informe a disciplina e o curso/área."}), 400

        normalizado = _normalizar_disciplina_publica_ia(disciplina_informada, curso_area)
        nome = normalizado["nome_sugerido"]
        ementa = str(normalizado.get("ementa_base") or "").strip()
        departamento = str(normalizado.get("departamento_sugerido") or curso_area).strip()
        if not ementa:
            raise ValueError("Não foi possível preparar a ementa-base da disciplina.")

        catalogo = _buscar_disciplina_catalogo(nome)
        if catalogo and int(catalogo.get("carga_horaria") or 80) != carga:
            catalogo = None
        plano_existente = _plano_catalogo_atual(catalogo.get("id") if catalogo else None)

        # Pedido/carrinho: várias disciplinas podem ser reunidas antes do checkout.
        conn = get_db_connection(); cur = conn.cursor()
        try:
            if pedido_recebido:
                cur.execute(
                    "SELECT * FROM solicitacoes_matricula_publica WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s ORDER BY id",
                    (pedido_recebido,),
                )
                existentes = cur.fetchall()
                if not existentes:
                    raise ValueError("O pedido informado não foi encontrado. Inicie uma nova seleção.")
                if len(existentes) >= 30:
                    raise ValueError("Este pedido já atingiu o limite de 30 disciplinas.")
                if any(r.get("cobranca_id") or r.get("data_aceite") or r.get("data_pagamento") for r in existentes):
                    raise ValueError("Este pedido já entrou na etapa de contratação e não aceita novas disciplinas.")
                pedido_token = _pedido_token_publico(existentes[0])
                item_ordem = max(int(r.get("item_ordem") or 0) for r in existentes) + 1
                if any((r.get("disciplina_confirmada") or "").strip().lower() == nome.strip().lower() and int(r.get("carga_horaria") or 0) == carga for r in existentes):
                    raise ValueError("Essa disciplina com a mesma carga horária já está na sua seleção.")
            else:
                pedido_token = "PED-" + secrets.token_urlsafe(18)
                item_ordem = 1
            docente_id, docente_nome = _selecionar_docente_publico(cur, pedido_token, nome, carga)
        finally:
            conn.close()

        token = secrets.token_urlsafe(24)
        plano_dados_salvos = {}
        if plano_existente and not conteudos_esperados:
            # Reaproveita o plano institucional já existente. A cópia abaixo é apenas a prévia pública.
            html_plano = str(plano_existente.get("conteudo_html") or "")
            if not html_plano:
                plano_existente = None

        if not plano_existente or conteudos_esperados:
            from api_planos import consultar_openai_para_plano
            ementa_para_ia = ementa
            if conteudos_esperados:
                ementa_para_ia += "\n\nCONFORME SUA EMENTA, priorize e distribua os seguintes conteúdos esperados ou específicos: " + conteudos_esperados
            conteudo_ia = consultar_openai_para_plano({
                "disciplina": nome,
                "ementa": ementa_para_ia,
                "carga_horaria": f"{carga} horas",
            })
            dados_html = _sanitizar_conteudo_plano_publico(conteudo_ia)
            plano_dados_salvos = dict(dados_html)
            modalidade = _limpar_texto_publico(dados_html.pop("modalidade", None) or "EaD", 40)
            plano_dados_salvos["modalidade"] = modalidade
            numero_unidades = max(6, min(8, int(plano_dados_salvos.get("numero_unidades") or 6)))
            plano_dados_salvos["numero_unidades"] = numero_unidades
            dados_html.pop("numero_unidades", None)
            codigo = f"PREVIA-{secrets.token_hex(5).upper()}"
            hash_doc = hashlib.sha256(f"{token}|{nome}|{carga}|{conteudos_esperados}".encode("utf-8")).hexdigest()
            base_url = request.host_url.rstrip("/")
            qr = gerar_qrcode_base64(f"{base_url}/matricula/{token}")
            html_plano = gerar_html_plano_ensino(
                disciplina=nome.upper(), codigo=codigo, hash_completa=hash_doc,
                carga_horaria=f"{carga} horas", modalidade=modalidade, docente=docente_nome,
                data_formatada=datetime.now().strftime("%d/%m/%Y"), qr_code_base64=qr,
                numero_unidades=numero_unidades, **dados_html,
            )

        aviso = "<style>.sigeu-previa-aviso{position:fixed;top:8px;left:50%;transform:translateX(-50%);z-index:99999;background:#fff;border:1px solid #111;color:#111;padding:6px 12px;font:700 10px Arial;letter-spacing:.6px}@media print{.sigeu-previa-aviso{display:block}}</style>"
        html_plano = html_plano.replace("</head>", aviso + "<meta name='robots' content='noindex,nofollow'></head>", 1)
        html_plano = html_plano.replace("<body>", "<body><div class='sigeu-previa-aviso'>PRÉVIA DE PLANO DE ENSINO • SEM VALIDADE ACADÊMICA</div>", 1)

        agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute(
            """INSERT INTO solicitacoes_matricula_publica
            (token,pedido_token,item_ordem,status,disciplina_digitada,disciplina_confirmada,curso_area,departamento,carga_horaria,ementa_sugerida,plano_html,plano_dados_json,valor_total,docente_id,docente_nome,disciplina_id,plano_documento_id,data_criacao,data_plano)
            VALUES(%s,%s,%s,'previa',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (token,pedido_token,item_ordem,disciplina_informada,nome,normalizado.get("curso_area") or curso_area,departamento,carga,
             (ementa + (("\n\nConteúdos esperados/específicos: " + conteudos_esperados) if conteudos_esperados else "")),
             html_plano,json.dumps(plano_dados_salvos,ensure_ascii=False),_preco_disciplina_publica(carga),docente_id,docente_nome,
             catalogo.get("id") if catalogo else None,(plano_existente.get("id") if (plano_existente and not conteudos_esperados) else None),agora,agora),
        )
        solicitacao_id = cur.fetchone()["id"]
        conn.commit(); conn.close()
        return jsonify({
            "success": True, "id": solicitacao_id, "token": token, "pedido_token": pedido_token,
            "nome_confirmado": nome, "curso_area": normalizado.get("curso_area") or curso_area,
            "departamento": departamento, "carga_horaria": carga, "docente": docente_nome,
            "plano_reutilizado": bool(plano_existente and not conteudos_esperados),
            "url": url_for("matricula_publica_resumo", token=token),
        })
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/matricula/<token>")
def matricula_publica_resumo(token):
    sol, itens, total, faltantes = _resumo_pedido_publico(token)
    if not sol:
        return "Solicitação não encontrada.", 404
    itens_view = []
    for item in itens:
        d = dict(item)
        preco = _preco_disciplina_publica(d.get("carga_horaria"))
        d["preco"] = preco
        d["preco_txt"] = _moeda_br(preco)
        itens_view.append(d)
    return render_template(
        "matricula_publica_resumo.html",
        sol=sol, itens=itens_view, total=total, total_txt=_moeda_br(total), faltantes=faltantes,
        pedido_token=_pedido_token_publico(sol),
    )


@app.route("/matricula/<token>/finalizar", methods=["GET", "POST"])
def matricula_publica_finalizar(token):
    sol, itens, total, faltantes = _resumo_pedido_publico(token)
    if not sol:
        return "Solicitação não encontrada.", 404
    if any(i.get("cobranca_id") or i.get("data_pagamento") for i in itens):
        return redirect(url_for("matricula_publica_contratar", token=token))
    if request.method == "POST":
        quer = (request.form.get("quer_extra") or "0").strip()
        texto = _limpar_texto_publico(request.form.get("solicitacao_extra"), 3000) if quer == "1" else ""
        if quer == "1" and len(texto) < 3:
            return render_template("matricula_publica_finalizar.html", sol=sol, itens=itens, total=total, total_txt=_moeda_br(total), faltantes=faltantes, erro="Conte brevemente o que você gostaria de solicitar."), 400
        agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        pedido = _pedido_token_publico(sol)
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute(
            "UPDATE solicitacoes_matricula_publica SET solicitacao_extra=%s,solicitacao_extra_respondida=TRUE,data_solicitacao_extra=%s WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s",
            (texto, agora, pedido),
        )
        conn.commit(); conn.close()
        return redirect(url_for("matricula_publica_contratar", token=token))
    return render_template("matricula_publica_finalizar.html", sol=sol, itens=itens, total=total, total_txt=_moeda_br(total), faltantes=faltantes)


@app.route("/matricula/<token>/plano")
def matricula_publica_plano(token):
    sol = _get_solicitacao_publica(token=token)
    if not sol or not sol.get("plano_html"):
        return "Prévia não encontrada.", 404
    resp = app.make_response(sol["plano_html"])
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


def _criar_aluno_pendente_publico(sol, form):
    nome = (form.get("nome") or "").strip()
    email = (form.get("email") or "").strip().lower()
    cpf = re.sub(r"\D", "", form.get("cpf") or "")
    telefone = (form.get("telefone") or "").strip()
    endereco = (form.get("endereco") or "").strip()
    cidade = (form.get("cidade") or "").strip()
    estado = (form.get("estado") or "").strip().upper()[:2]
    cep = (form.get("cep") or "").strip()
    if not nome or "@" not in email or len(cpf) != 11 or not telefone or not endereco or not cidade or len(estado) != 2 or not cep:
        raise ValueError("Preencha corretamente nome, CPF, e-mail, telefone e endereço.")
    if form.get("aceite_termos") != "1":
        raise ValueError("É necessário ler e aceitar os termos da contratação.")

    itens = _itens_pedido_publico(sol=sol)
    total, faltantes = _preco_total_pedido_publico(itens)
    if not total or total <= 0:
        if faltantes:
            raise ValueError("Há carga horária sem preço configurado no Render: " + ", ".join(f"{x}h" for x in faltantes) + ".")
        raise ValueError("O valor do pedido ainda não está configurado.")
    pedido = _pedido_token_publico(sol)

    conn = get_db_connection(); cur = conn.cursor()
    try:
        # O carrinho reúne várias disciplinas em uma única matrícula nova.
        # Matrículas já existentes continuam pelo atendimento/ambiente autenticado, evitando duplicidade de CPF.
        cur.execute(
            """SELECT a.id FROM dados_pessoais dp JOIN alunos a ON a.id=dp.aluno_id
               WHERE regexp_replace(COALESCE(dp.cpf,''),'[^0-9]','','g')=%s LIMIT 1""",
            (cpf,),
        )
        if cur.fetchone():
            raise ValueError("Já existe cadastro com este CPF. Para uma nova contratação em matrícula existente, utilize o atendimento do SIGEU ou seu ambiente acadêmico.")
        while True:
            ra = gerar_ra()
            cur.execute("SELECT id FROM alunos WHERE ra=%s", (ra,))
            if not cur.fetchone():
                break
        cur.execute("INSERT INTO alunos(nome,email,ra,senha) VALUES(%s,%s,%s,%s) RETURNING id", (nome, email, ra, generate_password_hash(cpf)))
        aluno_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO dados_pessoais(aluno_id,cpf,telefone,endereco,cidade,estado,cep,curso_referencia) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (aluno_id, cpf, telefone, endereco, cidade, estado, cep, sol.get("curso_area") or sol.get("departamento") or ""),
        )

        cur.execute(
            "INSERT INTO situacao_financeira(aluno_id,forma_pagamento,status,parcelas_total,parcelas_pagas,valor_total) VALUES(%s,'mercadopago','pendente',1,0,%s)",
            (aluno_id, total),
        )
        agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        cur.execute(
            """UPDATE solicitacoes_matricula_publica
               SET status='aguardando_pagamento',nome=%s,email=%s,cpf=%s,telefone=%s,endereco=%s,cidade=%s,estado=%s,cep=%s,
                   aluno_id=%s,aceite_termos=TRUE,data_aceite=%s
               WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s""",
            (nome, email, cpf, telefone, endereco, cidade, estado, cep, aluno_id, agora, pedido),
        )
        conn.commit()
        extra = next((i.get("solicitacao_extra") for i in itens if i.get("solicitacao_extra")), "")
        if extra:
            try:
                destino = (os.getenv("SIGEU_ADMIN_NOTIFICATION_EMAIL") or "claroevandro95@gmail.com").strip()
                lista = "<br>".join(f"• {escape(i.get('disciplina_confirmada') or '')} — {int(i.get('carga_horaria') or 0)}h" for i in itens)
                html = f"<html><body style='font-family:Arial;color:#202428'><h2>Solicitação adicional antes do pagamento</h2><p><b>Interessado:</b> {escape(nome)}<br><b>E-mail:</b> {escape(email)}<br><b>Telefone:</b> {escape(telefone)}</p><p><b>Disciplinas selecionadas:</b><br>{lista}</p><p><b>Pedido adicional:</b><br>{escape(extra)}</p><p>O interessado seguirá agora para o Mercado Pago.</p></body></html>"
                _smtp_enviar(destino, f"SIGEU | Solicitação adicional - {nome}", html)
            except Exception as exc:
                print(f"Aviso e-mail solicitação adicional: {exc}")
        return aluno_id, nome, email, total
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.route("/matricula/<token>/contratar", methods=["GET", "POST"])
def matricula_publica_contratar(token):
    sol, itens, total, faltantes = _resumo_pedido_publico(token)
    if not sol:
        return "Solicitação não encontrada.", 404
    pedido = _pedido_token_publico(sol)
    if not all(bool(i.get("solicitacao_extra_respondida")) for i in itens):
        return redirect(url_for("matricula_publica_finalizar", token=token))

    itens_view = []
    for item in itens:
        d = dict(item)
        d["preco"] = _preco_disciplina_publica(d.get("carga_horaria"))
        d["preco_txt"] = _moeda_br(d["preco"])
        itens_view.append(d)

    if request.method == "GET":
        return render_template("matricula_publica_contratar.html", sol=sol, itens=itens_view, preco=total, preco_txt=_moeda_br(total), faltantes=faltantes, termos=_termo_contratacao_publica())
    try:
        cobranca_id_existente = next((i.get("cobranca_id") for i in itens if i.get("cobranca_id")), None)
        if cobranca_id_existente:
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute("SELECT checkout_url,sandbox_checkout_url FROM pagamentos_mercadopago WHERE id=%s", (cobranca_id_existente,))
            cob = cur.fetchone(); conn.close()
            if cob:
                checkout = (cob.get("sandbox_checkout_url") if str(os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")).startswith("TEST-") else cob.get("checkout_url")) or cob.get("checkout_url") or cob.get("sandbox_checkout_url")
                if checkout:
                    return redirect(checkout)

        aluno_id_existente = next((i.get("aluno_id") for i in itens if i.get("aluno_id")), None)
        if aluno_id_existente:
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute("SELECT id,nome,email FROM alunos WHERE id=%s", (aluno_id_existente,))
            aluno_existente = cur.fetchone(); conn.close()
            if not aluno_existente:
                raise ValueError("Cadastro pendente não encontrado. Procure o atendimento do SIGEU.")
            aluno_id = aluno_existente["id"]; nome = aluno_existente["nome"]; email = aluno_existente["email"]
            total, faltantes = _preco_total_pedido_publico(itens)
            if not total:
                raise ValueError("Há carga horária sem preço configurado no Render.")
        else:
            aluno_id, nome, email, total = _criar_aluno_pendente_publico(sol, request.form)

        titulo = (f"{len(itens)} unidades curriculares SIGEU" if len(itens) > 1 else f"Unidade Curricular: {itens[0]['disciplina_confirmada']} - {int(itens[0]['carga_horaria'])}h")
        cobranca = criar_preferencia_mercadopago(
            aluno_id=aluno_id,
            nome=nome,
            email=email,
            valor_total=total,
            contrato_id=None,
            base_url=request.host_url.rstrip("/"),
            item_title=titulo,
            metadata_extra={"solicitacao_matricula_id": sol["id"], "pedido_token": pedido, "tipo": "matricula_publica"},
        )
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute(
            "UPDATE solicitacoes_matricula_publica SET cobranca_id=%s WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s",
            (cobranca["id"], pedido),
        )
        conn.commit(); conn.close()
        return redirect(cobranca["checkout_url"])
    except Exception as exc:
        return render_template("matricula_publica_contratar.html", sol=sol, itens=itens_view, preco=total, preco_txt=_moeda_br(total), faltantes=faltantes, termos=_termo_contratacao_publica(), erro=str(exc)), 400


def _token_solicitacao_publica_retorno_mp():
    external = (request.args.get("external_reference") or "").strip()
    pref = (request.args.get("preference_id") or "").strip()
    if not external and not pref:
        return None
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if external:
            cur.execute("SELECT s.token FROM solicitacoes_matricula_publica s JOIN pagamentos_mercadopago p ON p.id=s.cobranca_id WHERE p.external_reference=%s LIMIT 1", (external,))
        else:
            cur.execute("SELECT s.token FROM solicitacoes_matricula_publica s JOIN pagamentos_mercadopago p ON p.id=s.cobranca_id WHERE p.preference_id=%s LIMIT 1", (pref,))
        row = cur.fetchone()
        return row.get("token") if row else None
    finally:
        conn.close()


def _assegurar_disciplina_e_plano_publico(solicitacao_id):
    """Cria/vincula a disciplina e garante um único plano institucional corrente por disciplina."""
    sol = _get_solicitacao_publica(solicitacao_id=solicitacao_id)
    if not sol:
        return None, None

    nome = (sol.get("disciplina_confirmada") or sol.get("disciplina_digitada") or "").strip()
    carga = int(sol.get("carga_horaria") or 80)
    if not nome:
        raise ValueError("Solicitação sem nome de disciplina confirmado.")

    try:
        dados_plano = json.loads(sol.get("plano_dados_json") or "{}")
        if not isinstance(dados_plano, dict):
            dados_plano = {}
    except Exception:
        dados_plano = {}

    conn = get_db_connection(); cur = conn.cursor()
    disciplina_id = sol.get("disciplina_id")
    plano_documento_id = sol.get("plano_documento_id")
    try:
        if disciplina_id:
            cur.execute("SELECT id FROM disciplinas WHERE id=%s", (disciplina_id,))
            if not cur.fetchone():
                disciplina_id = None

        if not disciplina_id:
            cur.execute(
                "SELECT id FROM disciplinas WHERE LOWER(TRIM(nome))=LOWER(TRIM(%s)) AND COALESCE(carga_horaria,80)=%s ORDER BY id LIMIT 1",
                (nome, carga),
            )
            disc = cur.fetchone()
            if disc:
                disciplina_id = disc["id"]
            else:
                cur.execute("INSERT INTO disciplinas (nome,carga_horaria) VALUES(%s,%s) RETURNING id", (nome, carga))
                disciplina_id = cur.fetchone()["id"]
                for i in range(1, 5):
                    cur.execute(
                        "INSERT INTO capitulos (disciplina_id,titulo,video_url,pdf_url) VALUES(%s,%s,'','') RETURNING id",
                        (disciplina_id, f"Capítulo {i}"),
                    )
                    capitulo_id = cur.fetchone()["id"]
                    cur.execute("INSERT INTO provas (capitulo_id,questoes_json) VALUES(%s,'[]')", (capitulo_id,))
            cur.execute("UPDATE solicitacoes_matricula_publica SET disciplina_id=%s WHERE id=%s", (disciplina_id, solicitacao_id))

        # Sempre usa um docente real já vinculado; se faltar, sorteia um docente ativo do MEW e grava o vínculo.
        docente_nome = _docente_documental_disciplina(cur, disciplina_id, nome)
        cur.execute("""
            SELECT dd.docente_id FROM disciplina_docente dd
            JOIN docentes d ON d.id=dd.docente_id
            WHERE dd.disciplina_id=%s AND COALESCE(d.ativo,1)=1
            ORDER BY dd.id DESC LIMIT 1
        """, (disciplina_id,))
        drow = cur.fetchone()
        docente_id = drow.get("docente_id") if drow else None
        cur.execute(
            "UPDATE solicitacoes_matricula_publica SET disciplina_id=%s,docente_id=%s,docente_nome=%s WHERE id=%s",
            (disciplina_id, docente_id, docente_nome, solicitacao_id),
        )

        # Plano alternativo solicitado: substitui o plano corrente da disciplina, nunca cria duplicata lógica.
        if dados_plano:
            modalidade = _limpar_texto_publico(dados_plano.pop("modalidade", None) or "EaD", 40)
            try:
                numero_unidades = max(6, min(8, int(dados_plano.pop("numero_unidades", 6) or 6)))
            except Exception:
                numero_unidades = 6
            codigo = gerar_codigo_simples()
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            hash_documento = gerar_hash_documento(f"plano_publico_{solicitacao_id}_{disciplina_id}_{timestamp}", "PUBLICO", timestamp)
            data_formatada = datetime.now().strftime("%d/%m/%Y")
            data_emissao = datetime.now().strftime("%d/%m/%Y %H:%M")
            data_validade = (datetime.now() + timedelta(days=365 * 5)).strftime("%d/%m/%Y")
            base_url = (os.getenv("SIGEU_LOGIN_URL") or "").strip().rstrip("/") or request.host_url.rstrip("/")
            qr_code_base64 = gerar_qrcode_base64(f"{base_url}/validar-documento/{codigo}")
            metadados = criar_metadados_documento(None, "plano_ensino", codigo, hash_documento)
            html_oficial = gerar_html_plano_ensino(
                disciplina=nome.upper(), codigo=codigo, hash_completa=hash_documento,
                carga_horaria=f"{carga} horas", modalidade=modalidade, docente=docente_nome,
                data_formatada=data_formatada, qr_code_base64=qr_code_base64,
                numero_unidades=numero_unidades, **dados_plano,
            )
            cur.execute("""
                SELECT id FROM documentos_autenticados
                WHERE disciplina_id=%s AND COALESCE(tipo,tipo_documento)='plano_ensino'
                ORDER BY id DESC LIMIT 1
            """, (disciplina_id,))
            atual = cur.fetchone()
            if atual:
                plano_documento_id = atual["id"]
                cur.execute("""
                    UPDATE documentos_autenticados
                    SET codigo=%s,codigo_autenticacao=%s,conteudo_html=%s,data_geracao=%s,qr_code=%s,
                        hash_documento=%s,data_emissao=%s,data_validade=%s,metadados=%s,
                        tipo='plano_ensino',tipo_documento='plano_ensino',disciplina_id=%s
                    WHERE id=%s
                """, (codigo,codigo,html_oficial,data_emissao,qr_code_base64,hash_documento,
                      data_emissao,data_validade,metadados,disciplina_id,plano_documento_id))
            else:
                cur.execute("""
                    INSERT INTO documentos_autenticados
                    (codigo,codigo_autenticacao,aluno_id,aluno_nome,aluno_ra,tipo,tipo_documento,conteudo_html,data_geracao,
                     qr_code,hash_documento,data_emissao,data_validade,metadados,disciplina_id)
                    VALUES(%s,%s,NULL,'PLANO INSTITUCIONAL','GERAL','plano_ensino','plano_ensino',%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (codigo,codigo,html_oficial,data_emissao,qr_code_base64,hash_documento,
                      data_emissao,data_validade,metadados,disciplina_id))
                plano_documento_id = cur.fetchone()["id"]
        else:
            # Sem pedido de nova ementa: reutiliza o plano institucional existente, se houver.
            if plano_documento_id:
                cur.execute("SELECT id FROM documentos_autenticados WHERE id=%s AND COALESCE(tipo,tipo_documento)='plano_ensino'", (plano_documento_id,))
                if not cur.fetchone():
                    plano_documento_id = None
            if not plano_documento_id:
                cur.execute("""
                    SELECT id FROM documentos_autenticados
                    WHERE disciplina_id=%s AND COALESCE(tipo,tipo_documento)='plano_ensino'
                    ORDER BY id DESC LIMIT 1
                """, (disciplina_id,))
                atual = cur.fetchone()
                plano_documento_id = atual.get("id") if atual else None

        cur.execute(
            "UPDATE solicitacoes_matricula_publica SET disciplina_id=%s,plano_documento_id=%s,docente_id=%s,docente_nome=%s WHERE id=%s",
            (disciplina_id, plano_documento_id, docente_id, docente_nome, solicitacao_id),
        )
        conn.commit()
        return disciplina_id, plano_documento_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _marcar_solicitacao_publica_pago(solicitacao_id, payment_id=None):
    sol = _get_solicitacao_publica(solicitacao_id=solicitacao_id)
    if not sol:
        return False
    pedido = _pedido_token_publico(sol)
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute(
        "SELECT id,data_pagamento FROM solicitacoes_matricula_publica WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s FOR UPDATE",
        (pedido,),
    )
    rows = cur.fetchall()
    if not rows:
        conn.close(); return False
    novo_pagamento = not any(bool(r.get("data_pagamento")) for r in rows)
    cur.execute(
        """UPDATE solicitacoes_matricula_publica
           SET status=CASE WHEN status IN ('em_analise','documentos_aprovados','liberado') THEN status ELSE 'aguardando_documentos' END,
               data_pagamento=COALESCE(data_pagamento,%s)
           WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s""",
        (agora, pedido),
    )
    conn.commit(); conn.close()
    for row in rows:
        _assegurar_disciplina_e_plano_publico(row["id"])
    return novo_pagamento


def _sincronizar_pagamento_publico(token, payment_id):
    sol = _get_solicitacao_publica(token=token)
    if not sol or not sol.get("cobranca_id"):
        return False
    pedido = _pedido_token_publico(sol)
    sdk = get_mercadopago_sdk()
    resultado = sdk.payment().get(payment_id)
    pag = resultado.get("response", {}) if isinstance(resultado, dict) else {}
    if pag.get("status") != "approved":
        return False
    external = pag.get("external_reference")
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT external_reference FROM pagamentos_mercadopago WHERE id=%s", (sol["cobranca_id"],))
    cob = cur.fetchone()
    if not cob or not external or external != cob.get("external_reference"):
        conn.close(); return False
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    cur.execute(
        "UPDATE pagamentos_mercadopago SET payment_id=%s,status='pago',status_mp='approved',data_atualizacao=%s,data_pagamento=COALESCE(data_pagamento,%s) WHERE id=%s",
        (str(payment_id), agora, agora, sol["cobranca_id"]),
    )
    cur.execute(
        "UPDATE situacao_financeira SET status='pago',parcelas_pagas=parcelas_total WHERE id=(SELECT id FROM situacao_financeira WHERE aluno_id=%s ORDER BY id DESC LIMIT 1)",
        (sol["aluno_id"],),
    )
    cur.execute(
        "SELECT id,data_pagamento FROM solicitacoes_matricula_publica WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s",
        (pedido,),
    )
    rows = cur.fetchall()
    novo_pagamento = not any(bool(r.get("data_pagamento")) for r in rows)
    cur.execute(
        """UPDATE solicitacoes_matricula_publica SET status=CASE WHEN status IN ('em_analise','documentos_aprovados','liberado') THEN status ELSE 'aguardando_documentos' END,
           data_pagamento=COALESCE(data_pagamento,%s) WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s""",
        (agora, pedido),
    )
    conn.commit(); conn.close()
    for row in rows:
        _assegurar_disciplina_e_plano_publico(row["id"])
    if novo_pagamento:
        try:
            enviar_email_pagamento_publico(sol["id"])
            enviar_alerta_admin_matricula_publica(sol["id"], fase="pagamento")
        except Exception as exc:
            print(f"Aviso e-mail retorno MP: {exc}")
    return True


def _smtp_enviar(destinatario, assunto, html_corpo, texto_corpo=None):
    host = os.getenv("TITAN_SMTP_HOST", "smtp.titan.email").strip()
    usuario = (os.getenv("TITAN_SMTP_USER") or "").strip()
    senha = (os.getenv("TITAN_SMTP_PASSWORD") or "").strip()
    port = int(os.getenv("TITAN_SMTP_PORT", "465"))
    from_name = os.getenv("TITAN_FROM_NAME", "SIGEU Educacional").strip()
    if not usuario or not senha:
        return False
    from email.message import EmailMessage
    from email.utils import formataddr
    import smtplib
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = formataddr((from_name, usuario))
    msg["To"] = destinatario
    msg.set_content(texto_corpo or re.sub(r"<[^>]+>", " ", html_corpo))
    msg.add_alternative(html_corpo, subtype="html")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(usuario, senha)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(usuario, senha)
            smtp.send_message(msg)
    return True


def enviar_email_pagamento_publico(solicitacao_id):
    sol = _get_solicitacao_publica(solicitacao_id=solicitacao_id)
    if not sol or not sol.get("email"):
        return False
    itens = _itens_pedido_publico(sol=sol)
    base = ((os.getenv("SIGEU_LOGIN_URL") or request.host_url.rstrip("/")).split("/login")[0].rstrip("/")) if request else "https://campusvirtualfacop.com.br"
    link = f"{base}/matricula/{sol['token']}/documentos"
    lista = "".join(f"<li><b>{escape(i.get('disciplina_confirmada') or '')}</b> — {int(i.get('carga_horaria') or 0)}h — {escape(i.get('docente_nome') or 'Docente responsável')}</li>" for i in itens)
    html = f"""<html><body style='font-family:Arial;color:#1f2326'><h2>Pagamento confirmado</h2><p>Olá, <b>{escape(sol.get('nome') or '')}</b>.</p><p>Recebemos sua contratação das seguintes unidades curriculares:</p><ul>{lista}</ul><p>As disciplinas e seus planos de ensino já foram preparados no SIGEU. Agora envie a documentação para conferência cadastral e acadêmica.</p><p><a href='{link}'>Enviar documentação</a></p><p><b>Prazo de conferência:</b> até 3 horas após o envio completo.<br><b>Início das unidades curriculares:</b> em até 24 horas após a aprovação e liberação acadêmica.</p><p>GRUPO EDUCACIONAL UNIFICADO<br>SIGEU Educacional<br>FACOP CERTIFICADORA</p></body></html>"""
    return _smtp_enviar(sol["email"], "SIGEU | Pagamento confirmado", html)


def enviar_alerta_admin_matricula_publica(solicitacao_id, fase="documentos"):
    sol = _get_solicitacao_publica(solicitacao_id=solicitacao_id)
    if not sol:
        return False
    itens = _itens_pedido_publico(sol=sol)
    total, _ = _preco_total_pedido_publico(itens)
    destino = (os.getenv("SIGEU_ADMIN_NOTIFICATION_EMAIL") or "claroevandro95@gmail.com").strip()
    base = request.host_url.rstrip("/") if request else "https://campusvirtualfacop.com.br"
    etapa = "PAGAMENTO APROVADO" if fase == "pagamento" else "DOCUMENTAÇÃO ENVIADA"
    acao = "As disciplinas, planos e docentes já foram preparados automaticamente. Aguarde o envio dos documentos." if fase == "pagamento" else "Valide a documentação e clique em liberar. As disciplinas, planos e docentes já estão vinculados."
    lista = "".join(f"<li><b>{escape(i.get('disciplina_confirmada') or '')}</b> — {int(i.get('carga_horaria') or 0)}h — {escape(i.get('docente_nome') or 'Docente responsável')}</li>" for i in itens)
    extra = next((i.get("solicitacao_extra") for i in itens if i.get("solicitacao_extra")), "")
    extra_html = f"<p><b>Solicitação adicional:</b><br>{escape(extra)}</p>" if extra else ""
    html = f"""<html><body style='font-family:Arial;color:#202428'><h2>{etapa} — nova contratação SIGEU</h2><p><b>Aluno:</b> {escape(sol.get('nome') or '')}<br><b>E-mail:</b> {escape(sol.get('email') or '')}<br><b>Valor total:</b> {_moeda_br(total)}</p><p><b>Unidades curriculares:</b></p><ul>{lista}</ul>{extra_html}<p>{acao}</p><p><a href='{base}/mew/solicitacoes-matricula'>Abrir solicitações no MEW</a></p></body></html>"""
    return _smtp_enviar(destino, f"SIGEU | {etapa} - {len(itens)} disciplina(s)", html)


@app.route("/matricula/<token>/documentos", methods=["GET", "POST"])
def matricula_publica_documentos(token):
    sol = _get_solicitacao_publica(token=token)
    if not sol:
        return "Solicitação não encontrada.", 404
    itens = _itens_pedido_publico(sol=sol)
    pedido = _pedido_token_publico(sol)
    cobranca_id = next((i.get("cobranca_id") for i in itens if i.get("cobranca_id")), None)
    pago = False
    if cobranca_id:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT status FROM pagamentos_mercadopago WHERE id=%s", (cobranca_id,))
        cob = cur.fetchone(); conn.close()
        pago = bool(cob and cob.get("status") == "pago")
    if request.method == "POST":
        if not pago:
            return render_template("matricula_publica_documentos.html", sol=sol, itens=itens, pago=False, erro="O pagamento ainda está em confirmação."), 409
        if not r2_is_configured():
            return render_template("matricula_publica_documentos.html", sol=sol, itens=itens, pago=True, erro="Armazenamento R2 não configurado."), 500
        arquivos = [f for f in request.files.getlist("documentos") if f and f.filename]
        if not arquivos:
            return render_template("matricula_publica_documentos.html", sol=sol, itens=itens, pago=True, erro="Selecione pelo menos um documento."), 400
        if len(arquivos) > 8:
            return render_template("matricula_publica_documentos.html", sol=sol, itens=itens, pago=True, erro="Envie no máximo 8 arquivos por solicitação."), 400
        permitidas = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
        enviados = []
        for arq in arquivos:
            nome = secure_filename(arq.filename or "documento")
            ext = Path(nome).suffix.lower()
            if ext not in permitidas:
                return render_template("matricula_publica_documentos.html", sol=sol, itens=itens, pago=True, erro=f"Formato não permitido: {nome}. Use PDF, JPG, PNG ou WEBP."), 400
            ctype = arq.mimetype or guess_content_type(nome)
            key = make_key("matriculas/documentos", nome, pedido)
            r2_upload_fileobj(arq.stream, key, ctype, {"pedido_token": pedido, "aluno_id": sol.get("aluno_id") or ""})
            enviados.append({"nome": nome, "key": key, "content_type": ctype})
        agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        obs = (request.form.get("observacao") or "").strip()[:1500]
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute(
            """UPDATE solicitacoes_matricula_publica SET status='em_analise',documentos_json=%s,observacao_aluno=%s,data_documentos=%s
               WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s""",
            (json.dumps(enviados, ensure_ascii=False), obs, agora, pedido),
        )
        conn.commit(); conn.close()
        try:
            enviar_alerta_admin_matricula_publica(sol["id"], fase="documentos")
        except Exception as exc:
            print(f"Aviso e-mail documentos: {exc}")
        sol = _get_solicitacao_publica(token=token)
        itens = _itens_pedido_publico(sol=sol)
        return render_template("matricula_publica_documentos.html", sol=sol, itens=itens, pago=True, enviado=True)
    return render_template("matricula_publica_documentos.html", sol=sol, itens=itens, pago=pago)


@app.route("/mew/solicitacoes-matricula")
def mew_solicitacoes_matricula_publica():
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM solicitacoes_matricula_publica ORDER BY id DESC LIMIT 900")
    rows = cur.fetchall(); conn.close()

    grupos = {}
    ordem = []
    for r in rows:
        d = dict(r)
        chave = _pedido_token_publico(d)
        if chave not in grupos:
            grupos[chave] = []
            ordem.append(chave)
        grupos[chave].append(d)

    solicitacoes = []
    for chave in ordem:
        itens = sorted(grupos[chave], key=lambda x: (int(x.get("item_ordem") or x.get("id") or 0), int(x.get("id") or 0)))
        principal = dict(itens[0])
        docs = []
        for item in itens:
            try:
                cand = json.loads(item.get("documentos_json") or "[]")
            except Exception:
                cand = []
            if cand:
                docs = cand
                break
        for doc in docs:
            if doc.get("key"):
                doc["url"] = r2_presigned_url(doc["key"], download_name=doc.get("nome") or "documento", inline=True)
        total, _ = _preco_total_pedido_publico(itens)
        principal["documentos"] = docs
        principal["itens"] = itens
        principal["valor_txt"] = _moeda_br(total)
        principal["pedido_token"] = chave
        principal["solicitacao_extra"] = next((i.get("solicitacao_extra") for i in itens if i.get("solicitacao_extra")), "")
        # Datas/status são atualizados em bloco; usa o valor mais recente disponível.
        for campo in ("data_pagamento","data_documentos","data_aprovacao","data_liberacao","observacao_mew"):
            principal[campo] = next((i.get(campo) for i in reversed(itens) if i.get(campo)), principal.get(campo))
        principal["status"] = next((i.get("status") for i in reversed(itens) if i.get("status")), principal.get("status"))
        solicitacoes.append(principal)
    return render_template("mew/solicitacoes_matricula.html", solicitacoes=solicitacoes)


@app.route("/mew/solicitacoes-matricula/<int:solicitacao_id>/aprovar", methods=["POST"])
def mew_aprovar_documentos_matricula_publica(solicitacao_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    sol = _get_solicitacao_publica(solicitacao_id=solicitacao_id)
    if not sol:
        return redirect("/mew/solicitacoes-matricula?erro=Solicitacao+nao+encontrada")
    itens = _itens_pedido_publico(sol=sol)
    pedido = _pedido_token_publico(sol)
    if not any(i.get("data_pagamento") for i in itens):
        return redirect("/mew/solicitacoes-matricula?erro=Pagamento+ainda+nao+confirmado")
    docs_atuais = []
    for i in itens:
        try:
            docs_atuais = json.loads(i.get("documentos_json") or "[]")
        except Exception:
            docs_atuais = []
        if docs_atuais:
            break
    if not docs_atuais:
        return redirect("/mew/solicitacoes-matricula?erro=Nenhum+documento+foi+enviado")
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    obs = (request.form.get("observacao_mew") or "").strip()[:1500]
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute(
        "UPDATE solicitacoes_matricula_publica SET status='documentos_aprovados',data_aprovacao=%s,observacao_mew=%s WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s",
        (agora, obs, pedido),
    )
    conn.commit(); conn.close()
    if sol.get("email"):
        try:
            lista = "".join(f"<li><b>{escape(i.get('disciplina_confirmada') or '')}</b> — {int(i.get('carga_horaria') or 0)}h</li>" for i in itens)
            html = f"<html><body style='font-family:Arial'><h2>Documentação conferida</h2><p>Olá, <b>{escape(sol.get('nome') or '')}</b>. Sua documentação foi aprovada para o pedido abaixo:</p><ul>{lista}</ul><p>A equipe acadêmica está concluindo a liberação. O início acadêmico será disponibilizado em até 24 horas.</p><p>SIGEU Educacional • GRUPO EDUCACIONAL UNIFICADO • FACOP CERTIFICADORA</p></body></html>"
            _smtp_enviar(sol["email"], "SIGEU | Documentação aprovada", html)
        except Exception as exc:
            print(f"Aviso e-mail aprovação: {exc}")
    return redirect("/mew/solicitacoes-matricula")


@app.route("/mew/solicitacoes-matricula/<int:solicitacao_id>/liberar", methods=["POST"])
def mew_liberar_matricula_publica(solicitacao_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    sol = _get_solicitacao_publica(solicitacao_id=solicitacao_id)
    if not sol or not sol.get("aluno_id"):
        return redirect("/mew/solicitacoes-matricula?erro=Solicitacao+sem+aluno")
    itens = _itens_pedido_publico(sol=sol)
    pedido = _pedido_token_publico(sol)
    if not itens or any(i.get("status") not in ("documentos_aprovados", "liberado") for i in itens):
        return redirect("/mew/solicitacoes-matricula?erro=Aprove+a+documentacao+antes+de+liberar")
    if not any(i.get("data_pagamento") for i in itens):
        return redirect("/mew/solicitacoes-matricula?erro=Pagamento+ainda+nao+confirmado")

    # Garante disciplina + plano + professor para cada item antes da matrícula do aluno.
    for item in itens:
        _assegurar_disciplina_e_plano_publico(item["id"])
    itens = _itens_pedido_publico(sol=sol)

    inicio = datetime.now() + timedelta(days=1)
    fim = inicio + timedelta(days=int(os.getenv("DISCIPLINA_PRAZO_DIAS", "60")))
    conn = get_db_connection(); cur = conn.cursor()
    try:
        for item in itens:
            disciplina_id = item.get("disciplina_id")
            if not disciplina_id:
                raise ValueError(f"Disciplina não preparada: {item.get('disciplina_confirmada')}")
            cur.execute("SELECT id,nome FROM disciplinas WHERE id=%s", (disciplina_id,))
            if not cur.fetchone():
                raise ValueError("Disciplina não encontrada.")
            cur.execute(
                "INSERT INTO aluno_disciplina(aluno_id,disciplina_id) VALUES(%s,%s) ON CONFLICT(aluno_id,disciplina_id) DO NOTHING",
                (sol["aluno_id"], disciplina_id),
            )
            cur.execute(
                """INSERT INTO aluno_disciplina_datas(aluno_id,disciplina_id,data_inicio,data_fim_previsto)
                VALUES(%s,%s,%s,%s)
                ON CONFLICT(aluno_id,disciplina_id) DO UPDATE SET data_inicio=EXCLUDED.data_inicio,data_fim_previsto=EXCLUDED.data_fim_previsto""",
                (sol["aluno_id"], disciplina_id, inicio.strftime("%d/%m/%Y"), fim.strftime("%d/%m/%Y")),
            )
        conn.commit()
    except Exception:
        conn.rollback(); conn.close(); raise
    conn.close()

    contrato_id = next((i.get("contrato_id") for i in itens if i.get("contrato_id")), None) or criar_contrato_aluno(sol["aluno_id"])
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute(
        "UPDATE solicitacoes_matricula_publica SET status='liberado',contrato_id=%s,data_liberacao=%s WHERE COALESCE(NULLIF(TRIM(pedido_token),''),token)=%s",
        (contrato_id, agora, pedido),
    )
    cur.execute("SELECT payment_id FROM pagamentos_mercadopago WHERE id=%s", (sol.get("cobranca_id"),))
    pag = cur.fetchone(); conn.commit(); conn.close()
    try:
        enviar_boas_vindas_titan(sol["aluno_id"], referencia=f"matricula-publica:{pedido}:liberada", pagamento_id=str((pag or {}).get("payment_id") or ""))
    except Exception as exc:
        print(f"Aviso e-mail liberação: {exc}")
    return redirect("/mew/solicitacoes-matricula?sucesso=Matricula+liberada")



# ============================================================================
# SIGEU 2026-09-08 — PADRÃO ÚNICO DE DOCUMENTOS ACADÊMICOS
# Este bloco substitui, em tempo de execução, os templates legados acima sem
# remover compatibilidade com as rotas e os registros já existentes.
# ============================================================================
from documentos_institucionais import (
    build_declaration as _doc_build_declaration,
    build_history as _doc_build_history,
    build_plan as _doc_build_plan,
)


def _parse_unidades_plano_legacy(conteudo_programatico, limite=8):
    """Converte o texto antigo de conteúdo programático em blocos de unidades."""
    texto = str(conteudo_programatico or "").strip()
    if not texto:
        return []
    # O gerador antigo usa blocos separados por linha em branco e títulos UNIDADE.
    blocos = re.split(r"\n\s*\n(?=\s*(?:UNIDADE|Unidade))", texto)
    unidades = []
    for i, bloco in enumerate(blocos[:limite], 1):
        linhas = [x.strip() for x in str(bloco).splitlines() if x.strip()]
        if not linhas:
            continue
        titulo = linhas[0]
        topicos = [re.sub(r"^[•\-–—\s]+", "", x).strip() for x in linhas[1:] if x.strip()]
        unidades.append({"titulo": titulo or f"UNIDADE {i}", "topicos": topicos})
    return unidades


def gerar_html_plano_ensino(disciplina, codigo, hash_completa, carga_horaria,
                             modalidade, docente, data_formatada, qr_code_base64,
                             numero_unidades=6, **kwargs):
    """Plano institucional padronizado: 6 a 8 unidades, todas na página 2."""
    from api_planos import METODOLOGIA_FIXA, SISTEMA_AVALIACAO_FIXO

    try:
        numero_unidades = max(6, min(8, int(numero_unidades or kwargs.get("numero_unidades") or 6)))
    except Exception:
        numero_unidades = 6

    unidades = kwargs.get("conteudo_programatico_estruturado")
    if not isinstance(unidades, list):
        unidades = _parse_unidades_plano_legacy(kwargs.get("conteudo_programatico"), numero_unidades)

    return _doc_build_plan(
        disciplina=str(disciplina or ""),
        codigo=str(codigo or ""),
        hash_documento=str(hash_completa or ""),
        carga_horaria=str(carga_horaria or ""),
        modalidade=str(modalidade or "EaD"),
        docente=str(docente or "Docente não cadastrado"),
        data_formatada=str(data_formatada or datetime.now().strftime("%d/%m/%Y")),
        qr_code=str(qr_code_base64 or ""),
        objetivo_geral=kwargs.get("objetivo_geral", ""),
        objetivos_especificos=kwargs.get("objetivos_especificos", ""),
        ementa=kwargs.get("ementa_expandida", kwargs.get("ementa", "")),
        habilidades=kwargs.get("habilidades", ""),
        pre_requisitos=kwargs.get("pre_requisitos", "Não há pré-requisitos formais."),
        enquadramento_curricular=kwargs.get("enquadramento_curricular", ""),
        metodologia_html=METODOLOGIA_FIXA,
        avaliacao_html=SISTEMA_AVALIACAO_FIXO,
        bibliografia_basica=kwargs.get("bibliografia_basica", ""),
        bibliografia_complementar=kwargs.get("bibliografia_complementar", ""),
        unidades=unidades,
        numero_unidades=numero_unidades,
    )


def _html_historico_integrado(aluno, disciplinas, codigo, qr_code, hash_documento):
    """Histórico com páginas explícitas e 8 disciplinas por folha, sem cortes."""
    return _doc_build_history(
        dict(aluno or {}),
        [dict(d) for d in (disciplinas or [])],
        str(codigo or ""),
        str(qr_code or ""),
        str(hash_documento or ""),
    )


def _html_declaracao_integrada(aluno, d, codigo, qr_code, hash_documento):
    """Declaração no mesmo perfil visual do Plano e do Histórico."""
    return _doc_build_declaration(
        dict(aluno or {}), dict(d or {}), str(codigo or ""), str(qr_code or ""), str(hash_documento or "")
    )


def gerar_historico_automatico(aluno_id, disciplinas, dados_aluno, qr_code_base64,
                                codigo, hash_documento, ano_manual=None,
                                ira_manual='N/I', total_disciplinas_manual='0',
                                frequencia_manual='N/I'):
    """Versão administrativa do histórico usando os dados atuais do PostgreSQL."""
    aluno = dict(dados_aluno or {})
    base = [dict(d) for d in (disciplinas or [])]
    ids = [int(d["id"]) for d in base if d.get("id") is not None]
    enriquecidas = []
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Dados cadastrais completos para a primeira página.
        cur.execute("""
            SELECT nome_pai,nome_mae,naturalidade,nacionalidade,data_nascimento,
                   sexo,estado_civil,curso_referencia
            FROM dados_pessoais WHERE aluno_id=%s
        """, (aluno_id,))
        extra = cur.fetchone() or {}
        aluno.update({k:v for k,v in dict(extra).items() if v not in (None, "")})

        if ids:
            cur.execute("""
                SELECT d.id,d.nome,COALESCE(d.carga_horaria,80) AS carga_horaria,
                       nf.nota_final,nf.media_final,nf.status AS status_final,nf.data_realizacao,
                       adt.frequencia,adt.data_inicio,
                       doc.nome AS docente_nome,doc.titulacao
                FROM disciplinas d
                LEFT JOIN notas_finais nf ON nf.aluno_id=%s AND nf.disciplina_id=d.id
                LEFT JOIN aluno_disciplina_datas adt ON adt.aluno_id=%s AND adt.disciplina_id=d.id
                LEFT JOIN LATERAL (
                    SELECT dd.docente_id FROM disciplina_docente dd
                    JOIN docentes dx ON dx.id=dd.docente_id
                    WHERE dd.disciplina_id=d.id AND COALESCE(dx.ativo,1)=1
                    ORDER BY dd.ano_semestre DESC,dd.id DESC LIMIT 1
                ) dd_ultimo ON TRUE
                LEFT JOIN docentes doc ON doc.id=dd_ultimo.docente_id
                WHERE d.id=ANY(%s)
                ORDER BY d.nome
            """, (aluno_id, aluno_id, ids))
            for row in cur.fetchall():
                item = dict(row)
                if item.get("docente_nome"):
                    nome_doc = item["docente_nome"]
                    if item.get("titulacao"):
                        nome_doc = f"{nome_doc} ({item['titulacao']})"
                else:
                    nome_doc = _docente_documental_disciplina(cur, item["id"], item["nome"])
                item["docente"] = nome_doc
                enriquecidas.append(item)
            conn.commit()
    finally:
        conn.close()

    if not enriquecidas:
        enriquecidas = base

    # IRA, quantidade de aprovadas e cargas são sempre derivados das disciplinas
    # carregadas do banco pelo construtor institucional. Os parâmetros manuais
    # são mantidos apenas por compatibilidade com chamadas antigas.
    return _doc_build_history(
        aluno, enriquecidas, str(codigo or ""), str(qr_code_base64 or ""), str(hash_documento or ""),
        ira=None, ano_referencia=ano_manual or obter_configuracao_ano()
    )


def gerar_declaracao_conclusao(aluno_id, disciplina_id, dados_aluno, dados_disciplina, ano_manual=None):
    """Compatibilidade com chamadas antigas, já no padrão visual atual."""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    ra = str((dados_aluno or {}).get("ra") or "")
    codigo = f"DECL-{ra}-{disciplina_id}-{timestamp}"
    hash_documento = gerar_hash_documento(f"declaracao_{aluno_id}_{disciplina_id}", ra, timestamp)
    base_url = os.getenv("SIGEU_PUBLIC_URL", "https://sigeueducacional.com.br").rstrip("/")
    qr = gerar_qrcode_base64(f"{base_url}/validar-documento/{codigo}")
    d = dict(dados_disciplina or {})
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("""
            SELECT d.nome,COALESCE(d.carga_horaria,80) AS carga_horaria,
                   nf.nota_final,nf.media_final,nf.status AS status_final,nf.data_realizacao,
                   adt.frequencia,doc.nome AS docente_nome
            FROM disciplinas d
            LEFT JOIN notas_finais nf ON nf.aluno_id=%s AND nf.disciplina_id=d.id
            LEFT JOIN aluno_disciplina_datas adt ON adt.aluno_id=%s AND adt.disciplina_id=d.id
            LEFT JOIN LATERAL (
              SELECT dd.docente_id FROM disciplina_docente dd WHERE dd.disciplina_id=d.id
              ORDER BY dd.ano_semestre DESC,dd.id DESC LIMIT 1
            ) ult ON TRUE
            LEFT JOIN docentes doc ON doc.id=ult.docente_id
            WHERE d.id=%s
        """, (aluno_id, aluno_id, disciplina_id))
        row = cur.fetchone()
        conn.close()
        if row:
            d.update(dict(row)); d["docente"] = d.get("docente_nome") or d.get("docente")
    except Exception:
        pass
    return _doc_build_declaration(dict(dados_aluno or {}), d, codigo, qr, hash_documento)



# ============================================================================
# AVISOS ACADÊMICOS — MEW -> PORTAL DO ALUNO
# ============================================================================

def _aviso_media_url(row):
    row = dict(row or {})
    if row.get("media_r2_key"):
        try:
            return r2_presigned_url(row["media_r2_key"], expires=3600, inline=True)
        except Exception:
            return None
    return row.get("media_url") or None


@app.route("/mew/avisos-academicos")
def mew_avisos_academicos():
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id,nome,ra FROM alunos ORDER BY nome")
        alunos = cur.fetchall()
        cur.execute("""
            SELECT a.*,
                   COUNT(DISTINCT d.aluno_id) AS total_destinatarios,
                   COUNT(DISTINCT v.aluno_id) AS total_fechamentos
            FROM avisos_academicos a
            LEFT JOIN avisos_academicos_destinatarios d ON d.aviso_id=a.id
            LEFT JOIN avisos_academicos_visualizacoes v ON v.aviso_id=a.id
            GROUP BY a.id
            ORDER BY a.id DESC
            LIMIT 200
        """)
        avisos=[]
        for row in cur.fetchall():
            a=dict(row)
            a["media_url_exibicao"]=_aviso_media_url(a)
            avisos.append(a)
    finally:
        conn.close()
    return render_template("mew/avisos_academicos.html", alunos=alunos, avisos=avisos)


@app.route("/mew/avisos-academicos/salvar", methods=["POST"])
def mew_salvar_aviso_academico():
    if not session.get("mew_admin"):
        return jsonify({"success":False,"message":"Não autorizado"}),403
    titulo=(request.form.get("titulo") or "").strip()[:160]
    mensagem=(request.form.get("mensagem") or "").strip()[:5000]
    destino=(request.form.get("destino") or "todos").strip().lower()
    media_tipo=(request.form.get("media_tipo") or "").strip().lower()
    media_url=(request.form.get("media_url") or "").strip()[:2000]
    arquivo=request.files.get("media_file")
    alunos_ids=[]
    for valor in request.form.getlist("alunos_ids"):
        try: alunos_ids.append(int(valor))
        except Exception: pass
    alunos_ids=sorted(set(alunos_ids))
    if not titulo:
        return jsonify({"success":False,"message":"Informe o título do aviso."}),400
    if destino not in {"todos","selecionados"}:
        destino="todos"
    if destino=="selecionados" and not alunos_ids:
        return jsonify({"success":False,"message":"Selecione pelo menos um aluno."}),400
    if media_tipo not in {"","imagem","video"}:
        return jsonify({"success":False,"message":"Tipo de mídia inválido."}),400

    media_r2_key=None; media_mime=None
    if arquivo and arquivo.filename:
        nome=secure_filename(arquivo.filename) or "midia"
        mime=(arquivo.mimetype or guess_content_type(nome) or "application/octet-stream").lower()
        if mime.startswith("image/"):
            media_tipo="imagem"
        elif mime.startswith("video/"):
            media_tipo="video"
        else:
            return jsonify({"success":False,"message":"O arquivo precisa ser uma imagem ou um vídeo."}),400
        if not r2_is_configured():
            return jsonify({"success":False,"message":"O R2 precisa estar configurado para enviar imagem/vídeo pelo MEW."}),500
        media_r2_key=make_key("avisos-academicos",nome,datetime.now().strftime("%Y%m%d"))
        try:
            r2_upload_fileobj(arquivo.stream,media_r2_key,mime,{"origem":"mew-avisos","tipo":media_tipo})
            media_mime=mime; media_url=None
        except Exception as exc:
            return jsonify({"success":False,"message":f"Falha ao enviar a mídia: {exc}"}),500
    elif media_url and not media_tipo:
        ext=media_url.lower().split("?")[0]
        media_tipo="video" if ext.endswith((".mp4",".webm",".mov",".m4v")) else "imagem"

    conn=get_db_connection(); cur=conn.cursor()
    try:
        cur.execute("""
            INSERT INTO avisos_academicos
            (titulo,mensagem,publico_todos,media_tipo,media_url,media_r2_key,media_mime,ativo,criado_por)
            VALUES(%s,%s,%s,%s,%s,%s,%s,TRUE,'MEW') RETURNING id
        """,(titulo,mensagem,destino=="todos",media_tipo or None,media_url or None,media_r2_key,media_mime))
        aviso_id=cur.fetchone()["id"]
        if destino=="selecionados":
            cur.executemany(
                "INSERT INTO avisos_academicos_destinatarios(aviso_id,aluno_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                [(aviso_id,aid) for aid in alunos_ids]
            )
        conn.commit()
        aviso_retorno = {
            "id": aviso_id,
            "titulo": titulo,
            "mensagem": mensagem,
            "publico_todos": destino == "todos",
            "total_destinatarios": len(alunos_ids) if destino == "selecionados" else 0,
            "media_tipo": media_tipo or "",
            "media_url_exibicao": _aviso_media_url({"media_r2_key": media_r2_key, "media_url": media_url}),
            "ativo": True,
        }
        return jsonify({"success":True,"id":aviso_id,"aviso":aviso_retorno})
    except Exception as exc:
        conn.rollback()
        if media_r2_key:
            try: delete_object(media_r2_key)
            except Exception: pass
        return jsonify({"success":False,"message":f"Erro ao salvar aviso: {exc}"}),500
    finally:
        conn.close()


@app.route("/mew/avisos-academicos/<int:aviso_id>/alternar", methods=["POST"])
def mew_alternar_aviso_academico(aviso_id):
    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if not session.get("mew_admin"):
        if ajax:
            return jsonify({"success": False, "message": "Sessão do MEW expirada."}), 403
        return redirect("/mew/login")
    conn=get_db_connection(); cur=conn.cursor()
    try:
        cur.execute("UPDATE avisos_academicos SET ativo=NOT ativo WHERE id=%s RETURNING ativo",(aviso_id,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            if ajax:
                return jsonify({"success": False, "message": "Aviso não encontrado."}), 404
            return redirect("/mew/avisos-academicos")
        conn.commit()
        if ajax:
            return jsonify({"success": True, "ativo": bool(row.get("ativo"))})
        return redirect("/mew/avisos-academicos")
    except Exception as exc:
        conn.rollback()
        if ajax:
            return jsonify({"success": False, "message": f"Não foi possível alterar o aviso: {exc}"}), 500
        return redirect("/mew/avisos-academicos")
    finally:
        conn.close()


@app.route("/mew/avisos-academicos/<int:aviso_id>/excluir", methods=["POST"])
def mew_excluir_aviso_academico(aviso_id):
    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if not session.get("mew_admin"):
        if ajax:
            return jsonify({"success": False, "message": "Sessão do MEW expirada."}), 403
        return redirect("/mew/login")
    conn=get_db_connection(); cur=conn.cursor()
    row={}
    try:
        cur.execute("SELECT media_r2_key FROM avisos_academicos WHERE id=%s",(aviso_id,))
        row=cur.fetchone()
        if not row:
            if ajax:
                return jsonify({"success": False, "message": "Aviso não encontrado."}), 404
            return redirect("/mew/avisos-academicos")
        cur.execute("DELETE FROM avisos_academicos WHERE id=%s",(aviso_id,))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if ajax:
            return jsonify({"success": False, "message": f"Não foi possível excluir o aviso: {exc}"}), 500
        return redirect("/mew/avisos-academicos")
    finally:
        conn.close()
    if row.get("media_r2_key"):
        try: delete_object(row["media_r2_key"])
        except Exception: pass
    if ajax:
        return jsonify({"success": True})
    return redirect("/mew/avisos-academicos")


@app.route("/api/avisos-academicos")
def api_avisos_academicos_aluno():
    aluno_id=session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success":False,"avisos":[]}),401
    conn=get_db_connection(); cur=conn.cursor()
    try:
        cur.execute("""
            SELECT a.*
            FROM avisos_academicos a
            WHERE a.ativo=TRUE
              AND (
                a.publico_todos=TRUE OR EXISTS(
                    SELECT 1 FROM avisos_academicos_destinatarios d
                    WHERE d.aviso_id=a.id AND d.aluno_id=%s
                )
              )
              AND NOT EXISTS(
                SELECT 1 FROM avisos_academicos_visualizacoes v
                WHERE v.aviso_id=a.id AND v.aluno_id=%s
              )
            ORDER BY a.id ASC
            LIMIT 20
        """,(aluno_id,aluno_id))
        avisos=[]
        for row in cur.fetchall():
            a=dict(row)
            avisos.append({
                "id":a["id"],"titulo":a.get("titulo") or "Aviso acadêmico",
                "mensagem":a.get("mensagem") or "","media_tipo":a.get("media_tipo") or "",
                "media_url":_aviso_media_url(a),
            })
        return jsonify({"success":True,"avisos":avisos})
    finally:
        conn.close()


@app.route("/api/avisos-academicos/<int:aviso_id>/fechar", methods=["POST"])
def api_fechar_aviso_academico(aviso_id):
    aluno_id=session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success":False}),401
    conn=get_db_connection(); cur=conn.cursor()
    try:
        # Só permite marcar como fechado um aviso que realmente alcança esse aluno.
        cur.execute("""
            SELECT 1 FROM avisos_academicos a
            WHERE a.id=%s AND (a.publico_todos=TRUE OR EXISTS(
                SELECT 1 FROM avisos_academicos_destinatarios d WHERE d.aviso_id=a.id AND d.aluno_id=%s
            ))
        """,(aviso_id,aluno_id))
        if not cur.fetchone():
            return jsonify({"success":False}),404
        cur.execute("""
            INSERT INTO avisos_academicos_visualizacoes(aviso_id,aluno_id)
            VALUES(%s,%s) ON CONFLICT(aviso_id,aluno_id) DO UPDATE SET fechado_em=CURRENT_TIMESTAMP
        """,(aviso_id,aluno_id))
        conn.commit()
        return jsonify({"success":True})
    finally:
        conn.close()




if __name__ == "__main__":
    # Em desenvolvimento local, faz a mesma migração usada pelo Procfile do Render.
    init_db()
    init_contratos_db()
    init_pagamentos_db()
    init_documentos_integrados_db()
    app.run(debug=True)

