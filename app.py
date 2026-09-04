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
from weasyprint import HTML
load_dotenv() 

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

from api_planos import planos_bp
app.register_blueprint(planos_bp, url_prefix='/api')

import os
import psycopg2
import psycopg2.extras


def get_db_connection():
    """Conecta exclusivamente ao PostgreSQL."""
    return psycopg2.connect(
        os.environ.get("DATABASE_URL"),
        sslmode="require",
        cursor_factory=psycopg2.extras.RealDictCursor
    )


def init_pagamentos_db():
    """Garante a tabela de cobranças do Mercado Pago no PostgreSQL."""
    init_contratos_db()
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


def criar_preferencia_mercadopago(aluno_id, nome, email, valor_total, contrato_id=None, base_url=None):
    init_pagamentos_db()
    valor = round(float(valor_total), 2)
    external_reference = f"SIGEU-ALUNO-{aluno_id}-{int(time.time())}-{secrets.token_hex(3)}"
    base_url = (base_url or "https://campusvirtualfacop.com.br").rstrip("/")
    preference_data = {
        "items": [{
            "id": f"aluno-{aluno_id}",
            "title": f"Serviços educacionais - aluno {nome}",
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
            "contrato_id": str(contrato_id) if contrato_id else ""
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
    init_contratos_db()
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
            FOREIGN KEY (aluno_id) REFERENCES alunos(id),
            FOREIGN KEY (disciplina_id) REFERENCES disciplinas(id),
            UNIQUE(aluno_id, disciplina_id)
        )
    """)
    
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
    
    # Verificar se já fez todas as 4 provas dos capítulos
    cursor.execute("""
        SELECT COUNT(*) as total_provas_feitas 
        FROM notas 
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))
    
    total_provas = cursor.fetchone()["total_provas_feitas"] or 0
    
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
    if total_provas >= 4 and fez_final:
        return True, "concluida_com_final"
    elif total_provas >= 4 and not fez_final:
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
    """
    Gera HTML da declaração de conclusão de disciplina
    """
    from datetime import datetime
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Buscar dados adicionais do aluno
    cursor.execute("""
        SELECT nome_pai, nome_mae, naturalidade, nacionalidade, 
               data_nascimento, sexo, estado_civil, curso_referencia
        FROM dados_pessoais 
        WHERE aluno_id = %s
    """, (aluno_id,))
    
    dados_adicionais = cursor.fetchone()
    
    # Buscar informações específicas da disciplina (nota final, período)
    cursor.execute("""
        SELECT nf.media_final, nf.status, nf.data_realizacao,
               addd.data_inicio, addd.data_fim_previsto
        FROM notas_finais nf
        LEFT JOIN aluno_disciplina_datas addd ON nf.aluno_id = addd.aluno_id AND nf.disciplina_id = addd.disciplina_id
        WHERE nf.aluno_id = %s AND nf.disciplina_id = %s
    """, (aluno_id, disciplina_id))
    
    info_final = cursor.fetchone()
    
    conn.close()
    
    # Dados do aluno
    nome_aluno = dados_aluno.get('nome', '')
    ra_aluno = dados_aluno.get('ra', '')
    cpf_aluno = dados_aluno.get('cpf_formatado', '')
    
    # Dados da disciplina
    nome_disciplina = dados_disciplina.get('nome', '')
    classe_nome_disciplina = 'disciplina-nome longo' if len(nome_disciplina) > 40 else 'disciplina-nome'
    carga_horaria = dados_disciplina.get('carga', 80)
    
    # Determinar nota e status
    nota_final = "N/I"
    status = "Aprovado"
    data_conclusao = datetime.now().strftime("%d/%m/%Y")
    periodo = ""
    
    if info_final:
        if info_final['media_final']:
            nota_final = f"{float(info_final['media_final']):.2f}"
        if info_final['status']:
            status = "Aprovado" if info_final['status'] == 'aprovado' else "Reprovado"
        if info_final['data_realizacao']:
            data_conclusao = info_final['data_realizacao'].split(' ')[0] if ' ' in info_final['data_realizacao'] else info_final['data_realizacao']
        
        # Determinar período (semestre/ano)
        if info_final['data_inicio']:
            try:
                data_obj = datetime.strptime(info_final['data_inicio'], "%d/%m/%Y")
                ano = data_obj.year
                mes = data_obj.month
                semestre = "1º" if mes <= 6 else "2º"
                periodo = f"{semestre} semestre de {ano}"
            except:
                periodo = f"ano {datetime.now().year}"
        else:
            periodo = f"ano {datetime.now().year}"
    
    # Data atual
    data_atual = datetime.now().strftime("%d de %B de %Y")
    # Mapeamento de meses em português
    meses_pt = {
        'January': 'janeiro', 'February': 'fevereiro', 'March': 'março',
        'April': 'abril', 'May': 'maio', 'June': 'junho',
        'July': 'julho', 'August': 'agosto', 'September': 'setembro',
        'October': 'outubro', 'November': 'novembro', 'December': 'dezembro'
    }
    for eng, pt in meses_pt.items():
        data_atual = data_atual.replace(eng, pt)
    
    # Ano para o documento
    ano_documento = ano_manual if ano_manual else datetime.now().year
    
    # HTML CORRIGIDO - MUDEI AQUI PARA USAR {{ qrcode_base64 }}
    html = '''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>DECLARAÇÃO DE CONCLUSÃO - ''' + nome_disciplina + '''</title>

<style>
/* TIPOGRAFIA INSTITUCIONAL - ARIAL/CALIBRI */
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 0;
    background: #c9c9c9;
    font-family: "Arial Nova", "Arial", "Calibri", "Segoe UI", sans-serif;
    font-size: 10.5pt;
    color: #1a1a1a;
    line-height: 1.4;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}

/* FOLHA A4 COM MARGENS PRECISAS */
.folha {
    width: 210mm;
    height: 297mm;
    margin: 0 auto;
    background: #fefefe;
    position: relative;
    overflow: hidden;
    box-shadow: 0 0 20px rgba(0,0,0,0.3);
    padding: 15mm 20mm 25mm 20mm;
}

/* BORDA DE SEGURANÇA - ESTILO PAPEL MOEDA */
.borda-seguranca {
    position: absolute;
    top: 8mm;
    left: 8mm;
    right: 8mm;
    bottom: 8mm;
    border: 0.5pt solid #1a237e;
    pointer-events: none;
}

.borda-seguranca::before {
    content: "";
    position: absolute;
    top: 2mm;
    left: 2mm;
    right: 2mm;
    bottom: 2mm;
    border: 0.3pt dashed #1a237e;
    opacity: 0.5;
}

/* CANTONEIRAS DE SEGURANÇA */
.cantoneira {
    position: absolute;
    width: 15mm;
    height: 15mm;
    border: 2pt solid #1a237e;
    z-index: 100;
}

.cantoneira.top-left {
    top: 6mm;
    left: 6mm;
    border-right: none;
    border-bottom: none;
}

.cantoneira.top-right {
    top: 6mm;
    right: 6mm;
    border-left: none;
    border-bottom: none;
}

.cantoneira.bottom-left {
    bottom: 6mm;
    left: 6mm;
    border-right: none;
    border-top: none;
}

.cantoneira.bottom-right {
    bottom: 6mm;
    right: 6mm;
    border-left: none;
    border-top: none;
}

/* MARCA D'ÁGUA PRINCIPAL - SELO INSTITUCIONAL */
.marca-dagua-principal {
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
}

/* MARCA D'ÁGUA SECUNDÁRIA - PATTERN GEOMÉTRICO */
.marca-dagua-pattern {
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
}

/* MICROTEXTO DE SEGURANÇA NA BORDA */
.microtexto-borda {
    position: absolute;
    font-family: "Arial", sans-serif;
    font-size: 5pt;
    color: rgba(26,35,126,0.3);
    letter-spacing: 1px;
    text-transform: uppercase;
    white-space: nowrap;
    z-index: 2;
}

.microtexto-borda.top {
    top: 5mm;
    left: 50%;
    transform: translateX(-50%);
}

.microtexto-borda.bottom {
    bottom: 5mm;
    left: 50%;
    transform: translateX(-50%);
}

.microtexto-borda.left {
    left: 3mm;
    top: 50%;
    transform: translateY(-50%) rotate(-90deg);
    transform-origin: center;
}

.microtexto-borda.right {
    right: 3mm;
    top: 50%;
    transform: translateY(-50%) rotate(90deg);
    transform-origin: center;
}

/* FAIXA SUPERIOR IDENTIFICADORA */
.faixa-identificadora {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 4mm;
    background: repeating-linear-gradient(
        90deg,
        #1a237e 0px,
        #1a237e 5mm,
        #ffffff 5mm,
        #ffffff 10mm,
        #1a237e 10mm,
        #1a237e 15mm
    );
    z-index: 10;
}

/* CABEÇALHO INSTITUCIONAL */
.cabecalho {
    position: relative;
    z-index: 5;
    border-bottom: 1.5pt solid #1a237e;
    padding-bottom: 4mm;
    margin-bottom: 10mm;
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.logo-area {
    display: flex;
    align-items: center;
    gap: 5mm;
}

.logo-area img {
    width: 25mm;
    height: auto;
    opacity: 0.9;
}

.instituicao-info {
    flex: 1;
}

.instituicao-nome {
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 14pt;
    color: #1a237e;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    line-height: 1.2;
    margin-top: 8mm;
}

.instituicao-sub {
    font-family: "Arial", sans-serif;
    font-size: 8pt;
    color: #444;
    margin-top: 2mm;
    line-height: 1.3;
}

/* SELO DE AUTENTICIDADE NO CABEÇALHO */
.selo-autenticidade {
    width: 22mm;
    height: 22mm;
    border: 1.5pt solid #1a237e;
    border-radius: 50%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: "Arial", sans-serif;
    font-size: 6pt;
    color: #1a237e;
    text-align: center;
    line-height: 1.1;
    position: relative;
    background: radial-gradient(circle, rgba(26,35,126,0.05) 0%, transparent 70%);
}

.selo-autenticidade::before {
    content: "";
    display: inline-block;
    width: 24px;
    height: 16px;
    margin-bottom: 1mm;
    margin-right: 4px;
    vertical-align: middle;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='16' viewBox='0 0 24 16'%3E%3Crect x='0' y='0' width='2' height='16' fill='%231a237e'/%3E%3Crect x='4' y='0' width='1' height='16' fill='%231a237e'/%3E%3Crect x='7' y='0' width='3' height='16' fill='%231a237e'/%3E%3Crect x='12' y='0' width='1' height='16' fill='%231a237e'/%3E%3Crect x='15' y='0' width='2' height='16' fill='%231a237e'/%3E%3Crect x='19' y='0' width='1' height='16' fill='%231a237e'/%3E%3Crect x='22' y='0' width='2' height='16' fill='%231a237e'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-size: contain;
}

/* NÚMERO DE CONTROLE NO CANTO */
.numero-controle-box {
    position: absolute;
    top: 12mm;
    right: 12mm;
    border: 0.5pt solid #1a237e;
    padding: 2mm 4mm;
    font-family: "Courier New", monospace;
    font-size: 7pt;
    color: #1a237e;
    background: rgba(26,35,126,0.03);
    z-index: 20;
}

.numero-controle-box::before {
    content: "Nº CONTROLE: ";
    font-weight: bold;
}

/* TÍTULO DO DOCUMENTO */
.titulo-documento {
    text-align: center;
    margin: 1mm 0 10mm 0;
    position: relative;
    z-index: 5;
}

.titulo-principal {
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 18pt;
    color: #1a237e;
    text-transform: uppercase;
    letter-spacing: 4px;
    margin-bottom: 3mm;
    position: relative;
    display: inline-block;
    padding: 0 15mm;
}

/* LINHAS DECORATIVAS LATERAIS DO TÍTULO */
.titulo-principal::before,
.titulo-principal::after {
    content: "";
    position: absolute;
    top: 50%;
    width: 10mm;
    height: 1pt;
    background: #1a237e;
}

.titulo-principal::before {
    left: 0;
}

.titulo-principal::after {
    right: 0;
}

.titulo-sub {
    font-family: "Arial", sans-serif;
    font-size: 9pt;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 3px;
    border-top: 0.5pt solid #ccc;
    border-bottom: 0.5pt solid #ccc;
    padding: 2mm 0;
    display: inline-block;
}

/* TEXTO DE ABERTURA */
.texto-abertura {
    text-align: justify;
    margin-bottom: 8mm;
    position: relative;
    z-index: 5;
    font-size: 10.5pt;
    line-height: 1.6;
    text-indent: 15mm;
}

.destaque {
    font-weight: bold;
    color: #1a237e;
    font-family: "Arial Black", "Arial", sans-serif;
}

/* BOX DE IDENTIFICAÇÃO - ESTILO FICHA CRIMINAL */
.box-identificacao {
    border: 1pt solid #1a237e;
    margin: 8mm 0;
    position: relative;
    z-index: 5;
    background: rgba(26,35,126,0.02);
}

.box-identificacao-header {
    background: #1a237e;
    color: #fff;
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 8pt;
    text-transform: uppercase;
    letter-spacing: 2px;
    padding: 1mm 4mm;
    text-align: center;
}

.box-identificacao-content {
    padding: 3mm;
}

.linha-dado {
    display: flex;
    margin-bottom: 3mm;
    border-bottom: 0.3pt dotted #999;
    padding-bottom: 2mm;
}

.linha-dado:last-child {
    margin-bottom: 0;
    border-bottom: none;
}

.rotulo {
    width: 25mm;
    font-family: "Arial", sans-serif;
    font-size: 8pt;
    color: #1a237e;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.valor {
    flex: 1;
    font-family: "Arial", sans-serif;
    font-size: 11pt;
    color: #000;
    font-weight: bold;
    padding-left: 3mm;
}

/* BOX DE DISCIPLINA */
.box-disciplina {
    border: 1pt solid #1a237e;
    border-left: 4pt solid #1a237e;
    margin: 8mm 0;
    padding: 5mm;
    position: relative;
    z-index: 5;
    background: #fff;
}

.box-disciplina::before {
    content: "DADOS DA DISCIPLINA";
    position: absolute;
    top: -3mm;
    left: 5mm;
    background: #fff;
    padding: 0 3mm;
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 7pt;
    color: #1a237e;
    letter-spacing: 1px;
}

.disciplina-nome {
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 12pt;
    color: #1a237e;
    text-align: center;
    margin: 3mm 0 5mm 0;
    text-transform: uppercase;
    line-height: 1.3;
    word-break: break-word;
    hyphens: auto;
    max-width: 100%;
}
.disciplina-nome.longo {
    font-size: 10pt;
    line-height: 1.2;
}

.disciplina-dados {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 3mm;
    font-size: 9pt;
}

.dado-item {
    text-align: center;
    border-right: 0.5pt solid #ddd;
    padding: 2mm;
}

.dado-item:last-child {
    border-right: none;
}

.dado-label {
    font-size: 7pt;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 1mm;
    line-height: 1.2;

}

.dado-valor {
    font-weight: bold;
    color: #1a237e;
    font-size: 10pt;
}

/* TEXTO DECLARATÓRIO */
.texto-declaratorio2 {
    text-align: justify;
    margin: 3mm 0;
    position: relative;
    z-index: 5;
    font-size: 10.5pt;
    line-height: 1.6;
    text-indent: 15mm;
    margin-left: 27mm;

}

.texto-declaratorio1 {
    text-align: justify;
    margin: 3mm 0;
    position: relative;
    z-index: 5;
    font-size: 10.5pt;
    line-height: 1.6;
    text-indent: 15mm;
}
/* SELO DE AUTENTICAÇÃO GRANDE */
.selo-grande {
    position: absolute;
    bottom: 45mm;
    right: 15mm;
    width: 35mm;
    height: 35mm;
    border: 2pt solid rgba(26,35,126,0.3);
    border-radius: 50%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: "Arial", sans-serif;
    font-size: 6pt;
    color: rgba(26,35,126,0.4);
    text-align: center;
    line-height: 1.2;
    transform: rotate(-15deg);
    z-index: 3;
    pointer-events: none;
}

.selo-grande::before {
    content: "AUTENTICIDADE";
    font-weight: bold;
    font-size: 7pt;
    margin-bottom: 2mm;
    letter-spacing: 1px;
}

.selo-grande::after {
    content: "★ ★ ★";
    font-size: 8pt;
    margin-top: 2mm;
}

/* DATA E LOCAL */
.data-local {
    text-align: right;
    margin: 20mm 0 10mm 0;
    font-family: "Arial", sans-serif;
    font-size: 8pt;
    color: #333;
    position: relative;
    z-index: 5;
    font-style: italic;
}

/* ASSINATURA */
.assinatura-area {
    margin-top: 20mm;
    text-align: center;
    position: relative;
    z-index: 5;
    page-break-inside: avoid;
}

.assinatura-linha {
    width: 70mm;
    height: 0;
    border-top: 0.5pt solid #000;
    margin: 0 auto 3mm auto;
    position: relative;
}

.assinatura-linha::before {
    content: "";
    position: absolute;
    left: 50%;
    top: -2mm;
    transform: translateX(-50%);
    width: 20mm;
    height: 4mm;
    border-left: 0.5pt solid #999;
    border-right: 0.5pt solid #999;
}

.assinatura-nome {
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 11pt;
    color: #1a237e;
    margin-bottom: 1mm;
}

.assinatura-cargo {
    font-family: "Arial", sans-serif;
    font-size: 8pt;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 1px;
}

/* QR CODE AREA */
.qr-code-box {
    position: absolute;
    bottom: 23mm;
    left: 15mm;
    width: 30mm;
    height: 30mm;
    border: 0.5pt solid #ccc;
    background: #fafafa;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    z-index: 5;
}

.qr-code-label {
    font-size: 6pt;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 2mm;
}

#qr-code-placeholder {
    width: 20mm;
    height: 20mm;
    background: #e0e0e0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 6pt;
    color: #999;
}

/* RODAPÉ TÉCNICO */
.rodape-tecnico {
    position: absolute;
    bottom: 17mm;
    left: 50mm;
    right: 15mm;
    font-family: "Arial", sans-serif;
    font-size: 6.5pt;
    color: #666;
    text-align: center;
    line-height: 1.4;
    z-index: 5;
    border-top: 0.3pt solid #ddd;
    padding-top: 3mm;
}

.rodape-tecnico strong {
    color: #1a237e;
}

/* MICROTEXTOS DE SEGURANÇA */
.microtexto-seguranca {
    position: absolute;
    font-family: "Arial", sans-serif;
    font-size: 5pt;
    color: rgba(0,0,0,0.15);
    z-index: 2;
    letter-spacing: 0.5px;
}

.micro-1 { top: 30mm; left: 10mm; transform: rotate(90deg); }
.micro-2 { top: 50mm; right: 10mm; transform: rotate(-90deg); }
.micro-3 { bottom: 80mm; left: 12mm; }
.micro-4 { bottom: 100mm; right: 50mm; }

/* PRINT STYLES */
@media print {
    body {
        background: #fff;
    }
    
    .folha {
        box-shadow: none;
        margin: 0;
    }
}
</style>
</head>

<body>
<div class="folha">
    <!-- ELEMENTOS DE SEGURANÇA E BORDA -->
    <div class="borda-seguranca"></div>
    <div class="cantoneira top-left"></div>
    <div class="cantoneira top-right"></div>
    <div class="cantoneira bottom-left"></div>
    <div class="cantoneira bottom-right"></div>
    
    <!-- MICROTEXTOS DE BORDA -->
    <div class="microtexto-borda top">DOCUMENTO OFICIAL - FCP Certificadora | SiGEu Educ - VALIDAÇÃO DIGITAL OBRIGATÓRIA</div>
    <div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98 <strong> | F142485-1/-Coord. Acad. Tatiane R. G. Lourenço- </strong></div>
    <div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
    <div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>
    
    <!-- MARCAS D'ÁGUA -->
    <div class="marca-dagua-principal">FACOP/CERTIFICADORA/SiGEU EDUCACIONAL</div>
    <div class="marca-dagua-pattern"></div>
    
    <!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
    <div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
    <div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
    <div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO FACOP/CERTIFICADORA/SiGEU EDUCACIONAL</div>
    <div class="microtexto-seguranca micro-4">AUTENTICIDADE VERIFICÁVEL</div>
    
    <!-- FAIXA IDENTIFICADORA -->
    <div class="faixa-identificadora"></div>
    
    <!-- NÚMERO DE CONTROLE -->
    <div class="numero-controle-box">DOC-''' + ra_aluno + '''-''' + periodo + '''-''' + nota_final + '''</div>
    
    <!-- CABEÇALHO -->
    <div class="cabecalho">
        <div class="logo-area">
            <img src="/static/img/logo_declaracao.png" alt="Logo Institucional">
            <div class="instituicao-info">
                <div class="instituicao-nome">FACOP - SiGEu</div>
                <div class="instituicao-sub">
                    Faculdade do Centro Oeste Paulista 04.344.730/0001-60.<br>
                    Credenciada pela Portaria MEC nº 887 de 26/07/2017<br>
                    Polo educacional - Grupo Educacional Unificado LTDA
                </div>
            </div>
        </div>
        <div class="selo-autenticidade">
            FCP-SiGEu<br>e-SIGEU-GTP-2026
        </div>
    </div>
    
    <!-- TÍTULO -->
    <div class="titulo-documento">
        <div class="titulo-principal">Declaração</div>
        <div class="titulo-sub">Conclusão de Disciplina Isolada</div>
    </div>
    
    <!-- TEXTO DE ABERTURA -->
    <div class="texto-abertura">
        A <span class="destaque">FACULDADE DO CENTRO OESTE PAULISTA (FACOP)</span>, 
        instituição de ensino superior devidamente credenciada pelo Ministério da Educação, 
        no âmbito do Convênio Educacional <span class="destaque">FACOP/SiGEu – Grupo Educacional Unificado LTDA</span>, 
        inscrita no CNPJ sob o nº 04.344.730/0001-60, 
        <strong>DECLARA</strong> para os devidos fins de direito que:
    </div>
    
    <!-- BOX DE IDENTIFICAÇÃO DO ALUNO -->
    <div class="box-identificacao">
        <div class="box-identificacao-header">Dados do Discente</div>
        <div class="box-identificacao-content">
            <div class="linha-dado">
                <div class="rotulo">Nome:</div>
                <div class="valor">''' + nome_aluno + '''</div>
            </div>
            <div class="linha-dado">
                <div class="rotulo">RA:</div>
                <div class="valor">''' + ra_aluno + '''</div>
            </div>
            <div class="linha-dado">
                <div class="rotulo">CPF:</div>
                <div class="valor">''' + cpf_aluno + '''</div>
            </div>
        </div>
    </div>
    
    <!-- BOX DE DADOS DA DISCIPLINA -->
    <div class="box-disciplina">
        <div class="''' + classe_nome_disciplina + '''">''' + nome_disciplina + '''</div>
        <div class="disciplina-dados">
            <div class="dado-item">
                <div class="dado-label">Modalidade</div>
                <div class="dado-valor">Disciplina Isolada</div>
            </div>
            <div class="dado-item">
                <div class="dado-label">Período</div>
                <div class="dado-valor">''' + periodo + '''</div>
            </div>
            <div class="dado-item">
                <div class="dado-label">Carga Horária</div>
                <div class="dado-valor">''' + str(carga_horaria) + '''h</div>
            </div>
        </div>
    </div>
    
    <!-- TEXTO DECLARATÓRIO -->
    <div class="texto-declaratorio1">
        Concluiu com <strong>aproveitamento</strong> a disciplina acima referenciada, 
        com resultado final <span class="destaque">''' + status + '''</span> e nota 
        <span class="destaque">''' + nota_final + ''' </span>(média), atendendo integralmente aos critérios 
        de avaliação estabelecidos no Regimento Geral da Instituição e na legislação 
        educacional vigente (Lei nº 9.394/1996 - LDBEN e alterações subsequentes).
    </div>
    
    <div class="texto-declaratorio2">
        A frequência e o aproveitamento encontram-se devidamente registrados nos sistemas 
        acadêmicos da instituição, podendo esta declaração ser utilizada para fins de 
        comprovação de conclusão de componente curricular, aproveitamento de estudos 
        ou quaisquer outros fins que se fizerem necessários, conforme determinação legal.
    </div>

    <!-- SELO GRANDE DE AUTENTICAÇÃO -->
    <div class="selo-grande">
        VALIDADO<br>
        ELETRONICAMENTE<br>
        ''' + data_atual + '''
    </div>
    
    <!-- DATA E LOCAL -->
    <div class="data-local">
        São Paulo – SP, ''' + data_atual + '''.
    </div>
    
    <!-- QR CODE - AGORA USA O TEMPLATE COM {{ qrcode_base64 }} -->
    <div class="qr-code-box">
    <div class="qr-code-label">Validação Digital</div>
    <div id="qr-code-placeholder">
        <!-- Símbolo simples de código de barras usando SVG -->
        <svg width="60" height="40" viewBox="0 0 60 40" style="opacity: 0.6;">
            <rect x="2" y="5" width="4" height="30" fill="#1a237e"/>
            <rect x="8" y="5" width="2" height="30" fill="#1a237e"/>
            <rect x="12" y="5" width="6" height="30" fill="#1a237e"/>
            <rect x="20" y="5" width="3" height="30" fill="#1a237e"/>
            <rect x="25" y="5" width="2" height="30" fill="#1a237e"/>
            <rect x="30" y="5" width="5" height="30" fill="#1a237e"/>
            <rect x="37" y="5" width="2" height="30" fill="#1a237e"/>
            <rect x="42" y="5" width="4" height="30" fill="#1a237e"/>
            <rect x="48" y="5" width="3" height="30" fill="#1a237e"/>
            <rect x="53" y="5" width="2" height="30" fill="#1a237e"/>
            <rect x="57" y="5" width="1" height="30" fill="#1a237e"/>
        </svg>
    </div>
</div>
    
    <!-- RODAPÉ TÉCNICO -->
    <div class="rodape-tecnico">
        <strong>DOCUMENTO GERADO ELETRONICAMENTE</strong> em conformidade com as Leis nº 11.419/06, 14.063/20 e nº 9.394/96 e nº 5.154/2004.<br>
        Este documento possui validade jurídica sem assinatura física mediante validação pelo QR Code acima.<br>
        Para verificar autenticidade:<strong> https://campusvirtualfacop.com.br/validar-documento</strong> | Protocolo: ''' + ra_aluno + '''-''' + periodo + '''
    </div>
</div>
</body>
</html>'''
    
    # 👇 NOVO CÓDIGO - substitui TODO o bloco antigo
    codigo_autenticacao = f"{ra_aluno}-{disciplina_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    dados_qr = f"https://campusvirtualfacop.com.br/validar-documento/DECL-{codigo_autenticacao}"
    qrcode_base64 = gerar_qrcode_base64(dados_qr)
    
    from flask import render_template_string
    return render_template_string(html, qrcode_base64=qrcode_base64)

