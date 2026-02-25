from flask import Blueprint, request, jsonify
from openai import OpenAI
import os
import json
import random
import string
from datetime import datetime
import hashlib
from dotenv import load_dotenv
load_dotenv() 

# Configurar OpenAI (MESMA CHAVE)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Criar blueprint
planos_bp = Blueprint('planos', __name__)

# ============================================
# CONTEÚDOS ESTÁTICOS (FIXOS)
# ============================================

METODOLOGIA_FIXA = """
<div style="font-family: 'Arial', 'Times New Roman', serif; text-align: justify; line-height: 1.6;">
    <p style="margin-bottom: 12pt;"><strong>Metodologia:</strong></p>
    <p style="margin-bottom: 8pt;">As aulas a distância serão realizadas em videoaulas, material disponível no Ambiente Virtual de Aprendizagem (AVA), atividades de apoio para exploração e enriquecimento do conteúdo trabalhado, fóruns de discussão, atividades de sistematização, avaliações e laboratórios práticos virtuais.</p>
    
    <p style="margin-bottom: 8pt; margin-top: 12pt;"><strong>Recursos Didáticos:</strong></p>
    <ul style="margin-left: 20pt; margin-bottom: 10pt;">
        <li>Livro didático;</li>
        <li>Videoaula;</li>
        <li>Fóruns;</li>
        <li>Estudos Dirigidos (Estudo de caso);</li>
        <li>Experimentos em laboratório virtual;</li>
        <li>Biblioteca virtual;</li>
        <li>Atividades em campo.</li>
    </ul>
</div>
"""

SISTEMA_AVALIACAO_FIXO = """
<div style="font-family: 'Arial', 'Times New Roman', serif; text-align: justify; line-height: 1.6;">
    <p style="margin-bottom: 12pt; text-align: justify;">
        <span style="font-weight: 700; color: #1a237e;">CONCLUSÃO:</span> Aprovação com média final igual ou superior a 6,0 (seis) e frequência 
        mínima de 75% (setenta e cinco por cento) das atividades programadas.
    </p>
    
    <p style="margin-bottom: 6pt; text-align: justify;">
        <span style="font-weight: 700; color: #1a237e;">SISTEMA DE AVALIAÇÃO:</span> A disciplina contempla 4 (quatro) avaliações parciais 
        (AV1, AV2, AV3, AV4) com valor de 4,0 (quatro) pontos cada e 1 (uma) Prova Final Escrita (PFE) 
        com valor de 6,0 (seis) pontos.
    </p>
    
    <p style="margin-bottom: 6pt; text-align: justify; background: #ebf8ff; padding: 8pt 12pt; border-left: 4px solid #3182ce; border-radius: 0 6px 6px 0;">
        <span style="font-weight: 700;">MÉDIA PARCIAL (MP):</span> MP = (AV1 + AV2 + AV3 + AV4) ÷ 4
    </p>
    
    <p style="margin-bottom: 6pt; text-align: justify; background: #ebf8ff; padding: 8pt 12pt; border-left: 4px solid #3182ce; border-radius: 0 6px 6px 0;">
        <span style="font-weight: 700;">MÉDIA FINAL (MF):</span> MF = (MP × 4 + PFE × 6) ÷ 10
    </p>
    
    <p style="margin-bottom: 6pt; text-align: justify; margin-top: 10pt;">
        <span style="font-weight: 700; color: #1a237e;">CONCEITOS:</span>
    </p>
    <ul style="margin-left: 20pt; margin-bottom: 10pt; text-align: justify; list-style-type: square; color: #2c5282;">
        <li style="margin-bottom: 4pt;">< 5,0 pontos → <span style="font-weight: 700;">INSUFICIENTE</span> - Não demonstra domínio dos conteúdos</li>
        <li style="margin-bottom: 4pt;">5,0 a 6,9 pontos → <span style="font-weight: 700;">REGULAR</span> - Demonstra domínio parcial</li>
        <li style="margin-bottom: 4pt;">7,0 a 8,9 pontos → <span style="font-weight: 700;">BOM</span> - Demonstra domínio satisfatório</li>
        <li style="margin-bottom: 4pt;">9,0 a 10,0 pontos → <span style="font-weight: 700;">EXCELENTE</span> - Demonstra domínio pleno</li>
    </ul>
    
    <p style="margin-bottom: 6pt; text-align: justify;">
        <span style="font-weight: 700; color: #1a237e;">AVALIAÇÃO SUBSTITUTIVA:</span> Ofertada ao estudante que, por motivo justificado, 
        não realizou uma das avaliações parciais, substituindo integralmente a nota ausente.
    </p>
    
    <p style="margin-bottom: 6pt; text-align: justify; background: #fffaf0; padding: 8pt 12pt; border-left: 4px solid #dd6b20; border-radius: 0 6px 6px 0;">
        <span style="font-weight: 700;">AVALIAÇÃO SUPLEMENTAR:</span> Caso o aluno não alcance no mínimo 60% da pontuação distribuída, 
        haverá a Avaliação Suplementar com todo o conteúdo da disciplina. 
        Média final = (Resultado Final + Nota Prova Suplementar) / 2. Aprovação ≥ 60 pontos.
    </p>
</div>
"""

