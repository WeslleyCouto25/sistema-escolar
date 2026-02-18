from pydoc import html
import sqlite3
import json
import time
import random
from datetime import datetime
from flask import Flask, render_template, render_template_string, request, redirect, session, url_for, jsonify, flash
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
import hashlib
import json
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

from api_planos import planos_bp
app.register_blueprint(planos_bp, url_prefix='/api')


def get_db_connection():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Tabela de alunos (com RA)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alunos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT,
            email TEXT,
            ra TEXT UNIQUE,
            senha TEXT
        )
    """)

    # Tabela de disciplinas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS disciplinas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT
        )
    """)

    # Tabela de capítulos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS capitulos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            disciplina_id INTEGER,
            titulo TEXT,
            video_url TEXT,
            pdf_url TEXT
        )
    """)

    # Tabela de provas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS provas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            capitulo_id INTEGER,
            questoes_json TEXT
        )
    """)

    # Tabela de notas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aluno_id INTEGER,
            disciplina_id INTEGER,
            capitulo INTEGER,
            nota INTEGER
        )
    """)

    # Tabela aluno ↔ disciplina
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS aluno_disciplina (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aluno_id INTEGER,
            disciplina_id INTEGER,
            UNIQUE(aluno_id, disciplina_id)
        )
    """)

    # Tabela de solicitações de material didático
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS solicitacoes_material (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aluno_id INTEGER,
            tipo TEXT,
            detalhes TEXT,
            data_solicitacao TEXT,
            entregue INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS solicitacoes_documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            disciplina_id INTEGER,
            questoes_json TEXT,
            FOREIGN KEY (disciplina_id) REFERENCES disciplinas(id)
        )
    """)
    

    conn.commit()
    conn.close()


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
        WHERE aluno_id = ? AND disciplina_id = ?
    """, (aluno_id, disciplina_id))
    
    total_provas = cursor.fetchone()["total_provas_feitas"] or 0
    
    # Verificar se já fez a prova final
    cursor.execute("""
        SELECT id FROM notas_finais 
        WHERE aluno_id = ? AND disciplina_id = ?
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
        WHERE aluno_id = ? AND disciplina_id = ?
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
        WHERE aluno_id = ?
    """, (aluno_id,))
    
    dados_adicionais = cursor.fetchone()
    
    # Buscar informações específicas da disciplina (nota final, período)
    cursor.execute("""
        SELECT nf.media_final, nf.status, nf.data_realizacao,
               addd.data_inicio, addd.data_fim_previsto
        FROM notas_finais nf
        LEFT JOIN aluno_disciplina_datas addd ON nf.aluno_id = addd.aluno_id AND nf.disciplina_id = addd.disciplina_id
        WHERE nf.aluno_id = ? AND nf.disciplina_id = ?
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
    <div class="microtexto-borda top">DOCUMENTO OFICIAL - FACOP/SIGEU - VALIDAÇÃO DIGITAL OBRIGATÓRIA</div>
    <div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98 <strong> | F142485-1/-Coord. Acad. Tatiane R. G. Lourenço- </strong></div>
    <div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
    <div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>
    
    <!-- MARCAS D'ÁGUA -->
    <div class="marca-dagua-principal">FACOP SiGEu</div>
    <div class="marca-dagua-pattern"></div>
    
    <!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
    <div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
    <div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
    <div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO FACOP/SIGEU</div>
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
            Facop/SiGEu<br>e-SIGEU-ICP-2026
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
        no âmbito do Convênio Educacional <span class="destaque">FACOP/SiGEU – Grupo Educacional Unificado LTDA</span>, 
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
            <img src="{{ qrcode_base64 }}" alt="QR Code de Validação" style="width: 100%; height: 100%; object-fit: contain;">
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
    dados_qr = f"https://campusvirtualfacop.com.br/validar-documento/{codigo_autenticacao}"
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
        WHERE aluno_id = ? AND disciplina_id = ?
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
            "SELECT * FROM alunos WHERE ra = ? AND senha = ?",
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
    aluno_id = session.get("aluno_id")
    if not aluno_id:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # Buscar dados pessoais do aluno
    cursor.execute("SELECT * FROM dados_pessoais WHERE aluno_id = ?", (aluno_id,))
    dados_pessoais = cursor.fetchone()
    
    # Buscar situação financeira
    cursor.execute("SELECT * FROM situacao_financeira WHERE aluno_id = ? ORDER BY id DESC LIMIT 1", (aluno_id,))
    situacao_financeira = cursor.fetchone()
    
    # Buscar disciplinas do aluno
    cursor.execute("""
        SELECT d.id, d.nome
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        WHERE ad.aluno_id = ?
    """, (aluno_id,))
    disciplinas = cursor.fetchall()

    # Buscar notas
    cursor.execute("""
        SELECT n.disciplina_id, n.capitulo, n.nota, d.nome AS disciplina_nome
        FROM notas n
        JOIN disciplinas d ON n.disciplina_id = d.id
        WHERE n.aluno_id = ?
        ORDER BY n.disciplina_id, n.capitulo
    """, (aluno_id,))
    notas = cursor.fetchall()

    # Buscar solicitações de material
    cursor.execute("""
        SELECT sm.*, d.nome AS disciplina_nome
        FROM solicitacoes_material sm
        LEFT JOIN disciplinas d ON sm.disciplina_id = d.id
        WHERE sm.aluno_id = ?
        ORDER BY sm.data_solicitacao DESC
    """, (aluno_id,))
    solicitacoes_material = cursor.fetchall()

    # Buscar solicitações de declarações
    cursor.execute("""
        SELECT *
        FROM solicitacoes_declaracoes
        WHERE aluno_id = ?
        ORDER BY data_solicitacao DESC
    """, (aluno_id,))
    solicitacoes_declaracoes = cursor.fetchall()

    # Calcular totais
    cursor.execute("SELECT COUNT(*) as total FROM notas WHERE aluno_id = ?", (aluno_id,))
    total_provas = cursor.fetchone()["total"]
    
    cursor.execute("SELECT AVG(nota) as media FROM notas WHERE aluno_id = ?", (aluno_id,))
    media_result = cursor.fetchone()
    media = media_result["media"] if media_result["media"] else 0
    media_geral = round(media, 2)

    # Contar material pendente
    cursor.execute("""
        SELECT COUNT(*) as pendente 
        FROM solicitacoes_material 
        WHERE aluno_id = ? AND entregue = 0
    """, (aluno_id,))
    material_pendente_result = cursor.fetchone()
    material_pendente = material_pendente_result["pendente"] if material_pendente_result else 0

    # Contar declarações pendentes
    cursor.execute("""
        SELECT COUNT(*) as pendente 
        FROM solicitacoes_declaracoes 
        WHERE aluno_id = ? AND entregue = 0
    """, (aluno_id,))
    declaracoes_pendentes_result = cursor.fetchone()
    declaracoes_pendentes = declaracoes_pendentes_result["pendente"] if declaracoes_pendentes_result else 0

    # ⬇️ NOVO: Buscar documentos não visualizados ⬇️
    cursor.execute("""
        SELECT COUNT(*) as total 
        FROM documentos_enviados 
        WHERE aluno_id = ? AND status = 'enviado'
    """, (aluno_id,))
    nao_visualizados = cursor.fetchone()["total"] or 0

    conn.close()

    # Funções para template - ATUALIZADAS
    def calcular_progresso(aluno_id, disciplina_id):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Contar capítulos totais da disciplina
            cursor.execute("SELECT COUNT(*) as total FROM capitulos WHERE disciplina_id = ?", (disciplina_id,))
            total_result = cursor.fetchone()
            total_capitulos = total_result["total"] if total_result else 0
            
            if total_capitulos == 0:
                conn.close()
                return 0
            
            # Contar provas realizadas (capítulos com nota)
            cursor.execute("""
                SELECT COUNT(DISTINCT capitulo) as feitas 
                FROM notas 
                WHERE aluno_id = ? AND disciplina_id = ?
            """, (aluno_id, disciplina_id))
            provas_result = cursor.fetchone()
            provas_feitas = provas_result["feitas"] if provas_result else 0
            
            # Calcular porcentagem
            progresso = (provas_feitas / total_capitulos) * 100 if total_capitulos > 0 else 0
            
            # Verificar se tem nota da prova final
            cursor.execute("""
                SELECT nota_final 
                FROM notas_finais 
                WHERE aluno_id = ? AND disciplina_id = ?
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
            cursor.execute("SELECT COUNT(*) as total FROM capitulos WHERE disciplina_id = ?", (disciplina_id,))
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
                WHERE aluno_id = ? AND disciplina_id = ?
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
        nao_visualizados=nao_visualizados  # ⬅️ NOVO PARÂMETRO ADICIONADO
    )