# ↓↓↓ COLOQUE AQUI ↓↓↓
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
        cursor.execute(
            "SELECT * FROM alunos WHERE ra = %s AND senha = %s",
            (ra, senha)
        )
        aluno = cursor.fetchone()
        conn.close()

        if aluno:
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
    init_documentos_integrados_db()
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar dados pessoais do aluno
    cursor.execute("SELECT * FROM dados_pessoais WHERE aluno_id = %s", (aluno_id,))
    dados_pessoais = cursor.fetchone()
    
    # Buscar situação financeira
    cursor.execute("SELECT * FROM situacao_financeira WHERE aluno_id = %s ORDER BY id DESC LIMIT 1", (aluno_id,))
    situacao_financeira = cursor.fetchone()
    
    # Buscar disciplinas do aluno
    cursor.execute("""
        SELECT d.id, d.nome
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        WHERE ad.aluno_id = %s
    """, (aluno_id,))
    disciplinas = cursor.fetchall()

    # Buscar notas
    cursor.execute("""
        SELECT n.disciplina_id, n.capitulo, n.nota, d.nome AS disciplina_nome
        FROM notas n
        JOIN disciplinas d ON n.disciplina_id = d.id
        WHERE n.aluno_id = %s
        ORDER BY n.disciplina_id, n.capitulo
    """, (aluno_id,))
    notas = cursor.fetchall()

    # Buscar solicitações de material
    cursor.execute("""
        SELECT sm.*, d.nome AS disciplina_nome
        FROM solicitacoes_material sm
        LEFT JOIN disciplinas d ON sm.disciplina_id = d.id
        WHERE sm.aluno_id = %s
        ORDER BY sm.data_solicitacao DESC
    """, (aluno_id,))
    solicitacoes_material = cursor.fetchall()

    # Buscar solicitações de declarações
    cursor.execute("""
        SELECT *
        FROM solicitacoes_declaracoes
        WHERE aluno_id = %s
        ORDER BY data_solicitacao DESC
    """, (aluno_id,))
    solicitacoes_declaracoes = cursor.fetchall()

    # Calcular totais
    cursor.execute("SELECT COUNT(*) as total FROM notas WHERE aluno_id = %s", (aluno_id,))
    total_provas = cursor.fetchone()["total"]
    
    cursor.execute("SELECT AVG(nota) as media FROM notas WHERE aluno_id = %s", (aluno_id,))
    media_result = cursor.fetchone()
    media = media_result["media"] if media_result["media"] else 0
    media_geral = round(media, 2)

    # Contar material pendente
    cursor.execute("""
        SELECT COUNT(*) as pendente 
        FROM solicitacoes_material 
        WHERE aluno_id = %s AND entregue = 0
    """, (aluno_id,))
    material_pendente_result = cursor.fetchone()
    material_pendente = material_pendente_result["pendente"] if material_pendente_result else 0

    # Contar declarações pendentes
    cursor.execute("""
        SELECT COUNT(*) as pendente 
        FROM solicitacoes_declaracoes 
        WHERE aluno_id = %s AND entregue = 0
    """, (aluno_id,))
    declaracoes_pendentes_result = cursor.fetchone()
    declaracoes_pendentes = declaracoes_pendentes_result["pendente"] if declaracoes_pendentes_result else 0

    # Buscar documentos não visualizados
    cursor.execute("""
        SELECT COUNT(*) as total 
        FROM documentos_enviados 
        WHERE aluno_id = %s AND status = 'enviado'
    """, (aluno_id,))
    nao_visualizados = cursor.fetchone()["total"] or 0

    # ========== DISCIPLINAS ALTERNATIVAS ==========
    # Buscar disciplinas alternativas do aluno
    cursor.execute("""
        SELECT da.*, 
               (SELECT COUNT(*) FROM anexos_disciplina_alternativa 
                WHERE aluno_id = %s AND disciplina_id = da.id) as total_anexos,
               (SELECT AVG(nota) FROM anexos_disciplina_alternativa 
                WHERE aluno_id = %s AND disciplina_id = da.id AND nota IS NOT NULL) as media_nota
        FROM disciplinas_alternativas da
        JOIN aluno_disciplina_alternativa ada ON da.id = ada.disciplina_id
        WHERE ada.aluno_id = %s AND da.ativa = 1
    """, (aluno_id, aluno_id, aluno_id))
    
    disciplinas_alternativas_raw = cursor.fetchall()
    
    # Converter para lista de dicionários e calcular progresso
    disciplinas_alternativas = []
    for da in disciplinas_alternativas_raw:
        da_dict = dict(da)
        media_nota = da_dict.get('media_nota') or 0
        da_dict['progresso'] = min(100, int(media_nota * 10)) if media_nota else 0
        disciplinas_alternativas.append(da_dict)
    # ========== FIM DISCIPLINAS ALTERNATIVAS ==========
    
    conn.close()

    # Funções para template
    def calcular_progresso(aluno_id, disciplina_id):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Contar capítulos totais da disciplina
            cursor.execute("SELECT COUNT(*) as total FROM capitulos WHERE disciplina_id = %s", (disciplina_id,))
            total_result = cursor.fetchone()
            total_capitulos = total_result["total"] if total_result else 0
            
            if total_capitulos == 0:
                conn.close()
                return 0
            
            # Contar provas realizadas (capítulos com nota)
            cursor.execute("""
                SELECT COUNT(DISTINCT capitulo) as feitas 
                FROM notas 
                WHERE aluno_id = %s AND disciplina_id = %s
            """, (aluno_id, disciplina_id))
            provas_result = cursor.fetchone()
            provas_feitas = provas_result["feitas"] if provas_result else 0
            
            # Calcular porcentagem
            progresso = (provas_feitas / total_capitulos) * 100 if total_capitulos > 0 else 0
            
            # Verificar se tem nota da prova final
            cursor.execute("""
                SELECT nota_final 
                FROM notas_finais 
                WHERE aluno_id = %s AND disciplina_id = %s
            """, (aluno_id, disciplina_id))
            nota_final_result = cursor.fetchone()
            nota_final = nota_final_result[0] if nota_final_result else None
            
            conn.close()
            
            # Se já fez prova final, progresso é 100%
            if nota_final is not None:
                return 100
            
            # Arredondar para múltiplos de 25 para mostrar progresso visual
            progresso_arredondado = round(progresso)
            if progresso_arredondado == 100:
                return 100
            elif progresso_arredondado >= 75:
                return 75
            elif progresso_arredondado >= 50:
                return 50
            elif progresso_arredondado >= 25:
                return 25
            else:
                return 0 if progresso_arredondado == 0 else 25
            
        except Exception as e:
            print(f"Erro ao calcular progresso: {e}")
            return 0

    def contar_capitulos(disciplina_id):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total FROM capitulos WHERE disciplina_id = %s", (disciplina_id,))
            total_result = cursor.fetchone()
            total = total_result["total"] if total_result else 0
            conn.close()
            return total
        except Exception as e:
            print(f"Erro ao contar capítulos: {e}")
            return 0

    def contar_provas_realizadas(aluno_id, disciplina_id):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(DISTINCT capitulo) as total 
                FROM notas 
                WHERE aluno_id = %s AND disciplina_id = %s
            """, (aluno_id, disciplina_id))
            total_result = cursor.fetchone()
            total = total_result["total"] if total_result else 0
            conn.close()
            return total
        except Exception as e:
            print(f"Erro ao contar provas: {e}")
            return 0

    return render_template(
        "dashboard.html",
        aluno_nome=session.get("aluno_nome"),
        aluno_ra=session.get("aluno_ra"),
        aluno_email=session.get("aluno_email"),
        dados_pessoais=dados_pessoais,
        situacao_financeira=situacao_financeira,
        disciplinas=disciplinas,
        disciplinas_alternativas=disciplinas_alternativas,  # NOVO PARÂMETRO
        notas=notas,
        solicitacoes_material=solicitacoes_material,
        solicitacoes_declaracoes=solicitacoes_declaracoes,
        total_provas_realizadas=total_provas,
        media_geral=media_geral,
        material_pendente=material_pendente,
        declaracoes_pendentes=declaracoes_pendentes,
        calcular_progresso=calcular_progresso,
        contar_capitulos=contar_capitulos,
        contar_provas_realizadas=contar_provas_realizadas,
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
    nota_final = nota_final_row[0] if nota_final_row else None
    
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
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})
    
    try:
        data = request.json
        conn = get_db_connection()
        cursor = conn.cursor()
        
        nota_final = data['nota_final'] if data['nota_final'] else None
        
        cursor.execute("""
            INSERT INTO notas_finais 
            (aluno_id, disciplina_id, nota_final, data_avaliacao)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (aluno_id, disciplina_id) DO UPDATE SET
                nota_final = EXCLUDED.nota_final,
                data_avaliacao = EXCLUDED.data_avaliacao
        """, (data['aluno_id'], data['disciplina_id'], nota_final))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Nota final salva com sucesso!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"})
    
    
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
        WHERE n.aluno_id = %s AND n.disciplina_id = %s AND n.capitulo = %s
    """, (aluno_id, disciplina_id, capitulo_numero))
    
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
                    background: #007bff; 
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
                    background: #007bff; 
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
        WHERE n.aluno_id = %s AND n.disciplina_id = %s AND n.capitulo = %s
    """, (aluno_id, disciplina_id, capitulo_numero))
    
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
                    background: #d1ecf1; 
                    color: #0c5460; 
                    padding: 30px; 
                    border-radius: 10px; 
                    margin: 20px auto; 
                    max-width: 600px;
                    border: 1px solid #bee5eb;
                }}
                .btn {{ 
                    display: inline-block; 
                    background: #007bff; 
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
                    background: #007bff; 
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
                    background: #007bff; 
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
        cursor.execute("""
            INSERT INTO notas (aluno_id, disciplina_id, capitulo, nota)
            VALUES (%s, %s, %s, %s)
        """, (aluno_id, disciplina_id, capitulo_numero, nota))
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
        WHERE n.aluno_id = %s AND n.disciplina_id = %s AND n.capitulo = %s
    """, (aluno_id, disciplina_id, capitulo_numero))
    
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
                    background: #d1ecf1; 
                    color: #0c5460; 
                    padding: 30px; 
                    border-radius: 10px; 
                    margin: 20px auto; 
                    max-width: 600px;
                    border: 1px solid #bee5eb;
                }}
                .btn {{ 
                    display: inline-block; 
                    background: #007bff; 
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
                    background: #007bff; 
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

    init_documentos_integrados_db()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS total FROM alunos")
    total_alunos = cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM disciplinas")
    total_disciplinas = cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM solicitacoes_material WHERE entregue = 0")
    material_pendente = cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM solicitacoes_declaracoes WHERE entregue = 0")
    declaracoes_pendente = cursor.fetchone()["total"]
    total_solicitacoes_pendentes = material_pendente + declaracoes_pendente
    cursor.execute("SELECT COUNT(*) AS total FROM notas")
    total_provas = cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM solicitacoes_documentos WHERE status = 'pendente'")
    documentos_pendente = cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM solicitacoes_documentos_integrados WHERE status IN ('pendente','erro','aguardando_aprovacao')")
    integrados_pendente = cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM documentos_autenticados WHERE tipo='plano_ensino'")
    total_planos = cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM documentos_autenticados")
    total_documentos = cursor.fetchone()["total"]
    conn.close()

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

    init_contratos_db()
    init_pagamentos_db()

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
            cursor.execute("""
                INSERT INTO alunos (nome, email, ra, senha)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (nome, email, ra, senha))
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

    cursor.execute("SELECT * FROM alunos ORDER BY nome")
    alunos = cursor.fetchall()
    alunos_completo = []

    for aluno in alunos:
        cursor.execute("SELECT * FROM dados_pessoais WHERE aluno_id = %s", (aluno["id"],))
        dados_pessoais = cursor.fetchone()

        cursor.execute("""
            SELECT * FROM situacao_financeira
            WHERE aluno_id = %s ORDER BY id DESC LIMIT 1
        """, (aluno["id"],))
        situacao_financeira = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as total FROM aluno_disciplina WHERE aluno_id = %s", (aluno["id"],))
        count = cursor.fetchone()

        cursor.execute("""
            SELECT ad.disciplina_id, d.nome, addd.data_inicio, addd.data_fim_previsto
            FROM aluno_disciplina ad
            LEFT JOIN aluno_disciplina_datas addd
              ON ad.aluno_id = addd.aluno_id AND ad.disciplina_id = addd.disciplina_id
            LEFT JOIN disciplinas d ON ad.disciplina_id = d.id
            WHERE ad.aluno_id = %s
        """, (aluno["id"],))
        disciplinas_aluno = cursor.fetchall()

        cursor.execute("""
            SELECT * FROM pagamentos_mercadopago
            WHERE aluno_id = %s
            ORDER BY CASE WHEN status = 'pago' THEN 0 ELSE 1 END, id DESC
            LIMIT 1
        """, (aluno["id"],))
        pagamento_mp = cursor.fetchone()

        cursor.execute("""
            SELECT * FROM contratos_alunos
            WHERE aluno_id = %s ORDER BY id DESC LIMIT 1
        """, (aluno["id"],))
        contrato = cursor.fetchone()

        alunos_completo.append({
            "id": aluno["id"], "nome": aluno["nome"], "email": aluno["email"], "ra": aluno["ra"],
            "cpf": dados_pessoais["cpf"] if dados_pessoais else "",
            "telefone": dados_pessoais["telefone"] if dados_pessoais else "",
            "forma_pagamento": situacao_financeira["forma_pagamento"] if situacao_financeira else "",
            "status_financeiro": situacao_financeira["status"] if situacao_financeira else "",
            "valor_total": situacao_financeira["valor_total"] if situacao_financeira else 0,
            "parcelas_total": situacao_financeira["parcelas_total"] if situacao_financeira else 0,
            "parcelas_pagas": situacao_financeira["parcelas_pagas"] if situacao_financeira else 0,
            "total_disciplinas": count["total"] if count else 0,
            "disciplinas_datas": disciplinas_aluno,
            "nome_pai": dados_pessoais["nome_pai"] if dados_pessoais else "",
            "nome_mae": dados_pessoais["nome_mae"] if dados_pessoais else "",
            "data_nascimento": dados_pessoais["data_nascimento"] if dados_pessoais else "",
            "sexo": dados_pessoais["sexo"] if dados_pessoais else "",
            "naturalidade": dados_pessoais["naturalidade"] if dados_pessoais else "",
            "nacionalidade": dados_pessoais["nacionalidade"] if dados_pessoais else "",
            "estado_civil": dados_pessoais["estado_civil"] if dados_pessoais else "",
            "email_alternativo": dados_pessoais["email_alternativo"] if dados_pessoais else "",
            "pagamento_mp": pagamento_mp,
            "contrato": contrato
        })

    cobranca_criada = None
    cobranca_id = request.args.get("cobranca_id")
    if cobranca_id and cobranca_id.isdigit():
        cursor.execute("SELECT * FROM pagamentos_mercadopago WHERE id = %s", (int(cobranca_id),))
        cobranca_criada = cursor.fetchone()

    conn.close()
    return render_template(
        "mew/alunos.html",
        disciplinas=disciplinas,
        alunos=alunos_completo,
        cobranca_criada=cobranca_criada,
        erro_mp=request.args.get("erro_mp"),
        sucesso=request.args.get("sucesso")
    )


@app.route("/mew/gerar-cobranca/<int:aluno_id>", methods=["POST"])
def mew_gerar_cobranca_aluno(aluno_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    init_pagamentos_db()
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
    init_pagamentos_db()
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

        # E-mail transacional: não bloqueia o webhook caso o Titan ainda não esteja configurado.
        if status_mp == "approved" and not ja_pago:
            try:
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
    return render_template_string("""
    <div style='font-family:Arial;max-width:680px;margin:70px auto;text-align:center'>
      <h1 style='color:#15803d'>✅ Pagamento recebido</h1>
      <p>O Mercado Pago informou que o pagamento foi aprovado. O status também será confirmado automaticamente pelo sistema.</p>
      <a href='/dashboard'>Ir para o ambiente do aluno</a>
    </div>""")


@app.route("/pagamento/mercadopago/pendente")
def pagamento_mercadopago_pendente():
    return render_template_string("""
    <div style='font-family:Arial;max-width:680px;margin:70px auto;text-align:center'>
      <h1>⏳ Pagamento pendente</h1><p>Assim que o Mercado Pago aprovar, o sistema atualizará o status automaticamente.</p>
      <a href='/'>Voltar</a>
    </div>""")


@app.route("/pagamento/mercadopago/falha")
def pagamento_mercadopago_falha():
    return render_template_string("""
    <div style='font-family:Arial;max-width:680px;margin:70px auto;text-align:center'>
      <h1 style='color:#b91c1c'>❌ Pagamento não concluído</h1><p>Nenhuma baixa financeira foi realizada.</p>
      <a href='/'>Voltar</a>
    </div>""")


@app.route("/mew/disciplinas", methods=["GET", "POST"])
def mew_disciplinas():
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        # 1. Criar a disciplina
        nome_disciplina = request.form.get("nome_disciplina")
        cursor.execute("INSERT INTO disciplinas (nome) VALUES (%s) RETURNING id", (nome_disciplina,))
        disciplina_id = cursor.fetchone()["id"]

        # 2. Criar os 4 capítulos com seus materiais e provas
        for i in range(1, 5):
            titulo = request.form.get(f"titulo_{i}")
            video_url = request.form.get(f"video_{i}")
            pdf_url = request.form.get(f"pdf_{i}")
            questoes_json = request.form.get(f"questoes_{i}")

            # Validar JSON das questões
            try:
                json.loads(questoes_json)  # Valida se é JSON válido
            except json.JSONDecodeError:
                # Se JSON inválido, criar um padrão
                questoes_json = json.dumps([
                    {
                        "pergunta": f"Pergunta padrão do capítulo {i}",
                        "opcoes": {"A": "Opção A", "B": "Opção B", "C": "Opção C", "D": "Opção D"},
                        "resposta_certa": "A"
                    }
                ])

            # Inserir capítulo
            cursor.execute("""
                INSERT INTO capitulos (disciplina_id, titulo, video_url, pdf_url)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (disciplina_id, titulo, video_url, pdf_url))
            
            capitulo_id = cursor.fetchone()["id"]

            # Inserir prova com as questões
            cursor.execute("""
                INSERT INTO provas (capitulo_id, questoes_json)
                VALUES (%s, %s)
            """, (capitulo_id, questoes_json))

        conn.commit()
        conn.close()
        return redirect("/mew/disciplinas")

    # GET: Mostrar disciplinas existentes
    cursor.execute("SELECT * FROM disciplinas ORDER BY id")
    disciplinas = cursor.fetchall()
    conn.close()

    return render_template("mew/disciplinas.html", disciplinas=disciplinas)

@app.route("/mew/editar-disciplina/<int:disciplina_id>", methods=["GET", "POST"])
def mew_editar_disciplina(disciplina_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()


    if request.method == "POST":
        # Atualizar nome da disciplina
        nome = request.form.get("nome_disciplina")
        cursor.execute("UPDATE disciplinas SET nome = %s WHERE id = %s", (nome, disciplina_id))   

        # Atualizar capítulos
        cursor.execute("SELECT id FROM capitulos WHERE disciplina_id = %s ORDER BY id", (disciplina_id,))
        capitulos = cursor.fetchall()


        for i, cap in enumerate(capitulos, start=1):
            titulo = request.form.get(f"titulo_{i}")
            video = request.form.get(f"video_{i}")
            pdf = request.form.get(f"pdf_{i}")
            questoes = request.form.get(f"questoes_{i}")

            # valida JSON
            try:
                json.loads(questoes)
            except Exception as e:
                print(f"[MEW][Editar Disciplina] JSON inválido | Disciplina {disciplina_id} | Capítulo {cap['id']} | Erro: {e}")
                continue

            cursor.execute("""
                UPDATE capitulos
                SET titulo = %s, video_url = %s, pdf_url = %s
                WHERE id = %s
            """, (titulo, video, pdf, cap["id"]))

            cursor.execute("UPDATE provas SET questoes_json = %s WHERE capitulo_id = %s",
            (questoes, cap["id"]))

        conn.commit()
        conn.close()
        return redirect("/mew/disciplinas")

    # GET
    cursor.execute("SELECT * FROM disciplinas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()

    cursor.execute("""
        SELECT c.*, p.questoes_json
        FROM capitulos c
        LEFT JOIN provas p ON p.capitulo_id = c.id
        WHERE c.disciplina_id = %s
        ORDER BY c.id
    """, (disciplina_id,))


    capitulos = cursor.fetchall()
    conn.close()

    return render_template("mew/editar_disciplina.html",
                            disciplina=disciplina,
                            capitulos=capitulos)

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
        SELECT sd.*, a.nome as aluno_nome, a.email as aluno_email
        FROM solicitacoes_documentos sd
        JOIN alunos a ON sd.aluno_id = a.id
        ORDER BY sd.data_solicitacao DESC
    """)
    solicitacoes_documentos = cursor.fetchall()
    
    # Para cada documento, buscar disciplinas
    for s in solicitacoes_documentos:
        disciplinas_ids = s['disciplinas_ids']
        if disciplinas_ids:
            ids_list = [int(id.strip()) for id in disciplinas_ids.split(',') if id.strip()]
            if ids_list:
                placeholders = ','.join(['%s'] * len(ids_list))
                cursor.execute(f"""
                    SELECT STRING_AGG(nome, ',') as nomes
                    FROM disciplinas 
                    WHERE id IN ({placeholders})
                """, ids_list)
                result = cursor.fetchone()
                s['disciplinas_nomes'] = result['nomes'] if result and result['nomes'] else ''
            else:
                s['disciplinas_nomes'] = ''
        else:
            s['disciplinas_nomes'] = ''
    
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
                """, (nome, email, senha, aluno_id))
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
    """Retorna o histórico de solicitações de documentos do aluno"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "message": "Não autenticado"})
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT sd.*
        FROM solicitacoes_documentos sd
        WHERE sd.aluno_id = %s
        ORDER BY sd.data_solicitacao DESC
    """, (aluno_id,))
    
    solicitacoes_raw = cursor.fetchall()
    
    # Converter para lista de dicionários
    resultado = []
    for s in solicitacoes_raw:
        s_dict = dict(s)
        
        # Buscar nomes das disciplinas
        disciplinas_ids = s_dict['disciplinas_ids']
        if disciplinas_ids:
            # Converter string de IDs em lista
            ids_list = [int(id.strip()) for id in disciplinas_ids.split(',') if id.strip()]
            if ids_list:
                # Buscar nomes das disciplinas
                placeholders = ','.join(['%s'] * len(ids_list))
                cursor.execute(f"""
                    SELECT STRING_AGG(nome, ',') as nomes
                    FROM disciplinas 
                    WHERE id IN ({placeholders})
                """, ids_list)
                result = cursor.fetchone()
                s_dict['disciplinas_nomes'] = result['nomes'] if result and result['nomes'] else 'N/A'
            else:
                s_dict['disciplinas_nomes'] = 'N/A'
        else:
            s_dict['disciplinas_nomes'] = 'N/A'
        
        resultado.append(s_dict)
    
    conn.close()
    
    return jsonify({"success": True, "solicitacoes": resultado})

@app.route("/mew/solicitacoes-documentos")
def mew_solicitacoes_documentos():
    """Painel MEW para gerenciar solicitações de documentos"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Buscar solicitações de documentos
    cursor.execute("""
        SELECT sd.*, a.nome as aluno_nome, a.email as aluno_email
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
    """)
    
    solicitacoes_raw = cursor.fetchall()
    
    # Converter para lista de dicionários
    solicitacoes = []
    for s in solicitacoes_raw:
        # Converter registro para dicionário
        s_dict = dict(s)
        
        # Buscar nomes das disciplinas
        disciplinas_ids = s_dict['disciplinas_ids']
        if disciplinas_ids:
            # Converter string de IDs em lista
            ids_list = [int(id.strip()) for id in disciplinas_ids.split(',') if id.strip()]
            if ids_list:
                # Buscar nomes das disciplinas
                placeholders = ','.join(['%s'] * len(ids_list))
                cursor.execute(f"""
                    SELECT STRING_AGG(nome, ',') as nomes
                    FROM disciplinas 
                    WHERE id IN ({placeholders})
                """, ids_list)
                result = cursor.fetchone()
                s_dict['disciplinas_nomes'] = result['nomes'] if result and result['nomes'] else ''
            else:
                s_dict['disciplinas_nomes'] = ''
        else:
            s_dict['disciplinas_nomes'] = ''
        
        solicitacoes.append(s_dict)
    
    conn.close()
    
    return render_template("mew/solicitacoes_documentos.html", solicitacoes=solicitacoes)

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
                    background: #d1ecf1; 
                    color: #0c5460; 
                    padding: 30px; 
                    border-radius: 10px; 
                    margin: 20px auto; 
                    max-width: 600px;
                    border: 1px solid #bee5eb;
                }
                .btn { 
                    display: inline-block; 
                    background: #007bff; 
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
                    background: #007bff; 
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
    
    # Calcular média das 4 provas da disciplina
    cursor.execute("""
        SELECT AVG(nota) as media_disciplina 
        FROM notas 
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))
    
    result = cursor.fetchone()
    media_disciplina = result["media_disciplina"] if result and result["media_disciplina"] else 0
    
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
    cursor.execute("SELECT COUNT(*) as total FROM notas_finais WHERE status = 'aprovado'")
    total_aprovados = cursor.fetchone()["total"] or 0
    cursor.execute("SELECT COUNT(*) as total FROM notas_finais WHERE status = 'reprovado'")
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