# ============================================
# FUNÇÕES AUXILIARES
# ============================================

def gerar_codigo_autenticacao():
    """Gera código único de autenticação"""
    data = datetime.now().strftime("%Y%m%d")
    random_num = ''.join(random.choices(string.digits, k=6))
    return f"FACOP/SiGEU-{data}-{random_num}"

def gerar_hash_completa(codigo, data):
    """Gera hash SHA-256"""
    conteudo_hash = f"{codigo}:{data}:facop:sigeu:2026"
    hash_obj = hashlib.sha256(conteudo_hash.encode())
    return hash_obj.hexdigest().upper()

# ============================================
# PROMPT SIMPLIFICADO (SEM FALLBACK)
# ============================================
def gerar_prompt_simplificado(dados):
    """Gera prompt para a IA gerar APENAS os campos necessários - VERSÃO CORRIGIDA"""
    
    prompt = f"""
VOCÊ É UM ESPECIALISTA EM PLANOS DE ENSINO DA FACOP/SiGEU.

## DADOS DA DISCIPLINA
- **Disciplina**: {dados['disciplina']}
- **Curso**: {dados.get('curso', 'ENGENHARIA AMBIENTAL E SANITÁRIA')}
- **Ementa Base**: {dados['ementa']}
- **Carga Horária**: {dados.get('carga_horaria', '80H')}

## INSTRUÇÕES ESPECÍFICAS - CUMPRA EXATAMENTE

### 1. OBJETIVO GERAL (1 parágrafo curto)
Descreva o objetivo geral da disciplina em 1 parágrafo objetivo, focado no que o estudante será capaz de fazer ao final do curso.

### 2. OBJETIVOS ESPECÍFICOS (EXATAMENTE 5 itens numerados)
Liste 5 objetivos específicos que o estudante deve alcançar, numerados de 1 a 5.
Formato: "1. Primeiro objetivo. 2. Segundo objetivo. 3. Terceiro objetivo. 4. Quarto objetivo. 5. Quinto objetivo."
Cada objetivo deve começar com verbo no infinitivo.

### 3. EMENTA (EXATAMENTE 20 itens numerados)
Crie uma ementa expandida com EXATAMENTE 20 itens numerados (1. ao 20.).
Formato OBRIGATÓRIO: "1. Primeiro tópico. 2. Segundo tópico. 3. Terceiro tópico. ... 20. Vigésimo tópico."
Cada tópico deve ser uma frase curta e objetiva sobre um conteúdo específico da disciplina.
NÃO use ponto e vírgula. NÃO use citações. Apenas os 20 tópicos numerados separados por ". ".

### 4. CONTEÚDO PROGRAMÁTICO (4 unidades com quantidades EXATAS)
Crie 4 unidades com a seguinte estrutura EXATA:

UNIDADE I – [TÍTULO DA UNIDADE]
• Subtópico 1
• Subtópico 2
• Subtópico 3
• Subtópico 4
• Subtópico 5
• Subtópico 6

UNIDADE II – [TÍTULO DA UNIDADE]
• Subtópico 1
• Subtópico 2
• Subtópico 3
• Subtópico 4
• Subtópico 5
• Subtópico 6

UNIDADE III – [TÍTULO DA UNIDADE]
• Subtópico 1
• Subtópico 2
• Subtópico 3
• Subtópico 4
• Subtópico 5

UNIDADE IV – [TÍTULO DA UNIDADE]
• Subtópico 1
• Subtópico 2
• Subtópico 3
• Subtópico 4
• Subtópico 5

TOTAL EXATO: 22 subtópicos (6+6+5+5). NÃO altere as quantidades.

IMPORTANTE: Use o símbolo • (bullet point) antes de cada subtópico. Use quebras de linha reais entre os itens.

### 5. HABILIDADES (10 a 14 itens em numeração romana)
Liste habilidades específicas que o aluno desenvolverá na disciplina.
Formato OBRIGATÓRIO: "I - primeira habilidade. II - segunda habilidade. III - terceira habilidade." (numerais romanos seguidos de hífen)
Mínimo 10, máximo 14 habilidades.

### 6. BIBLIOGRAFIA (FORMATO ABNT)

**Básica** (EXATAMENTE 5 obras):
- Livros REAIS de editoras reconhecidas (EXISTENTES)
- Pelo menos 1 obra em inglês
Formato EXATO: SOBRENOME, Nome. <strong>Título</strong>. Edição. Cidade: Editora, ano.
Separe cada obra com <br>

**Complementar** (EXATAMENTE 3 obras):
- Livros REAIS de editoras reconhecidas (EXISTENTES)
- Pelo menos 1 obra em inglês
Formato EXATO: SOBRENOME, Nome. <strong>Título</strong>. Edição. Cidade: Editora, ano.
Separe cada obra com <br>

## FORMATO DE SAÍDA (JSON EXATO)
{{
    "objetivo_geral": "Texto do objetivo geral da disciplina...",
    "objetivos_especificos": "1. Primeiro objetivo. 2. Segundo objetivo. 3. Terceiro objetivo. 4. Quarto objetivo. 5. Quinto objetivo.",
    "ementa_expandida": "1. Tópico 1. 2. Tópico 2. 3. Tópico 3. ... 20. Tópico 20.",
    "conteudo_programatico": "UNIDADE I – Título\\n• Subtópico 1\\n• Subtópico 2\\n• Subtópico 3\\n• Subtópico 4\\n• Subtópico 5\\n• Subtópico 6\\n\\nUNIDADE II – Título\\n• Subtópico 1\\n• Subtópico 2\\n• Subtópico 3\\n• Subtópico 4\\n• Subtópico 5\\n• Subtópico 6\\n\\nUNIDADE III – Título\\n• Subtópico 1\\n• Subtópico 2\\n• Subtópico 3\\n• Subtópico 4\\n• Subtópico 5\\n\\nUNIDADE IV – Título\\n• Subtópico 1\\n• Subtópico 2\\n• Subtópico 3\\n• Subtópico 4\\n• Subtópico 5",
    "habilidades": "I - Primeira habilidade. II - Segunda habilidade. III - Terceira habilidade. IV - Quarta habilidade. V - Quinta habilidade. VI - Sexta habilidade. VII - Sétima habilidade. VIII - Oitava habilidade. IX - Nona habilidade. X - Décima habilidade.",
    "bibliografia_basica": "SOBRENOME, Nome. <strong>Título</strong>. Ed. Cidade: Editora, ano.<br>SOBRENOME, Nome. <strong>Título</strong>. Ed. Cidade: Editora, ano.<br>SOBRENOME, Nome. <strong>Título</strong>. Ed. Cidade: Editora, ano.<br>SOBRENOME, Nome. <strong>Título</strong>. Ed. Cidade: Editora, ano.<br>SOBRENOME, Nome. <strong>Título</strong>. Ed. Cidade: Editora, ano.",
    "bibliografia_complementar": "SOBRENOME, Nome. <strong>Título</strong>. Ed. Cidade: Editora, ano.<br>SOBRENOME, Nome. <strong>Título</strong>. Ed. Cidade: Editora, ano.<br>SOBRENOME, Nome. <strong>Título</strong>. Ed. Cidade: Editora, ano."
}}

DISCIPLINA: {dados['disciplina']}
CURSO: {dados.get('curso', 'ENGENHARIA AMBIENTAL E SANITÁRIA')}
EMENTA BASE: {dados['ementa']}

GERAR JSON AGORA. NÃO INCLUA TEXTO ANTES OU DEPOIS DO JSON. USE ESTRITAMENTE O FORMATO ACIMA.
"""
    return prompt