@app.route("/mew/notas/capitulos/<int:aluno_id>/<int:disciplina_id>")
def mew_notas_capitulos(aluno_id, disciplina_id):
    """Gerenciar notas dos capítulos e prova final de um aluno em uma disciplina"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Buscar informações do aluno
    cursor.execute("SELECT id, nome, ra FROM alunos WHERE id = ?", (aluno_id,))
    aluno = cursor.fetchone()
    
    if not aluno:
        conn.close()
        return "Aluno não encontrado", 404
    
    # Buscar informações da disciplina
    cursor.execute("SELECT id, nome FROM disciplinas WHERE id = ?", (disciplina_id,))
    disciplina = cursor.fetchone()
    
    if not disciplina:
        conn.close()
        return "Disciplina não encontrada", 404
    
    # Buscar notas existentes dos capítulos
    cursor.execute("""
        SELECT id, capitulo, nota 
        FROM notas 
        WHERE aluno_id = ? AND disciplina_id = ?
        ORDER BY capitulo
    """, (aluno_id, disciplina_id))
    notas_capitulos = cursor.fetchall()
    
    # Buscar nota da prova final
    cursor.execute("""
        SELECT nota_final 
        FROM notas_finais 
        WHERE aluno_id = ? AND disciplina_id = ?
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
    cursor.execute("SELECT * FROM disciplinas WHERE id = ?", (disciplina_id,))
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
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (disciplina_id, pergunta, opcao_a, opcao_b, opcao_c, opcao_d, resposta_correta))
        
        conn.commit()
        conn.close()
        return redirect(f"/mew/questoes-final/{disciplina_id}?sucesso=Questão+adicionada")
    
    # GET: Listar questões existentes
    cursor.execute("SELECT * FROM questoes_finais WHERE disciplina_id = ? ORDER BY id", (disciplina_id,))
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
    cursor.execute("SELECT disciplina_id FROM questoes_finais WHERE id = ?", (questao_id,))
    questao = cursor.fetchone()
    disciplina_id = questao["disciplina_id"] if questao else None
    
    cursor.execute("DELETE FROM questoes_finais WHERE id = ?", (questao_id,))
    
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
    
    cursor.execute("SELECT COUNT(*) as total FROM questoes_finais WHERE disciplina_id = ?", (disciplina_id,))
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
            INSERT OR REPLACE INTO notas_finais 
            (aluno_id, disciplina_id, nota_final, data_avaliacao)
            VALUES (?, ?, ?, datetime('now'))
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
        WHERE aluno_id = ? AND disciplina_id = ?
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
    cursor.execute("SELECT * FROM disciplinas WHERE id = ?", (disciplina_id,))
    disciplina = cursor.fetchone()
    
    cursor.execute("""
        SELECT c.id, c.titulo, c.video_url, c.pdf_url, p.id AS prova_id
        FROM capitulos c
        LEFT JOIN provas p ON p.capitulo_id = c.id
        WHERE c.disciplina_id = ?
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
        WHERE n.aluno_id = ? AND n.disciplina_id = ? AND n.capitulo = ?
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
    cursor.execute("SELECT nome FROM alunos WHERE id = ?", (aluno_id,))
    aluno = cursor.fetchone()
    
    # Obter informações da disciplina e capítulo
    cursor.execute("SELECT nome FROM disciplinas WHERE id = ?", (disciplina_id,))
    disciplina = cursor.fetchone()
    
    cursor.execute("""
        SELECT c.titulo, p.questoes_json 
        FROM capitulos c
        LEFT JOIN provas p ON p.capitulo_id = c.id
        WHERE c.disciplina_id = ?
        ORDER BY c.id
        LIMIT 1 OFFSET ?
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
        WHERE n.aluno_id = ? AND n.disciplina_id = ? AND n.capitulo = ?
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
        WHERE c.disciplina_id = ?
        ORDER BY c.id
        LIMIT 1 OFFSET ?
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
        WHERE capitulo_id = ?
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
            resposta_aluno = request.form.get(f"q{i}")
            acertou = resposta_aluno == q["resposta_certa"]
            
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
            VALUES (?, ?, ?, ?)
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
                   (SELECT titulo FROM capitulos WHERE disciplina_id = ? 
                    ORDER BY id LIMIT 1 OFFSET ?) AS capitulo_titulo
            FROM alunos a, disciplinas d
            WHERE a.id = ? AND d.id = ?
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
        WHERE n.aluno_id = ? AND n.disciplina_id = ? AND n.capitulo = ?
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
        WHERE disciplina_id = ? 
        ORDER BY id 
        LIMIT 1 OFFSET ?
    """, (disciplina_id, capitulo_numero - 1))
    
    capitulo = cursor.fetchone()
    
    # Buscar questões para calcular acertos
    cursor.execute("""
        SELECT p.questoes_json
        FROM provas p
        JOIN capitulos c ON p.capitulo_id = c.id
        WHERE c.disciplina_id = ?
        ORDER BY c.id
        LIMIT 1 OFFSET ?
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
        WHERE ad.aluno_id = ?
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
    cursor.execute("SELECT nome FROM disciplinas WHERE id = ?", (disciplina_id,))
    disciplina = cursor.fetchone()
    disciplina_nome = disciplina["nome"] if disciplina else ""
    
    # Inserir solicitação
    data_solicitacao = datetime.now().strftime("%d/%m/%Y %H:%M")
    
    detalhes_material = f"{material_nome} - {disciplina_nome}"
    if observacoes:
        detalhes_material += f" ({observacoes})"
    
    cursor.execute("""
        INSERT INTO solicitacoes_material (aluno_id, disciplina_id, material, data_solicitacao)
        VALUES (?, ?, ?, ?)
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
        VALUES (?, ?, ?, ?)
    """, (aluno_id, tipo, detalhes, data_solicitacao))
    
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "message": "Solicitação registrada"})


# ==========================
# MEW - PAINEL ADMIN
# ==========================

'''@app.route("/mew/login", methods=["GET", "POST"])
def mew_login():
    if request.method == "POST":
        email = request.form.get("email")
        senha = request.form.get("senha")

        admin_email = os.environ.get("MEW_ADMIN_EMAIL")
        admin_password_hash = os.environ.get("MEW_ADMIN_PASSWORD_HASH")

        if (
            email == admin_email
            and admin_password_hash
            and check_password_hash(admin_password_hash, senha)
        ):
            session["mew_admin"] = True
            return redirect("/mew/dashboard")

    return render_template("mew/login.html")'''
    
@app.route("/mew/login", methods=["GET", "POST"])
def mew_login():
    if request.method == "POST":
        email = request.form.get("email")
        senha = request.form.get("senha")

        if email == "admin@mew.com" and senha == "123456":
            session["mew_admin"] = True
            return redirect("/mew/dashboard")

    return render_template("mew/login.html")
    



@app.route("/mew/dashboard")
def mew_dashboard():
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Contar total de alunos
    cursor.execute("SELECT COUNT(*) as total FROM alunos")
    total_alunos = cursor.fetchone()["total"]
    
    # Contar total de disciplinas
    cursor.execute("SELECT COUNT(*) as total FROM disciplinas")
    total_disciplinas = cursor.fetchone()["total"]
    
    # Contar solicitações pendentes
    cursor.execute("SELECT COUNT(*) as total FROM solicitacoes_material WHERE entregue = 0")
    material_pendente = cursor.fetchone()["total"]
    
    cursor.execute("SELECT COUNT(*) as total FROM solicitacoes_declaracoes WHERE entregue = 0")
    declaracoes_pendente = cursor.fetchone()["total"]
    
    total_solicitacoes_pendentes = material_pendente + declaracoes_pendente
    
    # Contar total de provas realizadas
    cursor.execute("SELECT COUNT(*) as total FROM notas")
    total_provas = cursor.fetchone()["total"]
    
    # Adicione após as outras contagens na função mew_dashboard()
    cursor.execute("SELECT COUNT(*) as total FROM solicitacoes_documentos WHERE status = 'pendente'")
    documentos_pendente = cursor.fetchone()["total"]

    conn.close()
    
    return render_template(
        "mew/dashboard.html",
        total_alunos=total_alunos,
        total_disciplinas=total_disciplinas,
        total_solicitacoes_pendentes=total_solicitacoes_pendentes,
        total_provas=total_provas,
        total_solicitacoes_documentos_pendentes = documentos_pendente
    )