@app.route("/mew/importar-questoes-json/<int:disciplina_id>", methods=["POST"])
def importar_questoes_json(disciplina_id):
    """Importa questões da prova final via JSON - VERSÃO CORRIGIDA"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})
    
    try:
        # Obter o JSON enviado
        json_data = request.form.get("questoes_json")
        
        if not json_data:
            return jsonify({"success": False, "message": "JSON vazio"})
        
        # Parse do JSON
        questoes = json.loads(json_data)
        
        # Validar formato
        if not isinstance(questoes, list):
            return jsonify({"success": False, "message": "Formato inválido. Deve ser uma lista."})
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        count = 0
        for q in questoes:
            # FORMATO 1: Com opcoes como dicionário
            if 'opcoes' in q and isinstance(q['opcoes'], dict):
                try:
                    cursor.execute("""
                        INSERT INTO questoes_finais 
                        (disciplina_id, pergunta, opcao_a, opcao_b, opcao_c, opcao_d, resposta_correta)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        disciplina_id,
                        q['pergunta'],
                        q['opcoes'].get('A', ''),
                        q['opcoes'].get('B', ''),
                        q['opcoes'].get('C', ''),
                        q['opcoes'].get('D', ''),
                        q.get('resposta_certa', '')  # Note: resposta_certa (com 'a' no final)
                    ))
                    count += 1
                except Exception as e:
                    print(f"Erro ao inserir questão: {e}")
                    continue
                    
            # FORMATO 2: Com opcao_a, opcao_b, etc diretamente
            elif all(k in q for k in ['opcao_a', 'opcao_b', 'opcao_c', 'opcao_d']):
                try:
                    cursor.execute("""
                        INSERT INTO questoes_finais 
                        (disciplina_id, pergunta, opcao_a, opcao_b, opcao_c, opcao_d, resposta_correta)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        disciplina_id,
                        q['pergunta'],
                        q['opcao_a'],
                        q['opcao_b'],
                        q['opcao_c'],
                        q['opcao_d'],
                        q.get('resposta_correta', q.get('resposta_certa', ''))
                    ))
                    count += 1
                except Exception as e:
                    print(f"Erro ao inserir questão: {e}")
                    continue
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": f"{count} questões importadas com sucesso!",
            "count": count
        })
        
    except json.JSONDecodeError as e:
        return jsonify({"success": False, "message": f"JSON inválido: {str(e)}"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"})
    
@app.route("/mew/exportar-questoes-json/<int:disciplina_id>")
def exportar_questoes_json(disciplina_id):
    """Exporta questões como JSON"""
    if not session.get("mew_admin"):
        return jsonify({"error": "Não autorizado"})
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT pergunta, opcao_a, opcao_b, opcao_c, opcao_d, resposta_correta
        FROM questoes_finais 
        WHERE disciplina_id = %s
        ORDER BY id
    """, (disciplina_id,))
    
    questoes = []
    for row in cursor.fetchall():
        questoes.append(dict(row))
    
    conn.close()
    
    return jsonify({
        "disciplina_id": disciplina_id,
        "total_questoes": len(questoes),
        "questoes": questoes
    })
    
@app.route("/situacao-academica")
def situacao_academica():
    """Página com situação acadêmica completa do aluno"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Buscar dados do aluno
    cursor.execute("SELECT nome, ra FROM alunos WHERE id = %s", (aluno_id,))
    aluno = cursor.fetchone()
    
    if not aluno:
        flash("Aluno não encontrado.", "error")
        return redirect(url_for("dashboard"))
    
    # Buscar disciplinas do aluno
    cursor.execute("""
        SELECT d.id, d.nome
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        WHERE ad.aluno_id = %s
        ORDER BY d.nome
    """, (aluno_id,))
    disciplinas = cursor.fetchall()
    
    # Buscar notas dos capítulos
    cursor.execute("""
        SELECT n.disciplina_id, n.capitulo, n.nota, d.nome AS disciplina_nome
        FROM notas n
        JOIN disciplinas d ON n.disciplina_id = d.id
        WHERE n.aluno_id = %s
        ORDER BY n.disciplina_id, n.capitulo
    """, (aluno_id,))
    notas_capitulos = cursor.fetchall()
    
    # Buscar notas finais
    cursor.execute("""
        SELECT nf.*, d.nome as disciplina_nome
        FROM notas_finais nf
        JOIN disciplinas d ON nf.disciplina_id = d.id
        WHERE nf.aluno_id = %s
        ORDER BY d.nome
    """, (aluno_id,))
    notas_finais = cursor.fetchall()
    
    # Calcular situação de cada disciplina
    situacao_disciplinas = []
    
    for d in disciplinas:
        disciplina_id = d['id']
        disciplina_nome = d['nome']
        
        # Buscar notas dos capítulos desta disciplina
        notas_disc = [n for n in notas_capitulos if n['disciplina_id'] == disciplina_id]
        
        # Buscar nota final desta disciplina
        nota_final = next((nf for nf in notas_finais if nf['disciplina_id'] == disciplina_id), None)
        
        # Calcular média dos capítulos
        media_capitulos = 0
        if notas_disc:
            media_capitulos = sum(n['nota'] for n in notas_disc) / len(notas_disc)
        
        # Calcular situação
        status = "cursando"
        media_final = None
        situacao = "Cursando"
        
        if nota_final:
            media_final = nota_final['media_final']
            status = nota_final['status']
            situacao = "Aprovado" if status == "aprovado" else "Reprovado"
        elif len(notas_disc) == 4:  # Todas as 4 provas feitas, mas sem final
            media_final = media_capitulos
            situacao = "Aguardando final"
            status = "aguardando_final"
        elif len(notas_disc) > 0:  # Algumas provas feitas
            situacao = "Em andamento"
            status = "cursando"
        
        situacao_disciplinas.append({
            'id': disciplina_id,
            'nome': disciplina_nome,
            'notas_capitulos': notas_disc,
            'nota_final': nota_final,
            'media_capitulos': round(media_capitulos, 2) if notas_disc else 0,
            'media_final': round(media_final, 2) if media_final else None,
            'status': status,
            'situacao': situacao,
            'capitulos_feitos': len(notas_disc),
            'capitulos_total': 4
        })
    
    # Calcular estatísticas gerais
    total_disciplinas = len(situacao_disciplinas)
    disciplinas_aprovadas = len([d for d in situacao_disciplinas if d['situacao'] == "Aprovado"])
    disciplinas_reprovadas = len([d for d in situacao_disciplinas if d['situacao'] == "Reprovado"])
    disciplinas_cursando = len([d for d in situacao_disciplinas if d['situacao'] == "Em andamento"])
    disciplinas_aguardando_final = len([d for d in situacao_disciplinas if d['situacao'] == "Aguardando final"])
    
    # Calcular média geral (considerando apenas disciplinas com nota final)
    disciplinas_com_final = [d for d in situacao_disciplinas if d['media_final'] is not None]
    media_geral = sum(d['media_final'] for d in disciplinas_com_final) / len(disciplinas_com_final) if disciplinas_com_final else 0
    
    conn.close()
    
    return render_template(
        "situacao_academica.html",
        aluno_nome=aluno['nome'],
        aluno_ra=aluno['ra'],
        situacao_disciplinas=situacao_disciplinas,
        total_disciplinas=total_disciplinas,
        disciplinas_aprovadas=disciplinas_aprovadas,
        disciplinas_reprovadas=disciplinas_reprovadas,
        disciplinas_cursando=disciplinas_cursando,
        disciplinas_aguardando_final=disciplinas_aguardando_final,
        media_geral=round(media_geral, 2),
        notas_finais=notas_finais,
        now=datetime.now()
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

def buscar_documento_db(codigo):
    """Busca documento no banco - VERSÃO CORRETA para sua estrutura de tabela"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Buscar pelo código (conforme sua tabela documentos_autenticados)
        cursor.execute("SELECT * FROM documentos_autenticados WHERE codigo = %s", (codigo,))
        
        documento = cursor.fetchone()
        conn.close()
        
        if documento:
            # Converter para dicionário (ajuste os índices conforme sua tabela)
            # Sua tabela tem: 0=id, 1=codigo, 2=aluno_nome, 3=aluno_ra, 4=tipo, 5=conteudo_html, 6=data_geracao
            return {
                'codigo': documento[1],
                'aluno_nome': documento[2],
                'aluno_ra': documento[3],
                'tipo': documento[4],
                'conteudo_html': documento[5],
                'data_geracao': documento[6]
            }
        return None
        
    except Exception as e:
        print(f"Erro ao buscar documento: {e}")
        return None

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
    """Busca documento no banco - VERSÃO CORRETA para sua estrutura de tabela"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Buscar pelo código (conforme sua tabela documentos_autenticados)
        cursor.execute("SELECT * FROM documentos_autenticados WHERE codigo = %s", (codigo,))
        
        documento = cursor.fetchone()
        conn.close()
        
        if documento:
            # Converter para dicionário (ajuste os índices conforme sua tabela)
            # Sua tabela tem: 0=id, 1=codigo, 2=aluno_nome, 3=aluno_ra, 4=tipo, 5=conteudo_html, 6=data_geracao
            return {
                'codigo': documento[1],
                'aluno_nome': documento[2],
                'aluno_ra': documento[3],
                'tipo': documento[4],
                'conteudo_html': documento[5],
                'data_geracao': documento[6]
            }
        return None
        
    except Exception as e:
        print(f"Erro ao buscar documento: {e}")
        return None
    
    
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
    
    # Buscar disciplinas do aluno
    cursor.execute("""
        SELECT d.id, d.nome, 
               (SELECT COUNT(*) FROM capitulos WHERE disciplina_id = d.id) as total_capitulos,
               (SELECT COUNT(DISTINCT capitulo) FROM notas 
                WHERE aluno_id = %s AND disciplina_id = d.id) as provas_feitas
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        WHERE ad.aluno_id = %s
        ORDER BY d.nome
    """, (aluno_id, aluno_id))
    
    disciplinas = cursor.fetchall()
    
    conn.close()
    
    return render_template(
        "mew/notas_disciplinas.html",
        aluno=aluno,
        disciplinas=disciplinas
    )

@app.route("/mew/gerenciar-notas/disciplina/<int:aluno_id>/<int:disciplina_id>")
def mew_notas_disciplina(aluno_id, disciplina_id):
    """Mostra e gerencia notas de uma disciplina específica"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Buscar informações do aluno e disciplina
    cursor.execute("SELECT id, nome, ra FROM alunos WHERE id = %s", (aluno_id,))
    aluno = cursor.fetchone()
    
    cursor.execute("SELECT id, nome FROM disciplinas WHERE id = %s", (disciplina_id,))
    disciplina = cursor.fetchone()
    
    if not aluno or not disciplina:
        conn.close()
        return "Aluno ou disciplina não encontrados", 404
    
    # Buscar capítulos da disciplina
    cursor.execute("SELECT id, titulo FROM capitulos WHERE disciplina_id = %s ORDER BY id", (disciplina_id,))
    capitulos = cursor.fetchall()
    
    # Buscar notas existentes
    cursor.execute("""
        SELECT capitulo, nota 
        FROM notas 
        WHERE aluno_id = %s AND disciplina_id = %s
        ORDER BY capitulo
    """, (aluno_id, disciplina_id))
    notas_existentes = {row['capitulo']: row['nota'] for row in cursor.fetchall()}
    
    # Buscar nota final (se existir)
    cursor.execute("""
        SELECT nota_final, media_disciplina, media_final, status 
        FROM notas_finais 
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))
    nota_final = cursor.fetchone()
    
    # Buscar datas de liberação dos capítulos
    cursor.execute("""
        SELECT data_inicio, prova_final_aberta 
        FROM aluno_disciplina_datas 
        WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))
    datas_info = cursor.fetchone()
    
    # Calcular progresso atual
    total_capitulos = len(capitulos)
    provas_feitas = len(notas_existentes)
    progresso_atual = 0
    if total_capitulos > 0:
        progresso_percentual = (provas_feitas / total_capitulos) * 100
        # Arredondar para 0, 25, 50, 75, 100
        if progresso_percentual == 100:
            progresso_atual = 100
        elif progresso_percentual >= 75:
            progresso_atual = 75
        elif progresso_percentual >= 50:
            progresso_atual = 50
        elif progresso_percentual >= 25:
            progresso_atual = 25
        else:
            progresso_atual = 0
    
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
        total_capitulos=total_capitulos,
        provas_feitas=provas_feitas
    )

@app.route("/mew/gerenciar-notas/salvar", methods=["POST"])
def mew_salvar_notas():
    """Salva ou atualiza notas do aluno"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})
    
    aluno_id = request.form.get("aluno_id")
    disciplina_id = request.form.get("disciplina_id")
    acao = request.form.get("acao")
    
    if not all([aluno_id, disciplina_id, acao]):
        return jsonify({"success": False, "message": "Dados incompletos"})
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        if acao == "salvar_nota":
            capitulo = request.form.get("capitulo")
            nota = request.form.get("nota")
            
            if not capitulo or not nota:
                conn.close()
                return jsonify({"success": False, "message": "Capítulo ou nota não informados"})
            
            # Verificar se já existe nota
            cursor.execute("""
                SELECT id FROM notas 
                WHERE aluno_id = %s AND disciplina_id = %s AND capitulo = %s
            """, (aluno_id, disciplina_id, capitulo))
            
            if cursor.fetchone():
                # Atualizar
                cursor.execute("""
                    UPDATE notas SET nota = %s 
                    WHERE aluno_id = %s AND disciplina_id = %s AND capitulo = %s
                """, (nota, aluno_id, disciplina_id, capitulo))
            else:
                # Inserir
                cursor.execute("""
                    INSERT INTO notas (aluno_id, disciplina_id, capitulo, nota)
                    VALUES (%s, %s, %s, %s)
                """, (aluno_id, disciplina_id, capitulo, nota))
            
            message = "Nota salva com sucesso"
            
        elif acao == "excluir_nota":
            capitulo = request.form.get("capitulo")
            
            if not capitulo:
                conn.close()
                return jsonify({"success": False, "message": "Capítulo não informado"})
            
            cursor.execute("""
                DELETE FROM notas 
                WHERE aluno_id = %s AND disciplina_id = %s AND capitulo = %s
            """, (aluno_id, disciplina_id, capitulo))
            
            message = "Nota excluída com sucesso"
            
        elif acao == "salvar_final":
            nota_final_val = request.form.get("nota_final")
            media_disciplina = request.form.get("media_disciplina")
            media_final = request.form.get("media_final")
            status = request.form.get("status")
            
            if not all([nota_final_val, media_disciplina, media_final, status]):
                conn.close()
                return jsonify({"success": False, "message": "Dados da prova final incompletos"})
            
            # Verificar se já existe nota final
            cursor.execute("""
                SELECT id FROM notas_finais 
                WHERE aluno_id = %s AND disciplina_id = %s
            """, (aluno_id, disciplina_id))
            
            if cursor.fetchone():
                # Atualizar
                cursor.execute("""
                    UPDATE notas_finais 
                    SET nota_final = %s, media_disciplina = %s, media_final = %s, status = %s
                    WHERE aluno_id = %s AND disciplina_id = %s
                """, (nota_final_val, media_disciplina, media_final, status, aluno_id, disciplina_id))
            else:
                # Inserir
                data_realizacao = datetime.now().strftime("%d/%m/%Y %H:%M")
                cursor.execute("""
                    INSERT INTO notas_finais 
                    (aluno_id, disciplina_id, nota_final, media_disciplina, media_final, status, data_realizacao)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (aluno_id, disciplina_id, nota_final_val, media_disciplina, media_final, status, data_realizacao))
            
            message = "Nota final salva com sucesso"
            
        elif acao == "excluir_final":
            cursor.execute("""
                DELETE FROM notas_finais 
                WHERE aluno_id = %s AND disciplina_id = %s
            """, (aluno_id, disciplina_id))
            
            message = "Nota final excluída com sucesso"
            
        elif acao == "atualizar_progresso":
            novo_progresso = request.form.get("progresso")
            data_inicio = request.form.get("data_inicio")
            prova_final_aberta = request.form.get("prova_final_aberta", "0")
            
            if not novo_progresso:
                conn.close()
                return jsonify({"success": False, "message": "Progresso não informado"})
            
            # Determinar capítulos feitos baseado no progresso
            progresso_map = {
                "0": 0,   # 0% - nenhuma prova
                "25": 1,  # 25% - 1ª prova
                "50": 2,  # 50% - 2ªs provas
                "75": 3,  # 75% - 3ªs provas
                "100": 4  # 100% - 4ªs provas
            }
            
            cap_feitos = progresso_map.get(novo_progresso, 0)
            
            # Remover notas além do progresso
            if cap_feitos < 4:
                cursor.execute("""
                    DELETE FROM notas 
                    WHERE aluno_id = %s AND disciplina_id = %s AND capitulo > %s
                """, (aluno_id, disciplina_id, cap_feitos))
            
            # Atualizar datas
            cursor.execute("""
                SELECT id FROM aluno_disciplina_datas 
                WHERE aluno_id = %s AND disciplina_id = %s
            """, (aluno_id, disciplina_id))
            
            if cursor.fetchone():
                # Atualizar
                if data_inicio:
                    cursor.execute("""
                        UPDATE aluno_disciplina_datas 
                        SET data_inicio = %s, prova_final_aberta = %s
                        WHERE aluno_id = %s AND disciplina_id = %s
                    """, (data_inicio, prova_final_aberta, aluno_id, disciplina_id))
                else:
                    cursor.execute("""
                        UPDATE aluno_disciplina_datas 
                        SET prova_final_aberta = %s
                        WHERE aluno_id = %s AND disciplina_id = %s
                    """, (prova_final_aberta, aluno_id, disciplina_id))
            else:
                # Inserir (se tiver data_inicio)
                if data_inicio:
                    cursor.execute("""
                        INSERT INTO aluno_disciplina_datas 
                        (aluno_id, disciplina_id, data_inicio, prova_final_aberta)
                        VALUES (%s, %s, %s, %s)
                    """, (aluno_id, disciplina_id, data_inicio, prova_final_aberta))
            
            message = "Progresso atualizado com sucesso"
            
        else:
            conn.close()
            return jsonify({"success": False, "message": "Ação inválida"})
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True, 
            "message": message,
            "redirect": f"/mew/gerenciar-notas/disciplina/{aluno_id}/{disciplina_id}"
        })
        
    except Exception as e:
        conn.close()
        return jsonify({"success": False, "message": f"Erro: {str(e)}"})
    
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
        
def salvar_documento_autenticado(documento_data):
    """Salva um documento autenticado no banco - VERSÃO SIMPLIFICADA"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Verificar se a tabela existe
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documentos_autenticados (
            id SERIAL PRIMARY KEY,
            codigo TEXT UNIQUE,
            aluno_nome TEXT,
            aluno_ra TEXT,
            tipo TEXT,
            conteudo_html TEXT,
            data_geracao TEXT
            )
        """)
        
        # Inserir documento
        cursor.execute("""
            INSERT INTO documentos_autenticados 
            (codigo_autenticacao, aluno_id, tipo_documento, hash_documento, 
             conteudo_html, data_emissao, observacoes, aluno_nome, aluno_ra)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            documento_data['codigo_autenticacao'],
            documento_data['aluno_id'],
            documento_data['tipo_documento'],
            documento_data['hash_documento'],
            documento_data['conteudo_html'],
            documento_data['data_emissao'].strftime('%d/%m/%Y %H:%M'),
            documento_data.get('observacoes', ''),
            documento_data.get('aluno_nome', ''),
            documento_data.get('aluno_ra', '')
        ))
        
        documento_id = cursor.fetchone()["id"]
        conn.commit()
        conn.close()
        
        return documento_id
        
    except Exception as e:
        print(f"Erro em salvar_documento_autenticado: {e}")
        if 'conn' in locals():
            conn.close()
        return None
    
    
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
    """Busca todas as disciplinas de um aluno com notas - VERSÃO MELHORADA"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            d.id, 
            d.nome, 
            d.carga_horaria,
            addd.data_inicio,
            addd.data_fim_previsto,
            doc.nome as docente_nome,
            doc.titulacao as docente_titulacao,
            dd.ano_semestre,
            n1.nota as nota1,
            n2.nota as nota2,
            n3.nota as nota3,
            n4.nota as nota4,
            nf.nota_final,
            nf.media_disciplina,
            nf.media_final,
            nf.status as status_final
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        LEFT JOIN aluno_disciplina_datas addd ON ad.aluno_id = addd.aluno_id 
            AND ad.disciplina_id = addd.disciplina_id
        LEFT JOIN disciplina_docente dd ON d.id = dd.disciplina_id
        LEFT JOIN docentes doc ON dd.docente_id = doc.id
        LEFT JOIN notas n1 ON ad.aluno_id = n1.aluno_id AND d.id = n1.disciplina_id AND n1.capitulo = 1
        LEFT JOIN notas n2 ON ad.aluno_id = n2.aluno_id AND d.id = n2.disciplina_id AND n2.capitulo = 2
        LEFT JOIN notas n3 ON ad.aluno_id = n3.aluno_id AND d.id = n3.disciplina_id AND n3.capitulo = 3
        LEFT JOIN notas n4 ON ad.aluno_id = n4.aluno_id AND d.id = n4.disciplina_id AND n4.capitulo = 4
        LEFT JOIN notas_finais nf ON ad.aluno_id = nf.aluno_id AND d.id = nf.disciplina_id
        WHERE ad.aluno_id = %s
        GROUP BY d.id
        ORDER BY d.nome
    """, (aluno_id,))
    
    disciplinas_raw = cursor.fetchall()
    disciplinas = []
    
    from datetime import datetime
    
    for disc in disciplinas_raw:
        # Determinar carga horária
        carga_horaria = disc['carga_horaria'] if disc['carga_horaria'] else 80
        
        # Determinar docente
        docente_display = "Docente Responsável — Coordenação Acadêmica SIGEU"
        if disc['docente_nome']:
            docente_display = disc['docente_nome']
            if disc['docente_titulacao']:
                docente_display += f" ({disc['docente_titulacao']})"
        
        # Determinar período
        periodo = disc['ano_semestre'] if disc['ano_semestre'] else ""
        if not periodo and disc['data_inicio']:
            try:
                data_obj = datetime.strptime(disc['data_inicio'], "%d/%m/%Y")
                ano = data_obj.year
                mes = data_obj.month
                semestre = "1" if mes <= 6 else "2"
                periodo = f"{ano}.{semestre}"
            except:
                periodo = f"{datetime.now().year}.1"
        elif not periodo:
            periodo = f"{datetime.now().year}.1"
        
        # Determinar nota para exibição
        nota_final = disc['media_final'] if disc['media_final'] is not None else disc['nota_final']
        if nota_final is not None:
            nota_exibicao = round(float(nota_final), 2)
        else:
            nota_exibicao = None
        
        # Determinar status
        if disc['status_final'] == 'aprovado':
            status_display = 'APROVADO'
        elif disc['status_final'] == 'reprovado':
            status_display = 'REPROVADO'
        else:
            status_display = 'CURSANDO'
        
        # Determinar semestre
        semestre = periodo.split('.')[-1] if '.' in periodo else "1"
        
        disciplina = {
            'id': disc['id'],
            'nome': disc['nome'],
            'periodo': periodo,
            'semestre': semestre,
            'carga': carga_horaria,
            'docente': docente_display,
            'nota': nota_exibicao,
            'status': status_display,
            'nota1': disc['nota1'],
            'nota2': disc['nota2'],
            'nota3': disc['nota3'],
            'nota4': disc['nota4'],
            'nota_final': disc['nota_final'],
            'media_final': disc['media_final']
        }
        disciplinas.append(disciplina)
    
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
    """Salva um documento autenticado no banco - VERSÃO CORRIGIDA"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Verificar se a tabela existe
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documentos_autenticados (
                id SERIAL PRIMARY KEY,
                codigo_autenticacao TEXT UNIQUE,
                aluno_id INTEGER,
                tipo_documento TEXT,
                hash_documento TEXT,
                conteudo_html TEXT,
                data_emissao TEXT,
                observacoes TEXT,
                aluno_nome TEXT,
                aluno_ra TEXT,
                FOREIGN KEY (aluno_id) REFERENCES alunos(id)
            )
        """)
        
        # Inserir documento
        cursor.execute("""
            INSERT INTO documentos_autenticados 
            (codigo_autenticacao, aluno_id, tipo_documento, hash_documento, 
             conteudo_html, data_emissao, observacoes, aluno_nome, aluno_ra)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            documento_data['codigo_autenticacao'],
            documento_data['aluno_id'],
            documento_data['tipo_documento'],
            documento_data['hash_documento'],
            documento_data['conteudo_html'],
            documento_data['data_emissao'].strftime('%d/%m/%Y %H:%M'),
            documento_data.get('observacoes', ''),
            documento_data.get('aluno_nome', ''),
            documento_data.get('aluno_ra', '')
        ))
        
        documento_id = cursor.fetchone()["id"]
        conn.commit()
        
    except Exception as e:
        print(f"Erro ao salvar documento: {e}")
        documento_id = None
    finally:
        conn.close()
    
    return documento_id

