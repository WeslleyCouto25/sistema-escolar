#!/usr/bin/env python3
"""
API para Geração de Planos de Ensino com IA - FACOP/SiGEU
VERSÃO INTEGRADA COM O SISTEMA PRINCIPAL
"""

from flask import Blueprint, request, jsonify
from openai import OpenAI
import os
import json
import random
import string
from datetime import datetime
import hashlib

# Configurar OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Criar blueprint
planos_bp = Blueprint('planos', __name__)

# ============================================
# FUNÇÕES AUXILIARES
# ============================================

def gerar_distribuicao_unidades():
    """
    Gera distribuição ALEATÓRIA de subtópicos por unidade (5 a 8 por unidade)
    Total mínimo: 20 subtópicos, máximo: 30 subtópicos
    """
    u1 = random.randint(5, 8)
    u2 = random.randint(5, 8)
    u3 = random.randint(5, 8)
    u4 = random.randint(5, 8)
    
    while u1 + u2 + u3 + u4 < 20 or u1 + u2 + u3 + u4 > 30:
        u1 = random.randint(5, 8)
        u2 = random.randint(5, 8)
        u3 = random.randint(5, 8)
        u4 = random.randint(5, 8)
    
    return [u1, u2, u3, u4]

def formatar_criterios_avaliacao(modalidade, tem_relatorio=False, tem_ficha=False):
    """Formata os critérios de avaliação"""
    html = """
    <div style="font-family: 'Inter', 'Times New Roman', serif; text-align: justify; line-height: 1.6;">
        <p style="margin-bottom: 12pt; text-align: justify;">
            <span style="font-weight: 700; color: #0a3b2a;">CONCLUSÃO:</span> Aprovação com média final igual ou superior a 6,0 (seis) e frequência 
            mínima de 75% (setenta e cinco por cento) das atividades programadas.
        </p>
        
        <p style="margin-bottom: 6pt; text-align: justify;">
            <span style="font-weight: 700; color: #0a3b2a;">SISTEMA DE AVALIAÇÃO:</span> A disciplina contempla 4 (quatro) avaliações parciais 
            (AV1, AV2, AV3, AV4) com valor de 4,0 (quatro) pontos cada e 1 (uma) Prova Final Escrita (PFE) 
            com valor de 6,0 (seis) pontos.
        </p>
        
        <p style="margin-bottom: 6pt; text-align: justify; background: #ecf7f0; padding: 8pt 12pt; border-left: 4px solid #267a4e; border-radius: 0 6px 6px 0;">
            <span style="font-weight: 700;">MÉDIA PARCIAL (MP):</span> MP = (AV1 + AV2 + AV3 + AV4) ÷ 4
        </p>
        
        <p style="margin-bottom: 6pt; text-align: justify; background: #ecf7f0; padding: 8pt 12pt; border-left: 4px solid #267a4e; border-radius: 0 6px 6px 0;">
            <span style="font-weight: 700;">MÉDIA FINAL (MF):</span> MF = (MP × 4 + PFE × 6) ÷ 10
        </p>
    """
    
    if tem_relatorio:
        html += """
        <p style="margin-bottom: 6pt; text-align: justify;">
            <span style="font-weight: 700; color: #0a3b2a;">RELATÓRIO CIENTÍFICO:</span> Compõe 40% da nota das avaliações parciais. O relatório 
            segue as normas ABNT e contempla: introdução, fundamentação teórica, metodologia, resultados, 
            discussão e considerações finais.
        </p>
        """
    
    if tem_ficha:
        html += """
        <p style="margin-bottom: 6pt; text-align: justify;">
            <span style="font-weight: 700; color: #0a3b2a;">FICHA DE ACOMPANHAMENTO:</span> Instrumento de avaliação processual para atividades 
            práticas, considerando: cumprimento de protocolos (30%), manuseio de equipamentos (20%), 
            registro e análise de dados (30%) e postura profissional (20%).
        </p>
        """
    
    html += """
        <p style="margin-bottom: 6pt; text-align: justify; margin-top: 10pt;">
            <span style="font-weight: 700; color: #0a3b2a;">CONCEITOS:</span>
        </p>
        <ul style="margin-left: 20pt; margin-bottom: 10pt; text-align: justify; list-style-type: square; color: #1f4e3c;">
            <li style="margin-bottom: 4pt;">< 5,0 pontos → <span style="font-weight: 700;">INSUFICIENTE</span> - Não demonstra domínio dos conteúdos</li>
            <li style="margin-bottom: 4pt;">5,0 a 6,9 pontos → <span style="font-weight: 700;">REGULAR</span> - Demonstra domínio parcial</li>
            <li style="margin-bottom: 4pt;">7,0 a 8,9 pontos → <span style="font-weight: 700;">BOM</span> - Demonstra domínio satisfatório</li>
            <li style="margin-bottom: 4pt;">9,0 a 10,0 pontos → <span style="font-weight: 700;">EXCELENTE</span> - Demonstra domínio pleno</li>
        </ul>
        
        <p style="margin-bottom: 6pt; text-align: justify;">
            <span style="font-weight: 700; color: #0a3b2a;">AVALIAÇÃO SUBSTITUTIVA:</span> Ofertada ao estudante que, por motivo justificado, 
            não realizou uma das avaliações parciais, substituindo integralmente a nota ausente.
        </p>
    </div>
    """
    
    return html