@app.route("/mew/alunos", methods=["GET", "POST"])
def mew_alunos():
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM disciplinas")
    disciplinas = cursor.fetchall()

    if request.method == "POST":
        # Dados básicos do aluno
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
        forma_pagamento = request.form.get("forma_pagamento")
        valor_total = request.form.get("valor_total")
        data_inicio = request.form.get("data_inicio")
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

        # ===== RA (MATRÍCULA) =====
        ra_input = request.form.get("ra")

        if ra_input:
            ra = ra_input.strip()
    # VALIDAÇÃO APENAS PARA O RA (matrícula)
            if not ra.isdigit() or len(ra) != 8:  # RA continua com 8 dígitos
                conn.close()
                return "RA inválido. Deve conter exatamente 8 números.", 400

        cursor.execute("SELECT id FROM alunos WHERE ra = ?", (ra,))
        if cursor.fetchone():
            conn.close()
            return "RA já existente. Utilize outro número.", 400
        else:
            while True:
                ra = gerar_ra()  # Esta função já gera 8 dígitos
                cursor.execute("SELECT id FROM alunos WHERE ra = ?", (ra,))
                if not cursor.fetchone():
                    break

        # ===== RG (NÃO VALIDAR - PODE TER QUALQUER TAMANHO) =====
        rg = request.form.get("rg")  # Aceita qualquer valor, sem validação de tamanho

        # ===== INSERIR ALUNO =====
        cursor.execute("""
            INSERT INTO alunos (nome, email, ra, senha)
            VALUES (?, ?, ?, ?)
        """, (nome, email, ra, senha))
        aluno_id = cursor.lastrowid

        # ===== DADOS PESSOAIS (COM TODOS OS CAMPOS) =====
        cursor.execute("""
            INSERT INTO dados_pessoais 
            (aluno_id, cpf, rg, telefone, endereco, cidade, estado, cep, 
             curso_referencia, nome_pai, nome_mae, naturalidade, nacionalidade,
             data_nascimento, sexo, estado_civil, email_alternativo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (aluno_id, cpf, rg, telefone, endereco, cidade, estado, cep,
              curso_referencia, nome_pai, nome_mae, naturalidade, nacionalidade,
              data_nascimento, sexo, estado_civil, email_alternativo))

        # ===== SITUAÇÃO FINANCEIRA =====
        if forma_pagamento and valor_total:
            try:
                valor_total = float(valor_total.replace(",", "."))
            except ValueError:
                conn.close()
                return "Valor total inválido.", 400

            if forma_pagamento in ["avista", "cartao"]:
                status = "pago"
                parcelas_total = 1
                parcelas_pagas = 1
            else:  # boleto_pix
                status = "parcial"
                parcelas_total = 2
                parcelas_pagas = 1

            cursor.execute("""
                INSERT INTO situacao_financeira
                (aluno_id, forma_pagamento, status, parcelas_total, parcelas_pagas, valor_total)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                aluno_id,
                forma_pagamento,
                status,
                parcelas_total,
                parcelas_pagas,
                valor_total
            ))

        # ===== DATAS DAS DISCIPLINAS =====
        from datetime import datetime, timedelta

        if not data_inicio:
            conn.close()
            return "Data de início não informada.", 400

        try:
            data_inicio_obj = datetime.strptime(data_inicio, "%Y-%m-%d")
        except ValueError:
            conn.close()
            return "Formato de data inválido.", 400
        
        data_fim_obj = data_inicio_obj + timedelta(days=prazo_dias)
        data_fim = data_fim_obj.strftime("%d/%m/%Y")
        data_inicio_formatada = data_inicio_obj.strftime("%d/%m/%Y")

        disciplinas_selecionadas = request.form.getlist("disciplinas")

        for d_id in disciplinas_selecionadas:
            cursor.execute("""
                INSERT INTO aluno_disciplina (aluno_id, disciplina_id)
                VALUES (?, ?)
            """, (aluno_id, d_id))

            cursor.execute("""
                INSERT INTO aluno_disciplina_datas
                (aluno_id, disciplina_id, data_inicio, data_fim_previsto)
                VALUES (?, ?, ?, ?)
            """, (aluno_id, d_id, data_inicio_formatada, data_fim))

        conn.commit()
        conn.close()
        return redirect("/mew/alunos")

    # ===== GET: LISTAGEM DE ALUNOS =====
    cursor.execute("SELECT * FROM alunos")
    alunos = cursor.fetchall()

    alunos_completo = []

    for aluno in alunos:
        cursor.execute("SELECT * FROM dados_pessoais WHERE aluno_id = ?", (aluno["id"],))
        dados_pessoais = cursor.fetchone()

        cursor.execute("""
            SELECT * FROM situacao_financeira
            WHERE aluno_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (aluno["id"],))
        situacao_financeira = cursor.fetchone()

        cursor.execute("""
            SELECT COUNT(*) as total
            FROM aluno_disciplina
            WHERE aluno_id = ?
        """, (aluno["id"],))
        count = cursor.fetchone()

        cursor.execute("""
            SELECT ad.disciplina_id, d.nome, addd.data_inicio, addd.data_fim_previsto
            FROM aluno_disciplina ad
            LEFT JOIN aluno_disciplina_datas addd
                ON ad.aluno_id = addd.aluno_id
               AND ad.disciplina_id = addd.disciplina_id
            LEFT JOIN disciplinas d ON ad.disciplina_id = d.id
            WHERE ad.aluno_id = ?
        """, (aluno["id"],))
        disciplinas_aluno = cursor.fetchall()

        alunos_completo.append({
            "id": aluno["id"],
            "nome": aluno["nome"],
            "email": aluno["email"],
            "ra": aluno["ra"],
            "cpf": dados_pessoais["cpf"] if dados_pessoais else "",
            "telefone": dados_pessoais["telefone"] if dados_pessoais else "",
            "forma_pagamento": situacao_financeira["forma_pagamento"] if situacao_financeira else "",
            "status_financeiro": situacao_financeira["status"] if situacao_financeira else "",
            "valor_total": situacao_financeira["valor_total"] if situacao_financeira else 0,
            "parcelas_total": situacao_financeira["parcelas_total"] if situacao_financeira else 0,
            "parcelas_pagas": situacao_financeira["parcelas_pagas"] if situacao_financeira else 0,
            "total_disciplinas": count["total"] if count else 0,
            "disciplinas_datas": disciplinas_aluno,
            # NOVOS CAMPOS
            "nome_pai": dados_pessoais["nome_pai"] if dados_pessoais else "",
            "nome_mae": dados_pessoais["nome_mae"] if dados_pessoais else "",
            "data_nascimento": dados_pessoais["data_nascimento"] if dados_pessoais else "",
            "sexo": dados_pessoais["sexo"] if dados_pessoais else "",
            "naturalidade": dados_pessoais["naturalidade"] if dados_pessoais else "",
            "nacionalidade": dados_pessoais["nacionalidade"] if dados_pessoais else "",
            "estado_civil": dados_pessoais["estado_civil"] if dados_pessoais else "",
            "email_alternativo": dados_pessoais["email_alternativo"] if dados_pessoais else "",
        })

    conn.close()

    return render_template(
        "mew/alunos.html",
        disciplinas=disciplinas,
        alunos=alunos_completo
    )

@app.route("/mew/disciplinas", methods=["GET", "POST"])
def mew_disciplinas():
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        # 1. Criar a disciplina
        nome_disciplina = request.form.get("nome_disciplina")
        cursor.execute("INSERT INTO disciplinas (nome) VALUES (?)", (nome_disciplina,))
        disciplina_id = cursor.lastrowid

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
                VALUES (?, ?, ?, ?)
            """, (disciplina_id, titulo, video_url, pdf_url))
            
            capitulo_id = cursor.lastrowid

            # Inserir prova com as questões
            cursor.execute("""
                INSERT INTO provas (capitulo_id, questoes_json)
                VALUES (?, ?)
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
        cursor.execute("UPDATE disciplinas SET nome = ? WHERE id = ?", (nome, disciplina_id))   

        # Atualizar capítulos
        cursor.execute("SELECT id FROM capitulos WHERE disciplina_id = ? ORDER BY id", (disciplina_id,))
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
                SET titulo = ?, video_url = ?, pdf_url = ?
                WHERE id = ?
            """, (titulo, video, pdf, cap["id"]))

            cursor.execute("UPDATE provas SET questoes_json = ? WHERE capitulo_id = ?",
            (questoes, cap["id"]))

        conn.commit()
        conn.close()
        return redirect("/mew/disciplinas")

    # GET
    cursor.execute("SELECT * FROM disciplinas WHERE id = ?", (disciplina_id,))
    disciplina = cursor.fetchone()

    cursor.execute("""
        SELECT c.*, p.questoes_json
        FROM capitulos c
        LEFT JOIN provas p ON p.capitulo_id = c.id
        WHERE c.disciplina_id = ?
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
                placeholders = ','.join(['?'] * len(ids_list))
                cursor.execute(f"""
                    SELECT GROUP_CONCAT(nome) as nomes
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
            WHERE id = ?
        """, (id,))
    elif tipo == "declaracao":
        cursor.execute("""
            UPDATE solicitacoes_declaracoes 
            SET entregue = 1 
            WHERE id = ?
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
        cursor.execute("DELETE FROM solicitacoes_material WHERE id = ?", (id,))
    elif tipo == "declaracao":
        cursor.execute("DELETE FROM solicitacoes_declaracoes WHERE id = ?", (id,))
    
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
                    SET nome = ?, email = ?, senha = ?
                    WHERE id = ?
                """, (nome, email, senha, aluno_id))
            else:
                cursor.execute("""
                    UPDATE alunos 
                    SET nome = ?, email = ?
                    WHERE id = ?
                """, (nome, email, aluno_id))
            
            # Verificar se já existem dados pessoais
            cursor.execute("SELECT id FROM dados_pessoais WHERE aluno_id = ?", (aluno_id,))
            dados_existentes = cursor.fetchone()
            
            if dados_existentes:
                # Atualizar dados existentes (COM TODOS OS CAMPOS)
                cursor.execute("""
                    UPDATE dados_pessoais 
                    SET cpf = ?, rg = ?, telefone = ?, endereco = ?, 
                        cidade = ?, estado = ?, cep = ?, curso_referencia = ?,
                        nome_pai = ?, nome_mae = ?, naturalidade = ?, nacionalidade = ?,
                        data_nascimento = ?, sexo = ?, estado_civil = ?, email_alternativo = ?
                    WHERE aluno_id = ?
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
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                cursor.execute("SELECT id FROM situacao_financeira WHERE aluno_id = ?", (aluno_id,))
                situacao_existente = cursor.fetchone()
                
                if situacao_existente:
                    # Atualizar
                    cursor.execute("""
                        UPDATE situacao_financeira 
                        SET forma_pagamento = ?, status = ?, 
                            parcelas_total = ?, parcelas_pagas = ?, 
                            valor_total = ?
                        WHERE aluno_id = ?
                    """, (forma_pagamento, status_financeiro, 
                          parcelas_total, parcelas_pagas, 
                          valor_total_float, aluno_id))
                else:
                    # Inserir
                    cursor.execute("""
                        INSERT INTO situacao_financeira 
                        (aluno_id, forma_pagamento, status, 
                         parcelas_total, parcelas_pagas, valor_total)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (aluno_id, forma_pagamento, status_financeiro,
                          parcelas_total, parcelas_pagas, valor_total_float))
            
            # Gerenciar disciplinas
            if request.form.get("gerenciar_disciplinas"):
                disciplinas_selecionadas = request.form.getlist("disciplinas")
                
                # Buscar disciplinas atuais
                cursor.execute("SELECT disciplina_id FROM aluno_disciplina WHERE aluno_id = ?", (aluno_id,))
                disciplinas_atuais = [str(row['disciplina_id']) for row in cursor.fetchall()]
                
                # Remover disciplinas desmarcadas
                for d_id in disciplinas_atuais:
                    if d_id not in disciplinas_selecionadas:
                        try:
                            cursor.execute("DELETE FROM aluno_disciplina WHERE aluno_id = ? AND disciplina_id = ?", 
                                          (aluno_id, d_id))
                            cursor.execute("DELETE FROM aluno_disciplina_datas WHERE aluno_id = ? AND disciplina_id = ?", 
                                          (aluno_id, d_id))
                        except:
                            pass  # Ignorar erros em exclusões
                
                # Adicionar/atualizar disciplinas selecionadas
                for d_id in disciplinas_selecionadas:
                    # Verificar se já existe matrícula
                    cursor.execute("SELECT id FROM aluno_disciplina WHERE aluno_id = ? AND disciplina_id = ?", 
                                  (aluno_id, d_id))
                    existe = cursor.fetchone()
                    
                    if not existe:
                        # Adicionar nova matrícula
                        cursor.execute("""
                            INSERT INTO aluno_disciplina (aluno_id, disciplina_id)
                            VALUES (?, ?)
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
                                INSERT OR REPLACE INTO aluno_disciplina_datas 
                                (aluno_id, disciplina_id, data_inicio, data_fim_previsto)
                                VALUES (?, ?, ?, ?)
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
        
        cursor.execute("SELECT * FROM alunos WHERE id = ?", (aluno_id,))
        aluno = cursor.fetchone()
        
        if not aluno:
            conn.close()
            return "Aluno não encontrado", 404
        
        cursor.execute("SELECT * FROM dados_pessoais WHERE aluno_id = ?", (aluno_id,))
        dados_pessoais = cursor.fetchone()
        
        # Buscar situação financeira
        cursor.execute("""
            SELECT * FROM situacao_financeira 
            WHERE aluno_id = ? 
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
            WHERE ad.aluno_id = ?
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
    cursor.execute("DELETE FROM situacao_financeira WHERE aluno_id = ?", (aluno_id,))
    cursor.execute("DELETE FROM dados_pessoais WHERE aluno_id = ?", (aluno_id,))
    cursor.execute("DELETE FROM notas WHERE aluno_id = ?", (aluno_id,))
    cursor.execute("DELETE FROM aluno_disciplina WHERE aluno_id = ?", (aluno_id,))
    cursor.execute("DELETE FROM solicitacoes_material WHERE aluno_id = ?", (aluno_id,))
    cursor.execute("DELETE FROM solicitacoes_declaracoes WHERE aluno_id = ?", (aluno_id,))
    cursor.execute("DELETE FROM alunos WHERE id = ?", (aluno_id,))
    
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
        WHERE ad.aluno_id = ?
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
        VALUES (?, ?, ?, ?, ?)
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
        WHERE sd.aluno_id = ?
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
                placeholders = ','.join(['?'] * len(ids_list))
                cursor.execute(f"""
                    SELECT GROUP_CONCAT(nome) as nomes
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
        # Converter sqlite3.Row para dicionário
        s_dict = dict(s)
        
        # Buscar nomes das disciplinas
        disciplinas_ids = s_dict['disciplinas_ids']
        if disciplinas_ids:
            # Converter string de IDs em lista
            ids_list = [int(id.strip()) for id in disciplinas_ids.split(',') if id.strip()]
            if ids_list:
                # Buscar nomes das disciplinas
                placeholders = ','.join(['?'] * len(ids_list))
                cursor.execute(f"""
                    SELECT GROUP_CONCAT(nome) as nomes
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
        SET status = ?, resposta = ?, arquivo_url = ?, data_resposta = ?
        WHERE id = ?
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
    
    cursor.execute("DELETE FROM solicitacoes_documentos WHERE id = ?", (id,))
    
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
                WHERE nf.aluno_id = ? AND nf.disciplina_id = d.id) as ja_realizada,
               (SELECT COUNT(*) FROM questoes_finais qf WHERE qf.disciplina_id = d.id) as total_questoes
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        LEFT JOIN liberacao_final lf ON d.id = lf.disciplina_id AND lf.aluno_id = ?
        WHERE ad.aluno_id = ?
        AND lf.liberada = 1
        AND date(lf.data_liberacao) <= date('now')
    """, (aluno_id, aluno_id, aluno_id))
    
    disciplinas = cursor.fetchall()
    
    # Buscar resultados anteriores
    cursor.execute("""
        SELECT nf.*, d.nome as disciplina_nome
        FROM notas_finais nf
        JOIN disciplinas d ON nf.disciplina_id = d.id
        WHERE nf.aluno_id = ?
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
        cursor.execute("DELETE FROM notas_finais WHERE disciplina_id = ?", (disciplina_id,))
        
        # 2. Questões finais
        cursor.execute("DELETE FROM questoes_finais WHERE disciplina_id = ?", (disciplina_id,))
        
        # 3. Provas finais
        cursor.execute("DELETE FROM provas_finais WHERE disciplina_id = ?", (disciplina_id,))
        
        # 4. Liberações finais
        cursor.execute("DELETE FROM liberacao_final WHERE disciplina_id = ?", (disciplina_id,))
        
        # 5. Notas dos alunos
        cursor.execute("DELETE FROM notas WHERE disciplina_id = ?", (disciplina_id,))
        
        # 6. Solicitações de material
        cursor.execute("DELETE FROM solicitacoes_material WHERE disciplina_id = ?", (disciplina_id,))
        
        # 7. Solicitações de documentos
        cursor.execute("DELETE FROM solicitacoes_documentos WHERE disciplinas_ids LIKE ?", 
                      (f'%{disciplina_id}%',))
        
        # 8. Datas das disciplinas dos alunos
        cursor.execute("DELETE FROM aluno_disciplina_datas WHERE disciplina_id = ?", (disciplina_id,))
        
        # 9. Associações aluno-disciplina
        cursor.execute("DELETE FROM aluno_disciplina WHERE disciplina_id = ?", (disciplina_id,))
        
        # 10. Provas dos capítulos (primeiro deletar provas)
        cursor.execute("""
            DELETE FROM provas 
            WHERE capitulo_id IN (
                SELECT id FROM capitulos WHERE disciplina_id = ?
            )
        """, (disciplina_id,))
        
        # 11. Capítulos
        cursor.execute("DELETE FROM capitulos WHERE disciplina_id = ?", (disciplina_id,))
        
        # 12. Finalmente, a disciplina
        cursor.execute("DELETE FROM disciplinas WHERE id = ?", (disciplina_id,))
        
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
    
    cursor.execute("SELECT id FROM notas_finais WHERE aluno_id = ? AND disciplina_id = ?", 
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
        WHERE disciplina_id = ? 
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
    cursor.execute("SELECT nome FROM disciplinas WHERE id = ?", (disciplina_id,))
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
    
    # Buscar questões
    cursor.execute("SELECT * FROM questoes_finais WHERE disciplina_id = ?", (disciplina_id,))
    todas_questoes = cursor.fetchall()
    
    # Contar acertos
    acertos = 0
    for i, questao in enumerate(todas_questoes[:30], 1):
        resposta_aluno = request.form.get(f"q{i}")
        if resposta_aluno == questao["resposta_correta"]:
            acertos += 1
    
    # Calcular nota da prova final (0-10)
    nota_final = round((acertos / 30) * 10, 2)
    
    # Calcular média das 4 provas da disciplina
    cursor.execute("""
        SELECT AVG(nota) as media_disciplina 
        FROM notas 
        WHERE aluno_id = ? AND disciplina_id = ?
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
        VALUES (?, ?, ?, ?, ?, ?, ?)
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
            WHERE nf.aluno_id = ? AND nf.disciplina_id = ?
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
    
    cursor.execute("SELECT COUNT(*) as total FROM questoes_finais WHERE disciplina_id = ?", (disciplina_id,))
    total_questoes = cursor.fetchone()["total"] or 0
    
    if total_questoes < 30:
        conn.close()
        return redirect(f"/mew/avaliacao-final?erro=Disciplina+precisa+de+30+questões+({total_questoes}/30)")
    
    # Verificar se já existe liberação para este aluno nesta disciplina
    cursor.execute("SELECT id FROM liberacao_final WHERE aluno_id = ? AND disciplina_id = ?", 
                  (aluno_id, disciplina_id))
    
    if cursor.fetchone():
        # Atualizar data e liberar
        cursor.execute("""
            UPDATE liberacao_final 
            SET data_liberacao = ?, liberada = 1 
            WHERE aluno_id = ? AND disciplina_id = ?
        """, (data_liberacao, aluno_id, disciplina_id))
    else:
        # Inserir nova liberação
        cursor.execute("""
            INSERT INTO liberacao_final (aluno_id, disciplina_id, data_liberacao, liberada)
            VALUES (?, ?, ?, 1)
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
    
    cursor.execute("DELETE FROM liberacao_final WHERE id = ?", (liberacao_id,))
    
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
    cursor.execute("SELECT * FROM disciplinas WHERE id = ?", (disciplina_id,))
    disciplina = cursor.fetchone()
    
    # Buscar TODAS as questões (sem limite)
    cursor.execute("SELECT * FROM questoes_finais WHERE disciplina_id = ? ORDER BY id", (disciplina_id,))
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
                        VALUES (?, ?, ?, ?, ?, ?, ?)
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
                        VALUES (?, ?, ?, ?, ?, ?, ?)
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
        WHERE disciplina_id = ?
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
    cursor.execute("SELECT nome, ra FROM alunos WHERE id = ?", (aluno_id,))
    aluno = cursor.fetchone()
    
    if not aluno:
        flash("Aluno não encontrado.", "error")
        return redirect(url_for("dashboard"))
    
    # Buscar disciplinas do aluno
    cursor.execute("""
        SELECT d.id, d.nome
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        WHERE ad.aluno_id = ?
        ORDER BY d.nome
    """, (aluno_id,))
    disciplinas = cursor.fetchall()
    
    # Buscar notas dos capítulos
    cursor.execute("""
        SELECT n.disciplina_id, n.capitulo, n.nota, d.nome AS disciplina_nome
        FROM notas n
        JOIN disciplinas d ON n.disciplina_id = d.id
        WHERE n.aluno_id = ?
        ORDER BY n.disciplina_id, n.capitulo
    """, (aluno_id,))
    notas_capitulos = cursor.fetchall()
    
    # Buscar notas finais
    cursor.execute("""
        SELECT nf.*, d.nome as disciplina_nome
        FROM notas_finais nf
        JOIN disciplinas d ON nf.disciplina_id = d.id
        WHERE nf.aluno_id = ?
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


@app.route("/mew/deletar-documento/<codigo>")
def mew_deletar_documento(codigo):
    """Deleta um documento autenticado"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM documentos_autenticados WHERE codigo_autenticacao = ?", (codigo,))
    
    conn.commit()
    conn.close()
    
    return redirect("/mew/listar-documentos?sucesso=Documento+removido")

# ==========================
# VALIDAÇÃO PÚBLICA DE DOCUMENTOS
# ==========================

@app.route("/validar-documento")
def validar_documento_publico():
    """Página pública para validação de documentos"""
    return render_template("validar_documento.html")


@app.route("/validar-documento/<codigo>")
def validar_documento_por_codigo(codigo):
    """
    Valida um documento específico pelo código (versão completa)
    VERSÃO CORRIGIDA
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT codigo, aluno_nome, aluno_ra, tipo, data_emissao, data_validade, hash_documento, metadados
            FROM documentos_autenticados 
            WHERE codigo = ?
        """, (codigo.upper(),))
        
        documento = cursor.fetchone()
        conn.close()
        
        if documento:
            # Converter para dicionário
            doc_dict = dict(documento)
            
            # Calcular status de validade
            from datetime import datetime
            hoje = datetime.now()
            
            if doc_dict.get('data_validade'):
                data_validade = datetime.strptime(doc_dict['data_validade'], "%d/%m/%Y")
                status = "válido" if hoje <= data_validade else "expirado"
            else:
                status = "válido"  # Se não tiver data, considerar válido
            
            return render_template(
                "resultado_validacao_completo.html",
                valido=True,
                codigo=codigo,
                documento=doc_dict,
                status=status
            )
        else:
            return render_template(
                "resultado_validacao.html",
                valido=False,
                codigo=codigo,
                mensagem="Documento não encontrado ou código inválido."
            )
            
    except Exception as e:
        print(f"Erro na validação: {e}")
        return render_template(
            "resultado_validacao.html",
            valido=False,
            codigo=codigo,
            mensagem=f"Erro na validação: {str(e)}"
        )
        

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
            WHERE codigo = ?
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
        cursor.execute("SELECT * FROM documentos_autenticados WHERE codigo = ?", (codigo,))
        
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
                SET data_inicio = ?, data_fim_previsto = ?
                WHERE aluno_id = ? AND disciplina_id = ?
            """, (data_inicio_fmt, data_fim, aluno_id, disciplina_id))

        # ➕ ADICIONAR DISCIPLINA
        elif acao == "adicionar":
            data_inicio = request.form.get("data_inicio")

            from datetime import datetime, timedelta
            data_inicio_obj = datetime.strptime(data_inicio, "%Y-%m-%d")
            data_fim = (data_inicio_obj + timedelta(days=60)).strftime("%d/%m/%Y")

            cursor.execute("""
                INSERT OR IGNORE INTO aluno_disciplina (aluno_id, disciplina_id)
                VALUES (?, ?)
            """, (aluno_id, disciplina_id))

            cursor.execute("""
                INSERT OR REPLACE INTO aluno_disciplina_datas
                (aluno_id, disciplina_id, data_inicio, data_fim_previsto)
                VALUES (?, ?, ?, ?)
            """, (aluno_id, disciplina_id,
                  data_inicio_obj.strftime("%d/%m/%Y"),
                  data_fim))

        # ❌ REMOVER DISCIPLINA
        elif acao == "remover":
            cursor.execute("""
                DELETE FROM aluno_disciplina
                WHERE aluno_id = ? AND disciplina_id = ?
            """, (aluno_id, disciplina_id))

            cursor.execute("""
                DELETE FROM aluno_disciplina_datas
                WHERE aluno_id = ? AND disciplina_id = ?
            """, (aluno_id, disciplina_id))

        conn.commit()

    # 🔎 DADOS PARA O GET
    cursor.execute("SELECT id, nome FROM alunos WHERE id = ?", (aluno_id,))
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
            AND addd.aluno_id = ?
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
    cursor.execute("SELECT id, nome, ra FROM alunos WHERE id = ?", (aluno_id,))
    aluno = cursor.fetchone()
    
    if not aluno:
        conn.close()
        return "Aluno não encontrado", 404
    
    # Buscar disciplinas do aluno
    cursor.execute("""
        SELECT d.id, d.nome, 
               (SELECT COUNT(*) FROM capitulos WHERE disciplina_id = d.id) as total_capitulos,
               (SELECT COUNT(DISTINCT capitulo) FROM notas 
                WHERE aluno_id = ? AND disciplina_id = d.id) as provas_feitas
        FROM disciplinas d
        JOIN aluno_disciplina ad ON d.id = ad.disciplina_id
        WHERE ad.aluno_id = ?
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
    cursor.execute("SELECT id, nome, ra FROM alunos WHERE id = ?", (aluno_id,))
    aluno = cursor.fetchone()
    
    cursor.execute("SELECT id, nome FROM disciplinas WHERE id = ?", (disciplina_id,))
    disciplina = cursor.fetchone()
    
    if not aluno or not disciplina:
        conn.close()
        return "Aluno ou disciplina não encontrados", 404
    
    # Buscar capítulos da disciplina
    cursor.execute("SELECT id, titulo FROM capitulos WHERE disciplina_id = ? ORDER BY id", (disciplina_id,))
    capitulos = cursor.fetchall()
    
    # Buscar notas existentes
    cursor.execute("""
        SELECT capitulo, nota 
        FROM notas 
        WHERE aluno_id = ? AND disciplina_id = ?
        ORDER BY capitulo
    """, (aluno_id, disciplina_id))
    notas_existentes = {row['capitulo']: row['nota'] for row in cursor.fetchall()}
    
    # Buscar nota final (se existir)
    cursor.execute("""
        SELECT nota_final, media_disciplina, media_final, status 
        FROM notas_finais 
        WHERE aluno_id = ? AND disciplina_id = ?
    """, (aluno_id, disciplina_id))
    nota_final = cursor.fetchone()
    
    # Buscar datas de liberação dos capítulos
    cursor.execute("""
        SELECT data_inicio, prova_final_aberta 
        FROM aluno_disciplina_datas 
        WHERE aluno_id = ? AND disciplina_id = ?
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
                WHERE aluno_id = ? AND disciplina_id = ? AND capitulo = ?
            """, (aluno_id, disciplina_id, capitulo))
            
            if cursor.fetchone():
                # Atualizar
                cursor.execute("""
                    UPDATE notas SET nota = ? 
                    WHERE aluno_id = ? AND disciplina_id = ? AND capitulo = ?
                """, (nota, aluno_id, disciplina_id, capitulo))
            else:
                # Inserir
                cursor.execute("""
                    INSERT INTO notas (aluno_id, disciplina_id, capitulo, nota)
                    VALUES (?, ?, ?, ?)
                """, (aluno_id, disciplina_id, capitulo, nota))
            
            message = "Nota salva com sucesso"
            
        elif acao == "excluir_nota":
            capitulo = request.form.get("capitulo")
            
            if not capitulo:
                conn.close()
                return jsonify({"success": False, "message": "Capítulo não informado"})
            
            cursor.execute("""
                DELETE FROM notas 
                WHERE aluno_id = ? AND disciplina_id = ? AND capitulo = ?
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
                WHERE aluno_id = ? AND disciplina_id = ?
            """, (aluno_id, disciplina_id))
            
            if cursor.fetchone():
                # Atualizar
                cursor.execute("""
                    UPDATE notas_finais 
                    SET nota_final = ?, media_disciplina = ?, media_final = ?, status = ?
                    WHERE aluno_id = ? AND disciplina_id = ?
                """, (nota_final_val, media_disciplina, media_final, status, aluno_id, disciplina_id))
            else:
                # Inserir
                data_realizacao = datetime.now().strftime("%d/%m/%Y %H:%M")
                cursor.execute("""
                    INSERT INTO notas_finais 
                    (aluno_id, disciplina_id, nota_final, media_disciplina, media_final, status, data_realizacao)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (aluno_id, disciplina_id, nota_final_val, media_disciplina, media_final, status, data_realizacao))
            
            message = "Nota final salva com sucesso"
            
        elif acao == "excluir_final":
            cursor.execute("""
                DELETE FROM notas_finais 
                WHERE aluno_id = ? AND disciplina_id = ?
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
                    WHERE aluno_id = ? AND disciplina_id = ? AND capitulo > ?
                """, (aluno_id, disciplina_id, cap_feitos))
            
            # Atualizar datas
            cursor.execute("""
                SELECT id FROM aluno_disciplina_datas 
                WHERE aluno_id = ? AND disciplina_id = ?
            """, (aluno_id, disciplina_id))
            
            if cursor.fetchone():
                # Atualizar
                if data_inicio:
                    cursor.execute("""
                        UPDATE aluno_disciplina_datas 
                        SET data_inicio = ?, prova_final_aberta = ?
                        WHERE aluno_id = ? AND disciplina_id = ?
                    """, (data_inicio, prova_final_aberta, aluno_id, disciplina_id))
                else:
                    cursor.execute("""
                        UPDATE aluno_disciplina_datas 
                        SET prova_final_aberta = ?
                        WHERE aluno_id = ? AND disciplina_id = ?
                    """, (prova_final_aberta, aluno_id, disciplina_id))
            else:
                # Inserir (se tiver data_inicio)
                if data_inicio:
                    cursor.execute("""
                        INSERT INTO aluno_disciplina_datas 
                        (aluno_id, disciplina_id, data_inicio, prova_final_aberta)
                        VALUES (?, ?, ?, ?)
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
        WHERE a.id = ?
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        
        documento_id = cursor.lastrowid
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
        WHERE a.id = ?
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
        WHERE ad.aluno_id = ?
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
        docente_display = "Professor Titular"
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
        WHERE a.id = ?
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        
        documento_id = cursor.lastrowid
        conn.commit()
        
    except Exception as e:
        print(f"Erro ao salvar documento: {e}")
        documento_id = None
    finally:
        conn.close()
    
    return documento_id

@app.route("/mew/visualizar-documento/<codigo>")
def mew_visualizar_documento(codigo):
    """Visualiza um documento autenticado no MEW"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM documentos_autenticados WHERE codigo_autenticacao = ?", (codigo,))
    documento = cursor.fetchone()
    
    conn.close()
    
    if not documento:
        return "Documento não encontrado", 404
    
    # Adicionar botão de impressão e informações
    conteudo_completo = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Histórico Escolar - {documento['aluno_id']}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .no-print {{ 
                background: #f8f9fa; 
                padding: 20px; 
                border-radius: 10px; 
                margin: 20px 0; 
                border: 1px solid #ddd;
            }}
            .print-btn {{
                background: #1a237e;
                color: white;
                padding: 10px 20px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 16px;
            }}
            .print-btn:hover {{
                background: #0d1b6b;
            }}
            .info-box {{
                background: #e8f5e8;
                padding: 15px;
                border-radius: 5px;
                margin: 15px 0;
            }}
            @media print {{
                .no-print {{ display: none !important; }}
            }}
        </style>
    </head>
    <body>
        <div class="no-print">
            <h3>📄 Documento Autenticado</h3>
            <div class="info-box">
                <p><strong>Código:</strong> {documento['codigo_autenticacao']}</p>
                <p><strong>Data de Emissão:</strong> {documento['data_emissao']}</p>
                <p><strong>Hash:</strong> {documento['hash_documento'][:50]}...</p>
            </div>
            <button onclick="window.print()" class="print-btn">
                🖨️ Imprimir Documento
            </button>
            <button onclick="window.close()" style="margin-left: 10px; padding: 10px 20px; background: #6c757d; color: white; border: none; border-radius: 5px; cursor: pointer;">
                ❌ Fechar
            </button>
        </div>
        
        {documento['conteudo_html']}
        
        <script>
            // Focar na janela de impressão
            function imprimirDocumento() {{
                window.print();
            }}
            
            // Adicionar atalho de teclado (Ctrl+P)
            document.addEventListener('keydown', function(e) {{
                if ((e.ctrlKey || e.metaKey) && e.key === 'p') {{
                    e.preventDefault();
                    window.print();
                }}
            }});
        </script>
    </body>
    </html>
    """
    
    return conteudo_completo

@app.route('/mew/visualizar-documento/<codigo>')
def mew_visualizar_documento_completo(codigo):
    """Visualiza um documento autenticado - VERSÃO CORRIGIDA"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM documentos_autenticados WHERE codigo_autenticacao = ?", (codigo,))
    documento = cursor.fetchone()
    
    conn.close()
    
    if not documento:
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Documento não encontrado</title>
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
            </style>
        </head>
        <body>
            <div class="error-box">
                <h2>❌ Documento não encontrado</h2>
                <p>O documento com o código <strong>{}</strong> não foi encontrado.</p>
                <p><a href="/mew/gerar-documento">Voltar para gerar documento</a></p>
            </div>
        </body>
        </html>
        '''.format(codigo)
    
    return documento["conteudo_html"]

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
        VALUES (?, ?, ?, ?, ?, ?)
    """, (codigo, aluno_nome, aluno_ra, tipo, conteudo_html, data_geracao))
    
    conn.commit()
    conn.close()
    
    return True

def buscar_documento_por_codigo(codigo):
    """Busca documento pelo código"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM documentos_autenticados WHERE codigo = ?", (codigo,))
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
        WHERE ad.aluno_id = ?
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
        WHERE ad.aluno_id = ?
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

def gerar_historico_automatico(aluno_id, disciplinas, dados_aluno, qr_code_base64, codigo, hash_documento, ano_manual=None):
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
            cursor.execute("SELECT carga_horaria FROM disciplinas WHERE id = ?", (d['id'],))
            disciplina_info = cursor.fetchone()
            carga = disciplina_info['carga_horaria'] if disciplina_info and disciplina_info['carga_horaria'] else 80
            carga_total_aprovada += int(carga)
        
        # Para carga total cursada (todas disciplinas)
        cursor.execute("SELECT carga_horaria FROM disciplinas WHERE id = ?", (d['id'],))
        disciplina_info = cursor.fetchone()
        carga = disciplina_info['carga_horaria'] if disciplina_info and disciplina_info['carga_horaria'] else 80
        carga_total_cursada += int(carga)
    
    # Data atual
    from datetime import datetime
    data_atual = datetime.now().strftime("%d/%m/%Y")
    
    # Obter ano configurável
    ano_historico = ano_manual if ano_manual else obter_configuracao_ano()
    
    # ===== CORREÇÃO COMPLETA AQUI =====
    # Buscar IRA do banco de dados
    cursor.execute("""
        SELECT ira_ponderado, total_disciplinas_concluidas 
        FROM ira_aluno 
        WHERE aluno_id = ?
    """, (aluno_id,))

    ira_row = cursor.fetchone()
    
    # Inicializar variáveis com valores padrão
    ira_display = "N/I"
    ira_info = {
        'disciplinas_aprovadas': 0,
        'carga_total_aprovada': carga_total_aprovada
    }
    
    # Se encontrou IRA no banco, usar os dados reais
    if ira_row and ira_row['ira_ponderado']:
        ira_display = f"{ira_row['ira_ponderado']:.2f}"
        ira_info['disciplinas_aprovadas'] = ira_row['total_disciplinas_concluidas']
    # ===================================
    
    # Buscar dados adicionais do aluno
    cursor.execute("""
        SELECT nome_pai, nome_mae, naturalidade, nacionalidade, 
               data_nascimento, sexo, estado_civil, curso_referencia
        FROM dados_pessoais 
        WHERE aluno_id = ?
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
            WHERE d.id = ?
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
            docente = 'Professor Titular'
        
        # Determinar período
        cursor.execute("""
            SELECT data_inicio FROM aluno_disciplina_datas 
            WHERE aluno_id = ? AND disciplina_id = ?
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
            WHERE aluno_id = ? AND disciplina_id = ?
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
        
        linhas += f"""
            <tr>
                <td style="border: 1px solid #000; padding: 4px; text-align: center;">{periodo}</td>
                <td style="border: 1px solid #000; padding: 4px; text-align: left;">{d.get('nome', 'Disciplina')}</td>
                <td style="border: 1px solid #000; padding: 4px; text-align: center;">{semestre}</td>
                <td style="border: 1px solid #000; padding: 4px; text-align: center;">{carga_horaria}H</td>
                <td style="border: 1px solid #000; padding: 4px; text-align: left;">{docente}</td>
                <td style="border: 1px solid #000; padding: 4px; text-align: center;">{nota_display}</td>
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
    <div class="microtexto-borda top">DOCUMENTO OFICIAL - FACOP/SIGEU - VALIDAÇÃO DIGITAL OBRIGATÓRIA</div>
    <div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98 <strong> | H{datetime.now().strftime('%Y%m%d')}/Coord. Acad. Tatiane R. G. Lourenço- </strong></div>
    <div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
    <div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>
    
    <!-- MARCAS D'ÁGUA -->
    <div class="marca-dagua-principal">FACOP SiGEu</div>
    <div class="marca-dagua-pattern"></div>
    
    <!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
    <div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
    <div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
    <div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO FACOP/SIGEU</div>
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
            Facop/SiGEu<br>e-SIGEU-ICP-2026
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
    <div class="microtexto-borda top">DOCUMENTO OFICIAL - FACOP/SIGEU - VALIDAÇÃO DIGITAL OBRIGATÓRIA</div>
    <div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98 <strong> | H{datetime.now().strftime('%Y%m%d')}/Coord. Acad. Tatiane R. G. Lourenço- </strong></div>
    <div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
    <div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>
    
    <!-- MARCAS D'ÁGUA -->
    <div class="marca-dagua-principal">FACOP SiGEu</div>
    <div class="marca-dagua-pattern"></div>
    
    <!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
    <div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
    <div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
    <div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO FACOP/SIGEU</div>
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
            Facop/SiGEu<br>e-SIGEU-ICP-2026
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
            <p><strong>Normativo:</strong> Oferta de disciplina isolada de acordo com o art. 50 da Lei de Diretrizes e Bases da Educação Nacional - LDBEN (Lei nº 9.394/1996). Modalidade de ingresso isolada, respeitados os pré-requisitos exigidos para cada disciplina, conforme registrado no ato da matrícula, vinculada à estrutura curricular de curso reconhecido no convênio institucional FACOP/SiGEu.</p>
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

# ==========================
# ROTA PARA GERAR HISTÓRICO (SIMPLES)
# ==========================

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
        
        # Gerar HTML do histórico (AGORA COM QR CODE INCLUSO)
        html = gerar_historico_automatico(
            aluno_id, 
            disciplinas, 
            aluno_completo, 
            qr_code_base64, 
            codigo, 
            hash_documento,
            ano_manual
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

@app.route("/ver-documento/<codigo>")
def ver_documento_completo(codigo):
    """
    Mostra o documento completo com QR Code e informações de autenticação
    VERSÃO CORRIGIDA
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM documentos_autenticados WHERE codigo = ?", (codigo.upper(),))
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    
    cursor.execute("DELETE FROM documentos_autenticados WHERE codigo = ?", (codigo,))
    
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
            VALUES (?, ?, ?, ?)
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
            SET nome = ?, titulacao = ?, email = ?, telefone = ?, ativo = ?
            WHERE id = ?
        """, (nome, titulacao, email, telefone, ativo, docente_id))
        
        conn.commit()
        conn.close()
        return redirect("/mew/docentes?sucesso=Docente+atualizado")
    
    # GET: Buscar docente
    cursor.execute("SELECT * FROM docentes WHERE id = ?", (docente_id,))
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
    cursor.execute("SELECT id FROM disciplina_docente WHERE docente_id = ? LIMIT 1", (docente_id,))
    if cursor.fetchone():
        conn.close()
        return redirect("/mew/docentes?erro=Docente+está+associado+a+disciplinas")
    
    cursor.execute("DELETE FROM docentes WHERE id = ?", (docente_id,))
    
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
            SET carga_horaria = ?
            WHERE id = ?
        """, (carga_horaria, disciplina_id))
        
        # Se tiver docente, associar
        if docente_id and docente_id != "0":
            # Remover associação anterior para este ano/semestre
            cursor.execute("""
                DELETE FROM disciplina_docente 
                WHERE disciplina_id = ? AND ano_semestre = ?
            """, (disciplina_id, ano_semestre))
            
            # Adicionar nova associação
            cursor.execute("""
                INSERT INTO disciplina_docente (disciplina_id, docente_id, ano_semestre)
                VALUES (?, ?, ?)
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
    cursor.execute("SELECT id, nome, carga_horaria FROM disciplinas WHERE id = ?", (disciplina_id,))
    disciplina = cursor.fetchone()
    
    if not disciplina:
        conn.close()
        return jsonify({"error": "Disciplina não encontrada"})
    
    # Buscar docente associado (mais recente)
    cursor.execute("""
        SELECT d.id, d.nome, dd.ano_semestre
        FROM docentes d
        JOIN disciplina_docente dd ON d.id = dd.docente_id
        WHERE dd.disciplina_id = ?
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
            WHERE aluno_id = ? AND disciplina_id = ?
        """, (aluno_id, disciplina_id))
        
        if cursor.fetchone():
            # Atualizar
            cursor.execute("""
                UPDATE rendimento_academico 
                SET nota_final = ?, carga_horaria = ?, conceito = ?, peso = ?
                WHERE aluno_id = ? AND disciplina_id = ?
            """, (nota_final, carga_horaria, conceito, peso, aluno_id, disciplina_id))
        else:
            # Inserir
            cursor.execute("""
                INSERT INTO rendimento_academico 
                (aluno_id, disciplina_id, nota_final, carga_horaria, conceito, peso)
                VALUES (?, ?, ?, ?, ?, ?)
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
        WHERE ra.aluno_id = ? AND ra.disciplina_id = ?
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
        WHERE ad.aluno_id = ?
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
        WHERE ad.aluno_id = ?
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
            WHERE aluno_id = ? AND disciplina_id = ?
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    
    query = "SELECT codigo, aluno_nome, aluno_ra, tipo, data_emissao, disciplina_id FROM documentos_autenticados WHERE aluno_id = ?"
    params = [aluno_id]
    
    if tipo:
        query += " AND tipo = ?"
        params.append(tipo)
    
    query += " ORDER BY data_emissao DESC"
    
    cursor.execute(query, params)
    documentos = cursor.fetchall()
    
    # Buscar nomes das disciplinas
    result = []
    for doc in documentos:
        doc_dict = dict(doc)
        if doc_dict.get('disciplina_id'):
            cursor.execute("SELECT nome FROM disciplinas WHERE id = ?", (doc_dict['disciplina_id'],))
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
            WHERE id = ?
        """, (documento_id,))
        
        documento_row = cursor.fetchone()
        
        if not documento_row:
            conn.close()
            return jsonify({"success": False, "message": "Documento não encontrado"})
        
        # Converter para dicionário
        documento = dict(documento_row)
        
        # Verificar se o aluno existe
        cursor.execute("SELECT id, nome, ra FROM alunos WHERE id = ?", (aluno_id,))
        aluno = cursor.fetchone()
        if not aluno:
            conn.close()
            return jsonify({"success": False, "message": "Aluno não encontrado no sistema"})
        
        # Buscar nome da disciplina se houver
        disciplina_nome = None
        if documento.get('disciplina_id'):
            cursor.execute("SELECT nome FROM disciplinas WHERE id = ?", (documento['disciplina_id'],))
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'enviado')
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
        
        envio_id = cursor.lastrowid
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
        WHERE de.aluno_id = ?
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
        WHERE de.id = ? AND de.aluno_id = ?
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
            SET status = 'visualizado', data_visualizacao = ?
            WHERE id = ?
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
            📄 Documento disponibilizado pela FACOP/SiGEu
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
        WHERE id = ? AND aluno_id = ?
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
        query += " AND da.aluno_id = ?"
        params.append(aluno_id)
    
    if categoria and categoria != 'todos':
        query += " AND da.tipo = ?"
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
        cursor.execute("SELECT id FROM documentos_enviados WHERE documento_original_id = ?", (documento_id,))
        tem_envios = cursor.fetchone()
        
        if tem_envios:
            # Primeiro excluir os envios
            cursor.execute("DELETE FROM documentos_enviados WHERE documento_original_id = ?", (documento_id,))
        
        # Depois excluir o documento original
        cursor.execute("DELETE FROM documentos_autenticados WHERE id = ?", (documento_id,))
        
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
        SET status = 'visualizado', data_visualizacao = ?
        WHERE id = ? AND aluno_id = ? AND status = 'enviado'
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
        WHERE de.aluno_id = ?
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
Secretaria Acadêmica FACOP/SiGEu"""
    
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
Secretaria Acadêmica FACOP/SiGEu"""
    
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
Coordenação Acadêmica FACOP/SiGEu"""
    
    else:
        return f"""Olá {aluno_nome},

Um novo documento acadêmico foi disponibilizado para você! 📋

Este documento possui autenticação digital com QR Code e pode ser validado no site da instituição.

Para visualizar e baixar:
1. Clique no botão "Visualizar Documento" abaixo
2. Use a opção de impressão do navegador (Ctrl+P) para salvar como PDF
3. Guarde o código de autenticação para validação futura

Atenciosamente,
Secretaria Acadêmica FACOP/SiGEu"""

# ============================================
# MEW - GERAR PLANOS DE ENSINO COM IA
# ============================================

@app.route("/mew/gerar-plano-ensino")
def mew_gerar_plano_ensino():
    """Página para gerar planos de ensino com IA"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    from datetime import datetime
    hoje = datetime.now().strftime("%Y-%m-%d")
    
    return render_template("mew/gerar_plano_ensino.html", hoje=hoje)


@app.route("/mew/processar-plano-ensino", methods=["POST"])
def mew_processar_plano_ensino():
    """Processa a geração do plano de ensino com IA e salva no banco"""
    if not session.get("mew_admin"):
        return jsonify({"success": False, "message": "Não autorizado"})
    
    try:
        dados = request.json
        
        # ✅ CHAMAR DIRETAMENTE A FUNÇÃO DO MÓDULO (sem HTTP request)
        from api_planos import consultar_openai_para_plano
        
        conteudo_ia = consultar_openai_para_plano(dados)
        
        if not conteudo_ia:
            return jsonify({"success": False, "message": "Erro ao gerar conteúdo com IA"})
        
        # Dados do formulário
        disciplina = dados.get('disciplina', '').upper()
        carga_horaria = dados.get('carga_horaria', '120 horas')
        modalidade = dados.get('modalidade', 'EaD')
        docente = dados.get('docente', 'Roberto S. M. Souza')
        data_geracao = dados.get('data_geracao', '')
        
        # Formatar data
        from datetime import datetime, timedelta
        if data_geracao:
            data_obj = datetime.strptime(data_geracao, "%Y-%m-%d")
            data_formatada = data_obj.strftime("%d/%m/%Y")
        else:
            data_formatada = datetime.now().strftime("%d/%m/%Y")
        
        # Gerar código único e hash
        import hashlib
        import secrets
        
        codigo = gerar_codigo_simples()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        hash_documento = gerar_hash_documento(
            f"plano_ensino_{disciplina}_{data_geracao}",
            "ADMIN",
            timestamp
        )
        
        # Gerar link de validação
        base_url = request.host_url.rstrip('/')
        link_validacao = f"{base_url}/validar-documento/{codigo}"
        
        # Gerar QR Code
        qr_code_base64 = gerar_qrcode_base64(link_validacao)
        
        # Criar metadados
        metadados = criar_metadados_documento(
            aluno_id=None, 
            tipo_documento='plano_ensino', 
            codigo=codigo, 
            hash_val=hash_documento
        )
        
        # Data de emissão e validade
        data_emissao = datetime.now().strftime("%d/%m/%Y %H:%M")
        data_validade = (datetime.now() + timedelta(days=365*5)).strftime("%d/%m/%Y")
        
        # Gerar HTML completo do plano
        html_completo = gerar_html_plano_ensino(
            disciplina=disciplina,
            codigo=codigo,
            hash_completa=hash_documento,
            carga_horaria=carga_horaria,
            modalidade=modalidade,
            docente=docente,
            data_formatada=data_formatada,
            qr_code_base64=qr_code_base64,
            **conteudo_ia
        )
        
        # Salvar no banco de dados
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO documentos_autenticados 
            (codigo, aluno_id, aluno_nome, aluno_ra, tipo, conteudo_html, data_geracao,
             qr_code, hash_documento, data_emissao, data_validade, metadados)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            codigo,
            None,  # aluno_id (plano não é de um aluno específico)
            "ADMIN - MEW",
            "ADMIN",
            'plano_ensino',
            html_completo,
            data_emissao,
            qr_code_base64,
            hash_documento,
            data_emissao,
            data_validade,
            metadados
        ))
        
        documento_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True,
            "codigo": codigo,
            "hash": hash_documento,
            "qr_code": qr_code_base64,
            "disciplina": disciplina,
            "url_validacao": link_validacao,
            "url_visualizar": f"/ver-documento/{codigo}",
            "data_emissao": data_emissao,
            "data_validade": data_validade
        })
        
    except Exception as e:
        import traceback
        print(f"Erro em mew_processar_plano_ensino: {e}")
        print(traceback.format_exc())
        return jsonify({"success": False, "message": f"Erro ao processar plano: {str(e)}"})
    

@app.route("/mew/planos-ensino")
def mew_planos_ensino():
    """Lista todos os planos de ensino gerados"""
    if not session.get("mew_admin"):
        return redirect("/mew/login")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM documentos_autenticados 
        WHERE tipo = 'plano_ensino'
        ORDER BY data_geracao DESC
    """)
    
    planos = cursor.fetchall()
    total_planos = len(planos)
    
    conn.close()
    
    return render_template(
        "mew/planos_ensino.html",
        planos=planos,
        total_planos=total_planos
    )


def gerar_html_plano_ensino(disciplina, codigo, hash_completa, carga_horaria, 
                             modalidade, docente, data_formatada, qr_code_base64, **kwargs):
    """Gera o HTML completo do plano de ensino com QR Code"""
    
    # Extrair campos do kwargs
    objetivo_geral = kwargs.get('objetivo_geral', '')
    objetivos_especificos = kwargs.get('objetivos_especificos', '')
    ementa = kwargs.get('ementa_expandida', '')
    conteudo_programatico = kwargs.get('conteudo_programatico', '')
    metodologia = kwargs.get('metodologia', '')
    criterios_aprovacao = kwargs.get('criterios_aprovacao', '')
    bibliografia_basica = kwargs.get('bibliografia_basica', '')
    bibliografia_complementar = kwargs.get('bibliografia_complementar', '')
    encontros_sincronos = kwargs.get('encontros_sincronos', '')
    plataforma = kwargs.get('plataforma', '')
    pre_requisitos = kwargs.get('pre_requisitos_formatado', 'Não há pré-requisitos formais.')
    
    # HTML do plano (mesmo template do sistema original)
    html = f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, print-scale=1">
    <title>Plano de Ensino - {disciplina} | FACOP/SiGEU</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
        /* ESTILO PROFISSIONAL INSTITUCIONAL - FACOP/SiGEu */
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
<div class="microtexto-borda top">DOCUMENTO OFICIAL - FACOP/SIGEU - PLANO DE ENSINO</div>
<div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98</div>
<div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
<div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>

<!-- MARCAS D'ÁGUA -->
<div class="marca-dagua-principal">FACOP SiGEu</div>
<div class="marca-dagua-pattern"></div>

<!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
<div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
<div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
<div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO FACOP/SIGEU</div>
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
                        <h1>FACOP/SiGEU</h1>
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
<div class="microtexto-borda top">DOCUMENTO OFICIAL - FACOP/SIGEU - PLANO DE ENSINO</div>
<div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98</div>
<div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
<div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>

<!-- MARCAS D'ÁGUA -->
<div class="marca-dagua-principal">FACOP SiGEu</div>
<div class="marca-dagua-pattern"></div>

<!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
<div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
<div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
<div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO FACOP/SIGEU</div>
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
                        <h1>FACOP/SiGEU</h1>
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
<div class="microtexto-borda top">DOCUMENTO OFICIAL - FACOP/SIGEU - PLANO DE ENSINO</div>
<div class="microtexto-borda bottom">ESTE DOCUMENTO É DE PROPRIEDADE DA INSTITUIÇÃO - REPRODUÇÃO PROIBIDA - LEI 9.610/98</div>
<div class="microtexto-borda left">SISTEMA DE GESTÃO EDUCACIONAL UNIFICADO - SiGEu</div>
<div class="microtexto-borda right">MINISTÉRIO DA EDUCAÇÃO - MEC - PROCESSO Nº 887/2017</div>

<!-- MARCAS D'ÁGUA -->
<div class="marca-dagua-principal">FACOP SiGEu</div>
<div class="marca-dagua-pattern"></div>

<!-- MICROTEXTOS DE SEGURANÇA ESPALHADOS -->
<div class="microtexto-seguranca micro-1">DOCUMENTO OFICIAL - NÃO TRANSFERÍVEL</div>
<div class="microtexto-seguranca micro-2">VALIDAÇÃO ELETRÔNICA OBRIGATÓRIA</div>
<div class="microtexto-seguranca micro-3">SISTEMA ACADÊMICO FACOP/SIGEU</div>
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
                        <h1>FACOP/SiGEU</h1>
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
                <tr><td colspan="2" style="text-align: justify;">{metodologia}</td></tr>
            </table>

            <!-- 6) AVALIAÇÃO -->
            <table class="info-table">
                <tr><th colspan="2">6) CRITÉRIOS DE AVALIAÇÃO</th></tr>
                <tr><td colspan="2">{criterios_aprovacao}</td></tr>
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
                        << autenticação por infraestrutura de chaves FACOP/SiGEU >>
                    </div>
                </div>

                <div class="stamp-date">
                    <div class="secretary-signature">
                        <div class="secretary-name">DEAP • FACOP/SiGEU</div>
                        <div class="secretary-title">DEPARTAMENTO EDUCACIONAL</div>
                        <div class="signature-line">
                            <span class="simulated-signature">Roberto S. M. Souza</span>
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
        placeholders = ','.join(['?'] * len(documento_ids))
        
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
            WHERE id = ? AND tipo = 'plano_ensino'
        """, (documento_id,))
        
        documento = cursor.fetchone()
        if not documento:
            conn.close()
            return jsonify({"success": False, "message": "Plano de ensino não encontrado"})
        
        # Buscar dados do aluno
        cursor.execute("SELECT id, nome, ra FROM alunos WHERE id = ?", (aluno_id,))
        aluno = cursor.fetchone()
        if not aluno:
            conn.close()
            return jsonify({"success": False, "message": "Aluno não encontrado"})
        
        # Buscar nome da disciplina (do documento original)
        cursor.execute("SELECT nome FROM disciplinas WHERE id = ?", (documento['disciplina_id'],))
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
Coordenação Acadêmica FACOP/SiGEU"""
        
        mensagem_final = mensagem_personalizada if mensagem_personalizada.strip() else mensagem_padrao
        
        # Inserir registro de envio
        data_envio = datetime.now().strftime("%d/%m/%Y %H:%M")
        
        cursor.execute("""
            INSERT INTO documentos_enviados 
            (documento_original_id, aluno_id, codigo, tipo, titulo, disciplina_id, data_envio, mensagem, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'enviado')
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
        
        envio_id = cursor.lastrowid
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
    
if __name__ == "__main__":
    init_db()
    app.run(debug=True)