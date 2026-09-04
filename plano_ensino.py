#!/usr/bin/env python3
"""
Sistema de Geração de Planos de Ensino - SiGEU Educação
VERSÃO REFORMULADA - SÓ GERA O HTML, NÃO FAZ AUTENTICAÇÃO
"""

from openai import OpenAI
import os
import json
import traceback
from datetime import datetime

# ============================================
# CONFIGURAÇÃO DA OPENAI
# ============================================

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ============================================
# CONTEÚDOS ESTÁTICOS (FIXOS)
# ============================================

METODOLOGIA_FIXA = """
<div style="font-family: 'Inter', 'Times New Roman', serif; text-align: justify; line-height: 1.6;">
    <p style="margin-bottom: 12pt;"><strong>Metodologia:</strong></p>
    <p>As aulas a distância serão realizadas em videoaulas, material disponível no Ambiente Virtual de Aprendizagem (AVA), atividades de apoio para exploração e enriquecimento do conteúdo trabalhado, fóruns de discussão, atividades de sistematização, avaliações e laboratórios práticos virtuais.</p>
    <p style="margin-bottom: 8pt; margin-top: 12pt;"><strong>Recursos Didáticos:</strong></p>
    <p>Livro didático. Experimentos em laboratório virtual. Videoaula. Biblioteca virtual. Atividade de Campo/Prática. Fóruns. Estudos Dirigidos (Estudo de caso).</p>
</div>
"""

SISTEMA_AVALIACAO_FIXO = """
<div style="font-family: 'Inter', 'Times New Roman', serif; text-align: justify; line-height: 1.6;">
    <p style="margin-bottom: 12pt; text-align: justify;">
        <span style="font-weight: 700; color: #343a40;">CONCLUSÃO:</span> Aprovação com média final igual ou superior a 6,0 (seis) e frequência 
        mínima de 75% (setenta e cinco por cento) das atividades programadas.
    </p>
    
    <p style="margin-bottom: 6pt; text-align: justify;">
        <span style="font-weight: 700; color: #343a40;">SISTEMA DE AVALIAÇÃO:</span> Quatro avaliações parciais 
        (AV1, AV2, AV3, AV4) com valor de 4,0 e uma Prova Final (PF) com valor 6,0.
    </p>
    
    <p style="margin-bottom: 6pt; text-align: justify; background: #f1f3f4; padding: 8pt 12pt; border-left: 4px solid #6c757d; border-radius: 0 6px 6px 0;">
        <span style="font-weight: 700;">MÉDIA PARCIAL (MP):</span> MP = (AV1 + AV2 + AV3 + AV4) ÷ 4
    </p>
    
    <p style="margin-bottom: 6pt; text-align: justify; background: #f1f3f4; padding: 8pt 12pt; border-left: 4px solid #6c757d; border-radius: 0 6px 6px 0;">
        <span style="font-weight: 700;">MÉDIA FINAL (MF):</span> MF = (MP × 4 + PFE × 6) ÷ 10
    </p>
    
    <p style="margin-bottom: 6pt; text-align: justify; margin-top: 10pt;">
        <span style="font-weight: 700; color: #343a40;">CONCEITOS:</span>
    </p>
    <ul style="margin-left:10pt; margin-bottom:2pt; text-align:justify; list-style-type:square; color:#4d555b;">
        <li style="display:inline-block; width:48%;">&lt; 5,0 pontos → <span style="font-weight:700;">INSUFICIENTE</span></li>
        <li style="display:inline-block; width:48%;">5,0 a 6,9 pontos → <span style="font-weight:700;">REGULAR</span></li><br>
        <li style="display:inline-block; width:48%;">7,0 a 8,9 pontos → <span style="font-weight:700;">BOM</span></li>
        <li style="display:inline-block; width:48%;">9,0 a 10,0 pontos → <span style="font-weight:700;">EXCELENTE</span></li>
    </ul>
    
    <p style="margin-bottom: 6pt; text-align: justify; background: #fffaf0; padding: 8pt 12pt; border-left: 4px solid #dd6b20; border-radius: 0 6px 6px 0;">
        <span style="font-weight: 700;">AVALIAÇÃO SUPLEMENTAR:</span> Nota < 60, MF = ((Resultado Final + Nota Prova Suplementar)/2). AP: ≥ 60 pontos.
    </p>
</div>
"""