@app.route("/mew/visualizar-documento/<codigo>")
def mew_visualizar_documento(codigo):
    """Visualiza tanto documentos antigos (codigo_autenticacao) quanto novos (codigo)."""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    init_documentos_integrados_db()
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM documentos_autenticados WHERE codigo=%s OR codigo_autenticacao=%s ORDER BY id DESC LIMIT 1", (codigo, codigo))
    documento = cursor.fetchone(); conn.close()
    if not documento:
        return "Documento não encontrado", 404
    doc = dict(documento)
    cod = doc.get("codigo") or doc.get("codigo_autenticacao") or codigo
    conteudo = doc.get("conteudo_html") or "<p>Conteúdo indisponível.</p>"
    return f"""<!DOCTYPE html><html><head><meta charset='UTF-8'><title>Documento {cod}</title><style>body{{margin:0;font-family:Arial}}.barra{{background:#0a2c4e;color:white;padding:12px;text-align:center}}.btn{{position:fixed;right:20px;bottom:20px;background:#0a2c4e;color:white;padding:10px 15px;border:0;border-radius:5px;z-index:999}}@media print{{.barra,.btn{{display:none}}}}</style></head><body><div class='barra'>Documento autenticado • Código: {cod}</div>{conteudo}<button class='btn' onclick='window.print()'>Imprimir / PDF</button></body></html>"""


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
    
    # Calcular carga horária total APROVADA apenas
    carga_total_aprovada = 0
    carga_total_cursada = 0
    
    for d in disciplinas:
        # Contar apenas disciplinas com status APROVADO
        if d.get('status', '').upper() == 'APROVADO':
            # Buscar carga horária real da disciplina
            cursor.execute("SELECT carga_horaria FROM disciplinas WHERE id = %s", (d['id'],))
            disciplina_info = cursor.fetchone()
            carga = disciplina_info['carga_horaria'] if disciplina_info and disciplina_info['carga_horaria'] else 80
            carga_total_aprovada += int(carga)
        
        # Para carga total cursada (todas disciplinas)
        cursor.execute("SELECT carga_horaria FROM disciplinas WHERE id = %s", (d['id'],))
        disciplina_info = cursor.fetchone()
        carga = disciplina_info['carga_horaria'] if disciplina_info and disciplina_info['carga_horaria'] else 80
        carga_total_cursada += int(carga)
    
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
    
    # Gerar linhas da tabela
    linhas = ""
    for d in disciplinas:
        # Buscar informações adicionais da disciplina
        cursor.execute("""
            SELECT d.carga_horaria, doc.nome as docente_nome, doc.titulacao
            FROM disciplinas d
            LEFT JOIN disciplina_docente dd ON d.id = dd.disciplina_id
            LEFT JOIN docentes doc ON dd.docente_id = doc.id
            WHERE d.id = %s
            ORDER BY dd.ano_semestre DESC
            LIMIT 1
        """, (d['id'],))
        
        info_disc = cursor.fetchone()
        
        # Determinar carga horária
        carga_horaria = info_disc['carga_horaria'] if info_disc and info_disc['carga_horaria'] else 80
        
        # Determinar docente
        if info_disc and info_disc['docente_nome']:
            docente = info_disc['docente_nome']
            if info_disc['titulacao']:
                docente += f" ({info_disc['titulacao']})"
        else:
            docente = 'Docente Responsável — Coordenação Acadêmica SIGEU'
        
        # Determinar período
        cursor.execute("""
            SELECT data_inicio FROM aluno_disciplina_datas 
            WHERE aluno_id = %s AND disciplina_id = %s
        """, (aluno_id, d['id']))

        data_info = cursor.fetchone()
        if data_info and data_info['data_inicio']:
            try:
                data_obj = datetime.strptime(data_info['data_inicio'], "%d/%m/%Y")
                ano = data_obj.year
                mes = data_obj.month
                semestre = "1" if mes <= 6 else "2"
                periodo = f"{ano}.{semestre}"
            except:
                periodo = f"{datetime.now().year}.1"
        else:
            periodo = f"{datetime.now().year}.1"
        
        # Buscar nota final direto da tabela notas_finais
        cursor.execute("""
            SELECT media_final
            FROM notas_finais
            WHERE aluno_id = %s AND disciplina_id = %s
        """, (aluno_id, d['id']))

        nota_row = cursor.fetchone()

        if nota_row and nota_row['media_final'] is not None:
            nota_display = f"{float(nota_row['media_final']):.2f}"
        else:
            nota_display = "N/I"
        
        # Determinar status
        status_display = d.get('status', 'CURSANDO')

        # Determinar semestre
        semestre = periodo.split('.')[-1] if '.' in periodo else "1"

        # 👇 USA O VALOR DO FORMULÁRIO PARA FREQUÊNCIA
        frequencia = frequencia_manual

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
    
    # HTML COMPLETO COM QUEBRA DE PÁGINA ANTES DO RESUMO ACADÊMICO
    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>HISTÓRICO ESCOLAR - {dados_aluno.get('nome','')}</title>

<style>
/* TIPOGRAFIA INSTITUCIONAL - ARIAL/CALIBRI */
* {{
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}}

body {{
    margin: 0;
    padding: 0;
    background: #c9c9c9;
    font-family: "Arial Nova", "Arial", "Calibri", "Segoe UI", sans-serif;
    font-size: 10.5pt;
    color: #1a1a1a;
    line-height: 1.4;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}}

/* FOLHA A4 COM MARGENS PRECISAS */
.folha {{
    width: 210mm;
    min-height: 297mm;
    margin: 0 auto;
    background: #fefefe;
    position: relative;
    overflow: hidden;
    box-shadow: 0 0 20px rgba(0,0,0,0.3);
    padding: 15mm 20mm 25mm 20mm;
    page-break-after: always;
}}

/* BORDA DE SEGURANÇA - ESTILO PAPEL MOEDA */
.borda-seguranca {{
    position: absolute;
    top: 8mm;
    left: 8mm;
    right: 8mm;
    bottom: 8mm;
    border: 0.5pt solid #1a237e;
    pointer-events: none;
}}

.borda-seguranca::before {{
    content: "";
    position: absolute;
    top: 2mm;
    left: 2mm;
    right: 2mm;
    bottom: 2mm;
    border: 0.3pt dashed #1a237e;
    opacity: 0.5;
}}

/* CANTONEIRAS DE SEGURANÇA */
.cantoneira {{
    position: absolute;
    width: 15mm;
    height: 15mm;
    border: 2pt solid #1a237e;
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

/* MARCA D'ÁGUA PRINCIPAL - SELO INSTITUCIONAL */
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
    z-index: 2;
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
        #1a237e 0px,
        #1a237e 5mm,
        #ffffff 5mm,
        #ffffff 10mm,
        #1a237e 10mm,
        #1a237e 15mm
    );
    z-index: 10;
}}

/* CABEÇALHO INSTITUCIONAL */
.cabecalho {{
    position: relative;
    z-index: 5;
    border-bottom: 1.5pt solid #1a237e;
    padding-bottom: 4mm;
    margin-bottom: 10mm;
    display: flex;
    align-items: center;
    justify-content: space-between;
}}

.logo-area {{
    display: flex;
    align-items: center;
    gap: 5mm;
}}

.logo-area img {{
    width: 25mm;
    height: auto;
    opacity: 0.9;
}}

.instituicao-info {{
    flex: 1;
}}

.instituicao-nome {{
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 14pt;
    color: #1a237e;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    line-height: 1.2;
    margin-top: 8mm;
}}

.instituicao-sub {{
    font-family: "Arial", sans-serif;
    font-size: 8pt;
    color: #444;
    margin-top: 2mm;
    line-height: 1.3;
}}

/* SELO DE AUTENTICIDADE NO CABEÇALHO */
.selo-autenticidade {{
    width: 22mm;
    height: 22mm;
    border: 1.5pt solid #1a237e;
    border-radius: 50%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: "Arial", sans-serif;
    font-size: 6pt;
    color: #1a237e;
    text-align: center;
    line-height: 1.1;
    position: relative;
    background: radial-gradient(circle, rgba(26,35,126,0.05) 0%, transparent 70%);
}}

.selo-autenticidade::before {{
    content: "";
    display: inline-block;
    width: 24px;
    height: 16px;
    margin-bottom: 1mm;
    margin-right: 4px;
    vertical-align: middle;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='16' viewBox='0 0 24 16'%3E%3Crect x='0' y='0' width='2' height='16' fill='%231a237e'/%3E%3Crect x='4' y='0' width='1' height='16' fill='%231a237e'/%3E%3Crect x='7' y='0' width='3' height='16' fill='%231a237e'/%3E%3Crect x='12' y='0' width='1' height='16' fill='%231a237e'/%3E%3Crect x='15' y='0' width='2' height='16' fill='%231a237e'/%3E%3Crect x='19' y='0' width='1' height='16' fill='%231a237e'/%3E%3Crect x='22' y='0' width='2' height='16' fill='%231a237e'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-size: contain;
}}

/* NÚMERO DE CONTROLE NO CANTO */
.numero-controle-box {{
    position: absolute;
    top: 12mm;
    right: 12mm;
    border: 0.5pt solid #1a237e;
    padding: 2mm 4mm;
    font-family: "Courier New", monospace;
    font-size: 7pt;
    color: #1a237e;
    background: rgba(26,35,126,0.03);
    z-index: 20;
}}

.numero-controle-box::before {{
    content: "Nº CONTROLE: ";
    font-weight: bold;
}}

/* TÍTULO DO DOCUMENTO */
.titulo-documento {{
    text-align: center;
    margin: 1mm 0 10mm 0;
    position: relative;
    z-index: 5;
}}

.titulo-principal {{
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 18pt;
    color: #1a237e;
    text-transform: uppercase;
    letter-spacing: 4px;
    margin-bottom: 3mm;
    position: relative;
    display: inline-block;
    padding: 0 15mm;
}}

/* LINHAS DECORATIVAS LATERAIS DO TÍTULO */
.titulo-principal::before,
.titulo-principal::after {{
    content: "";
    position: absolute;
    top: 50%;
    width: 10mm;
    height: 1pt;
    background: #1a237e;
}}

.titulo-principal::before {{
    left: 0;
}}

.titulo-principal::after {{
    right: 0;
}}

.titulo-sub {{
    font-family: "Arial", sans-serif;
    font-size: 9pt;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 3px;
    border-top: 0.5pt solid #ccc;
    border-bottom: 0.5pt solid #ccc;
    padding: 2mm 0;
    display: inline-block;
}}

/* TEXTO DE ABERTURA */
.texto-abertura {{
    text-align: justify;
    margin-bottom: 8mm;
    position: relative;
    z-index: 5;
    font-size: 10.5pt;
    line-height: 1.6;
    text-indent: 15mm;
}}

.destaque {{
    font-weight: bold;
    color: #1a237e;
    font-family: "Arial Black", "Arial", sans-serif;
}}

/* BOX DE IDENTIFICAÇÃO - ESTILO FICHA CRIMINAL */
.box-identificacao {{
    border: 1pt solid #1a237e;
    margin: 8mm 0;
    position: relative;
    z-index: 5;
    background: rgba(26,35,126,0.02);
}}

.box-identificacao-header {{
    background: #1a237e;
    color: #fff;
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 8pt;
    text-transform: uppercase;
    letter-spacing: 2px;
    padding: 1mm 4mm;
    text-align: center;
}}

.box-identificacao-content {{
    padding: 3mm;
}}

.linha-dado {{
    display: flex;
    margin-bottom: 3mm;
    border-bottom: 0.3pt dotted #999;
    padding-bottom: 2mm;
}}

.linha-dado:last-child {{
    margin-bottom: 0;
    border-bottom: none;
}}

.rotulo {{
    width: 25mm;
    font-family: "Arial", sans-serif;
    font-size: 8pt;
    color: #1a237e;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

.valor {{
    flex: 1;
    font-family: "Arial", sans-serif;
    font-size: 11pt;
    color: #000;
    font-weight: bold;
    padding-left: 3mm;
}}

/* BOX DE DADOS PESSOAIS ESTENDIDOS */
.box-dados-pessoais {{
    border: 1pt solid #1a237e;
    margin: 8mm 0;
    padding: 5mm;
    position: relative;
    z-index: 5;
    background: #fff;
}}

.box-dados-pessoais::before {{
    content: "DADOS PESSOAIS COMPLETOS";
    position: absolute;
    top: -3mm;
    left: 5mm;
    background: #fff;
    padding: 0 3mm;
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 7pt;
    color: #1a237e;
    letter-spacing: 1px;
}}

.dados-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 3mm;
    margin-top: 2mm;
}}

.dado-item-historico {{
    margin-bottom: 2mm;
}}

.dado-label-historico {{
    font-size: 7pt;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

.dado-valor-historico {{
    font-weight: bold;
    color: #000;
    font-size: 10pt;
    border-bottom: 0.5pt dotted #ccc;
    padding-bottom: 1mm;
}}

/* TABELA DE DISCIPLINAS */
.tabela-disciplinas {{
    width: 100%;
    border-collapse: collapse;
    margin: 8mm 0;
    font-size: 8pt;
    z-index: 5;
    position: relative;
}}

.tabela-disciplinas th {{
    background: #1a237e;
    color: white;
    font-weight: bold;
    padding: 4px;
    text-align: center;
    font-size: 7pt;
    text-transform: uppercase;
}}

.tabela-disciplinas td {{
    border: 1px solid #1a237e;
    padding: 4px;
    vertical-align: middle;
}}

.tabela-disciplinas tr:nth-child(even) {{
    background: rgba(26,35,126,0.02);
}}

/* BOX DE RESUMO */
.box-resumo {{
    border: 1pt solid #1a237e;
    border-left: 4pt solid #1a237e;
    margin: 8mm 0;
    padding: 5mm;
    position: relative;
    z-index: 5;
    background: #fff;
}}

.box-resumo::before {{
    content: "RESUMO ACADÊMICO";
    position: absolute;
    top: -3mm;
    left: 5mm;
    background: #fff;
    padding: 0 3mm;
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 7pt;
    color: #1a237e;
    letter-spacing: 1px;
}}

.resumo-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 3mm;
}}

.resumo-item {{
    text-align: center;
    border-right: 0.5pt solid #ddd;
    padding: 2mm;
}}

.resumo-item:last-child {{
    border-right: none;
}}

.resumo-label {{
    font-size: 7pt;
    color: #666;
    text-transform: uppercase;
    margin-bottom: 1mm;
}}

.resumo-valor {{
    font-weight: bold;
    color: #1a237e;
    font-size: 12pt;
}}

.resumo-detalhe {{
    font-size: 7pt;
    color: #999;
}}

/* BOX DE SISTEMA DE AVALIAÇÃO */
.box-avaliacao {{
    border: 1pt solid #1a237e;
    margin: 8mm 0;
    padding: 5mm;
    position: relative;
    z-index: 5;
    background: #f9f9f9;
}}

.box-avaliacao::before {{
    content: "SISTEMA DE AVALIAÇÃO";
    position: absolute;
    top: -3mm;
    left: 5mm;
    background: #f9f9f9;
    padding: 0 3mm;
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 7pt;
    color: #1a237e;
    letter-spacing: 1px;
}}

.box-avaliacao2::before {{
    content: "OBSERVAÇÕES";
    position: absolute;
    top: -3mm;
    left: 5mm;
    background: #f9f9f9;
    padding: 0 3mm;
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 7pt;
    color: #1a237e;
    letter-spacing: 1px;
}}
/* SELO GRANDE DE AUTENTICAÇÃO */
.selo-grande {{
    position: absolute;
    bottom: 45mm;
    right: 15mm;
    width: 35mm;
    height: 35mm;
    border: 2pt solid rgba(26,35,126,0.3);
    border-radius: 50%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: "Arial", sans-serif;
    font-size: 6pt;
    color: rgba(26,35,126,0.4);
    text-align: center;
    line-height: 1.2;
    transform: rotate(-15deg);
    z-index: 3;
    pointer-events: none;
}}

.selo-grande::before {{
    content: "AUTENTICIDADE";
    font-weight: bold;
    font-size: 7pt;
    margin-bottom: 2mm;
    letter-spacing: 1px;
}}

.selo-grande::after {{
    content: "★ ★ ★";
    font-size: 8pt;
    margin-top: 2mm;
}}

/* DATA E LOCAL */
.data-local {{
    text-align: right;
    margin: 15mm 0 10mm 0;
    font-family: "Arial", sans-serif;
    font-size: 8pt;
    color: #333;
    position: relative;
    z-index: 5;
    font-style: italic;
}}

/* ASSINATURA */
.assinatura-area {{
    margin-top: 15mm;
    text-align: center;
    position: relative;
    z-index: 5;
    page-break-inside: avoid;
}}

.assinatura-linha {{
    width: 70mm;
    height: 0;
    border-top: 0.5pt solid #000;
    margin: 0 auto 3mm auto;
    position: relative;
}}

.assinatura-linha::before {{
    content: "";
    position: absolute;
    left: 50%;
    top: -2mm;
    transform: translateX(-50%);
    width: 20mm;
    height: 4mm;
    border-left: 0.5pt solid #999;
    border-right: 0.5pt solid #999;
}}

.assinatura-nome {{
    font-family: "Arial Black", "Arial", sans-serif;
    font-size: 11pt;
    color: #1a237e;
    margin-bottom: 1mm;
}}

.assinatura-cargo {{
    font-family: "Arial", sans-serif;
    font-size: 8pt;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

/* QR CODE AREA */
.qr-code-box {{
    position: absolute;
    bottom: 23mm;
    left: 15mm;
    width: 30mm;
    height: 30mm;
    border: 0.5pt solid #ccc;
    background: #fafafa;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    z-index: 5;
}}

.qr-code-label {{
    font-size: 6pt;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 2mm;
}}

#qr-code-placeholder {{
    width: 20mm;
    height: 20mm;
    display: flex;
    align-items: center;
    justify-content: center;
}}

/* RODAPÉ TÉCNICO */
.rodape-tecnico {{
    position: absolute;
    bottom: 12mm;
    left: 50mm;
    right: 15mm;
    font-family: "Arial", sans-serif;
    font-size: 6.5pt;
    color: #666;
    text-align: center;
    line-height: 1.4;
    z-index: 5;
    border-top: 0.3pt solid #ddd;
    padding-top: 3mm;
}}

.rodape-tecnico strong {{
    color: #1a237e;
}}

/* MICROTEXTOS DE SEGURANÇA */
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

/* OBSERVAÇÕES */
.observacoes-texto {{
    font-size: 7pt;
    line-height: 1.4;
    color: #333;
}}

/* PRINT STYLES */
@media print {{
    body {{
        background: #fff;
    }}
    
    .folha {{
        box-shadow: none;
        margin: 0;
    }}
}}
</style>
</head>

<body>
<!-- PRIMEIRA PÁGINA -->
<div class="folha">
    <!-- ELEMENTOS DE SEGURANÇA E BORDA -->
    <div class="borda-seguranca"></div>
    <div class="cantoneira top-left"></div>
    <div class="cantoneira top-right"></div>
    <div class="cantoneira bottom-left"></div>
    <div class="cantoneira bottom-right"></div>
    
    <!-- MICROTEXTOS DE BORDA -->
    <div class="microtexto-borda top">DOCUMENTO OFICIAL - FACOP/CERTIFICADORA/SiGEU EDUCACIONAL - VALIDAÇÃO DIGITAL OBRIGATÓRIA</div>
    <div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98 <strong> | H{datetime.now().strftime('%Y%m%d')}/Coord. Acad. Tatiane R. G. Lourenço- </strong></div>
    <div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
    <div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>
    
    <!-- MARCAS D'ÁGUA -->
    <div class="marca-dagua-principal">FACOP SiGEu</div>
    <div class="marca-dagua-pattern"></div>
    
    <!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
    <div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
    <div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
    <div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO - FCP Certificadora | SiGEu Educacional</div>
    <div class="microtexto-seguranca micro-4">AUTENTICIDADE VERIFICÁVEL</div>
    
    <!-- FAIXA IDENTIFICADORA -->
    <div class="faixa-identificadora"></div>
    
    <!-- NÚMERO DE CONTROLE -->
    <div class="numero-controle-box">HIST-{dados_aluno.get('ra','')}-{ano_historico}</div>
    
    <!-- CABEÇALHO -->
    <div class="cabecalho">
        <div class="logo-area">
            <img src="/static/img/logo_declaracao.png" alt="Logo Institucional">
            <div class="instituicao-info">
                <div class="instituicao-nome">FACOP - SiGEu</div>
                <div class="instituicao-sub">
                    Faculdade do Centro Oeste Paulista 04.344.730/0001-60.<br>
                    Credenciada pela Portaria MEC nº 887 de 26/07/2017<br>
                    Polo educacional - Grupo Educacional Unificado LTDA
                </div>
            </div>
        </div>
        <div class="selo-autenticidade">
            FCP-SiGEu<br>e-SIGEU-GTP-2026
        </div>
    </div>
    
    <!-- TÍTULO -->
    <div class="titulo-documento">
        <div class="titulo-principal">Histórico Escolar</div>
        <div class="titulo-sub">COMPONENTES CURRICULARES - {ano_historico}</div>
    </div>
    
    <!-- BOX DE IDENTIFICAÇÃO DO ALUNO (SIMPLIFICADO) -->
    <div class="box-identificacao">
        <div class="box-identificacao-header">Identificação do Discente</div>
        <div class="box-identificacao-content">
            <div class="linha-dado">
                <div class="rotulo">Nome:</div>
                <div class="valor">{dados_aluno.get('nome','')}</div>
            </div>
            <div class="linha-dado">
                <div class="rotulo">RA:</div>
                <div class="valor">{dados_aluno.get('ra','')}</div>
            </div>
            <div class="linha-dado">
                <div class="rotulo">CPF:</div>
                <div class="valor">{dados_aluno.get('cpf_formatado','')}</div>
            </div>
        </div>
    </div>
    
    <!-- BOX DE DADOS PESSOAIS COMPLETOS -->
    <div class="box-dados-pessoais">
        <div class="dados-grid">
            <div>
                <div class="dado-item-historico">
                    <div class="dado-label-historico">Filiação</div>
                    <div class="dado-valor-historico">{filiacao}</div>
                </div>
                <div class="dado-item-historico">
                    <div class="dado-label-historico">Naturalidade</div>
                    <div class="dado-valor-historico">{naturalidade}</div>
                </div>
                <div class="dado-item-historico">
                    <div class="dado-label-historico">Nacionalidade</div>
                    <div class="dado-valor-historico">{nacionalidade}</div>
                </div>
            </div>
            <div>
                <div class="dado-item-historico">
                    <div class="dado-label-historico">Data de Nascimento</div>
                    <div class="dado-valor-historico">{data_nascimento}</div>
                </div>
                <div class="dado-item-historico">
                    <div class="dado-label-historico">Sexo</div>
                    <div class="dado-valor-historico">{sexo_display}</div>
                </div>
                <div class="dado-item-historico">
                    <div class="dado-label-historico">Estado Civil</div>
                    <div class="dado-valor-historico">{estado_civil}</div>
                </div>
            </div>
        </div>
        <div style="margin-top: 2mm;">
            <div class="dado-label-historico">Curso/Referência</div>
            <div class="dado-valor-historico">{curso_referencia}</div>
        </div>
    </div>
    
    <!-- TABELA DE DISCIPLINAS -->
    <table class="tabela-disciplinas">
        <thead>
            <tr>
                <th>Período</th>
                <th>Componente Curricular</th>
                <th>Sem.</th>
                <th>C.H.</th>
                <th>Docente/Titulação</th>
                <th>Nota Final</th>
                <th>Frequência</th>
                <th>Resultado</th>
            </tr>
        </thead>
        <tbody>
            {linhas}
            <tr style="background: #f0f0f0; font-weight: bold;">
                <td colspan="3">Carga Horária Total Aprovada:</td>
                <td>{carga_total_aprovada}H</td>
                <td colspan="2">Carga Horária Total Cursada:</td>
                <td>{carga_total_cursada}H</td>
            </tr>
        </tbody>
    </table>
</div>

<!-- SEGUNDA PÁGINA - COMEÇA COM RESUMO ACADÊMICO -->
<div class="folha">
    <!-- ELEMENTOS DE SEGURANÇA E BORDA -->
    <div class="borda-seguranca"></div>
    <div class="cantoneira top-left"></div>
    <div class="cantoneira top-right"></div>
    <div class="cantoneira bottom-left"></div>
    <div class="cantoneira bottom-right"></div>
    
    <!-- MICROTEXTOS DE BORDA -->
    <div class="microtexto-borda top">DOCUMENTO OFICIAL - FCP Certificadora | SiGEu Educacional - VALIDAÇÃO DIGITAL OBRIGATÓRIA</div>
    <div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98 <strong> | H{datetime.now().strftime('%Y%m%d')}/Coord. Acad. Tatiane R. G. Lourenço- </strong></div>
    <div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
    <div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>
    
    <!-- MARCAS D'ÁGUA -->
    <div class="marca-dagua-principal">FACOP SiGEu</div>
    <div class="marca-dagua-pattern"></div>
    
    <!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
    <div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
    <div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
    <div class="microtexto-seguranca micro-3">FCP Certificadora | SiGEu Educacional</div>
    <div class="microtexto-seguranca micro-4">AUTENTICIDADE VERIFICÁVEL</div>
    
    <!-- FAIXA IDENTIFICADORA -->
    <div class="faixa-identificadora"></div>
    
    <!-- NÚMERO DE CONTROLE -->
    <div class="numero-controle-box">HIST-{dados_aluno.get('ra','')}-{ano_historico}</div>
    
    <!-- CABEÇALHO -->
    <div class="cabecalho">
        <div class="logo-area">
            <img src="/static/img/logo_declaracao.png" alt="Logo Institucional">
            <div class="instituicao-info">
                <div class="instituicao-nome">FACOP - SiGEu</div>
                <div class="instituicao-sub">
                    Faculdade do Centro Oeste Paulista 04.344.730/0001-60.<br>
                    Credenciada pela Portaria MEC nº 887 de 26/07/2017<br>
                    Polo educacional - Grupo Educacional Unificado LTDA
                </div>
            </div>
        </div>
        <div class="selo-autenticidade">
            FCP-SiGEu<br>e-SIGEU-GTP-2026 
        </div>
    </div>
    
    <!-- TÍTULO -->
    <div class="titulo-documento">
        <div class="titulo-principal">Histórico Escolar</div>
        <div class="titulo-sub">COMPONENTES CURRICULARES - {ano_historico}</div>
    </div>
    
    <!-- BOX DE RESUMO ACADÊMICO -->
    <div class="box-resumo">
        <div class="resumo-grid">
            <div class="resumo-item">
                <div class="resumo-label">Índice de Rendimento Acadêmico (IRA)</div>
                <div class="resumo-valor">{ira_display}</div>
            </div>
            <div class="resumo-item">
                <div class="resumo-label">Disciplinas Aprovadas</div>
                <div class="resumo-valor">{ira_info['disciplinas_aprovadas']}</div>
                <div class="resumo-detalhe">de {len(disciplinas)} cursadas</div>
            </div>
            <div class="resumo-item">
                <div class="resumo-label">Carga Horária Aprovada</div>
                <div class="resumo-valor">{carga_total_aprovada}H</div>
                <div class="resumo-detalhe">de {carga_total_cursada}H</div>
            </div>
        </div>
    </div>
    
    <!-- BOX DE SISTEMA DE AVALIAÇÃO -->
    <div class="box-avaliacao">
        <p style="margin: 2mm 0;"><strong>Distribuição dos 100 pontos:</strong> Produção Científica (20%) | Prova I (20%) | Prova II (20%) | Prova III (20%) | Prova IV (20%)</p>
        <p style="margin: 2mm 0;"><strong>Avaliação Suplementar:</strong> Conteúdo total da disciplina - Valor: 100 pontos (Pré-requisito: Resultado Final ≥ 20 e < 60)</p>
        <p style="margin: 2mm 0;"><strong>Média Final:</strong> (Resultado Final + Nota Prova Suplementar) / 2 | Mínimo para aprovação: ≥ 60 pontos.</p>
    </div>
    
    <!-- BOX DE OBSERVAÇÕES -->
    <div class="box-avaliacao2">
        <div class="observacoes-texto">
            <p><strong>Normativo:</strong> Oferta de disciplina isolada de acordo com o art. 50 da Lei de Diretrizes e Bases da Educação Nacional - LDBEN (Lei nº 9.394/1996). Modalidade de ingresso isolada, respeitados os pré-requisitos exigidos para cada disciplina, conforme registrado no ato da matrícula, vinculada à estrutura curricular de curso reconhecido no convênio institucional FACOP/CERTIFICADORA/SiGEU EDUCACIONAL.</p>
            <p style="margin-top: 2mm;">Este documento possui validade em todo território nacional e pode ser utilizado para fins de aproveitamento de estudos, comprovação de conclusão de componentes curriculares e demais fins legais.</p>
        </div>
    </div>
    
    <!-- SELO GRANDE DE AUTENTICAÇÃO -->
    <div class="selo-grande">
        VALIDADO<br>
        ELETRONICAMENTE<br>
        {data_atual}
    </div>
    
    <!-- DATA E LOCAL -->
    <div class="data-local">
        São Paulo – SP, {data_atual}.
    </div>
    
     <!-- QR CODE - JÁ INCLUSO -->
    <div class="qr-code-box">
        <div class="qr-code-label">Validação Digital</div>
        <div id="qr-code-placeholder">
            <img src="{qr_code_base64}" alt="QR Code de Validação" style="width: 100%; height: 100%; object-fit: contain;">
        </div>
    </div>
    
    <!-- SEÇÃO DE AUTENTICAÇÃO -->
    <div style="position: absolute; bottom: 17mm; left: 15mm; right: 15mm; background: #f8f9fa; padding: 10px; border-radius: 5px; font-size: 8pt; text-align: center; border-top: 1px solid #1a237e;">
    </div>
    
    <!-- RODAPÉ TÉCNICO -->
    <div class="rodape-tecnico">
        <strong>DOCUMENTO GERADO ELETRONICAMENTE</strong> em conformidade com as Leis nº 11.419/06, 14.063/20 e nº 9.394/96 e nº 5.154/2004.<br>
        Este documento possui validade jurídica sem assinatura física mediante validação pelo QR Code acima.<br>
        Para verificar autenticidade: <strong>https://campusvirtualfacop.com.br/validar-documento</strong> | Protocolo: HIST-{dados_aluno.get('ra','')}-{ano_historico}
    </div>
