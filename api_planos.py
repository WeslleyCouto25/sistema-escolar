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
    return f"FCP-CTF/SiGEU-educ-{data}-{random_num}"

def gerar_hash_completa(codigo, data):
    """Gera hash SHA-256"""
    conteudo_hash = f"{codigo}:{data}:facop:sigeu:2026"
    hash_obj = hashlib.sha256(conteudo_hash.encode())
    return hash_obj.hexdigest().upper()

# ============================================
# GERAÇÃO 100% AUTOMÁTICA DO CONTEÚDO VARIÁVEL
# O administrador informa apenas a disciplina e uma sugestão de ementa.
# Carga horária/docente vêm do banco; metodologia/avaliação são institucionais.
# Todo o restante abaixo é gerado pela IA.
# ============================================

ROMANOS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV"]


def sortear_estrutura_plano(numero_unidades=None):
    """Define a estrutura antes da IA: 6-8 unidades e 8-10 tópicos por unidade."""
    try:
        n = int(numero_unidades) if numero_unidades not in (None, "", 0, "0") else 0
    except Exception:
        n = 0
    if not 6 <= n <= 8:
        n = random.randint(6, 8)
    return n, [random.randint(8, 10) for _ in range(n)]


def _get_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY não configurada no ambiente.")
    return OpenAI(api_key=api_key)


def _modelo_planos():
    # Pode ser sobrescrito no Render sem alterar código.
    return os.getenv("OPENAI_PLANOS_MODEL", "gpt-5.6-terra")


def _json_da_resposta(texto):
    texto = (texto or "").strip()
    if texto.startswith("```"):
        texto = texto.strip("`").strip()
        if texto.lower().startswith("json"):
            texto = texto[4:].strip()
    inicio = texto.find("{")
    fim = texto.rfind("}")
    if inicio >= 0 and fim > inicio:
        texto = texto[inicio:fim + 1]
    return json.loads(texto)


def _lista(valor):
    if isinstance(valor, list):
        return [str(x).strip() for x in valor if str(x).strip()]
    if isinstance(valor, str):
        linhas = [x.strip(" -•\t") for x in valor.splitlines() if x.strip()]
        return linhas
    return []


def _formatar_objetivos(itens):
    return " ".join(f"{i}. {texto.rstrip('.')} .".replace(" .", ".") for i, texto in enumerate(itens, 1))


def _formatar_ementa(itens):
    return " ".join(f"{i}. {texto.rstrip('.')} .".replace(" .", ".") for i, texto in enumerate(itens, 1))


def _formatar_habilidades(itens):
    partes = []
    for i, texto in enumerate(itens):
        romano = ROMANOS[i] if i < len(ROMANOS) else str(i + 1)
        partes.append(f"{romano} - {texto.rstrip('.')}.")
    return " ".join(partes)


def _formatar_conteudo(unidades):
    if isinstance(unidades, str):
        return unidades.strip()
    if not isinstance(unidades, list):
        return ""
    blocos = []
    for idx, unidade in enumerate(unidades, 1):
        if not isinstance(unidade, dict):
            continue
        titulo = str(unidade.get("titulo") or f"UNIDADE {idx}").strip()
        itens = _lista(unidade.get("topicos"))
        blocos.append(titulo + "\n" + "\n".join(f"• {x.rstrip('.')}" for x in itens))
    return "\n\n".join(blocos)