# ============================================
# FUNÇÃO PRINCIPAL - CONSULTAR OPENAI (SEM FALLBACK)
# ============================================
def consultar_openai_para_plano(dados):
    """Consulta o ChatGPT para gerar os campos necessários - SEM FALLBACK"""
    
    # DEBUG: Mostrar enquadramento recebido
    print(f"📚 Enquadramento recebido: {dados.get('enquadramento_curricular', 'VAZIO')}")
    
    prompt = gerar_prompt_simplificado(dados)
    
    print(f"\n📘 Gerando plano para: {dados['disciplina']}")
    print("⏳ Consultando OpenAI...\n")
    
    response = client.chat.completions.create(
        model="gpt-4-1106-preview",
        messages=[
            {
                "role": "system",
                "content": "Você é um especialista em planos de ensino da FACOP/SiGEU. Retorne APENAS JSON válido com os campos solicitados. NÃO inclua markdown, NÃO inclua texto explicativo, APENAS o JSON puro."
            },
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        max_tokens=4000,
        response_format={"type": "json_object"}
    )
    
    conteudo = response.choices[0].message.content
    
    # Parse do JSON
    try:
        plano_json = json.loads(conteudo)
    except json.JSONDecodeError as e:
        raise Exception(f"Erro ao decodificar JSON da OpenAI: {str(e)}. Resposta: {conteudo[:200]}")
    
    # VALIDAÇÃO RIGOROSA - TODOS OS CAMPOS SÃO OBRIGATÓRIOS
    campos_obrigatorios = [
        'objetivo_geral',
        'objetivos_especificos',
        'ementa_expandida', 
        'conteudo_programatico', 
        'habilidades', 
        'bibliografia_basica', 
        'bibliografia_complementar'
    ]
    
    erros = []
    for campo in campos_obrigatorios:
        if campo not in plano_json:
            # Tentar encontrar campo similar (case insensitive)
            campo_encontrado = None
            for chave in plano_json.keys():
                if chave.lower() == campo.lower():
                    campo_encontrado = chave
                    plano_json[campo] = plano_json[chave]
                    break
            
            if not campo_encontrado:
                erros.append(f"Campo '{campo}' ausente no JSON retornado. Chaves disponíveis: {list(plano_json.keys())}")
        elif not plano_json[campo] or plano_json[campo].strip() == "":
            erros.append(f"Campo '{campo}' está vazio")
        elif campo == 'ementa_expandida' and len(plano_json[campo].split('. ')) < 18:
            erros.append(f"Ementa com menos de 20 itens: {len(plano_json[campo].split('. '))} itens encontrados")
    
    if erros:
        raise Exception("Erros de validação nos dados da IA:\n" + "\n".join(erros))
    
    return plano_json

# ============================================
# ROTAS DA API (para integração)
# ============================================

@planos_bp.route('/gerar-conteudo-plano', methods=['POST'])
def gerar_conteudo_plano():
    """Gera apenas o conteúdo do plano (sem HTML) para integração com o sistema principal"""
    try:
        dados = request.json
        if not dados.get('disciplina') or not dados.get('ementa'):
            return jsonify({'error': 'Disciplina e ementa obrigatórias'}), 400
        
        conteudo_ia = consultar_openai_para_plano(dados)
        
        return jsonify({
            'success': True,
            'conteudo': conteudo_ia
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Exportar funções para uso no app.py
__all__ = ['planos_bp', 'consultar_openai_para_plano', 'METODOLOGIA_FIXA', 'SISTEMA_AVALIACAO_FIXO', 'gerar_codigo_autenticacao', 'gerar_hash_completa']