</div>

</body>
</html>'''
    
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
                        background: #007bff; 
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
    """Lista todos os documentos gerados"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Criar tabela se não existir
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documentos_autenticados (
            id SERIAL PRIMARY KEY,
            codigo TEXT UNIQUE NOT NULL,
            aluno_id INTEGER,
            aluno_nome TEXT,
            aluno_ra TEXT,
            tipo TEXT,
            conteudo_html TEXT,
            data_geracao TEXT,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id)
        )
    ''')
    
    # Buscar documentos
    cursor.execute("SELECT * FROM documentos_autenticados ORDER BY data_geracao DESC")
    documentos = cursor.fetchall()
    conn.close()
    
    return render_template("mew/listar_documentos.html", documentos=documentos)

# ==========================
# ROTA PARA DELETAR DOCUMENTO (MEW)
# ==========================

@app.route("/mew/deletar-documento/<codigo>")
def deletar_documento(codigo):
    """Deleta um documento"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM documentos_autenticados WHERE codigo = %s OR codigo_autenticacao = %s", (codigo, codigo))
    
    conn.commit()
    conn.close()
    
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
        ativo = request.form.get("ativo", "1")
        
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
    
    conn.close()
    
    return render_template("mew/editar_docente.html", docente=docente)

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
        
        # Se tiver docente, associar
        if docente_id and docente_id != "0":
            # Remover associação anterior para este ano/semestre
            cursor.execute("""
                DELETE FROM disciplina_docente 
                WHERE disciplina_id = %s AND ano_semestre = %s
            """, (disciplina_id, ano_semestre))
            
            # Adicionar nova associação
            cursor.execute("""
                INSERT INTO disciplina_docente (disciplina_id, docente_id, ano_semestre)
                VALUES (%s, %s, %s)
            """, (disciplina_id, docente_id, ano_semestre))
        
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
        if nota >= 90: return ("A", 4.0)
        elif nota >= 80: return ("B", 3.0)
        elif nota >= 70: return ("C", 2.0)
        elif nota >= 60: return ("D", 1.0)
        else: return ("F", 0.0)
    
    # Calcular IRA
    soma_pontos = 0
    soma_carga = 0
    disciplinas_aprovadas = 0
    
    for disc in disciplinas:
        carga = disc['carga_horaria'] if disc['carga_horaria'] else 80
        
        if disc['status'] == 'aprovado' and disc['media_final'] is not None:
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
        
        # Gerar HTML da declaração
        html = gerar_declaracao_conclusao(
            aluno_id, 
            disciplina_id, 
            aluno_completo, 
            disciplina_selecionada, 
            ano_manual
        )
        
        # GERAR QR CODE com o link
        dados_qr = link_validacao
        qr_code_base64 = gerar_qrcode_base64(dados_qr)
        
        # Criar metadados
        metadados = criar_metadados_documento(aluno_id, 'declaracao_conclusao', codigo, hash_documento)
        
        # Data atual
        data_emissao = datetime.now().strftime("%d/%m/%Y %H:%M")
        data_validade = (datetime.now() + timedelta(days=365*5)).strftime("%d/%m/%Y")
        
        # ADICIONAR QR CODE AO HTML DA DECLARAÇÃO
        html_com_qr = html.replace(
            '</body>',
            f'''
    <!-- SEÇÃO DE AUTENTICAÇÃO -->
    <div style="margin-top: 30px; padding: 20px; border-top: 2px solid #1a237e; background: #f9f9f9;">
        
        <!-- CABEÇALHO DA SEÇÃO -->
        <div style="text-align: center; margin-bottom: 20px;">
            <span style="background: #1a237e; color: white; padding: 5px 20px; border-radius: 20px; font-size: 11px; font-weight: bold;">
                🔐 DOCUMENTO AUTENTICADO DIGITALMENTE
            </span>
        </div>
        
        <!-- QR CODE E INFORMAÇÕES -->
        <table style="width: 100%; border-collapse: collapse;">
            <tr>
                <td style="width: 25%; text-align: center; vertical-align: middle;">
                    <img src="{qr_code_base64}" style="width: 120px; height: 120px;" alt="QR Code">
                </td>
                <td style="width: 75%; padding-left: 20px; vertical-align: middle;">
                    <p style="margin: 5px 0; font-size: 11px;"><strong>Código:</strong> {codigo}</p>
                    <p style="margin: 5px 0; font-size: 11px;"><strong>Hash:</strong> {hash_documento[:30]}...</p>
                    <p style="margin: 5px 0; font-size: 11px;"><strong>Emissão:</strong> {data_emissao}</p>
                    <p style="margin: 5px 0; font-size: 11px;"><strong>Validade:</strong> {data_validade}</p>
                </td>
            </tr>
        </table>
        
        <!-- INSTRUÇÕES DE VALIDAÇÃO -->
        <div style="margin-top: 15px; background: #e8f5e8; padding: 10px; border-radius: 5px; font-size: 10px; text-align: center;">
            <p style="margin: 2px 0;">📌 Para validar este documento, acesse <strong>{base_url}/validar-documento</strong></p>
            <p style="margin: 2px 0;">e digite o código acima ou escaneie o QR Code</p>
        </div>
    </div>
    </body>
    '''
        )
        
        # Salvar no banco
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Garantir colunas
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN aluno_id INTEGER")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN qr_code TEXT")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN hash_documento TEXT")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN data_emissao TEXT")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN data_validade TEXT")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN metadados TEXT")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN disciplina_id INTEGER")
        except:
            pass
        
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
    """Busca documentos já gerados para um aluno"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})
    
    tipo = request.args.get('tipo', '')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT codigo, aluno_nome, aluno_ra, tipo, data_emissao, disciplina_id FROM documentos_autenticados WHERE aluno_id = %s"
    params = [aluno_id]
    
    if tipo:
        query += " AND tipo = %s"
        params.append(tipo)
    
    query += " ORDER BY data_emissao DESC"
    
    cursor.execute(query, params)
    documentos = cursor.fetchall()
    
    # Buscar nomes das disciplinas
    result = []
    for doc in documentos:
        doc_dict = dict(doc)
        if doc_dict.get('disciplina_id'):
            cursor.execute("SELECT nome FROM disciplinas WHERE id = %s", (doc_dict['disciplina_id'],))
            disc = cursor.fetchone()
            doc_dict['disciplina_nome'] = disc['nome'] if disc else None
        result.append(doc_dict)
    
    conn.close()
    
    return jsonify({
        "success": True,
        "documentos": result
    })
    
@app.route("/mew/gerenciar-documentos")
def mew_gerenciar_documentos():
    """Página de gerenciamento de documentos emitidos - INCLUINDO PLANOS DE ENSINO"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Buscar todos os alunos para o filtro
    cursor.execute("SELECT id, nome, ra FROM alunos ORDER BY nome")
    alunos = cursor.fetchall()
    
    # Buscar estatísticas - INCLUINDO PLANOS
    cursor.execute("SELECT COUNT(*) as total FROM documentos_autenticados")
    total_documentos = cursor.fetchone()["total"]
    
    cursor.execute("SELECT COUNT(*) as total FROM documentos_enviados WHERE status = 'enviado'")
    documentos_enviados = cursor.fetchone()["total"]
    
    cursor.execute("SELECT COUNT(*) as total FROM documentos_enviados WHERE status = 'visualizado'")
    documentos_visualizados = cursor.fetchone()["total"]
    
    # Buscar documentos com informações de envio - AGORA INCLUI PLANOS DE ENSINO
    cursor.execute("""
        SELECT 
            da.*,
            a.nome as aluno_nome,
            a.ra as aluno_ra,
            d.nome as disciplina_nome,
            de.id as envio_id,
            de.status as status_envio,
            de.data_envio,
            de.data_visualizacao,
            de.mensagem,
            CASE 
                WHEN da.tipo = 'plano_ensino' THEN 'Plano de Ensino'
                WHEN da.tipo = 'historico' THEN 'Histórico Escolar'
                WHEN da.tipo = 'declaracao_conclusao' THEN 'Declaração de Conclusão'
                ELSE da.tipo
            END as tipo_display
        FROM documentos_autenticados da
        LEFT JOIN alunos a ON da.aluno_id = a.id
        LEFT JOIN disciplinas d ON da.disciplina_id = d.id
        LEFT JOIN documentos_enviados de ON da.id = de.documento_original_id
        ORDER BY da.data_emissao DESC
    """)
    
    documentos_raw = cursor.fetchall()
    
    # Agrupar documentos por ID para evitar duplicatas
    documentos_dict = {}
    for doc in documentos_raw:
        doc_id = doc['id']
        if doc_id not in documentos_dict:
            doc_dict = dict(doc)
            doc_dict['envios'] = []
            if doc['envio_id']:
                doc_dict['envios'].append({
                    'id': doc['envio_id'],
                    'status': doc['status_envio'],
                    'data_envio': doc['data_envio'],
                    'data_visualizacao': doc['data_visualizacao'],
                    'mensagem': doc['mensagem']
                })
            documentos_dict[doc_id] = doc_dict
        else:
            if doc['envio_id']:
                documentos_dict[doc_id]['envios'].append({
                    'id': doc['envio_id'],
                    'status': doc['status_envio'],
                    'data_envio': doc['data_envio'],
                    'data_visualizacao': doc['data_visualizacao'],
                    'mensagem': doc['mensagem']
                })
    
    documentos = list(documentos_dict.values())
    conn.close()
    
    # Categorias para filtro - ADICIONADO PLANO DE ENSINO
    categorias = [
        {'id': 'historico', 'nome': 'Histórico Escolar'},
        {'id': 'declaracao_conclusao', 'nome': 'Declaração de Conclusão'},
        {'id': 'plano_ensino', 'nome': 'Plano de Ensino'},
        {'id': 'outros', 'nome': 'Outros Documentos'}
    ]
    
    return render_template(
        "mew/gerenciar_documentos.html",
        alunos=alunos,
        documentos=documentos,
        categorias=categorias,
        total_documentos=total_documentos,
        documentos_enviados=documentos_enviados,
        documentos_visualizados=documentos_visualizados
    )
    
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
        
        # 👇 PEGAR OS VALORES MANUAIS DO FORMULÁRIO
        ira_manual = data.get('ira_manual', 'N/I')
        total_disciplinas_manual = data.get('total_disciplinas', '0')
        frequencia = data.get('frequencia', 'N/I')  # 👈 DEFINIR A VARIÁVEL AQUI!
        
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
        
        # 👇 PASSAR OS VALORES MANUAIS PARA A FUNÇÃO (10 PARÂMETROS)
        html = gerar_historico_automatico(
            aluno_id, 
            disciplinas, 
            aluno_completo, 
            qr_code_base64, 
            codigo, 
            hash_documento,
            ano_manual,
            ira_manual,
            total_disciplinas_manual,
            frequencia  # 👈 10º PARÂMETRO
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
            SELECT * FROM documentos_autenticados 
            WHERE id = %s
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
                background: #1a237e;
                color: white;
                padding: 15px;
                text-align: center;
                font-size: 14px;
            }}
            .info-header .badge {{
                background: #ffd700;
                color: #1a237e;
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
                background: #1a237e;
                color: white;
                padding: 10px 20px;
                border-radius: 5px;
                text-decoration: none;
                font-size: 14px;
                z-index: 1000;
                box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            }}
            .back-btn:hover {{
                background: #0d1b6b;
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
    """API para filtrar documentos"""
    if not session.get("mew_admin"):
        return jsonify({"error": "Não autorizado"})
    
    aluno_id = request.args.get('aluno_id', '')
    categoria = request.args.get('categoria', '')
    status = request.args.get('status', '')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT 
            da.*,
            a.nome as aluno_nome,
            a.ra as aluno_ra,
            d.nome as disciplina_nome,
            de.id as envio_id,
            de.status as status_envio,
            de.data_envio,
            de.data_visualizacao
        FROM documentos_autenticados da
        JOIN alunos a ON da.aluno_id = a.id
        LEFT JOIN disciplinas d ON da.disciplina_id = d.id
        LEFT JOIN documentos_enviados de ON da.id = de.documento_original_id
        WHERE 1=1
    """
    params = []
    
    if aluno_id:
        query += " AND da.aluno_id = %s"
        params.append(aluno_id)
    
    if categoria and categoria != 'todos':
        query += " AND da.tipo = %s"
        params.append(categoria)
    
    if status:
        if status == 'enviados':
            query += " AND de.id IS NOT NULL"
        elif status == 'nao_enviados':
            query += " AND de.id IS NULL"
        elif status == 'visualizados':
            query += " AND de.status = 'visualizado'"
    
    query += " ORDER BY da.data_emissao DESC"
    
    cursor.execute(query, params)
    documentos = cursor.fetchall()
    
    conn.close()
    
    # Converter para lista de dicionários
    resultado = []
    for doc in documentos:
        doc_dict = dict(doc)
        resultado.append(doc_dict)
    
    return jsonify({"success": True, "documentos": resultado})


@app.route("/mew/excluir-documento/<int:documento_id>")
def mew_excluir_documento(documento_id):
    """Exclui um documento e seus envios relacionados"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Verificar se tem envios
        cursor.execute("SELECT id FROM documentos_enviados WHERE documento_original_id = %s", (documento_id,))
        tem_envios = cursor.fetchone()
        
        if tem_envios:
            # Primeiro excluir os envios
            cursor.execute("DELETE FROM documentos_enviados WHERE documento_original_id = %s", (documento_id,))
        
        # Depois excluir o documento original
        cursor.execute("DELETE FROM documentos_autenticados WHERE id = %s", (documento_id,))
        
        conn.commit()
        conn.close()
        
        return redirect("/mew/gerenciar-documentos?sucesso=Documento+excluído+com+sucesso")
        
    except Exception as e:
        conn.close()
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
Coordenação Acadêmica SiGEu Educacional - FACOP Certificadora"""
    
    else:
        return f"""Olá {aluno_nome},

Um novo documento acadêmico foi disponibilizado para você! 📋

Este documento possui autenticação digital com QR Code e pode ser validado no site da instituição.

Para visualizar e baixar:
1. Clique no botão "Visualizar Documento" abaixo
2. Use a opção de impressão do navegador (Ctrl+P) para salvar como PDF
3. Guarde o código de autenticação para validação futura

Atenciosamente,
Secretaria Acadêmica SiGEu Eduacional - Facop Certificadora"""

# ============================================
# MEW - GERAR PLANOS DE ENSINO COM IA
# ============================================

@app.route("/mew/gerar-plano-ensino")
def mew_gerar_plano_ensino():
    """Gera plano já vinculado a uma disciplina real do cadastro."""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    init_documentos_integrados_db()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome, COALESCE(carga_horaria,80) AS carga_horaria FROM disciplinas ORDER BY nome")
    disciplinas = cursor.fetchall()
    conn.close()
    hoje = datetime.now().strftime("%Y-%m-%d")
    return render_template("mew/gerar_plano_ensino.html", hoje=hoje, disciplinas=disciplinas)


@app.route("/mew/processar-plano-ensino", methods=["POST"])
def mew_processar_plano_ensino():
    """Gera o plano a partir apenas da disciplina cadastrada + sugestão de ementa."""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"}), 403

    init_documentos_integrados_db()
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
            **dados_html
        )

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO documentos_autenticados
            (codigo, aluno_id, aluno_nome, aluno_ra, tipo, conteudo_html, data_geracao,
             qr_code, hash_documento, data_emissao, data_validade, metadados, disciplina_id)
            VALUES (%s,NULL,'ADMIN - MEW','ADMIN','plano_ensino',%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            codigo, html_completo, data_emissao, qr_code_base64, hash_documento,
            data_emissao, data_validade, metadados, disciplina_id
        ))
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
    """Lista planos e mostra claramente a disciplina vinculada."""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    init_documentos_integrados_db()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT da.*, d.nome AS disciplina
        FROM documentos_autenticados da
        LEFT JOIN disciplinas d ON d.id=da.disciplina_id
        WHERE da.tipo='plano_ensino'
        ORDER BY da.id DESC
    """)
    planos = cursor.fetchall()
    cursor.execute("SELECT id,nome FROM disciplinas ORDER BY nome")
    disciplinas = cursor.fetchall()
    conn.close()
    return render_template("mew/planos_ensino.html", planos=planos, total_planos=len(planos), disciplinas=disciplinas)



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
    <title>Plano de Ensino - {disciplina} | FACOP/CERTIFICADORA/SiGEU EDUCACIONAL</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
        /* ESTILO PROFISSIONAL INSTITUCIONAL - FACOP/CERTIFICADORA/SiGEU EDUCACIONAL */
        /* PADRÃO DE CORES: AZUL MARINHO (#1a237e), CINZA, DETALHES DE SEGURANÇA */
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
            border: 0.5pt solid #1a237e; /* Borda fina azul marinho, igual cantoneiras */
            border-top: 8px solid #1a237e; /* Linha superior mais grossa azul marinho */
            border-bottom: 8px solid #1a237e; /* Linha inferior mais grossa azul marinho */
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
            color: #1a237e;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 2px;
            background: rgba(255,255,255,0.9);
            padding: 2mm 4mm;
            border: 0.5pt solid #1a237e;
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
            border-bottom: 1.5pt solid #1a237e; /* Linha azul marinho */
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
            color: #1a237e; /* Azul marinho */
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
            color: #1a237e;
            background: rgba(26,35,126,0.03); /* Fundo sutil azul */
            padding: 2mm 4mm;
            border: 0.5pt solid #1a237e;
            font-weight: 500;
        }}

        .meta-identifiers span {{
            display: block;
            margin-top: 2mm;
            background: #1a237e; /* Fundo azul marinho */
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
            color: #1a237e;
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
            background: #1a237e;
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
            border: 1pt solid #1a237e; /* Borda azul marinho */
            background: white;
            font-size: 10.5pt; /* Tamanho de fonte igual declaração */
        }}

        .info-table th {{
            background: #1a237e; /* Fundo azul marinho */
            color: white; /* Texto branco */
            font-weight: bold;
            text-align: left;
            vertical-align: top;
            width: 25%;
            padding: 4px 8px;
            border: 1pt solid #1a237e;
            font-size: 10pt;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .info-table td {{
            width: 75%;
            padding: 4px 8px;
            border: 1pt solid #1a237e;
            vertical-align: top;
            text-align: justify;
            background: white;
            color: #1a1a1a; /* Cor de texto principal */
            font-size: 10.5pt;
            line-height: 1.6;
        }}

        .info-table th[colspan="2"] {{
            background: #1a237e; /* Fundo azul marinho */
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
            color: #1a237e; /* Azul marinho */
            border-bottom: 0.5pt solid #1a237e; /* Linha azul */
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
            border-left: 4px solid #1a237e; /* Borda azul marinho */
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
            border-top: 2px solid #1a237e; /* Linha azul marinho */
            position: relative;
        }}

        .signature-block {{
            flex: 1.2;
            padding-right: 20px;
        }}

        .digital-signature {{
            font-family: 'Courier New', monospace;
            background: #1a237e; /* Fundo azul marinho */
            padding: 16px 18px;
            border-radius: 0; /* Sem bordas arredondadas */
            color: #dcf2e7;
            box-shadow: 0 4px 10px rgba(0,0,0,0.1);
            border-left: 8px solid #0d1b6b; /* Tom mais escuro de azul */
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
            background: #0d1b6b; /* Tom mais escuro */
            padding: 8px 12px;
            border-radius: 0;
            border: 0.5px solid #3f51b5;
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
            border-bottom: 5px solid #1a237e; /* Linha inferior azul marinho */
            text-align: right;
            width: 100%;
            box-shadow: -2px 6px 12px rgba(0,0,0,0.05);
        }}

        .secretary-name {{
            font-family: "Arial Black", "Arial", sans-serif;
            font-size: 22px;
            font-weight: 700;
            color: #1a237e; /* Azul marinho */
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

        .simulated-signature {{
            font-family: 'Brush Script MT', cursive, 'Parisienne', 'Lucida Handwriting', sans-serif;
            font-size: 34px;
            font-weight: 400;
            color: #1a237e; /* Azul marinho */
            margin-right: 5px;
            text-shadow: 1px 1px 2px rgba(0,0,0,0.1);
            border-bottom: 2px solid #1a237e; /* Linha azul */
            padding-bottom: 2px;
            line-height: 1.1;
        }}

        .date-today {{
            font-size: 16px;
            background: #1a237e; /* Fundo azul marinho */
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
            color: #1a237e; /* Azul marinho */
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
            background: #1a237e; /* Azul marinho */
            color: white;
            text-decoration: none;
            border-radius: 0; /* Botões retos */
            font-weight: 700;
            border: none;
            cursor: pointer;
            font-size: 14px;
            letter-spacing: 1px;
            text-transform: uppercase;
            border: 1px solid #0d1b6b;
            transition: all 0.2s;
        }}

        .btn:hover {{
            background: #0d1b6b; /* Tom mais escuro */
            transform: scale(1.02);
            box-shadow: 0 8px 16px rgba(0,0,0,0.2);
        }}
        
        .borda-seguranca {{
    position: absolute;
    top: 8mm;
    left: 8mm;
    right: 8mm;
    bottom: 8mm;
    border: 0.5pt solid #1a237e;
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
    border: 0.3pt dashed #1a237e;
    opacity: 0.5;
}}

/* CANTONEIRAS DE SEGURANÇA */
.cantoneira {{
    position: absolute;
    width: 15mm;
    height: 15mm;
    border: 2pt solid #1a237e;
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
        #1a237e 0px,
        #1a237e 5mm,
        #ffffff 5mm,
        #ffffff 10mm,
        #1a237e 10mm,
        #1a237e 15mm
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


        /* IMPRESSÃO */
        @media print {{
            body {{
                background: white;
                padding: 0;
            }}
            .page {{
                box-shadow: none;
                border: 0.5pt solid #1a237e; /* Borda fina */
                border-top: 8px solid #1a237e;
                border-bottom: 8px solid #1a237e;
                background: white;
                padding: 15mm 20mm 25mm 20mm;
                margin: 0 auto 0 auto;
                page-break-after: always;
            }}
            .page:last-child {{
                page-break-after: auto;
            }}
            .watermark {{
                opacity: 0.03;
                print-color-adjust: exact;
            }}
            .digital-signature {{
                background: #1a237e !important;
                color: white !important;
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
            }}
            .btn {{
                display: none;
            }}
            .info-table th {{
                background: #1a237e !important;
                color: white !important;
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
            }}
            .info-table th[colspan="2"] {{
                background: #1a237e !important;
                color: white !important;
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
<div class="microtexto-borda top">DOCUMENTO OFICIAL - FCP Certificadora | SiGEu Educacional - PLANO DE ENSINO</div>
<div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98</div>
<div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
<div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>

<!-- MARCAS D'ÁGUA -->
<div class="marca-dagua-principal">FACOP SiGEu</div>
<div class="marca-dagua-pattern"></div>

<!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
<div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
<div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
<div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO - FCP Certificadora | SiGEu Educacional</div>
<div class="microtexto-seguranca micro-4">AUTENTICIDADE VERIFICÁVEL</div>

<!-- FAIXA IDENTIFICADORA -->
<div class="faixa-identificadora"></div>
        <div class="page-number">PÁGINA 1/3</div>
        <div class="plano-content">
            <!-- CABEÇALHO INSTITUCIONAL -->
            <div class="header-institution">
                <div class="logo-area">
                    <img src="/static/img/logo_declaracao.png" alt="Logo FACOP/SiGEU" class="logo-img" onerror="this.style.display='none'">
                    <div class="institution-name">
                        <h1>FACOP Certificado | SiGEU Educacional</h1>
                        <h2>Faculdade do Centro Oeste Paulista • Sistema Integrado de Gestão Educacional</h2>
                    </div>
                </div>
                <div class="meta-identifiers">
    <div style="font-size:12px; margin-top: 5px;">PLANO INSTITUCIONAL • VÁLIDO PARA TODOS OS ALUNOS</div>
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
<div class="microtexto-borda top">DOCUMENTO OFICIAL - FCP Certificadora | SiGEu Educacional - PLANO DE ENSINO</div>
<div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98</div>
<div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
<div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>

<!-- MARCAS D'ÁGUA -->
<div class="marca-dagua-principal">FACOP SiGEu</div>
<div class="marca-dagua-pattern"></div>

<!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
<div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
<div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
<div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO - FCP Certificadora | SiGEu Educacional</div>
<div class="microtexto-seguranca micro-4">AUTENTICIDADE VERIFICÁVEL</div>

<!-- FAIXA IDENTIFICADORA -->
<div class="faixa-identificadora"></div>
        <div class="page-number">PÁGINA 2/3</div>
        <div class="plano-content">
            <!-- CABEÇALHO INSTITUCIONAL -->
            <div class="header-institution">
                <div class="logo-area">
                    <img src="/static/img/logo_declaracao.png" alt="Logo FACOP/SiGEU" class="logo-img" onerror="this.style.display='none'">
                    <div class="institution-name">
                        <h1>FACOP Certificadora/SiGEU Educacional</h1>
                        <h2>Faculdade do Centro Oeste Paulista • Sistema Integrado de Gestão Educacional</h2>
                    </div>
                </div>
                <div class="meta-identifiers">
                    <div style="font-size:12px; margin-top: 5px;">VALIDADO POR PORTARIA MEC • 2026</div>
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
<div class="microtexto-borda top">DOCUMENTO OFICIAL - FCP Certificadora | SiGEu Educacional - PLANO DE ENSINO</div>
<div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98</div>
<div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
<div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>

<!-- MARCAS D'ÁGUA -->
<div class="marca-dagua-principal">FACOP SiGEu</div>
<div class="marca-dagua-pattern"></div>

<!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
<div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
<div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
<div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO - FCP Certificadora | SiGEu Educacional</div>
<div class="microtexto-seguranca micro-4">AUTENTICIDADE VERIFICÁVEL</div>

<!-- FAIXA IDENTIFICADORA -->
<div class="faixa-identificadora"></div>
        <div class="page-number">PÁGINA 3/3</div>
        <div class="plano-content">
            <!-- CABEÇALHO INSTITUCIONAL -->
            <div class="header-institution">
                <div class="logo-area">
                    <img src="/static/img/logo_declaracao.png" alt="Logo FACOP/SiGEU" class="logo-img" onerror="this.style.display='none'">
                    <div class="institution-name">
                        <h1> SiGEU EDUC • FACOP CTF</h1>
                        <h2>Faculdade do Centro Oeste Paulista • Sistema Integrado de Gestão Educacional</h2>
                    </div>
                </div>
                <div class="meta-identifiers">
                    <div style="font-size:12px; margin-top: 5px;">VALIDADO POR PORTARIA MEC • 2026</div>
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
                    <p><strong>📌 DOCUMENTO AUTENTICADO DIGITALMENTE</strong></p>
                    <p><strong>Código:</strong> {codigo}</p>
                    <p><strong>Hash:</strong> {hash_completa[:30]}...</p>
                    <p><strong>Data de Emissão:</strong> {data_formatada}</p>
                    <p><strong>Validade:</strong> 5 anos</p>
                </div>
            </div>

            <!-- ÁREA DE AUTENTICAÇÃO -->
            <div class="signature-area">
                <div class="signature-block">
                    <div class="digital-signature">
                        <span class="hash-label">🔐 ASSINATURA DIGITAL • SHA-256</span>
                        <div class="hash-value">
                            {hash_completa}
                        </div>
                        <div style="margin-top:12px; display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:13px; background:#0a1e3a; padding:4px 14px; border-radius:18px;">⏻ integridade verificada</span>
                            <span style="font-size:16px;">🕒 {data_formatada}</span>
                        </div>
                    </div>
                    <div style="margin-top: 12px; color: #1d513b; font-size: 13px; font-weight: 600;">
                        << registro eletrônico de integridade • SHA-256 • SIGEU Educacional >>
                    </div>
                </div>

                <div class="stamp-date">
                    <div class="secretary-signature">
                        <div class="secretary-name">DEAP • FCP CTFC/SiGEU Educ</div>
                        <div class="secretary-title">DEPARTAMENTO EDUCACIONAL</div>
                        <div class="signature-line">
                            <span class="simulated-signature">{docente}</span>
                            <span style="font-size:28px; color:#0f402e;"><path xmlns="http://www.w3.org/2000/svg" d="M232,168H63.86c2.66-5.24,5.33-10.63,8-16.11,15,1.65,32.58-8.78,52.66-31.14,5,13.46,14.45,30.93,30.58,31.25,9.06.18,18.11-5.2,27.42-16.37C189.31,143.75,203.3,152,232,152a8,8,0,0,0,0-16c-30.43,0-39.43-10.45-40-16.11a7.67,7.67,0,0,0-5.46-7.75,8.14,8.14,0,0,0-9.25,3.49c-12.07,18.54-19.38,20.43-21.92,20.37-8.26-.16-16.66-19.52-19.54-33.42a8,8,0,0,0-14.09-3.37C101.54,124.55,88,133.08,79.57,135.29,88.06,116.42,94.4,99.85,98.46,85.9c6.82-23.44,7.32-39.83,1.51-50.1-3-5.38-9.34-11.8-22.06-11.8C61.85,24,49.18,39.18,43.14,65.65c-3.59,15.71-4.18,33.21-1.62,48s7.87,25.55,15.59,31.94c-3.73,7.72-7.53,15.26-11.23,22.41H24a8,8,0,0,0,0,16H37.41c-11.32,21-20.12,35.64-20.26,35.88a8,8,0,1,0,13.71,8.24c.15-.26,11.27-18.79,24.7-44.12H232a8,8,0,0,0,0-16ZM58.74,69.21C62.72,51.74,70.43,40,77.91,40c5.33,0,7.1,1.86,8.13,3.67,3,5.33,6.52,24.19-21.66,86.39C56.12,118.78,53.31,93,58.74,69.21Z"/></span>
                        </div>
                        <div style="display: flex; justify-content: flex-end; margin-top: 12px;">
                            <span class="date-today">{data_formatada}</span>
                        </div>
                    </div>
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
        <button onclick="window.print()" class="btn">🖨 IMPRIMIR PDF (3 PÁGINAS)</button>
        <a href="/mew/gerar-plano-ensino" class="btn">➕ NOVO PLANO</a>
        <a href="/mew/planos-ensino" class="btn">📋 LISTAR PLANOS</a>
    </div>
</body>
</html>'''
    
    return html

@app.route("/mew/excluir-documentos-lote", methods=["POST"])
def mew_excluir_documentos_lote():
    """Exclui múltiplos documentos em lote"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})
    
    try:
        data = request.get_json()
        documento_ids = data.get('documento_ids', [])
        
        if not documento_ids:
            return jsonify({"success": False, "message": "Nenhum documento selecionado"})
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Criar placeholders para a query
        placeholders = ','.join(['%s'] * len(documento_ids))
        
        # Primeiro excluir envios relacionados
        cursor.execute(f"""
            DELETE FROM documentos_enviados 
            WHERE documento_original_id IN ({placeholders})
        """, documento_ids)
        
        # Depois excluir documentos originais
        cursor.execute(f"""
            DELETE FROM documentos_autenticados 
            WHERE id IN ({placeholders})
        """, documento_ids)
        
        excluidos = cursor.rowcount
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True, 
            "message": f"{excluidos} documento(s) excluído(s)",
            "excluidos": excluidos
        })
        
    except Exception as e:
        if 'conn' in locals():
            conn.close()
        return jsonify({"success": False, "message": f"Erro: {str(e)}"})
    
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
            SELECT * FROM documentos_autenticados 
            WHERE id = %s AND tipo = 'plano_ensino'
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
    """Rota temporária para testar se a chave API está configurada"""
    if not session.get("mew_admin"):
        return "Não autorizado"
    
    chave = os.getenv("OPENAI_API_KEY")
    if chave:
        # Mostra apenas os primeiros 5 caracteres por segurança
        return f"API Key configurada: {chave[:5]}... (tamanho: {len(chave)})"
    else:
        return "API Key NÃO configurada no ambiente"

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
                    background: #007bff; 
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
    """Envia um anexo para a disciplina alternativa"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "message": "Não autenticado"})
    
    disciplina_id = request.form.get("disciplina_id")
    descricao = request.form.get("descricao", "")
    
    if not disciplina_id:
        return jsonify({"success": False, "message": "Disciplina não identificada"})
    
    # Verificar se tem arquivo
    if 'anexo' not in request.files:
        return jsonify({"success": False, "message": "Nenhum arquivo enviado"})
    
    arquivo = request.files['anexo']
    
    if arquivo.filename == '':
        return jsonify({"success": False, "message": "Nenhum arquivo selecionado"})
    
    # Salvar arquivo
    try:
        # Criar diretório se não existir
        upload_dir = os.path.join('static', 'uploads', 'disciplinas_alternativas', str(disciplina_id))
        os.makedirs(upload_dir, exist_ok=True)
        
        # Gerar nome único para o arquivo
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_seguro = f"aluno_{aluno_id}_{timestamp}_{arquivo.filename}"
        caminho_arquivo = os.path.join(upload_dir, nome_seguro)
        
        arquivo.save(caminho_arquivo)
        
        # URL pública
        url_arquivo = f"/{caminho_arquivo.replace(os.sep, '/')}"
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO anexos_disciplina_alternativa 
            (aluno_id, disciplina_id, nome_arquivo, url_arquivo, descricao, data_envio, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'pendente')
        """, (aluno_id, disciplina_id, arquivo.filename, url_arquivo, descricao, 
              datetime.now().strftime("%d/%m/%Y %H:%M")))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True, 
            "message": "Arquivo enviado com sucesso! Aguarde a correção do professor."
        })
        
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro ao enviar arquivo: {str(e)}"})