def gerar_prompt_completo(dados):
    """Gera o prompt com TODAS as especificações"""
    
    modalidade = dados.get('modalidade', 'EaD')
    tem_relatorio = dados.get('relatorio_cientifico', False)
    tem_ficha = dados.get('ficha_acompanhamento', False)
    dias_semana = dados.get('dias_semana', 'terças e quintas-feiras')
    horario_inicio = dados.get('horario_inicio', '19h00')
    horario_fim = dados.get('horario_fim', '21h30')
    pre_requisitos = dados.get('pre_requisitos', 'Não há pré-requisitos formais para esta disciplina.')
    
    distribuicao = gerar_distribuicao_unidades()
    total_subtopicos = sum(distribuicao)
    
    prompt = f"""
VOCÊ É O PROFESSOR DOUTOR DA FACOP/SiGEU, ESPECIALISTA EM PLANEJAMENTO EDUCACIONAL COM 30 ANOS DE EXPERIÊNCIA.

## DADOS DA DISCIPLINA
- **Disciplina**: {dados['disciplina']}
- **Ementa Básica (tópicos a serem expandidos)**: {dados['ementa']}
- **Carga Horária**: {dados.get('carga_horaria', '120 horas')}
- **Modalidade**: {modalidade}
- **Pré-requisitos**: {pre_requisitos}

============================================================================
## REGRAS ABSOLUTAS - NUNCA VIOLAR
============================================================================

### [REGRIA 01] EMENTA - FORMATO DE TÓPICOS NUMERADOS
A ementa DEVE ser apresentada EXCLUSIVAMENTE no seguinte formato:

"1. Título do primeiro tópico. 2. Título do segundo tópico. 3. Título do terceiro tópico. ... (até 20-30 tópicos)"

REGRAS OBRIGATÓRIAS:
1. Mínimo de 20 (VINTE) tópicos numerados
2. Máximo de 30 (TRINTA) tópicos numerados
3. NÚMERO ATUAL DE TÓPICOS A GERAR: {total_subtopicos + random.randint(16, 22)} (entre 20-30)
4. Cada tópico deve ser uma frase curta, direta, representando um conteúdo específico
5. NÃO usar ponto e vírgula dentro dos tópicos
6. NÃO usar citações (Autor, Ano)
7. TODOS os tópicos devem estar separados por ". " (ponto e espaço)
8. A ementa completa deve ser UMA ÚNICA STRING com todos os tópicos numerados sequencialmente

### [REGRIA 02] CONTEÚDO PROGRAMÁTICO - 4 UNIDADES COM DISTRIBUIÇÃO ALEATÓRIA
CRIE EXATAMENTE 4 UNIDADES com a seguinte distribuição de subtópicos:

<b>UNIDADE I – [NOME DA UNIDADE]</b>: EXATAMENTE {distribuicao[0]} subtópicos
<b>UNIDADE II – [NOME DA UNIDADE]</b>: EXATAMENTE {distribuicao[1]} subtópicos
<b>UNIDADE III – [NOME DA UNIDADE]</b>: EXATAMENTE {distribuicao[2]} subtópicos
<b>UNIDADE IV – [NOME DA UNIDADE]</b>: EXATAMENTE {distribuicao[3]} subtópicos

IMPORTANTE: Não utilize a palavra "Subtópico" antes dos itens. Liste apenas os conteúdos diretamente.
FORMATO EXATO:
<b>UNIDADE I – Título da Unidade</b>
• Primeiro conteúdo
• Segundo conteúdo
• Terceiro conteúdo
(assim por diante)

### [REGRIA 03] METODOLOGIA - TÉCNICA, DIRETA, 300 PALAVRAS EXATAS
"""
    if modalidade == 'Presencial':
        prompt += "METODOLOGIA PRESENCIAL (300 palavras):\n\n1. Aulas expositivas dialogadas..."
    elif modalidade == 'EaD':
        prompt += f"METODOLOGIA EAD (300 palavras):\n\n1. Ambiente Virtual de Aprendizagem..."
    elif modalidade == 'Híbrido':
        prompt += "METODOLOGIA HÍBRIDA (300 palavras):\n\nComponente presencial (40%)..."

    prompt += f"""

### [REGRIA 04] SISTEMA DE AVALIAÇÃO - FÓRMULAS EXATAS
**Estrutura**: 4 avaliações parciais (AV1, AV2, AV3, AV4) = 4,0 pontos cada | Prova Final Escrita (PFE) = 6,0 pontos
**Média Parcial**: MP = (AV1 + AV2 + AV3 + AV4) ÷ 4
**Média Final**: MF = (MP × 4 + PFE × 6) ÷ 10

### [REGRIA 05] BIBLIOGRAFIA - FORMATO ABNT RIGOROSO COM TÍTULOS EM NEGRITO
**Básica** (5 obras OBRIGATÓRIAS):
- Devem ser livros reais, publicados por editoras reconhecidas.
- Podem estar em português ou inglês.
- OBRIGATÓRIO: pelo menos 1 (uma) obra em inglês.
- Não inventar títulos fictícios.
Formato:
SOBRENOME, Nome. <strong>Título</strong>. Edição. Cidade: Editora, ano.

**Complementar** (3 obras OBRIGATÓRIAS):
- Devem ser obras reais e verificáveis.
- Podem estar em português ou inglês.
- OBRIGATÓRIO: pelo menos 1 (uma) obra em inglês.
- Não inventar títulos fictícios.
Formato:
SOBRENOME, Nome. <strong>Título</strong>. Edição. Cidade: Editora, ano.


### [REGRIA 06] OBJETIVOS ESPECÍFICOS - FORMATO SEM MARCADORES
Liste os objetivos específicos em HTML, separados por <br>, SEM usar <ul> ou <li>.
Exemplo: "Compreender os fundamentos teóricos.<br>Aplicar metodologias ativas.<br>Analisar casos práticos."

============================================================================
## FORMATO DE SAÍDA - JSON EXATO
============================================================================

{{
    "ementa_expandida": "1. Tópico 1. 2. Tópico 2. 3. Tópico 3. ...",
    "objetivo_geral": "...",
    "objetivos_especificos": "Objetivo 1.<br>Objetivo 2.<br>Objetivo 3.",
    "conteudo_programatico": "<b>UNIDADE I – Título</b>\\n• Conteúdo 1\\n• Conteúdo 2\\n\\n<b>UNIDADE II – Título</b>\\n• Conteúdo 1\\n• Conteúdo 2\\n\\n...",
    "metodologia": "...",
    "criterios_aprovacao": "...",
    "bibliografia_basica": "AUTOR, Nome. <strong>Título</strong>. Ed. Cidade: Editora, ano.<br>...",
    "bibliografia_complementar": "AUTOR, Nome. <strong>Título</strong>. Ed. Cidade: Editora, ano.<br>...",
    "encontros_sincronos": "...",
    "plataforma": "...",
    "pre_requisitos_formatado": "{pre_requisitos}"
}}

============================================================================
DISCIPLINA: {dados['disciplina']}
MODALIDADE: {modalidade}
GERAR PLANO DE ENSINO COMPLETO AGORA.
"""
    return prompt

