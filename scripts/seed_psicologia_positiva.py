"""Importacao manual, idempotente e transacional da disciplina Psicologia Positiva.
Execucao intencional SOMENTE em ambiente autorizado:
  SIGEU_TEST_CPF=... SIGEU_TEST_PASSWORD=... DATABASE_URL=... python scripts/seed_psicologia_positiva.py
Nao registrar credenciais em logs, Git ou parametros versionados.
"""
import os
import re
import json
import secrets
from datetime import date
import psycopg2
from werkzeug.security import generate_password_hash

BASE = "https://pub-1371be77f091443ba1bcf8b5247fbc21.r2.dev/PsicoPositiva/"
SITE = "https://www.sigeu.com"
TITULO = "Fundamentos da Psicologia Positiva"
CAPITULOS = [
    ("Introdução à Psicologia Positiva", BASE + "Aula1_PsicologiaPositiva.webm", BASE + "Aula_1_Apostila_Psicologia_Positiva.pdf"),
    ("Emoções positivas e bem-estar subjetivo", BASE + "Aula2_PsicologiaPositiva.webm", BASE + "Aula_2_Apostila_Psicologia_Positiva.pdf"),
    ("Forças de caráter e virtudes humanas", SITE, BASE + "Aula_3_Apostila_Psicologia_Positiva.pdf"),
    ("Resiliência, sentido de vida e aplicações", SITE, SITE),
]
QUESTOES = [
  [
    [
      "Qual é o objeto central da Psicologia Positiva?",
      "Estudar exclusivamente transtornos mentais",
      "Investigar condições de bem-estar e florescimento",
      "Eliminar todas as emoções negativas",
      "Substituir o atendimento clínico",
      "B"
    ],
    [
      "Qual pesquisador esteve associado à consolidação da Psicologia Positiva no final dos anos 1990?",
      "Martin Seligman",
      "Sigmund Freud",
      "Jean Piaget",
      "Ivan Pavlov",
      "A"
    ],
    [
      "O modelo PERMA inclui qual dimensão?",
      "Apenas prazer imediato",
      "Apenas renda financeira",
      "Relacionamentos",
      "Ausência completa de estresse",
      "C"
    ],
    [
      "No PERMA, a letra M representa:",
      "Memória",
      "Motricidade",
      "Maturidade",
      "Sentido ou propósito",
      "D"
    ],
    [
      "Qual afirmação descreve corretamente a Psicologia Positiva?",
      "Dispensa evidências",
      "Não nega o sofrimento humano",
      "Promete felicidade constante",
      "Considera todas as dificuldades individuais",
      "B"
    ],
    [
      "Uma intervenção baseada em forças pessoais deve:",
      "Considerar o contexto e a singularidade",
      "Ignorar limites e adversidades",
      "Aplicar a mesma estratégia para todos",
      "Substituir acompanhamento necessário",
      "A"
    ],
    [
      "Em Psicologia Positiva, florescimento refere-se principalmente a:",
      "Euforia permanente",
      "Ausência de conflitos",
      "Funcionamento positivo em múltiplas dimensões",
      "Sucesso financeiro obrigatório",
      "C"
    ],
    [
      "Qual procedimento é adequado para avaliar uma intervenção?",
      "Usar somente depoimentos",
      "Assumir causalidade por correlação",
      "Ignorar resultados negativos",
      "Utilizar medidas válidas e delineamento apropriado",
      "D"
    ],
    [
      "Qual é uma crítica pertinente ao uso superficial da Psicologia Positiva?",
      "Investigar relações sociais",
      "Responsabilizar exclusivamente o indivíduo por problemas estruturais",
      "Mensurar satisfação com a vida",
      "Considerar virtudes humanas",
      "B"
    ],
    [
      "A relação entre Psicologia tradicional e Psicologia Positiva pode ser descrita como:",
      "Complementaridade de enfoques",
      "Substituição da clínica",
      "Oposição absoluta",
      "Identidade de objetos e métodos",
      "A"
    ]
  ],
  [
    [
      "Bem-estar subjetivo envolve sobretudo:",
      "Somente renda",
      "Afetos e satisfação com a vida",
      "Apenas humor de um dia",
      "Ausência de qualquer sofrimento",
      "B"
    ],
    [
      "A teoria ampliar-e-construir foi proposta por:",
      "Maslow",
      "Skinner",
      "Barbara Fredrickson",
      "Piaget",
      "C"
    ],
    [
      "Na teoria ampliar-e-construir, emoções positivas podem:",
      "Ampliar repertórios de pensamento e ação",
      "Eliminar riscos sociais",
      "Garantir felicidade permanente",
      "Anular a necessidade de descanso",
      "A"
    ],
    [
      "Satisfação com a vida é entendida principalmente como:",
      "Uma reação fisiológica automática",
      "Uma emoção momentânea",
      "Um reflexo condicionado",
      "Um julgamento cognitivo global da própria vida",
      "D"
    ],
    [
      "Qual situação é compatível com bem-estar subjetivo?",
      "Estar alegre em todos os momentos",
      "Sentir tristeza e ainda avaliar a vida como significativa",
      "Jamais ter medo",
      "Ignorar as próprias emoções",
      "B"
    ],
    [
      "A gratidão, como intervenção, exige cuidado porque:",
      "Funciona igualmente para todos",
      "Substitui tratamento clínico",
      "Seus efeitos dependem de contexto e diferenças individuais",
      "Impede emoções desagradáveis",
      "C"
    ],
    [
      "O afeto negativo em escalas de bem-estar:",
      "É dimensão relevante, não sinônimo de transtorno",
      "Deve ser sempre zero",
      "É igual a insatisfação com a vida",
      "Invalida qualquer satisfação positiva",
      "A"
    ],
    [
      "Qual exemplo representa avaliação cognitiva de bem-estar?",
      "Aumento da frequência cardíaca",
      "Uma reação de susto",
      "Uma risada espontânea",
      "Considero minha trajetória satisfatória em geral",
      "D"
    ],
    [
      "Emoções positivas e negativas:",
      "São logicamente excludentes",
      "Podem coexistir na experiência humana",
      "São sempre patológicas",
      "Dependem apenas da personalidade",
      "B"
    ],
    [
      "Qual desenho oferece maior apoio à inferência causal de uma intervenção?",
      "Relato único",
      "Correlação transversal",
      "Ensaio controlado quando ético e viável",
      "Opinião sem medida",
      "C"
    ]
  ],
  [
    [
      "Forças de caráter são:",
      "Qualidades positivas reconhecíveis e desenvolvíveis",
      "Apenas habilidades físicas",
      "Diagnósticos clínicos",
      "Características imutáveis e hereditárias",
      "A"
    ],
    [
      "A classificação VIA descreve tradicionalmente:",
      "Quatro forças",
      "Dez forças",
      "Doze forças",
      "Vinte e quatro forças",
      "D"
    ],
    [
      "Qual conjunto corresponde às seis virtudes da classificação VIA?",
      "Poder, fama, riqueza, humor, vigor e êxito",
      "Sabedoria, coragem, humanidade, justiça, temperança e transcendência",
      "Disciplina, obediência, competição, velocidade, produtividade e sorte",
      "Prazer, autonomia, consumo, status, prestígio e fortuna",
      "B"
    ],
    [
      "Qual é um exemplo de força de caráter?",
      "Altura",
      "Velocidade de leitura",
      "Curiosidade",
      "Cor dos olhos",
      "C"
    ],
    [
      "A perseverança costuma ser entendida como:",
      "Persistir de maneira apropriada diante de obstáculos",
      "Nunca interromper uma atividade nociva",
      "Recusar ajuda",
      "Evitar metas desafiadoras",
      "A"
    ],
    [
      "A identificação das forças pessoais pode combinar:",
      "Apenas aparência",
      "Somente opinião do professor",
      "Apenas desempenho econômico",
      "Instrumentos, autorreflexão e observação contextual",
      "D"
    ],
    [
      "Qual é um cuidado ao usar resultados de inventários de forças?",
      "Assumi-los como diagnóstico definitivo",
      "Interpretá-los como indicadores sujeitos a limites",
      "Ignorar evidências de validade",
      "Ordenar pessoas por superioridade moral",
      "B"
    ],
    [
      "Qual atitude exemplifica aplicação da bondade?",
      "Evitar qualquer convivência",
      "Impor ajuda contra a vontade alheia",
      "Oferecer apoio respeitando necessidades e consentimento",
      "Buscar sempre reconhecimento público",
      "C"
    ],
    [
      "O uso equilibrado de forças envolve:",
      "Adequação ao contexto e prevenção de excessos",
      "Uso máximo em qualquer situação",
      "Desconsideração das outras pessoas",
      "Inibição sistemática de todas as forças",
      "A"
    ],
    [
      "Qual benefício é coerente com pesquisas sobre forças?",
      "Garantia de sucesso financeiro",
      "Imunidade ao estresse",
      "Ausência de conflitos",
      "Possível contribuição para engajamento e bem-estar",
      "D"
    ]
  ],
  [
    [
      "Resiliência é melhor compreendida como:",
      "Ausência de sofrimento",
      "Processo de adaptação diante de adversidades",
      "Traço fixo que todos possuem igualmente",
      "Obrigação de suportar tudo sozinho",
      "B"
    ],
    [
      "O conceito de sentido de vida envolve:",
      "Direção, coerência e significância percebidas",
      "Apenas entusiasmo",
      "Somente renda",
      "Ausência de dúvida",
      "A"
    ],
    [
      "Um fator de proteção relevante para resiliência é:",
      "Isolamento forçado",
      "Negação de recursos sociais",
      "Rede de apoio e acesso a recursos",
      "Sobrecarga permanente",
      "C"
    ],
    [
      "Qual afirmação sobre crescimento pós-traumático é adequada?",
      "É inevitável após qualquer trauma",
      "Dispensa suporte psicológico",
      "Pode ser imposto como meta",
      "Não deve ser presumido ou exigido de quem sofreu",
      "D"
    ],
    [
      "Em contexto escolar, uma intervenção responsável:",
      "Ignora desigualdades",
      "Integra relações, pertencimento e avaliação de resultados",
      "Substitui currículo por exercícios de gratidão",
      "Culpabiliza alunos em sofrimento",
      "B"
    ],
    [
      "Qual é um risco de programas de bem-estar organizacional?",
      "Usar indicadores de saúde",
      "Promover espaços de escuta",
      "Transferir toda a responsabilidade pelo adoecimento ao trabalhador",
      "Melhorar condições laborais",
      "C"
    ],
    [
      "A relação entre emoções positivas e resiliência é:",
      "Potencialmente protetiva, mas não garante adaptação",
      "Sempre automática",
      "Independente do contexto",
      "Equivalente a negar sofrimento",
      "A"
    ],
    [
      "Na avaliação de programas de Psicologia Positiva, convém:",
      "Ignorar grupo de comparação",
      "Medir apenas satisfação imediata",
      "Desconsiderar efeitos adversos",
      "Observar efeitos, limites, equidade e sustentabilidade",
      "D"
    ],
    [
      "Qual crítica considera a dimensão sociocultural do bem-estar?",
      "Todas as populações têm igual acesso a recursos",
      "Condições sociais podem limitar oportunidades de florescimento",
      "O bem-estar depende apenas de vontade",
      "A cultura não interfere no significado de felicidade",
      "B"
    ],
    [
      "Qual conclusão integra adequadamente as quatro aulas?",
      "Felicidade significa ausência permanente de tristeza",
      "Forças dispensam apoio social",
      "Bem-estar envolve emoções, relações, sentido e contextos",
      "Resiliência é sempre individual",
      "C"
    ]
  ]
]