@app.route("/excluir-anexo/<int:anexo_id>", methods=["POST"])
def excluir_anexo(anexo_id):
    """Exclui um anexo do aluno (apenas se não corrigido)"""
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "message": "Não autenticado"})
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verificar se o anexo pertence ao aluno e está pendente
    cursor.execute("""
        SELECT url_arquivo, status FROM anexos_disciplina_alternativa 
        WHERE id = %s AND aluno_id = %s
    """, (anexo_id, aluno_id))
    
    anexo = cursor.fetchone()
    
    if not anexo:
        conn.close()
        return jsonify({"success": False, "message": "Anexo não encontrado"})
    
    if anexo['status'] != 'pendente':
        conn.close()
        return jsonify({"success": False, "message": "Não é possível excluir anexo já corrigido"})
    
    # Deletar arquivo físico
    try:
        caminho = anexo['url_arquivo'].lstrip('/')
        if os.path.exists(caminho):
            os.remove(caminho)
    except:
        pass  # Se não conseguir deletar o arquivo, continua
    
    # Deletar do banco
    cursor.execute("DELETE FROM anexos_disciplina_alternativa WHERE id = %s", (anexo_id,))
    
    conn.commit()
    conn.close()
    
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
    
    # 2. ATUALIZAR ANEXO (com feedback)
    try:
        cursor.execute("""
            UPDATE anexos_disciplina_alternativa 
            SET nota = %s, feedback = %s, status = %s, data_correcao = %s
            WHERE id = %s
        """, (nota, feedback, status, datetime.now().strftime("%d/%m/%Y %H:%M"), anexo_id))
    except psycopg2.errors.UndefinedColumn:
        # Se a coluna feedback não existir, adicioná-la no PostgreSQL
        conn.rollback()
        cursor = conn.cursor()
        cursor.execute("ALTER TABLE anexos_disciplina_alternativa ADD COLUMN IF NOT EXISTS feedback TEXT")
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
    """Exclui uma disciplina alternativa e todos os dados relacionados"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Buscar anexos para deletar arquivos
    cursor.execute("SELECT url_arquivo FROM anexos_disciplina_alternativa WHERE disciplina_id = %s", (disciplina_id,))
    anexos = cursor.fetchall()
    
    for anexo in anexos:
        try:
            caminho = anexo['url_arquivo'].lstrip('/')
            if os.path.exists(caminho):
                os.remove(caminho)
        except:
            pass
    
    # Deletar dados relacionados
    cursor.execute("DELETE FROM notas_finais_alternativas WHERE disciplina_id = %s", (disciplina_id,))
    cursor.execute("DELETE FROM anexos_disciplina_alternativa WHERE disciplina_id = %s", (disciplina_id,))
    cursor.execute("DELETE FROM aluno_disciplina_alternativa WHERE disciplina_id = %s", (disciplina_id,))
    cursor.execute("DELETE FROM disciplinas_alternativas WHERE id = %s", (disciplina_id,))
    
    # Tentar deletar pasta de uploads
    try:
        pasta = os.path.join('static', 'uploads', 'disciplinas_alternativas', str(disciplina_id))
        if os.path.exists(pasta):
            import shutil
            shutil.rmtree(pasta)
    except:
        pass
    
    conn.commit()
    conn.close()
    
    return redirect("/mew/disciplinas-alternativas?sucesso=Disciplina+excluída")

@app.route("/contrato-pendente", methods=["GET", "POST"])
def contrato_pendente():
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    init_contratos_db()

    conn = get_db_connection()
    cursor = conn.cursor()

    # Segurança: a assinatura só pode ocorrer depois do pagamento aprovado.
    cursor.execute("""
        SELECT status
        FROM situacao_financeira
        WHERE aluno_id = %s
        ORDER BY id DESC
        LIMIT 1
    """, (aluno_id,))
    financeiro = cursor.fetchone()

    if financeiro and financeiro.get("status") != "pago":
        conn.close()
        return redirect(url_for("aguardando_pagamento"))

    cursor.execute("""
        SELECT c.*, a.nome, a.ra, a.email, dp.cpf
        FROM contratos_alunos c
        JOIN alunos a ON a.id = c.aluno_id
        LEFT JOIN dados_pessoais dp ON dp.aluno_id = a.id
        WHERE c.aluno_id = %s AND c.status = 'pendente'
        ORDER BY c.id DESC
        LIMIT 1
    """, (aluno_id,))
    contrato = cursor.fetchone()

    if not contrato:
        conn.close()
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        assinatura = request.form.get("assinatura", "")
        foto = request.form.get("foto_assinatura", "")
        aceite_contrato = request.form.get("aceite_contrato") == "1"
        aceite_foto = request.form.get("aceite_foto") == "1"

        if not aceite_contrato:
            conn.close()
            return "É obrigatório declarar a leitura e o aceite do contrato.", 400

        if not aceite_foto:
            conn.close()
            return "É obrigatória a autorização específica para o registro fotográfico desta assinatura.", 400

        if not validar_data_image(
            assinatura,
            {"data:image/png;base64", "data:image/jpeg;base64"},
            1_500_000
        ):
            conn.close()
            return "Assinatura eletrônica inválida ou muito grande.", 400

        if not validar_data_image(
            foto,
            {"data:image/jpeg;base64", "data:image/png;base64", "data:image/webp;base64"},
            3_000_000
        ):
            conn.close()
            return "Fotografia de confirmação inválida ou muito grande.", 400

        agora = agora_brasilia()
        data_assinatura = agora.strftime("%d/%m/%Y %H:%M:%S")
        ip_assinatura = obter_ip_cliente()
        user_agent = (request.headers.get("User-Agent") or "")[:1000]

        texto_aceite_completo = (
            TEXTO_ACEITE_CONTRATO
            + "\n\nAUTORIZAÇÃO DO REGISTRO FOTOGRÁFICO:\n"
            + TEXTO_ACEITE_FOTO
        )

        # O hash final vincula assinatura, foto, identidade, aceite e evidências técnicas.
        dados_hash = json.dumps({
            "contrato_id": contrato["id"],
            "aluno_id": aluno_id,
            "nome": contrato.get("nome") or "",
            "ra": contrato.get("ra") or "",
            "cpf": contrato.get("cpf") or "",
            "data_assinatura": data_assinatura,
            "assinatura": assinatura,
            "foto": foto,
            "ip": ip_assinatura,
            "user_agent": user_agent,
            "aceite": texto_aceite_completo,
            "versao": VERSAO_CONTRATO
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        hash_assinado = hashlib.sha256(dados_hash.encode("utf-8")).hexdigest().upper()

        caminho_publico = f"/contrato/pdf/{contrato['id']}"

        cursor.execute("""
            UPDATE contratos_alunos
            SET status = 'assinado',
                assinatura_base64 = %s,
                foto_assinatura_base64 = %s,
                arquivo_assinado_path = %s,
                data_assinatura = %s,
                ip_assinatura = %s,
                user_agent_assinatura = %s,
                aceite_contrato = TRUE,
                aceite_foto = TRUE,
                texto_aceite = %s,
                versao_contrato = %s,
                hash_assinado = %s
            WHERE id = %s AND status = 'pendente'
        """, (
            assinatura,
            foto,
            caminho_publico,
            data_assinatura,
            ip_assinatura,
            user_agent,
            texto_aceite_completo,
            VERSAO_CONTRATO,
            hash_assinado,
            contrato["id"]
        ))

        if cursor.rowcount != 1:
            conn.rollback()
            conn.close()
            return "Este contrato já foi assinado ou não está mais disponível.", 409

        conn.commit()
        conn.close()

        # Gera e grava o PDF definitivo. Se houver falha externa de renderização,
        # a assinatura permanece válida e o PDF poderá ser gerado novamente ao abrir a rota.
        try:
            gerar_pdf_contrato_assinado(contrato["id"], salvar=True)
        except Exception as e:
            print(f"Erro ao gerar PDF final do contrato {contrato['id']}: {e}")

        return redirect(url_for("visualizar_contrato_registro", contrato_id=contrato["id"]))

    conn.close()

    return render_template(
        "assinar_contrato.html",
        contrato=contrato,
        texto_aceite_contrato=TEXTO_ACEITE_CONTRATO,
        texto_aceite_foto=TEXTO_ACEITE_FOTO
    )


@app.route("/mew/contratos", methods=["GET", "POST"])
def mew_contratos():
    if not session.get("mew_admin"):
        return "Não autorizado", 403

    init_contratos_db()

    if request.method == "POST":
        aluno_id = request.form.get("aluno_id", type=int)
        if not aluno_id:
            return redirect("/mew/contratos")
        criar_contrato_aluno(aluno_id)
        return redirect("/mew/contratos")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT a.id, a.nome, a.ra
        FROM alunos a
        WHERE NOT EXISTS (
            SELECT 1 FROM contratos_alunos c WHERE c.aluno_id = a.id
        )
        ORDER BY a.nome
    """)
    alunos_sem_contrato = cursor.fetchall()

    cursor.execute("""
        SELECT c.*, a.nome, a.ra
        FROM contratos_alunos c
        JOIN alunos a ON a.id = c.aluno_id
        ORDER BY c.id DESC
    """)
    contratos = cursor.fetchall()
    conn.close()

    return render_template_string("""
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <title>MEW - Contratos Automáticos</title>
        <style>
            body { font-family:Arial,sans-serif; padding:30px; background:#f4f4f4; color:#111827; }
            .box { background:white; padding:25px; border-radius:10px; margin-bottom:25px; }
            select, button { padding:10px; margin:5px 0; width:100%; }
            button { background:#111827; color:white; border:0; cursor:pointer; }
            table { width:100%; border-collapse:collapse; background:white; }
            th, td { padding:10px; border-bottom:1px solid #ddd; text-align:left; }
            .pendente { color:#b45309; font-weight:bold; }
            .assinado { color:#15803d; font-weight:bold; }
            a { color:#1d4ed8; }
        </style>
    </head>
    <body>
        <p><a href="/mew/dashboard">← Voltar ao Dashboard</a></p>
        <div class="box">
            <h2>Contratos automáticos</h2>
            <p>O contrato padrão é criado automaticamente no cadastro do aluno. Não é mais necessário anexar PDF.</p>

            {% if alunos_sem_contrato %}
            <form method="POST">
                <label>Gerar contrato para cadastro antigo sem contrato:</label>
                <select name="aluno_id" required>
                    <option value="">Selecione...</option>
                    {% for aluno in alunos_sem_contrato %}
                    <option value="{{ aluno.id }}">{{ aluno.nome }} - RA {{ aluno.ra }}</option>
                    {% endfor %}
                </select>
                <button type="submit">GERAR CONTRATO PADRÃO</button>
            </form>
            {% endif %}
        </div>

        <div class="box">
            <h2>Contratos</h2>
            <table>
                <tr><th>Aluno</th><th>Matrícula/RA</th><th>Status</th><th>Envio</th><th>Documento</th></tr>
                {% for c in contratos %}
                <tr>
                    <td>{{ c.nome }}</td>
                    <td>{{ c.ra }}</td>
                    <td class="{{ c.status }}">{{ c.status|upper }}</td>
                    <td>{{ c.data_envio }}</td>
                    <td><a href="/contrato/registro/{{ c.id }}" target="_blank">Abrir contrato</a></td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </body>
    </html>
    """, contratos=contratos, alunos_sem_contrato=alunos_sem_contrato)

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
      .btn{display:inline-block;margin:10px;padding:13px 22px;border-radius:8px;background:#009ee3;color:#fff;text-decoration:none;font-weight:700}
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
    """Anexa qualquer arquivo e gera documento autenticado com QR Code - VERSÃO SIMPLES"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    if request.method == "GET":
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Anexar Documento - SiGEU Educacional</title>
            <style>
                body { font-family: Arial, sans-serif; padding: 40px; background: #f7fafc; }
                .container { max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); border-top: 4px solid #1a237e; }
                h1 { color: #1a237e; }
                label { display: block; margin-top: 15px; font-weight: bold; color: #333; }
                input, select, textarea { width: 100%; padding: 10px; margin-top: 5px; border: 1px solid #ddd; border-radius: 5px; }
                .btn { background: #1a237e; color: white; padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; margin-top: 20px; width: 100%; font-weight: bold; font-size: 16px; }
                .btn:hover { background: #0d1b6b; }
                .info { background: #e8f5e8; padding: 15px; border-radius: 5px; margin: 10px 0; border-left: 4px solid #16a34a; }
                .preview { background: #f1f5f9; padding: 10px; border-radius: 5px; margin-top: 10px; display: none; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📎 ANEXAR DOCUMENTO</h1>
                <p>Envie qualquer arquivo e gere um documento autenticado com QR Code.</p>
                
                <div class="info">
                    <strong>📌 Como funciona:</strong><br>
                    1. Selecione o tipo de documento<br>
                    2. Anexe o arquivo<br>
                    3. Clique em "Anexar e Autenticar"<br>
                    4. O documento será gerado com QR Code e código de autenticação
                </div>
                
                <form action="/mew/anexar-documento" method="POST" enctype="multipart/form-data">
                    <label>📋 Tipo de Documento *</label>
                    <select name="tipo" required>
                        <option value="">Selecione...</option>
                        <option value="plano_aula">Plano de Aula</option>
                        <option value="certificado">Certificado</option>
                        <option value="diploma">Diploma</option>
                        <option value="atestado">Atestado</option>
                        <option value="comprovante">Comprovante</option>
                        <option value="outro">Outro</option>
                    </select>
                    
                    <label>📝 Título do Documento</label>
                    <input type="text" name="titulo" placeholder="Ex: Plano de Aula - Matemática">
                    
                    <label>📝 Descrição (opcional)</label>
                    <textarea name="descricao" rows="3" placeholder="Descreva o documento..."></textarea>
                    
                    <label>📄 Arquivo *</label>
                    <input type="file" name="arquivo" required id="arquivoInput">
                    <div class="preview" id="previewDiv">
                        <strong>Arquivo selecionado:</strong> <span id="nomeArquivo"></span>
                    </div>
                    
                    <button type="submit" class="btn">🔐 ANEXAR E AUTENTICAR</button>
                </form>
                
                <p style="margin-top: 20px; text-align: center; color: #666;">
                    <a href="/mew/dashboard">⬅️ Voltar ao MEW</a>
                </p>
            </div>
            
            <script>
                document.getElementById('arquivoInput').addEventListener('change', function(e) {
                    const preview = document.getElementById('previewDiv');
                    const nome = document.getElementById('nomeArquivo');
                    if (this.files && this.files[0]) {
                        nome.textContent = this.files[0].name + ' (' + (this.files[0].size / 1024).toFixed(1) + ' KB)';
                        preview.style.display = 'block';
                    } else {
                        preview.style.display = 'none';
                    }
                });
            </script>
        </body>
        </html>
        '''
    
    # POST - Processa o arquivo
    try:
        from datetime import datetime, timedelta
        import secrets
        import hashlib
        import json
        import base64
        
        # Dados do formulário
        tipo = request.form.get("tipo")
        titulo = request.form.get("titulo", f"Documento {tipo}")
        descricao = request.form.get("descricao", "")
        
        if not tipo:
            return "Tipo de documento obrigatório", 400
        
        # Pega o arquivo
        if 'arquivo' not in request.files:
            return "Nenhum arquivo enviado", 400
        
        arquivo = request.files['arquivo']
        if arquivo.filename == '':
            return "Nenhum arquivo selecionado", 400
        
        # Lê o arquivo e codifica em base64
        arquivo_bytes = arquivo.read()
        arquivo_base64 = base64.b64encode(arquivo_bytes).decode()
        
        # Gera código de autenticação
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        codigo = f"DOC-{timestamp}-{secrets.token_hex(4).upper()}"
        
        # Gera hash
        hash_documento = hashlib.sha256(
            f"{tipo}{timestamp}{arquivo.filename}{arquivo_base64[:100]}".encode()
        ).hexdigest()
        
        # Gera link de validação e QR Code
        base_url = request.host_url.rstrip('/')
        link_validacao = f"{base_url}/validar-documento/{codigo}"
        qr_code_base64 = gerar_qrcode_base64(link_validacao)
        
        data_emissao = datetime.now().strftime("%d/%m/%Y %H:%M")
        data_validade = (datetime.now() + timedelta(days=365*5)).strftime("%d/%m/%Y")
        
        # Determinar ícone baseado na extensão
        file_ext = arquivo.filename.split('.')[-1].lower()
        file_icon = "📄"
        if file_ext in ['pdf']:
            file_icon = "📕"
        elif file_ext in ['jpg', 'jpeg', 'png', 'gif']:
            file_icon = "🖼️"
        elif file_ext in ['doc', 'docx']:
            file_icon = "📘"
        elif file_ext in ['xls', 'xlsx']:
            file_icon = "📊"
        
        # ============================================
        # GERA HTML IGUAL AOS OUTROS DOCUMENTOS
        # ============================================
        
        html_conteudo = f'''
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>{titulo} - SiGEU Educação</title>
            <style>
                * {{
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }}
                body {{
                    margin: 0;
                    padding: 0;
                    background: #c9c9c9;
                    font-family: "Arial Nova", "Arial", "Calibri", "Segoe UI", sans-serif;
                    font-size: 10.5pt;
                    color: #1a1a1a;
                    line-height: 1.4;
                    -webkit-print-color-adjust: exact;
                    print-color-adjust: exact;
                }}
                .folha {{
                    width: 210mm;
                    min-height: 297mm;
                    margin: 0 auto;
                    background: #fefefe;
                    position: relative;
                    overflow: hidden;
                    box-shadow: 0 0 20px rgba(0,0,0,0.3);
                    padding: 15mm 20mm 25mm 20mm;
                    page-break-after: always;
                }}
                .borda-seguranca {{
                    position: absolute;
                    top: 8mm;
                    left: 8mm;
                    right: 8mm;
                    bottom: 8mm;
                    border: 0.5pt solid #1a237e;
                    pointer-events: none;
                }}
                .borda-seguranca::before {{
                    content: "";
                    position: absolute;
                    top: 2mm;
                    left: 2mm;
                    right: 2mm;
                    bottom: 2mm;
                    border: 0.3pt dashed #1a237e;
                    opacity: 0.5;
                }}
                .cantoneira {{
                    position: absolute;
                    width: 15mm;
                    height: 15mm;
                    border: 2pt solid #1a237e;
                    z-index: 100;
                }}
                .cantoneira.top-left {{ top: 6mm; left: 6mm; border-right: none; border-bottom: none; }}
                .cantoneira.top-right {{ top: 6mm; right: 6mm; border-left: none; border-bottom: none; }}
                .cantoneira.bottom-left {{ bottom: 6mm; left: 6mm; border-right: none; border-top: none; }}
                .cantoneira.bottom-right {{ bottom: 6mm; right: 6mm; border-left: none; border-top: none; }}
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
                .microtexto-borda {{
                    position: absolute;
                    font-family: "Arial", sans-serif;
                    font-size: 5pt;
                    color: rgba(26,35,126,0.3);
                    letter-spacing: 1px;
                    text-transform: uppercase;
                    white-space: nowrap;
                    z-index: 2;
                }}
                .microtexto-borda.top {{ top: 5mm; left: 50%; transform: translateX(-50%); }}
                .microtexto-borda.bottom {{ bottom: 5mm; left: 50%; transform: translateX(-50%); }}
                .microtexto-borda.left {{ left: 3mm; top: 50%; transform: translateY(-50%) rotate(-90deg); transform-origin: center; }}
                .microtexto-borda.right {{ right: 3mm; top: 50%; transform: translateY(-50%) rotate(90deg); transform-origin: center; }}
                .faixa-identificadora {{
                    position: absolute;
                    top: 0;
                    left: 0;
                    right: 0;
                    height: 4mm;
                    background: repeating-linear-gradient(90deg, #1a237e 0px, #1a237e 5mm, #ffffff 5mm, #ffffff 10mm, #1a237e 10mm, #1a237e 15mm);
                    z-index: 10;
                }}
                .cabecalho {{
                    position: relative;
                    z-index: 5;
                    border-bottom: 1.5pt solid #1a237e;
                    padding-bottom: 4mm;
                    margin-bottom: 10mm;
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                }}
                .logo-area {{
                    display: flex;
                    align-items: center;
                    gap: 5mm;
                }}
                .logo-area img {{
                    width: 25mm;
                    height: auto;
                    opacity: 0.9;
                }}
                .instituicao-nome {{
                    font-family: "Arial Black", "Arial", sans-serif;
                    font-size: 14pt;
                    color: #1a237e;
                    text-transform: uppercase;
                    letter-spacing: 1.5px;
                    line-height: 1.2;
                    margin-top: 8mm;
                }}
                .instituicao-sub {{
                    font-family: "Arial", sans-serif;
                    font-size: 8pt;
                    color: #444;
                    margin-top: 2mm;
                    line-height: 1.3;
                }}
                .selo-autenticidade {{
                    width: 22mm;
                    height: 22mm;
                    border: 1.5pt solid #1a237e;
                    border-radius: 50%;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    font-family: "Arial", sans-serif;
                    font-size: 6pt;
                    color: #1a237e;
                    text-align: center;
                    line-height: 1.1;
                    position: relative;
                    background: radial-gradient(circle, rgba(26,35,126,0.05) 0%, transparent 70%);
                }}
                .selo-autenticidade::before {{
                    content: "";
                    display: inline-block;
                    width: 24px;
                    height: 16px;
                    margin-bottom: 1mm;
                    margin-right: 4px;
                    vertical-align: middle;
                    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='16' viewBox='0 0 24 16'%3E%3Crect x='0' y='0' width='2' height='16' fill='%231a237e'/%3E%3Crect x='4' y='0' width='1' height='16' fill='%231a237e'/%3E%3Crect x='7' y='0' width='3' height='16' fill='%231a237e'/%3E%3Crect x='12' y='0' width='1' height='16' fill='%231a237e'/%3E%3Crect x='15' y='0' width='2' height='16' fill='%231a237e'/%3E%3Crect x='19' y='0' width='1' height='16' fill='%231a237e'/%3E%3Crect x='22' y='0' width='2' height='16' fill='%231a237e'/%3E%3C/svg%3E");
                    background-repeat: no-repeat;
                    background-size: contain;
                }}
                .titulo-documento {{
                    text-align: center;
                    margin: 1mm 0 10mm 0;
                    position: relative;
                    z-index: 5;
                }}
                .titulo-principal {{
                    font-family: "Arial Black", "Arial", sans-serif;
                    font-size: 18pt;
                    color: #1a237e;
                    text-transform: uppercase;
                    letter-spacing: 4px;
                    margin-bottom: 3mm;
                    position: relative;
                    display: inline-block;
                    padding: 0 15mm;
                }}
                .titulo-principal::before, .titulo-principal::after {{
                    content: "";
                    position: absolute;
                    top: 50%;
                    width: 10mm;
                    height: 1pt;
                    background: #1a237e;
                }}
                .titulo-principal::before {{ left: 0; }}
                .titulo-principal::after {{ right: 0; }}
                .box-identificacao {{
                    border: 1pt solid #1a237e;
                    margin: 8mm 0;
                    position: relative;
                    z-index: 5;
                    background: rgba(26,35,126,0.02);
                }}
                .box-identificacao-header {{
                    background: #1a237e;
                    color: #fff;
                    font-family: "Arial Black", "Arial", sans-serif;
                    font-size: 8pt;
                    text-transform: uppercase;
                    letter-spacing: 2px;
                    padding: 1mm 4mm;
                    text-align: center;
                }}
                .box-identificacao-content {{
                    padding: 3mm;
                }}
                .linha-dado {{
                    display: flex;
                    margin-bottom: 3mm;
                    border-bottom: 0.3pt dotted #999;
                    padding-bottom: 2mm;
                }}
                .linha-dado:last-child {{ margin-bottom: 0; border-bottom: none; }}
                .rotulo {{
                    width: 25mm;
                    font-family: "Arial", sans-serif;
                    font-size: 8pt;
                    color: #1a237e;
                    font-weight: bold;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                }}
                .valor {{
                    flex: 1;
                    font-family: "Arial", sans-serif;
                    font-size: 11pt;
                    color: #000;
                    font-weight: bold;
                    padding-left: 3mm;
                }}
                .conteudo-arquivo {{
                    border: 1pt solid #ddd;
                    margin: 8mm 0;
                    padding: 5mm;
                    background: #f9f9f9;
                    position: relative;
                    z-index: 5;
                    border-left: 4pt solid #1a237e;
                }}
                .conteudo-arquivo::before {{
                    content: "📄 CONTEÚDO DO DOCUMENTO";
                    position: absolute;
                    top: -3mm;
                    left: 5mm;
                    background: #f9f9f9;
                    padding: 0 3mm;
                    font-family: "Arial Black", "Arial", sans-serif;
                    font-size: 7pt;
                    color: #1a237e;
                    letter-spacing: 1px;
                }}
                .conteudo-arquivo a {{
                    color: #1a237e;
                    text-decoration: none;
                    font-weight: bold;
                }}
                .conteudo-arquivo a:hover {{
                    text-decoration: underline;
                }}
                .qr-code-box {{
                    position: absolute;
                    bottom: 23mm;
                    left: 15mm;
                    width: 30mm;
                    height: 30mm;
                    border: 0.5pt solid #ccc;
                    background: #fafafa;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    z-index: 5;
                }}
                .qr-code-label {{
                    font-size: 6pt;
                    color: #666;
                    text-transform: uppercase;
                    letter-spacing: 1px;
                    margin-bottom: 2mm;
                }}
                .qr-code-box img {{
                    width: 20mm;
                    height: 20mm;
                    object-fit: contain;
                }}
                .rodape-tecnico {{
                    position: absolute;
                    bottom: 12mm;
                    left: 50mm;
                    right: 15mm;
                    font-family: "Arial", sans-serif;
                    font-size: 6.5pt;
                    color: #666;
                    text-align: center;
                    line-height: 1.4;
                    z-index: 5;
                    border-top: 0.3pt solid #ddd;
                    padding-top: 3mm;
                }}
                .rodape-tecnico strong {{
                    color: #1a237e;
                }}
                .data-local {{
                    text-align: right;
                    margin: 20mm 0 10mm 0;
                    font-family: "Arial", sans-serif;
                    font-size: 8pt;
                    color: #333;
                    position: relative;
                    z-index: 5;
                    font-style: italic;
                }}
                .assinatura-area {{
                    margin-top: 20mm;
                    text-align: center;
                    position: relative;
                    z-index: 5;
                    page-break-inside: avoid;
                }}
                .assinatura-linha {{
                    width: 70mm;
                    height: 0;
                    border-top: 0.5pt solid #000;
                    margin: 0 auto 3mm auto;
                    position: relative;
                }}
                .assinatura-nome {{
                    font-family: "Arial Black", "Arial", sans-serif;
                    font-size: 11pt;
                    color: #1a237e;
                    margin-bottom: 1mm;
                }}
                .assinatura-cargo {{
                    font-family: "Arial", sans-serif;
                    font-size: 8pt;
                    color: #555;
                    text-transform: uppercase;
                    letter-spacing: 1px;
                }}
                @media print {{
                    body {{ background: #fff; }}
                    .folha {{ box-shadow: none; margin: 0; }}
                }}
            </style>
        </head>
        <body>
            <div class="folha">
                <div class="borda-seguranca"></div>
                <div class="cantoneira top-left"></div>
                <div class="cantoneira top-right"></div>
                <div class="cantoneira bottom-left"></div>
                <div class="cantoneira bottom-right"></div>
                
                <div class="microtexto-borda top">DOCUMENTO OFICIAL - FCP Certificadora | SiGEu Educ - VALIDAÇÃO DIGITAL OBRIGATÓRIA</div>
                <div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98</div>
                <div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
                <div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>
                
                <div class="marca-dagua-principal">FACOP SiGEu</div>
                <div class="marca-dagua-pattern"></div>
                
                <div class="faixa-identificadora"></div>
                
                <div class="cabecalho">
                    <div class="logo-area">
                        <img src="/static/img/logo_declaracao.png" alt="Logo Institucional">
                        <div>
                            <div class="instituicao-nome">FACOP - SiGEu</div>
                            <div class="instituicao-sub">Faculdade do Centro Oeste Paulista<br>Credenciada pela Portaria MEC nº 887 de 26/07/2017</div>
                        </div>
                    </div>
                    <div class="selo-autenticidade">SiGEu Educacional<br>e-SIGEU-ICP-2026</div>
                </div>
                
                <div class="titulo-documento">
                    <div class="titulo-principal">{titulo.upper()}</div>
                </div>
                
                <div class="box-identificacao">
                    <div class="box-identificacao-header">DADOS DO DOCUMENTO</div>
                    <div class="box-identificacao-content">
                        <div class="linha-dado"><div class="rotulo">Tipo</div><div class="valor">{tipo.upper()}</div></div>
                        <div class="linha-dado"><div class="rotulo">Arquivo</div><div class="valor">{arquivo.filename}</div></div>
                        <div class="linha-dado"><div class="rotulo">Descrição</div><div class="valor">{descricao if descricao else 'Não informada'}</div></div>
                        <div class="linha-dado"><div class="rotulo">Data Emissão</div><div class="valor">{data_emissao}</div></div>
                        <div class="linha-dado"><div class="rotulo">Código</div><div class="valor" style="font-family:monospace;font-size:10pt;">{codigo}</div></div>
                    </div>
                </div>
                
                <div class="conteudo-arquivo">
                    <p style="text-align:center;padding:10px;">
                        <a href="data:application/octet-stream;base64,{arquivo_base64}" download="{arquivo.filename}" style="font-size:14pt;">
                            ACESSO ABERTO AO PLANO DE ENSINO: {arquivo.filename} ({file_icon})
                        </a>
                    </p>
                </div>
                
                <div class="data-local">São Paulo – SP, {datetime.now().strftime("%d de %B de %Y")}</div>
                
                <div class="assinatura-area">
    <img src="/static/img/assinatura_total.png" alt="Assinatura e Carimbo" style="max-width: 250px; height: auto;">
    <div style="margin-top: 5px; font-size: 8pt; color: #555; text-transform: uppercase; letter-spacing: 1px;">
        DEPARTAMENTO EDUCACIONAL • SiGEU Educacional
    </div>
</div>
                
                <div class="qr-code-box">
                    <div class="qr-code-label">Validação Digital</div>
                    <img src="{qr_code_base64}" alt="QR Code">
                </div>
                
                <div class="rodape-tecnico">
                    <strong>DOCUMENTO GERADO ELETRONICAMENTE</strong> em conformidade com as Leis nº 11.419/06 e 14.063/20.<br>
                    Para verificar autenticidade: <strong>{base_url}/validar-documento</strong> | Protocolo: {codigo}
                </div>
            </div>
        </body>
        </html>
        '''
        
        # Salva no banco
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Garantir colunas
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN aluno_id INTEGER")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN qr_code TEXT")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN hash_documento TEXT")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN data_emissao TEXT")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN data_validade TEXT")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE documentos_autenticados ADD COLUMN metadados TEXT")
        except:
            pass
        
        cursor.execute('''
            INSERT INTO documentos_autenticados 
            (codigo, aluno_id, aluno_nome, aluno_ra, tipo, conteudo_html, data_geracao,
             qr_code, hash_documento, data_emissao, data_validade, metadados)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            codigo,
            None,
            "ADMIN - MEW",
            "ADMIN",
            f"anexo_{tipo}",
            html_conteudo,
            data_emissao,
            qr_code_base64,
            hash_documento,
            data_emissao,
            data_validade,
            json.dumps({"tipo": tipo, "arquivo": arquivo.filename, "descricao": descricao, "titulo": titulo})
        ))
        
        conn.commit()
        conn.close()
        
        # Página de sucesso
        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Documento Autenticado - SiGEU Educação/Facop Certificadora</title>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 40px; background: #f7fafc; }}
                .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); border-top: 4px solid #16a34a; text-align: center; }}
                .success {{ color: #16a34a; font-size: 48px; }}
                h1 {{ color: #1a237e; }}
                .code {{ background: #f1f5f9; padding: 20px; border-radius: 5px; font-family: monospace; font-size: 16px; word-break: break-all; margin: 20px 0; }}
                .btn {{ display: inline-block; background: #1a237e; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin: 5px; }}
                .btn:hover {{ background: #0d1b6b; }}
                .info {{ background: #e8f5e8; padding: 15px; border-radius: 5px; margin: 15px 0; border-left: 4px solid #16a34a; text-align: left; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="success">✅</div>
                <h1>DOCUMENTO AUTENTICADO!</h1>
                
                <div class="info">
                    <p><strong>📌 Tipo:</strong> {tipo.upper()}</p>
                    <p><strong>📎 Arquivo:</strong> {arquivo.filename}</p>
                    <p><strong>📅 Emissão:</strong> {data_emissao}</p>
                </div>
                
                <p><strong>🔑 Código de Autenticação:</strong></p>
                <div class="code">{codigo}</div>
                
                <a href="{link_validacao}" class="btn" target="_blank">📄 Ver Documento</a>
                <a href="/validar-documento" class="btn" style="background:#6c757d;">🔍 Validar</a>
                <a href="/mew/anexar-documento" class="btn" style="background:#16a34a;">📎 Novo</a>
                <a href="/mew/dashboard" class="btn" style="background:#6c757d;">⬅️ Voltar</a>
                
                <p style="margin-top: 20px; color: #666; font-size: 14px;">
                    <strong>⚠️ Guarde este código!</strong> Ele será usado para validar o documento.
                </p>
            </div>
        </body>
        </html>
        '''
        
    except Exception as e:
        import traceback
        print(f"Erro: {e}")
        print(traceback.format_exc())
        return f"❌ Erro: {str(e)}", 500
    
    

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

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM projetos_finais
        WHERE aluno_id = %s
          AND disciplina_id = %s
          AND liberado = 1
    """, (aluno_id, disciplina_id))

    projeto = cursor.fetchone()

    if not projeto:
        conn.close()
        return redirect("/projeto-final?erro=Projeto+Final+não+liberado")

    if projeto["corrigido"]:
        conn.close()
        return redirect("/projeto-final?erro=Este+projeto+já+foi+corrigido")

    arquivo = request.files.get("arquivo")

    if not arquivo or arquivo.filename == "":
        conn.close()
        return redirect("/projeto-final?erro=Selecione+um+arquivo")

    extensoes_permitidas = {"pdf", "doc", "docx", "zip"}
    nome_original = arquivo.filename
    extensao = nome_original.rsplit(".", 1)[1].lower() if "." in nome_original else ""

    if extensao not in extensoes_permitidas:
        conn.close()
        return redirect(
            "/projeto-final?erro=Formato+não+permitido.+Use+PDF,+DOC,+DOCX+ou+ZIP"
        )

    upload_dir = os.path.join(
        "static",
        "uploads",
        "projetos_finais",
        str(aluno_id),
        str(disciplina_id)
    )
    os.makedirs(upload_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_seguro = secure_filename(nome_original)
    nome_salvo = f"{timestamp}_{nome_seguro}"
    caminho_completo = os.path.join(upload_dir, nome_salvo)

    arquivo.save(caminho_completo)

    arquivo_path = os.path.join(
        "uploads",
        "projetos_finais",
        str(aluno_id),
        str(disciplina_id),
        nome_salvo
    ).replace("\\", "/")

    data_envio = datetime.now().strftime("%d/%m/%Y %H:%M")

    cursor.execute("""
        UPDATE projetos_finais
        SET arquivo_path = %s,
            nome_arquivo = %s,
            data_envio = %s,
            nota = NULL,
            corrigido = 0,
            data_correcao = NULL
        WHERE aluno_id = %s
          AND disciplina_id = %s
    """, (
        arquivo_path,
        nome_original,
        data_envio,
        aluno_id,
        disciplina_id
    ))

    conn.commit()
    conn.close()

    return redirect("/projeto-final?sucesso=Projeto+enviado+com+sucesso")


@app.route("/mew/arquivo-final")
def arquivo_final():
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, nome, ra FROM alunos ORDER BY nome")
    alunos = cursor.fetchall()

    cursor.execute("SELECT id, nome FROM disciplinas ORDER BY nome")
    disciplinas = cursor.fetchall()

    cursor.execute("""
        SELECT
            pf.*,
            a.nome AS aluno_nome,
            a.ra AS aluno_ra,
            d.nome AS disciplina_nome
        FROM projetos_finais pf
        JOIN alunos a ON a.id = pf.aluno_id
        JOIN disciplinas d ON d.id = pf.disciplina_id
        ORDER BY
            CASE
                WHEN pf.arquivo_path IS NOT NULL
                 AND pf.corrigido = 0
                THEN 0
                ELSE 1
            END,
            pf.id DESC
    """)

    projetos = cursor.fetchall()
    conn.close()

    return render_template(
        "mew/arquivo_final.html",
        alunos=alunos,
        disciplinas=disciplinas,
        projetos=projetos
    )


@app.route("/mew/liberar-projeto-final", methods=["POST"])
def liberar_projeto_final():
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    aluno_id = request.form.get("aluno_id")
    disciplina_id = request.form.get("disciplina_id")
    titulo_atividade = (request.form.get("titulo_atividade") or "Projeto Final").strip()
    conteudo_atividade = (request.form.get("conteudo_atividade") or "").strip()
    arquivo_atividade = request.files.get("arquivo_atividade")

    if not aluno_id or not disciplina_id:
        return redirect(
            "/mew/arquivo-final?erro=Selecione+aluno+e+disciplina"
        )

    if not conteudo_atividade and (not arquivo_atividade or arquivo_atividade.filename == ""):
        return redirect(
            "/mew/arquivo-final?erro=Escreva+as+orientações+ou+anexe+o+arquivo+da+atividade"
        )

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM projetos_finais
        WHERE aluno_id = %s
          AND disciplina_id = %s
    """, (aluno_id, disciplina_id))

    existente = cursor.fetchone()

    arquivo_atividade_path = existente["arquivo_atividade_path"] if existente else None
    nome_arquivo_atividade = existente["nome_arquivo_atividade"] if existente else None

    if arquivo_atividade and arquivo_atividade.filename:
        extensoes_atividade = {"pdf", "doc", "docx"}
        nome_original_atividade = arquivo_atividade.filename
        extensao_atividade = (
            nome_original_atividade.rsplit(".", 1)[1].lower()
            if "." in nome_original_atividade
            else ""
        )

        if extensao_atividade not in extensoes_atividade:
            conn.close()
            return redirect(
                "/mew/arquivo-final?erro=Arquivo+da+atividade+deve+ser+PDF,+DOC+ou+DOCX"
            )

        upload_dir = os.path.join(
            "static",
            "uploads",
            "projetos_finais",
            "atividades",
            str(aluno_id),
            str(disciplina_id)
        )
        os.makedirs(upload_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_seguro = secure_filename(nome_original_atividade)
        nome_salvo = f"{timestamp}_{nome_seguro}"
        arquivo_atividade.save(os.path.join(upload_dir, nome_salvo))

        arquivo_atividade_path = os.path.join(
            "uploads",
            "projetos_finais",
            "atividades",
            str(aluno_id),
            str(disciplina_id),
            nome_salvo
        ).replace("\\", "/")
        nome_arquivo_atividade = nome_original_atividade

    agora = datetime.now().strftime("%d/%m/%Y %H:%M")

    if existente:
        cursor.execute("""
            UPDATE projetos_finais
            SET liberado = 1,
                titulo_atividade = %s,
                conteudo_atividade = %s,
                arquivo_atividade_path = %s,
                nome_arquivo_atividade = %s,
                data_liberacao = %s
            WHERE aluno_id = %s
              AND disciplina_id = %s
        """, (
            titulo_atividade,
            conteudo_atividade,
            arquivo_atividade_path,
            nome_arquivo_atividade,
            agora,
            aluno_id,
            disciplina_id
        ))
    else:
        cursor.execute("""
            INSERT INTO projetos_finais
            (
                aluno_id,
                disciplina_id,
                liberado,
                titulo_atividade,
                conteudo_atividade,
                arquivo_atividade_path,
                nome_arquivo_atividade,
                data_liberacao
            )
            VALUES (%s, %s, 1, %s, %s, %s, %s, %s)
        """, (
            aluno_id,
            disciplina_id,
            titulo_atividade,
            conteudo_atividade,
            arquivo_atividade_path,
            nome_arquivo_atividade,
            agora
        ))

    # Projeto Final substitui a prova final normal: desativa a liberação de 30 questões
    cursor.execute("""
        UPDATE liberacao_final
        SET liberada = 0
        WHERE aluno_id = %s
          AND disciplina_id = %s
    """, (aluno_id, disciplina_id))

    # Mantém coerência com qualquer flag antiga de abertura da prova final
    cursor.execute("""
        UPDATE aluno_disciplina_datas
        SET prova_final_aberta = 0
        WHERE aluno_id = %s
          AND disciplina_id = %s
    """, (aluno_id, disciplina_id))

    conn.commit()
    conn.close()

    return redirect(
        "/mew/arquivo-final?sucesso=Projeto+Final+liberado+e+prova+final+normal+bloqueada"
    )


@app.route("/mew/editar-projeto-final/<int:projeto_id>", methods=["POST"])
def editar_projeto_final(projeto_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")

    titulo_atividade = (request.form.get("titulo_atividade") or "Projeto Final").strip()
    conteudo_atividade = (request.form.get("conteudo_atividade") or "").strip()
    arquivo_atividade = request.files.get("arquivo_atividade")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM projetos_finais WHERE id = %s", (projeto_id,))
    projeto = cursor.fetchone()

    if not projeto:
        conn.close()
        return redirect("/mew/arquivo-final?erro=Projeto+não+encontrado")

    arquivo_atividade_path = projeto["arquivo_atividade_path"]
    nome_arquivo_atividade = projeto["nome_arquivo_atividade"]

    if arquivo_atividade and arquivo_atividade.filename:
        extensoes_atividade = {"pdf", "doc", "docx"}
        nome_original_atividade = arquivo_atividade.filename
        extensao_atividade = (
            nome_original_atividade.rsplit(".", 1)[1].lower()
            if "." in nome_original_atividade
            else ""
        )

        if extensao_atividade not in extensoes_atividade:
            conn.close()
            return redirect(
                "/mew/arquivo-final?erro=Arquivo+da+atividade+deve+ser+PDF,+DOC+ou+DOCX"
            )

        upload_dir = os.path.join(
            "static",
            "uploads",
            "projetos_finais",
            "atividades",
            str(projeto["aluno_id"]),
            str(projeto["disciplina_id"])
        )
        os.makedirs(upload_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_seguro = secure_filename(nome_original_atividade)
        nome_salvo = f"{timestamp}_{nome_seguro}"
        arquivo_atividade.save(os.path.join(upload_dir, nome_salvo))

        arquivo_atividade_path = os.path.join(
            "uploads",
            "projetos_finais",
            "atividades",
            str(projeto["aluno_id"]),
            str(projeto["disciplina_id"]),
            nome_salvo
        ).replace("\\", "/")
        nome_arquivo_atividade = nome_original_atividade

    cursor.execute("""
        UPDATE projetos_finais
        SET titulo_atividade = %s,
            conteudo_atividade = %s,
            arquivo_atividade_path = %s,
            nome_arquivo_atividade = %s
        WHERE id = %s
    """, (
        titulo_atividade,
        conteudo_atividade,
        arquivo_atividade_path,
        nome_arquivo_atividade,
        projeto_id
    ))

    conn.commit()
    conn.close()

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
        SELECT *
        FROM projetos_finais
        WHERE id = %s
    """, (projeto_id,))

    projeto = cursor.fetchone()

    if not projeto:
        conn.close()
        return redirect(
            "/mew/arquivo-final?erro=Projeto+não+encontrado"
        )

    if not projeto["arquivo_path"]:
        conn.close()
        return redirect(
            "/mew/arquivo-final?erro=O+aluno+ainda+não+enviou+o+arquivo"
        )

    aluno_id = projeto["aluno_id"]
    disciplina_id = projeto["disciplina_id"]

    cursor.execute("""
        SELECT AVG(nota) AS media_disciplina
        FROM notas
        WHERE aluno_id = %s
          AND disciplina_id = %s
    """, (
        aluno_id,
        disciplina_id
    ))

    resultado_media = cursor.fetchone()

    if resultado_media and resultado_media["media_disciplina"] is not None:
        media_disciplina = float(resultado_media["media_disciplina"])
    else:
        media_disciplina = 0

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
    """Usa docente real quando cadastrado; nunca inventa uma pessoa inexistente."""
    cursor.execute("""
        SELECT doc.nome, doc.titulacao
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
        return nome

    cursor.execute("SELECT docente_documental FROM disciplinas WHERE id = %s", (disciplina_id,))
    row = cursor.fetchone()
    if row and row.get("docente_documental"):
        return row["docente_documental"]

    # Designação funcional: evita atribuir falsamente a disciplina a uma pessoa real/fictícia.
    designacao = "Docente Responsável — Coordenação Acadêmica SIGEU"
    cursor.execute(
        "UPDATE disciplinas SET docente_documental = %s WHERE id = %s",
        (designacao, disciplina_id)
    )
    return designacao


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
               addd.data_inicio, addd.data_fim_previsto,
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

    cursor.execute("""
        SELECT COUNT(DISTINCT capitulo) AS total
        FROM notas WHERE aluno_id = %s AND disciplina_id = %s
    """, (aluno_id, disciplina_id))
    capitulos_avaliados = int((cursor.fetchone() or {}).get("total") or 0)

    cursor.execute("""
        SELECT corrigido, nota, arquivo_path, data_envio
        FROM projetos_finais
        WHERE aluno_id = %s AND disciplina_id = %s
        LIMIT 1
    """, (aluno_id, disciplina_id))
    projeto = cursor.fetchone()

    data_inicio = _parse_data_sigeu(d.get("data_inicio"))
    dias_cursados = (datetime.now().date() - data_inicio.date()).days if data_inicio else 0
    faltam_dias = max(0, 20 - dias_cursados)

    motivos = []
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
        final_concluido = bool(projeto.get("corrigido")) and projeto.get("nota") is not None
        if not final_concluido:
            motivos.append("Projeto Final ainda não foi corrigido e lançado")
    else:
        final_concluido = d.get("media_final") is not None or d.get("nota_final") is not None
        if not final_concluido:
            motivos.append("avaliação final ainda não foi concluída/lançada")

    aprovado = str(d.get("status_final") or "").lower() == "aprovado"
    if final_concluido and not aprovado:
        motivos.append("disciplina ainda não consta como aprovada")

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
    init_documentos_integrados_db()
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
    resumo = _calcular_ira_automatico(disciplinas)
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
          <td>{escape(d['nome'])}</td><td>{d['carga_horaria']}h</td><td>{escape(d['docente'])}</td>
          <td>{nota_txt}</td><td>{d['frequencia']:.0f}%</td><td>{status_txt}</td><td>{inicio}</td>
        </tr>""")
    return f"""<!doctype html><html lang='pt-br'><head><meta charset='utf-8'>
    <style>
    @page {{ size:A4; margin:16mm; }} body{{font-family:Arial,sans-serif;color:#18212b;font-size:10.5pt}}
    .cab{{border-bottom:3px solid #0a2c4e;padding-bottom:12px;margin-bottom:18px}} h1{{font-size:20pt;color:#0a2c4e;margin:0}}
    .sub{{color:#555;margin-top:5px}} .dados{{display:grid;grid-template-columns:1fr 1fr;gap:7px;background:#f5f7fa;padding:12px;margin:14px 0}}
    table{{width:100%;border-collapse:collapse;font-size:8.8pt}} th,td{{border:1px solid #aab3bd;padding:6px}} th{{background:#0a2c4e;color:#fff}}
    .resumo{{margin-top:18px;padding:12px;border:1px solid #c8d0d8}} .auth{{margin-top:18px;display:flex;gap:16px;align-items:center;border-top:1px solid #bbb;padding-top:12px}}
    .auth img{{width:90px;height:90px}} .hash{{font-family:monospace;font-size:7pt;word-break:break-all}}
    </style></head><body>
    <div class='cab'><h1>HISTÓRICO ACADÊMICO</h1><div class='sub'>SIGEU Educacional • documento eletrônico autenticado</div></div>
    <div class='dados'><div><b>Aluno:</b> {escape(aluno.get('nome',''))}</div><div><b>RA:</b> {escape(aluno.get('ra',''))}</div>
    <div><b>CPF:</b> {escape(aluno.get('cpf_formatado',''))}</div><div><b>Curso/Referência:</b> {escape(aluno.get('curso_referencia') or 'Disciplinas/Unidades Curriculares')}</div></div>
    <table><thead><tr><th>Disciplina</th><th>CH</th><th>Docente</th><th>Média</th><th>Frequência</th><th>Situação</th><th>Início</th></tr></thead>
    <tbody>{''.join(linhas)}</tbody></table>
    <div class='resumo'><b>IRA automático:</b> {resumo['ira']:.2f}/10 &nbsp; • &nbsp; <b>Disciplinas:</b> {resumo['total_disciplinas']} &nbsp; • &nbsp; <b>Carga horária:</b> {resumo['carga_total']}h</div>
    <div class='auth'><img src='{qr_code}'><div><b>Código:</b> {codigo}<br><b>Emissão:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}<div class='hash'>{hash_documento}</div></div></div>
    </body></html>"""