def consultar_openai_para_plano(dados):
    """Consulta o ChatGPT com o prompt completo e trata a resposta"""
    try:
        # Verificar se a API key existe
        if not os.getenv("OPENAI_API_KEY"):
            raise Exception("OPENAI_API_KEY não configurada no ambiente")
        
        prompt = gerar_prompt_completo(dados)
        print(f"📘 Gerando plano para disciplina: {dados['disciplina']}")
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Use modelo mais estável
            messages=[
                {"role": "system", "content": "Você é um professor doutor da FACOP/SiGEU. Gere planos de ensino seguindo TODAS as regras. RETORNE APENAS JSON VÁLIDO, SEM TEXTOS EXTRAS."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=3500,
            timeout=30
        )
        
        conteudo = response.choices[0].message.content
        
        # Extrair JSON
        inicio_json = conteudo.find('{')
        fim_json = conteudo.rfind('}') + 1
        
        if inicio_json == -1 or fim_json == 0:
            raise Exception("Resposta da API não contém JSON válido")
        
        json_str = conteudo[inicio_json:fim_json]
        plano_json = json.loads(json_str)
        
        # Resto do código...
        return plano_json
        
    except Exception as e:
        print(f"❌ ERRO em consultar_openai_para_plano: {e}")
        raise Exception(f"Erro ao gerar plano: {str(e)}")

# ============================================
# ROTAS DA API (mantidas para compatibilidade, mas não usadas internamente)
# ============================================

@planos_bp.route('/gerar-conteudo-plano', methods=['POST'])
def gerar_conteudo_plano():
    """Gera apenas o conteúdo do plano (sem HTML) para integração com o sistema principal"""
    try:
        dados = request.json
        if not dados.get('disciplina') or not dados.get('ementa'):
            return jsonify({'error': 'Disciplina e ementa obrigatórias'}), 400
        
        conteudo_ia = consultar_openai_para_plano(dados)
        
        # Formatar objetivos específicos
        if 'objetivos_especificos' in conteudo_ia:
            conteudo_ia['objetivos_especificos'] = conteudo_ia['objetivos_especificos'].replace('<ul>', '').replace('</ul>', '').replace('<li>', '').replace('</li>', '<br>')
        
        return jsonify({
            'success': True,
            'conteudo': conteudo_ia
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ✅ CORREÇÃO: Adicionar consultar_openai_para_plano ao __all__ para permitir importação direta
__all__ = ['planos_bp', 'consultar_openai_para_plano']