def _validar_e_normalizar(plano, numero_unidades=6, topicos_por_unidade_alvo=None):
    """Valida a resposta da IA sem prender o plano a quatro unidades.

    O gerador trabalha com 6 a 8 unidades. Todas cabem na página 2,
    o conteúdo de cada unidade é deliberadamente compacto.
    """
    if not isinstance(plano, dict):
        raise ValueError("A IA não retornou um objeto JSON válido.")

    try:
        numero_unidades = max(6, min(8, int(numero_unidades or 6)))
    except Exception:
        numero_unidades = 6
    alvos = list(topicos_por_unidade_alvo or [])
    if len(alvos) != numero_unidades:
        alvos = [8] * numero_unidades

    objetivo_geral = str(plano.get("objetivo_geral") or "").strip()
    objetivos = _lista(plano.get("objetivos_especificos"))
    ementa = _lista(plano.get("ementa_expandida"))
    unidades = plano.get("conteudo_programatico")
    habilidades = _lista(plano.get("habilidades"))
    basica = _lista(plano.get("bibliografia_basica"))
    complementar = _lista(plano.get("bibliografia_complementar"))
    pre_requisitos = str(plano.get("pre_requisitos") or "Não há pré-requisitos formais para esta disciplina.").strip()
    modalidade = str(plano.get("modalidade") or "EaD").strip()

    erros = []
    if not objetivo_geral:
        erros.append("objetivo_geral vazio")
    if len(objetivos) != 5:
        erros.append(f"objetivos_especificos deve ter 5 itens (recebeu {len(objetivos)})")
    if len(ementa) != 20:
        erros.append(f"ementa_expandida deve ter 20 itens (recebeu {len(ementa)})")
    if not isinstance(unidades, list) or len(unidades) != numero_unidades:
        erros.append(f"conteudo_programatico deve ter {numero_unidades} unidades")
    else:
        for i, unidade in enumerate(unidades, 1):
            topicos = _lista(unidade.get("topicos") if isinstance(unidade, dict) else None)
            alvo = int(alvos[i - 1])
            if len(topicos) != alvo:
                erros.append(f"unidade {i} deve ter exatamente {alvo} tópicos (recebeu {len(topicos)})")
    if not 8 <= len(habilidades) <= 12:
        erros.append(f"habilidades deve ter de 8 a 12 itens (recebeu {len(habilidades)})")
    if len(basica) != 5:
        erros.append(f"bibliografia_basica deve ter 5 obras (recebeu {len(basica)})")
    if len(complementar) != 3:
        erros.append(f"bibliografia_complementar deve ter 3 obras (recebeu {len(complementar)})")

    if erros:
        raise ValueError("; ".join(erros))

    unidades_normalizadas = []
    for idx, unidade in enumerate(unidades, 1):
        titulo = str((unidade or {}).get("titulo") or f"UNIDADE {idx}").strip()
        topicos = _lista((unidade or {}).get("topicos"))
        unidades_normalizadas.append({"titulo": titulo, "topicos": topicos})

    return {
        "objetivo_geral": objetivo_geral,
        "objetivos_especificos": _formatar_objetivos(objetivos),
        "ementa_expandida": _formatar_ementa(ementa),
        "conteudo_programatico": _formatar_conteudo(unidades_normalizadas),
        "conteudo_programatico_estruturado": unidades_normalizadas,
        "numero_unidades": numero_unidades,
        "habilidades": _formatar_habilidades(habilidades),
        "bibliografia_basica": "<br>".join(basica),
        "bibliografia_complementar": "<br>".join(complementar),
        "pre_requisitos": pre_requisitos,
        "modalidade": modalidade,
    }