def _html_declaracao_integrada(aluno, d, codigo, qr_code, hash_documento):
    nota = d.get("media_final") if d.get("media_final") is not None else d.get("nota_final")
    nota_txt = f"{float(nota):.2f}" if nota is not None else "N/I"
    data_conclusao = d.get("data_realizacao") or datetime.now().strftime("%d/%m/%Y")
    data_conclusao = str(data_conclusao).split(" ")[0]
    return f"""<!doctype html><html lang='pt-br'><head><meta charset='utf-8'>
    <style>@page{{size:A4;margin:20mm}}body{{font-family:Arial,sans-serif;color:#1b2430;line-height:1.65}}.box{{border:1px solid #9ba7b4;padding:24px;min-height:230mm;position:relative}}h1{{text-align:center;color:#0a2c4e;font-size:20pt;margin:15mm 0 20mm}}p{{text-align:justify;font-size:12pt}}.rod{{position:absolute;bottom:20mm;left:24px;right:24px;border-top:1px solid #bbb;padding-top:12px;display:flex;align-items:center;gap:15px}}.rod img{{width:86px}}.hash{{font-size:7pt;font-family:monospace;word-break:break-all}}</style></head><body>
    <div class='box'><div><b>SIGEU EDUCACIONAL</b><br><small>Declaração acadêmica eletrônica</small></div><h1>DECLARAÇÃO DE CONCLUSÃO DE DISCIPLINA</h1>
    <p>Declaramos, para os devidos fins, que <b>{escape(aluno.get('nome',''))}</b>, CPF {escape(aluno.get('cpf_formatado',''))}, matrícula/RA <b>{escape(aluno.get('ra',''))}</b>, concluiu com aproveitamento a disciplina <b>{escape(d['nome'])}</b>, com carga horária de <b>{d['carga_horaria']} horas</b>, frequência acadêmica registrada de <b>{d['frequencia']:.0f}%</b> e média final <b>{nota_txt}</b>.</p>
    <p>A conclusão foi registrada em {escape(data_conclusao)}. Docente/Responsável acadêmico registrado: <b>{escape(d['docente'])}</b>.</p>
    <p>Documento emitido eletronicamente mediante solicitação do acadêmico, com código e hash para verificação de integridade.</p>
    <div class='rod'><img src='{qr_code}'><div><b>Código:</b> {codigo}<br><b>Emissão:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}<div class='hash'>{hash_documento}</div></div></div></div></body></html>"""