def executar():
    url = os.environ.get("DATABASE_URL")
    cpf = re.sub(r"\D", "", os.environ.get("SIGEU_TEST_CPF", ""))
    senha = os.environ.get("SIGEU_TEST_PASSWORD", "")
    if not url or len(cpf) != 11 or not senha:
        raise SystemExit("Informe DATABASE_URL, SIGEU_TEST_CPF (11 numeros) e SIGEU_TEST_PASSWORD.")
    if len(QUESTOES) != 4 or any(len(q) != 10 for q in QUESTOES):
        raise RuntimeError("Sao necessarias 10 questoes para cada um dos 4 capitulos.")
    conn = psycopg2.connect(url)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM disciplinas WHERE LOWER(TRIM(nome)) = LOWER(%s)", (TITULO,))
                rows = cur.fetchall()
                if len(rows) > 1:
                    raise RuntimeError("Ha disciplinas duplicadas com o nome informado. Corrija manualmente.")
                if rows:
                    did = rows[0][0]
                else:
                    cur.execute("INSERT INTO disciplinas(nome) VALUES(%s) RETURNING id", (TITULO,))
                    did = cur.fetchone()[0]
                cur.execute("SELECT id FROM capitulos WHERE disciplina_id=%s ORDER BY id", (did,))
                caps = [r[0] for r in cur.fetchall()]
                if len(caps) > 4:
                    raise RuntimeError("Disciplina tem mais de 4 capitulos; revisao manual necessaria.")
                for idx, (titulo, video, pdf) in enumerate(CAPITULOS):
                    if idx < len(caps):
                        cid = caps[idx]
                        cur.execute("UPDATE capitulos SET titulo=%s,video_url=%s,pdf_url=%s WHERE id=%s",
                                    (titulo, video, pdf, cid))
                    else:
                        cur.execute("INSERT INTO capitulos(disciplina_id,titulo,video_url,pdf_url) VALUES(%s,%s,%s,%s) RETURNING id",
                                    (did, titulo, video, pdf))
                        cid = cur.fetchone()[0]
                    lista = [
                        {"pergunta": p, "opcoes": {"A": a,"B": b,"C": c,"D": d}, "resposta_certa": resp}
                        for p,a,b,c,d,resp in QUESTOES[idx]
                    ]
                    cur.execute("SELECT id FROM provas WHERE capitulo_id=%s ORDER BY id", (cid,))
                    prov = cur.fetchall()
                    if len(prov) > 1:
                        raise RuntimeError(f"Capitulo {idx+1} possui provas duplicadas.")
                    if prov:
                        cur.execute("UPDATE provas SET questoes_json=%s WHERE id=%s",
                                    (json.dumps(lista, ensure_ascii=False), prov[0][0]))
                    else:
                        cur.execute("INSERT INTO provas(capitulo_id,questoes_json) VALUES(%s,%s)",
                                    (cid, json.dumps(lista, ensure_ascii=False)))
                cur.execute("""SELECT a.id,a.nome,a.ra FROM alunos a
                    JOIN dados_pessoais dp ON dp.aluno_id=a.id
                    WHERE regexp_replace(COALESCE(dp.cpf,''),'[^0-9]','','g')=%s""", (cpf,))
                users = cur.fetchall()
                if len(users)>1:
                    raise RuntimeError("CPF associado a multiplos alunos; verificar antes de alterar.")
                if users:
                    aid, nome_existente, ra = users[0]
                    if "evandro" not in (nome_existente or "").lower():
                        raise RuntimeError("CPF ja cadastrado com outro nome. Nenhuma senha alterada.")
                    # Não redefinir senha de uma conta preexistente.
                    print("Aluno existente encontrado; senha original preservada.")
                else:
                    while True:
                        ra = "T" + str(secrets.randbelow(90000000) + 10000000)
                        cur.execute("SELECT 1 FROM alunos WHERE ra=%s", (ra,))
                        if not cur.fetchone():
                            break
                    cur.execute("INSERT INTO alunos(nome,email,ra,senha) VALUES(%s,%s,%s,%s) RETURNING id",
                                ("Evandro (Teste)", "aluno.teste@exemplo.invalid", ra, generate_password_hash(senha)))
                    aid = cur.fetchone()[0]
                    cur.execute("INSERT INTO dados_pessoais(aluno_id,cpf) VALUES(%s,%s)", (aid, cpf))
                cur.execute("INSERT INTO aluno_disciplina(aluno_id,disciplina_id) VALUES(%s,%s) ON CONFLICT(aluno_id,disciplina_id) DO NOTHING", (aid,did))
                cur.execute("""INSERT INTO aluno_disciplina_datas(aluno_id,disciplina_id,data_inicio)
                   VALUES(%s,%s,%s) ON CONFLICT(aluno_id,disciplina_id)
                   DO UPDATE SET data_inicio=EXCLUDED.data_inicio""", (aid,did,"01/01/2026"))
        print("Importacao concluida. Disciplina ID:", did, "Matricula/RA de teste:", ra)
        print("4 capitulos e 40 questoes conferidos. Inicio da matricula: 01/01/2026.")
    finally:
        conn.close()

if __name__ == "__main__":
    executar()