def gerar_prompt_simplificado(dados, correcao=""):
    disciplina = str(dados.get("disciplina") or "").strip()
    ementa_base = str(dados.get("ementa") or "").strip()
    carga = str(dados.get("carga_horaria") or "80 horas").strip()
    try:
        numero_unidades = max(6, min(8, int(dados.get("numero_unidades") or 6)))
    except Exception:
        numero_unidades = 6
    alvos = list(dados.get("topicos_por_unidade_alvo") or [])
    if len(alvos) != numero_unidades:
        alvos = [8] * numero_unidades
    regra_topicos = "; ".join(f"Unidade {i+1}: {alvos[i]} tópicos" for i in range(numero_unidades))

    return f"""
Você é especialista brasileiro em elaboração de planos de ensino de educação superior.
Crie o conteúdo pedagógico de um Plano de Ensino a partir SOMENTE do título da disciplina,
da sugestão de ementa fornecida pelo administrador e da carga horária já cadastrada no sistema.

DADOS FORNECIDOS:
- Disciplina: {disciplina}
- Sugestão de ementa: {ementa_base}
- Carga horária: {carga}
- Quantidade EXATA de unidades: {numero_unidades}
- Quantidade EXATA de tópicos por unidade: {regra_topicos}

REGRAS OBRIGATÓRIAS:
1. Preserve o sentido da sugestão de ementa, mas desenvolva-a tecnicamente.
2. Gere 1 objetivo geral.
3. Gere EXATAMENTE 5 objetivos específicos, iniciados por verbos no infinitivo.
4. Gere EXATAMENTE 20 tópicos de ementa expandida, sem citações.
5. Gere EXATAMENTE {numero_unidades} unidades de conteúdo programático.
6. Respeite EXATAMENTE esta distribuição de tópicos: {regra_topicos}. Cada tópico deve ser curto, específico e ter no máximo 8 palavras, de modo a caber no quadro compacto da página 2.
7. Os títulos das unidades devem ser curtos. Use “UNIDADE 1 — ...”, “UNIDADE 2 — ...” e assim sucessivamente.
8. Gere de 8 a 12 habilidades coerentes com a disciplina.
9. Gere pré-requisitos acadêmicos realistas. Se não forem necessários, escreva explicitamente que não há pré-requisitos formais.
10. A modalidade deve ser "EaD", salvo se o próprio título/ementa tornar outra modalidade indispensável.
11. Gere bibliografia básica com EXATAMENTE 5 obras e complementar com EXATAMENTE 3 obras.
12. As referências devem corresponder a LIVROS/OBRAS REAIS, publicados e reconhecíveis, adequados ao tema e ao contexto brasileiro de ensino superior.
13. NÃO invente autor, título, editora, edição ou ano. Prefira obras clássicas e consolidadas que você conheça com alta confiança.
14. Formate cada referência em padrão ABNT aproximado: SOBRENOME, Nome. Título. edição quando conhecida. Cidade: Editora, ano.
15. Não inclua ISBN, DOI ou URL se não houver segurança absoluta.
16. Não inclua Markdown, comentários ou explicações fora do JSON.

RETORNE EXATAMENTE UM JSON COM ESTA ESTRUTURA (a lista conteudo_programatico deve possuir exatamente {numero_unidades} objetos):
{{
  "objetivo_geral": "texto",
  "objetivos_especificos": ["item 1", "item 2", "item 3", "item 4", "item 5"],
  "ementa_expandida": ["tópico 1", "tópico 2", "... até 20"],
  "conteudo_programatico": [
    {{"titulo": "UNIDADE 1 — TÍTULO", "topicos": ["tópico 1", "... quantidade exata definida acima"]}}
  ],
  "habilidades": ["habilidade 1", "..."],
  "pre_requisitos": "texto",
  "modalidade": "EaD",
  "bibliografia_basica": ["referência 1", "referência 2", "referência 3", "referência 4", "referência 5"],
  "bibliografia_complementar": ["referência 1", "referência 2", "referência 3"]
}}

{('CORRIJA A RESPOSTA ANTERIOR: ' + correcao) if correcao else ''}
""".strip()


def consultar_openai_para_plano(dados):
    """Gera o conteúdo variável já com a estrutura sorteada antes da chamada à IA."""
    if not (dados.get("disciplina") and dados.get("ementa")):
        raise ValueError("Disciplina e sugestão de ementa são obrigatórias.")

    dados = dict(dados)
    numero_unidades, alvos = sortear_estrutura_plano(dados.get("numero_unidades"))
    dados["numero_unidades"] = numero_unidades
    dados["topicos_por_unidade_alvo"] = alvos

    client = _get_client()
    modelo = _modelo_planos()
    erro_anterior = ""

    # Duas tentativas: a segunda informa ao modelo exatamente o que faltou.
    for tentativa in range(2):
        prompt = gerar_prompt_simplificado(dados, erro_anterior)
        response = client.responses.create(
            model=modelo,
            input=[
                {
                    "role": "system",
                    "content": "Retorne apenas JSON válido. Não use markdown nem texto fora do JSON. Seja conservador: nunca invente referências bibliográficas."
                },
                {"role": "user", "content": prompt}
            ]
        )
        texto = getattr(response, "output_text", "") or ""
        try:
            bruto = _json_da_resposta(texto)
            return _validar_e_normalizar(bruto, numero_unidades=dados["numero_unidades"], topicos_por_unidade_alvo=dados["topicos_por_unidade_alvo"])
        except Exception as e:
            erro_anterior = str(e)
            if tentativa == 1:
                raise RuntimeError(f"A IA não retornou o plano no formato esperado: {erro_anterior}")

    raise RuntimeError("Falha inesperada ao gerar plano de ensino.")


# ============================================
# ROTAS DA API
# ============================================

@planos_bp.route('/gerar-conteudo-plano', methods=['POST'])
def gerar_conteudo_plano():
    try:
        dados = request.get_json(silent=True) or {}
        if not dados.get('disciplina') or not dados.get('ementa'):
            return jsonify({'error': 'Disciplina e sugestão de ementa são obrigatórias'}), 400

        conteudo_ia = consultar_openai_para_plano(dados)
        return jsonify({'success': True, 'conteudo': conteudo_ia})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


__all__ = [
    'planos_bp',
    'consultar_openai_para_plano',
    'METODOLOGIA_FIXA',
    'SISTEMA_AVALIACAO_FIXO',
    'gerar_codigo_autenticacao',
    'gerar_hash_completa',
    'sortear_estrutura_plano'
]