def _html_para_pdf(html_texto, base_url=None):
    return HTML(string=html_texto, base_url=base_url or request.host_url).write_pdf()


def _mesclar_pdfs(lista_pdfs):
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    buffers = []
    try:
        for pdf_bytes in lista_pdfs:
            b = BytesIO(pdf_bytes)
            buffers.append(b)
            reader = PdfReader(b)
            for page in reader.pages:
                writer.add_page(page)
        out = BytesIO()
        writer.write(out)
        return out.getvalue()
    finally:
        for b in buffers:
            try: b.close()
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
    init_documentos_integrados_db()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM solicitacoes_documentos_integrados WHERE id = %s", (solicitacao_id,))
        s = cursor.fetchone()
        if not s:
            raise ValueError("Solicitação não encontrada.")

        aluno = _dados_aluno_documentos(s["aluno_id"])
        if not aluno:
            raise ValueError("Aluno não encontrado.")

        ids = [int(x) for x in str(s.get("disciplinas_ids") or "").split(",") if x.strip().isdigit()]
        if not ids:
            raise ValueError("Nenhuma disciplina foi selecionada.")

        disciplinas = []
        for did in ids:
            status = _status_disciplina_documentos(s["aluno_id"], did, cursor)
            if not status.get("elegivel"):
                raise ValueError(f"{status.get('nome','Disciplina')}: {status.get('motivo')}")
            disciplinas.append(status)

        tipos = json.loads(s.get("tipos_documentos") or "[]")
        if not tipos:
            raise ValueError("Nenhum tipo de documento solicitado.")

        pdfs = []
        componentes = []
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        base_url = request.host_url.rstrip("/")

        if "historico" in tipos:
            codigo = f"HIST-{aluno['ra']}-{timestamp}-{secrets.token_hex(3).upper()}"
            hash_doc = gerar_hash_documento("historico-integrado-" + str(solicitacao_id), aluno["ra"], timestamp)
            qr = gerar_qrcode_base64(f"{base_url}/validar-documento/{codigo}")
            html_h = _html_historico_integrado(aluno, disciplinas, codigo, qr, hash_doc)
            doc_id = _salvar_componente_autenticado(cursor, aluno, "historico", html_h, codigo, hash_doc, None, qr)
            pdfs.append(_html_para_pdf(html_h, base_url))
            componentes.append({"id": doc_id, "tipo": "historico", "codigo": codigo})

        if "conclusao" in tipos:
            for d in disciplinas:
                codigo = f"DECL-{aluno['ra']}-{d['id']}-{timestamp}-{secrets.token_hex(2).upper()}"
                hash_doc = gerar_hash_documento(f"declaracao-{s['id']}-{d['id']}", aluno["ra"], timestamp)
                qr = gerar_qrcode_base64(f"{base_url}/validar-documento/{codigo}")
                html_d = _html_declaracao_integrada(aluno, d, codigo, qr, hash_doc)
                doc_id = _salvar_componente_autenticado(cursor, aluno, "declaracao_conclusao", html_d, codigo, hash_doc, d["id"], qr)
                pdfs.append(_html_para_pdf(html_d, base_url))
                componentes.append({"id": doc_id, "tipo": "declaracao_conclusao", "disciplina_id": d["id"], "codigo": codigo})

        if "plano_ensino" in tipos:
            for d in disciplinas:
                cursor.execute("""
                    SELECT id, codigo, conteudo_html, hash_documento
                    FROM documentos_autenticados
                    WHERE tipo = 'plano_ensino' AND disciplina_id = %s
                    ORDER BY id DESC LIMIT 1
                """, (d["id"],))
                plano = cursor.fetchone()
                if not plano or not plano.get("conteudo_html"):
                    raise ValueError(f"Plano de Ensino ainda não foi gerado/vinculado à disciplina {d['nome']}.")
                pdfs.append(_html_para_pdf(plano["conteudo_html"], base_url))
                componentes.append({"id": plano["id"], "tipo": "plano_ensino", "disciplina_id": d["id"], "codigo": plano.get("codigo")})

        if not pdfs:
            raise ValueError("Nenhum documento pôde ser gerado.")

        pdf_final = _mesclar_pdfs(pdfs)
        hash_pdf = hashlib.sha256(pdf_final).hexdigest().upper()
        codigo_pacote = f"PAC-{aluno['ra']}-{timestamp}-{secrets.token_hex(3).upper()}"
        nome_arquivo = f"SIGEU_documentos_{aluno['ra']}_{timestamp}.pdf"
        agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        cursor.execute("""
            UPDATE solicitacoes_documentos_integrados
            SET status='aguardando_aprovacao', mensagem_status=%s, codigo_pacote=%s,
                pdf_previa=%s, pdf_final=NULL, nome_arquivo=%s, hash_pdf=%s,
                componentes_json=%s, data_preparacao=%s
            WHERE id=%s
        """, (
            "Prévia automática pronta para conferência do MEW.", codigo_pacote,
            psycopg2.Binary(pdf_final), nome_arquivo, hash_pdf,
            json.dumps(componentes, ensure_ascii=False), agora, solicitacao_id
        ))
        conn.commit()
        return True, None
    except Exception as e:
        conn.rollback()
        try:
            cursor.execute("""
                UPDATE solicitacoes_documentos_integrados
                SET status='erro', mensagem_status=%s, pdf_previa=NULL
                WHERE id=%s
            """, (str(e), solicitacao_id))
            conn.commit()
        except Exception:
            conn.rollback()
        return False, str(e)
    finally:
        conn.close()


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
      <div style='background:#eef6ff;border-left:4px solid #0a2c4e;padding:12px;margin-bottom:14px'>
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

    init_documentos_integrados_db()
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
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    init_documentos_integrados_db()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM solicitacoes_documentos_integrados
        WHERE aluno_id=%s ORDER BY id DESC
    """, (aluno_id,))
    rows = cursor.fetchall()
    resultado = []
    for row in rows:
        r = dict(row)
        ids = [int(x) for x in str(r.get("disciplinas_ids") or "").split(",") if x.strip().isdigit()]
        if ids:
            cursor.execute("SELECT STRING_AGG(nome, ', ' ORDER BY nome) AS nomes FROM disciplinas WHERE id = ANY(%s)", (ids,))
            rr = cursor.fetchone()
            r["disciplinas_nomes"] = rr.get("nomes") if rr else ""
        else:
            r["disciplinas_nomes"] = ""
        r["arquivo_url"] = f"/documentos-integrados/{r['id']}/pdf" if r.get("status") == "aprovado" and r.get("pdf_final") else None
        resultado.append(r)
    conn.close()
    return jsonify({"success": True, "solicitacoes": resultado})


@app.route("/meus-documentos-integrados-api")
def meus_documentos_integrados_api():
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return jsonify({"success": False, "documentos": []}), 401
    init_documentos_integrados_db()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, tipo_solicitacao, codigo_pacote, nome_arquivo, data_aprovacao, hash_pdf,
               disciplinas_ids, mensagem_status
        FROM solicitacoes_documentos_integrados
        WHERE aluno_id=%s AND status='aprovado' AND pdf_final IS NOT NULL
        ORDER BY id DESC
    """, (aluno_id,))
    rows = cursor.fetchall()
    docs = []
    for row in rows:
        ids = [int(x) for x in str(row.get("disciplinas_ids") or "").split(",") if x.strip().isdigit()]
        nomes = ""
        if ids:
            cursor.execute("SELECT STRING_AGG(nome, ', ' ORDER BY nome) AS nomes FROM disciplinas WHERE id=ANY(%s)", (ids,))
            x = cursor.fetchone()
            nomes = x.get("nomes") if x else ""
        titulo = "Pacote Integrado: Histórico + Declaração + Plano" if row["tipo_solicitacao"] == "integrado" else {
            "historico": "Histórico Escolar",
            "conclusao": "Declaração de Conclusão",
            "plano_ensino": "Plano de Ensino"
        }.get(row["tipo_solicitacao"], "Documentos Acadêmicos")
        docs.append({
            "id": row["id"], "tipo": "pacote_integrado", "titulo": titulo,
            "disciplina_nome": nomes, "data_envio": row.get("data_aprovacao") or "",
            "mensagem": "Documento conferido e aprovado pela Secretaria/MEW.",
            "status": "enviado", "url": f"/documentos-integrados/{row['id']}/pdf"
        })
    conn.close()
    return jsonify({"success": True, "documentos": docs})


@app.route("/documentos-integrados/<int:solicitacao_id>/pdf")
def aluno_pdf_documentos_integrados(solicitacao_id):
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))
    init_documentos_integrados_db()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT pdf_final, nome_arquivo FROM solicitacoes_documentos_integrados
        WHERE id=%s AND aluno_id=%s AND status='aprovado'
    """, (solicitacao_id, aluno_id))
    row = cursor.fetchone()
    conn.close()
    if not row or not row.get("pdf_final"):
        return "Documento ainda não disponível.", 404
    return send_file(BytesIO(bytes(row["pdf_final"])), mimetype="application/pdf", as_attachment=False,
                     download_name=row.get("nome_arquivo") or f"documentos_{solicitacao_id}.pdf")


@app.route("/mew/documentos-integrados")
def mew_documentos_integrados():
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    init_documentos_integrados_db()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.*, a.nome AS aluno_nome, a.ra AS aluno_ra
        FROM solicitacoes_documentos_integrados s
        JOIN alunos a ON a.id=s.aluno_id
        ORDER BY CASE s.status WHEN 'pendente' THEN 1 WHEN 'erro' THEN 2 WHEN 'aguardando_aprovacao' THEN 3 ELSE 4 END, s.id DESC
    """)
    rows = cursor.fetchall()
    solicitacoes = []
    for row in rows:
        r = dict(row)
        ids = [int(x) for x in str(r.get("disciplinas_ids") or "").split(",") if x.strip().isdigit()]
        if ids:
            cursor.execute("SELECT STRING_AGG(nome, ', ' ORDER BY nome) AS nomes FROM disciplinas WHERE id=ANY(%s)", (ids,))
            x = cursor.fetchone()
            r["disciplinas_nomes"] = x.get("nomes") if x else ""
        else:
            r["disciplinas_nomes"] = ""
        solicitacoes.append(r)
    conn.close()
    return render_template("mew/documentos_integrados.html", solicitacoes=solicitacoes)


@app.route("/mew/documentos-integrados/<int:solicitacao_id>/conferir")
def mew_conferir_documentos_integrados(solicitacao_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    init_documentos_integrados_db()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.*, a.nome AS aluno_nome, a.ra AS aluno_ra
        FROM solicitacoes_documentos_integrados s JOIN alunos a ON a.id=s.aluno_id
        WHERE s.id=%s
    """, (solicitacao_id,))
    s = cursor.fetchone()
    conn.close()
    if not s:
        return "Solicitação não encontrada", 404
    if s.get("status") in ("pendente", "erro") or not s.get("pdf_previa"):
        _gerar_previa_solicitacao_integrada(solicitacao_id)
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("""SELECT s.*, a.nome AS aluno_nome, a.ra AS aluno_ra FROM solicitacoes_documentos_integrados s JOIN alunos a ON a.id=s.aluno_id WHERE s.id=%s""", (solicitacao_id,))
        s = cursor.fetchone(); conn.close()
    return render_template("mew/conferir_documentos_integrados.html", solicitacao=s)


@app.route("/mew/documentos-integrados/<int:solicitacao_id>/pdf-previa")
def mew_pdf_previa_integrada(solicitacao_id):
    if not session.get("mew_admin"):
        return "Não autorizado", 403
    init_documentos_integrados_db()
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT pdf_previa, nome_arquivo FROM solicitacoes_documentos_integrados WHERE id=%s", (solicitacao_id,))
    row = cursor.fetchone(); conn.close()
    if not row or not row.get("pdf_previa"):
        return "Prévia não disponível", 404
    return send_file(BytesIO(bytes(row["pdf_previa"])), mimetype="application/pdf", as_attachment=False,
                     download_name="PREVIA_" + (row.get("nome_arquivo") or f"documentos_{solicitacao_id}.pdf"))


@app.route("/mew/documentos-integrados/<int:solicitacao_id>/regerar", methods=["POST"])
def mew_regerar_documentos_integrados(solicitacao_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    ok, erro = _gerar_previa_solicitacao_integrada(solicitacao_id)
    if ok:
        return redirect(f"/mew/documentos-integrados/{solicitacao_id}/conferir?sucesso=Prévia+regenerada")
    return redirect(f"/mew/documentos-integrados/{solicitacao_id}/conferir?erro={url_quote(erro or 'Erro')}")


@app.route("/mew/documentos-integrados/<int:solicitacao_id>/aprovar", methods=["POST"])
def mew_aprovar_documentos_integrados(solicitacao_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    init_documentos_integrados_db()
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT pdf_previa FROM solicitacoes_documentos_integrados WHERE id=%s", (solicitacao_id,))
    row = cursor.fetchone()
    if not row or not row.get("pdf_previa"):
        conn.close()
        return redirect(f"/mew/documentos-integrados/{solicitacao_id}/conferir?erro=Gere+a+prévia+antes+de+aprovar")
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    pdf = bytes(row["pdf_previa"])
    cursor.execute("""
        UPDATE solicitacoes_documentos_integrados
        SET pdf_final=%s, status='aprovado', data_aprovacao=%s,
            mensagem_status='Conferido e aprovado pelo MEW. Disponível na plataforma do aluno.'
        WHERE id=%s
    """, (psycopg2.Binary(pdf), agora, solicitacao_id))
    conn.commit(); conn.close()
    return redirect("/mew/documentos-integrados?sucesso=Documento+aprovado+e+liberado+ao+aluno")


@app.route("/mew/plano-ensino/<int:documento_id>/vincular", methods=["POST"])
def mew_vincular_plano_disciplina(documento_id):
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    init_documentos_integrados_db()
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
    return f"""<!doctype html><html><head><meta charset='utf-8'><style>@page{{size:A4;margin:18mm}}body{{font-family:Arial;color:#18212b}}h1{{color:#0a2c4e}}.alerta{{background:#fff7db;border-left:4px solid #b8860b;padding:10px}}table{{width:100%;border-collapse:collapse}}td{{border-bottom:1px solid #ddd;padding:8px}}</style></head><body>
    <h1>RECIBO ELETRÔNICO / COMPROVANTE DE PAGAMENTO</h1>
    <p><b>SIGEU Educacional</b></p><div class='alerta'><b>Importante:</b> este recibo comprova o pagamento no sistema acadêmico e não substitui NFS-e ou outro documento fiscal oficial quando legalmente exigido.</div>
    <table><tr><td>Aluno</td><td>{escape(aluno.get('nome',''))}</td></tr><tr><td>Matrícula/RA</td><td>{escape(aluno.get('ra',''))}</td></tr><tr><td>Valor</td><td>{valor_txt}</td></tr><tr><td>Pagamento</td><td>{escape(str(pagamento_id or ''))}</td></tr><tr><td>Data</td><td>{escape(data_pagamento or '')}</td></tr></table>
    <h3>Serviços/disciplinas vinculados</h3><ul>{lista}</ul><p>Emitido automaticamente pelo SIGEU.</p></body></html>"""


def enviar_boas_vindas_titan(aluno_id, referencia, pagamento_id=None):
    """Envia e-mail apenas se as variáveis TITAN_* estiverem configuradas."""
    init_documentos_integrados_db()
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
    recibo_pdf = HTML(string=recibo_html, base_url=request.host_url if request else None).write_pdf()

    from email.message import EmailMessage
    from email.utils import formataddr
    import smtplib
    msg = EmailMessage()
    msg["Subject"] = f"Bem-vindo ao SIGEU Educacional | Matrícula {aluno['ra']}"
    msg["From"] = formataddr((from_name, usuario))
    msg["To"] = aluno["email"]
    msg.set_content(f"Bem-vindo ao SIGEU. Matrícula: {aluno['ra']}. Senha inicial: seu CPF, somente números. Acesse: {login_url}")
    corpo = f"""<html><body style='font-family:Arial;color:#1f2937'><h2>Bem-vindo ao SIGEU Educacional</h2><p>Olá, <b>{escape(aluno['nome'])}</b>.</p><p>Seu pagamento foi confirmado e sua matrícula está ativa.</p><div style='background:#f2f6fb;padding:16px;border-left:4px solid #0a2c4e'><b>Matrícula/RA:</b> {escape(aluno['ra'])}<br><b>Senha inicial:</b> seu CPF, somente números</div><p>Ao primeiro acesso, o sistema apresentará o contrato educacional para assinatura eletrônica. Após a assinatura, as disciplinas já vinculadas à matrícula ficarão disponíveis.</p><p><a href='{login_url}'>Acessar a Plataforma Acadêmica</a></p><p>Segue em anexo o recibo eletrônico/comprovante do pagamento. Ele não substitui NFS-e quando esta for legalmente exigida.</p><p>Atenciosamente,<br><b>SIGEU Educacional</b></p></body></html>"""
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
    init_contratos_db()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            c.id AS contrato_id,
            c.status AS contrato_status,
            c.assinatura_base64,
            c.foto_assinatura_base64,
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
        "assinatura_base64": aluno["assinatura_base64"] or "",
        "foto_assinatura_base64": aluno.get("foto_assinatura_base64") or "",
        "ip_assinatura": aluno.get("ip_assinatura") or "",
        "user_agent_assinatura": aluno.get("user_agent_assinatura") or "",
        "aceite_contrato": bool(aluno.get("aceite_contrato")),
        "aceite_foto": bool(aluno.get("aceite_foto")),
        "texto_aceite": aluno.get("texto_aceite") or "",
        "versao_contrato": aluno.get("versao_contrato") or VERSAO_CONTRATO
    }


@app.route("/contrato/registro/<int:contrato_id>")
def visualizar_contrato_registro(contrato_id):
    init_contratos_db()
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
    """Renderiza o contrato assinado e devolve o PDF definitivo em bytes."""
    dados = _dados_contrato_render(contrato_id)
    if not dados:
        raise ValueError("Contrato não encontrado.")
    if not dados.get("assinado"):
        raise ValueError("O contrato ainda não foi assinado.")

    html_final = render_template("contrato_padrao.html", **dados)
    pdf_bytes = HTML(string=html_final, base_url=request.url_root).write_pdf()

    if salvar:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE contratos_alunos
            SET pdf_assinado = %s,
                arquivo_assinado_path = %s
            WHERE id = %s
        """, (
            psycopg2.Binary(pdf_bytes),
            f"/contrato/pdf/{contrato_id}",
            contrato_id
        ))
        conn.commit()
        conn.close()

    return pdf_bytes, dados


@app.route("/contrato/pdf/<int:contrato_id>")
def contrato_pdf_assinado(contrato_id):
    init_contratos_db()

    dados = _dados_contrato_render(contrato_id)
    if not dados:
        return "Contrato não encontrado.", 404

    if not session.get("mew_admin") and session.get("aluno_id") != dados["aluno_id"]:
        return "Acesso não autorizado.", 403

    if not dados.get("assinado"):
        return "O contrato ainda não foi assinado.", 409

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT pdf_assinado FROM contratos_alunos WHERE id = %s", (contrato_id,))
    registro = cursor.fetchone()
    conn.close()

    pdf_bytes = None
    if registro and registro.get("pdf_assinado") is not None:
        pdf_bytes = bytes(registro["pdf_assinado"])

    if not pdf_bytes:
        pdf_bytes, dados = gerar_pdf_contrato_assinado(contrato_id, salvar=True)

    nome_seguro = re.sub(r"[^A-Za-z0-9_-]", "_", str(dados.get("ra") or contrato_id))
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"Contrato_SIGEU_{nome_seguro}.pdf"
    )


if __name__ == "__main__":
    # Inicializa o banco de dados PostgreSQL
    init_db()
    init_contratos_db()
    init_pagamentos_db()
    init_documentos_integrados_db()
    
    # Só roda localmente
    app.run(debug=True)