# ============================================
# FUNÇÕES DE GERAÇÃO
# ============================================

def gerar_prompt_simplificado(dados):
    """Mantém compatibilidade: usa o mesmo prompt central do api_planos.py."""
    from api_planos import gerar_prompt_simplificado as gerar_prompt_api
    return gerar_prompt_api(dados)


def consultar_openai_para_plano(dados):
    """Usa a geração central: disciplina + sugestão de ementa; restante vem da IA."""
    from api_planos import consultar_openai_para_plano as consultar_api
    return consultar_api(dados)


# ============================================
# FUNÇÃO PRINCIPAL - SÓ GERA O HTML
# ============================================

def gerar_html_plano(dados):
    """SÓ GERA O HTML DO PLANO - SEM AUTENTICAÇÃO, SEM QR CODE, SEM BANCO"""
    
    from datetime import datetime
    
    # Valida dados
    if not dados.get('disciplina') or not dados.get('ementa'):
        raise ValueError("Disciplina e ementa são obrigatórias")
    
    # Gera conteúdo via IA
    conteudo_ia = consultar_openai_para_plano(dados)
    
    data_formatada = datetime.now().strftime("%d/%m/%Y")
    
    # Extrai dados
    disciplina = dados['disciplina'].upper()
    departamento = dados.get('departamento', 'DEPARTAMENTO DE EDUCAÇÃO AMBIENTAL').upper()
    carga_horaria = dados.get('carga_horaria', '80 horas')
    modalidade = conteudo_ia.get('modalidade', 'EaD')
    pre_requisitos = conteudo_ia.get('pre_requisitos', 'Não há pré-requisitos formais para esta disciplina.')
    
    ementa_expandida = conteudo_ia['ementa_expandida']
    conteudo_programatico = conteudo_ia['conteudo_programatico']
    habilidades = conteudo_ia['habilidades']
    bibliografia_basica = conteudo_ia['bibliografia_basica']
    bibliografia_complementar = conteudo_ia['bibliografia_complementar']
    
    # Processa bibliografias
    if '<br>' not in bibliografia_basica and '\n' in bibliografia_basica:
        bibliografia_basica = bibliografia_basica.replace('\n', '<br>')
    if '<br>' not in bibliografia_complementar and '\n' in bibliografia_complementar:
        bibliografia_complementar = bibliografia_complementar.replace('\n', '<br>')
    
    # ============================================
    # TEMPLATE HTML DO PLANO (SEM CÓDIGO, SEM QR)
    # ============================================
    
    html = f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, print-scale=1">
    <title>Plano de Ensino - {disciplina} | SiGEU Educação - Facop Certificadora</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            background: #f8f9fa; 
            font-family: 'Inter', sans-serif; 
            display: flex; 
            flex-direction: column; 
            align-items: center; 
            padding: 40px 20px; 
        }}
        .page {{
            max-width: 1100px;
            width: 100%;
            background-color: #ffffff;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            border-radius: 0;
            padding: 48px 56px;
            position: relative;
            border: 1px solid #e2e8f0;
            border-top: 3px solid #5b6368;
            margin-bottom: 30px;
            break-after: page;
            page-break-after: always;
        }}
        .page:last-child {{ margin-bottom: 0; page-break-after: auto; }}
        
        .watermark {{
            position: absolute;
            top: 0; left: 0; width: 100%; height: 100%;
            pointer-events: none;
            z-index: 2;
            opacity: 0.05;
        }}
        .watermark-text {{
            position: absolute;
            font-size: 11px;
            color: #2d3337;
            bottom: 25px; right: 40px;
            padding: 4px 12px;
            border-radius: 0;
            border: 0.5px solid #cbd5e0;
            background: rgba(255,255,255,0.8);
        }}
        .page-number {{
            position: absolute;
            bottom: 20px; left: 40px;
            font-size: 10px;
            color: #4a5568;
            font-weight: 500;
            letter-spacing: 1px;
            background: #f8f9fa;
            padding: 4px 10px;
            border-radius: 0;
            border: 0.5px solid #e2e8f0;
            z-index: 10;
        }}
        
        .header-institution {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            border-bottom: 2px solid #5b6368;
            padding-bottom: 20px;
            margin-bottom: 32px;
        }}
        .logo-area {{ display: flex; align-items: center; gap: 15px; }}
        .logo-img {{ width: 70px; height: 70px; object-fit: contain; }}
        .institution-name h1 {{
            font-size: 28px;
            font-weight: 700;
            color: #343a40;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .institution-name h2 {{
            font-size: 14px;
            font-weight: 500;
            color: #4a5568;
            margin-top: 6px;
            border-left: 3px solid #6c757d;
            padding-left: 12px;
            background: #f1f3f4;
            padding: 6px 0 6px 12px;
            border-radius: 0;
        }}
        .meta-identifiers {{
            text-align: right;
            font-size: 11px;
            color: #2d3337;
            background: #f8f9fa;
            padding: 12px 16px;
            border-radius: 0;
            border: 0.5px solid #e2e8f0;
        }}
        .meta-identifiers span {{
            display: block;
            margin-top: 6px;
            background: #5b6368;
            color: #ffffff;
            padding: 4px 8px;
            border-radius: 0;
            font-family: monospace;
            font-size: 10px;
        }}
        
        .plano-title {{
            text-align: center;
            margin: 15px 0 30px;
        }}
        .plano-title h3 {{
            font-size: 32px;
            font-weight: 700;
            color: #343a40;
            letter-spacing: 4px;
            text-transform: uppercase;
            border-bottom: 2px solid #5b6368;
            display: inline-block;
            padding-bottom: 10px;
        }}
        
        .info-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20pt 0;
            border: 1pt solid #cbd5e0;
            background: white;
        }}
        .info-table th {{
            background: #eceff1;
            font-weight: 600;
            color: #343a40;
            text-align: left;
            width: 25%;
            padding: 10pt;
            border: 1pt solid #cbd5e0;
            font-size: 11pt;
            text-transform: uppercase;
        }}
        .info-table td {{
            width: 75%;
            padding: 10pt;
            border: 1pt solid #cbd5e0;
            vertical-align: top;
            text-align: justify;
            background: white;
            color: #2d3337;
            font-size: 11pt;
            line-height: 1.5;
        }}
        .info-table th[colspan="2"] {{
            background: #5b6368;
            color: white;
            text-align: center;
            font-size: 12pt;
            font-weight: 600;
        }}
        
        .ementa-topicos {{ text-align: justify; line-height: 1.7; color: #2d3337; }}
        .conteudo-programatico {{ white-space: pre-line; color: #2d3337; }}
        .conteudo-programatico strong {{ 
            font-size: 12pt; 
            color: #343a40; 
            border-bottom: 1px solid #6c757d; 
            padding-bottom: 2px;
            display: inline-block;
            margin-top: 10px;
        }}
        .conteudo-programatico strong:first-of-type {{ margin-top: 0; }}
        
        .habilidades-item {{ 
            margin-bottom: 6px; 
            text-align: justify;
            line-height: 1.5;
            color: #2d3337;
        }}
        .bibliografia-item {{ 
            margin-bottom: 8px; 
            line-height: 1.4;
            text-align: justify;
            color: #2d3337;
        }}
        
        .footer-area {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #e2e8f0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .validation-info {{
            font-size: 10px;
            color: #718096;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .hash-info {{
            font-family: monospace;
            font-size: 9px;
            color: #a0aec0;
            word-break: break-all;
            max-width: 300px;
            text-align: right;
        }}
        
        .btn {{
            display: inline-block;
            padding: 12px 28px;
            margin: 0 8px;
            background: #5b6368;
            color: white;
            text-decoration: none;
            border-radius: 0;
            font-weight: 600;
            cursor: pointer;
            font-size: 13px;
            letter-spacing: 1px;
            text-transform: uppercase;
            border: 1px solid #5b6368;
        }}
        .btn:hover {{ background: #343a40; }}
        
        @media print {{
            body {{ background: white; padding: 0; }}
            .page {{ box-shadow: none; border: 1px solid #cbd5e0; margin: 0; }}
            .btn {{ display: none; }}
            .info-table th {{ background: #eceff1 !important; -webkit-print-color-adjust: exact; }}
            .info-table th[colspan="2"] {{ background: #5b6368 !important; color: white !important; }}
        }}
    </style>
</head>
<body>
    <!-- PÁGINA 1 - IDENTIFICAÇÃO, EMENTA -->
    <div class="page">
        <div class="page-number">PÁGINA 1/4</div>
        <div class="watermark"><div class="watermark-text">PLANO DE ENSINO</div></div>
        
        <div class="header-institution">
            <div class="logo-area">
                <img src="/static/img/logo_declaracao.png" alt="Logo FACOP/SiGEU" class="logo-img" onerror="this.style.display='none'">
                <div class="institution-name">
                    <p><b>FACOP 04.344.730/0001-60 | SiGEU Educacional</b></p>
                    <h2>Faculdade do Centro Oeste Paulista Certificadora</h2>
                    <h2>SiGEu Educação - Disciplinas Isoladas</h2>
                    <h2>e-mec 19325/CP - 2025</h2>
                </div>
            </div>
            <div class="meta-identifiers">
                <div>VALIDADO POR CT EDUCACIONAL • 2026</div>
                <span>PLANO DE ENSINO</span>
            </div>
        </div>
        
        <div class="plano-title"><h3>PLANO DE ENSINO</h3></div>
        
        <table class="info-table">
            <tr><th colspan="2">1) IDENTIFICAÇÃO DA DISCIPLINA</th></tr>
            <tr><th>DISCIPLINA</th><td><strong>{disciplina}</strong></td></tr>
            <tr><th>DEPARTAMENTO</th><td>{departamento}</td></tr>
            <tr><th>Carga horária</th><td>{carga_horaria}</td></tr>
            <tr><th>Modalidade</th><td>{modalidade}</td></tr>
            <tr><th>Pré-requisitos</th><td>{pre_requisitos}</td></tr>
        </table>
        
        <table class="info-table">
            <tr><th colspan="2">2) EMENTA</th></tr>
            <tr><td colspan="2" class="ementa-topicos">{ementa_expandida}</td></tr>
        </table>
    </div>
    
    <!-- PÁGINA 2 - CONTEÚDO PROGRAMÁTICO E HABILIDADES -->
    <div class="page">
        <div class="page-number">PÁGINA 2/4</div>
        <div class="watermark"><div class="watermark-text">PLANO DE ENSINO</div></div>
        
        <div class="header-institution">
            <div class="logo-area">
                <img src="/static/img/logo_declaracao.png" alt="Logo FACOP/SiGEU" class="logo-img" onerror="this.style.display='none'">
                <div class="institution-name">
                    <p><b>FACOP 04.344.730/0001-60 | SiGEU Educacional </b></p>
                    <h2>Faculdade do Centro Oeste Paulista Certificadora</h2>
                    <h2>SiGEu Educação - Disciplinas Isoladas</h2>
                </div>
            </div>
            <div class="meta-identifiers">
                <div>VALIDADO POR CT EDUCACIONAL • 2026</div>
                <span>PLANO DE ENSINO</span>
            </div>
        </div>
        
        <div class="plano-title"><h3>PLANO DE ENSINO</h3></div>
        
        <table class="info-table">
            <tr><th colspan="2">3) CONTEÚDO PROGRAMÁTICO</th></tr>
            <tr><td colspan="2" class="conteudo-programatico">{conteudo_programatico.replace('\\n', '<br>')}</td></tr>
        </table>
        
        <table class="info-table">
            <tr><th colspan="2">4) HABILIDADES</th></tr>
            <tr><td colspan="2">
                {habilidades.replace('. ', '.<br>')}
            </td></tr>
        </table>
    </div>
    
    <!-- PÁGINA 3 - METODOLOGIA, AVALIAÇÃO -->
    <div class="page">
        <div class="page-number">PÁGINA 3/4</div>
        <div class="watermark"><div class="watermark-text">PLANO DE ENSINO</div></div>
        
        <div class="header-institution">
            <div class="logo-area">
                <img src="/static/img/logo_declaracao.png" alt="Logo FACOP/SiGEU" class="logo-img" onerror="this.style.display='none'">
                <div class="institution-name">
                    <p><b>FACOP 04.344.730/0001-60 | SiGEU Educacional </b></p>
                    <h2>Faculdade do Centro Oeste Paulista Certificadora</h2>
                    <h2>SiGEu Educação - Disciplinas Isoladas</h2>
                </div>
            </div>
            <div class="meta-identifiers">
                <div>VALIDADO POR PORTARIA MEC • 2026</div>
                <span>PLANO DE ENSINO</span>
            </div>
        </div>
        
        <div class="plano-title"><h3>PLANO DE ENSINO</h3></div>
        
        <table class="info-table">
            <tr><th colspan="2">5) METODOLOGIA</th></tr>
            <tr><td colspan="2">{METODOLOGIA_FIXA}</td></tr>
        </table>
        
        <table class="info-table">
            <tr><th colspan="2">6) SISTEMA DE AVALIAÇÃO</th></tr>
            <tr><td colspan="2">{SISTEMA_AVALIACAO_FIXO}</td></tr>
        </table>
    </div>
    
    <!-- PÁGINA 4 - BIBLIOGRAFIA -->
    <div class="page">
        <div class="page-number">PÁGINA 4/4</div>
        <div class="watermark"><div class="watermark-text">PLANO DE ENSINO</div></div>
        
        <div class="header-institution">
            <div class="logo-area">
                <img src="/static/img/logo_declaracao.png" alt="Logo FACOP/SiGEU" class="logo-img" onerror="this.style.display='none'">
                <div class="institution-name">
                    <p><b>FACOP 04.344.730/0001-60 | SiGEU Educacional </b></p>
                    <h2>Faculdade do Centro Oeste Paulista Certificadora</h2>
                    <h2>SiGEu Educação - Disciplinas Isoladas</h2>
                </div>
            </div>
            <div class="meta-identifiers">
                <div>VALIDADO POR PORTARIA MEC • 2026</div>
                <span>PLANO DE ENSINO</span>
            </div>
        </div>
        
        <table class="info-table">
            <tr><th colspan="2">7) BIBLIOGRAFIA</th></tr>
            <tr><th>Básica</th><td>
                {bibliografia_basica.replace('<br>', '<br>')}
            </td></tr>
            <tr><th>Complementar</th><td>
                {bibliografia_complementar.replace('<br>', '<br>')}
            </td></tr>
        </table>
        
        <div class="footer-area">
            <div class="validation-info">
                Documento validado digitalmente<br>
                Sistema SiGEU Educação
            </div>
            <div class="hash-info">
                GERADO POR IA • SiGEU Educação
            </div>
        </div>
    </div>
    
    <div class="botoes">
        <button onclick="window.print()" class="btn">🖨 IMPRIMIR PDF</button>
    </div>
</body>
</html>'''
    
    return html

# ============================================
# FUNÇÃO AUXILIAR PARA TESTE DIRETO
# ============================================

if __name__ == '__main__':
    # Teste rápido
    dados_teste = {
        'disciplina': 'Sistemas Hidráulicos e de Drenagem',
        'departamento': 'DEPARTAMENTO DE EDUCAÇÃO AMBIENTAL',
        'ementa': 'Introdução aos sistemas hidráulicos. Drenagem urbana. Controle de enchentes.',
        'carga_horaria': '80 horas',
        'modalidade': 'EaD'
    }
    
    try:
        html = gerar_html_plano(dados_teste)
        print("✅ Plano gerado com sucesso!")
        print(f"📄 Tamanho do HTML: {len(html)} caracteres")
        
        # Salva para teste
        with open('plano_teste.html', 'w', encoding='utf-8') as f:
            f.write(html)
        print("💾 Arquivo salvo como plano_teste.html")
        
    except Exception as e:
        print(f"❌ Erro: {e}")
        traceback.print_exc()
