"""Disciplina demonstrativa: Biologia Celular e Tecidual - 80H.
Idempotente, transacional. Não lança notas nem atesta frequência.
As 4 provas de capítulo + avaliação final contêm 10 questões cada.
"""
import os, json
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

NOME = "Biologia Celular e Tecidual - 80H"
TESTE_RA = "T10087552"
MATERIAL_PROVISORIO = "https://www.sigeu.com"
CAPITULOS = [
 "Fundamentos celulares, biomoléculas, membranas e microscopia",
 "Organelas, bioenergética, sinalização e ciclo celular",
 "Epitélios, tecidos conjuntivos, cartilagem, osso e sangue",
 "Tecido muscular, nervoso, reparação e integração morfofuncional"
]
QUESTOES = [
  [
    {
      "pergunta": "A teoria celular afirma que:",
      "opcoes": {
        "A": "A célula é a unidade estrutural e funcional dos organismos",
        "B": "Todos os tecidos são compostos por minerais",
        "C": "Apenas animais possuem células",
        "D": "Todas as células têm núcleo"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "A resolução de um microscópio corresponde à capacidade de:",
      "opcoes": {
        "A": "Aumentar indefinidamente uma imagem",
        "B": "Distinguir dois pontos muito próximos",
        "C": "Mudar a cor da amostra",
        "D": "Medir diretamente metabolismo"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Uma célula procariótica se distingue da eucariótica por:",
      "opcoes": {
        "A": "Ter necessariamente parede de celulose",
        "B": "Não realizar metabolismo",
        "C": "Não apresentar núcleo delimitado por membrana",
        "D": "Não possuir membrana plasmática"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "A membrana plasmática é formada principalmente por:",
      "opcoes": {
        "A": "Apenas DNA e RNA",
        "B": "Parede rígida de celulose",
        "C": "Filamentos de actina isolados",
        "D": "Bicamada lipídica com proteínas associadas"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "Na difusão simples, moléculas movem-se:",
      "opcoes": {
        "A": "Seguindo o gradiente de concentração sem gasto direto de ATP",
        "B": "Sempre contra o gradiente",
        "C": "Apenas dentro do núcleo",
        "D": "Somente por endocitose"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "A osmose caracteriza-se pelo movimento de:",
      "opcoes": {
        "A": "Proteínas em transporte vesicular",
        "B": "Água através de membrana semipermeável",
        "C": "Ribossomos entre organelas",
        "D": "Cromossomos no citoplasma"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "A principal função dos ribossomos é:",
      "opcoes": {
        "A": "Estocar ATP",
        "B": "Degradar lipídios",
        "C": "Realizar síntese de proteínas",
        "D": "Formar a membrana nuclear"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "Qual molécula armazena informação genética nas células humanas?",
      "opcoes": {
        "A": "ATP",
        "B": "Glicogênio",
        "C": "Colágeno",
        "D": "DNA"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "Uma preparação histológica com hematoxilina-eosina geralmente destaca:",
      "opcoes": {
        "A": "Núcleos e componentes citoplasmáticos com afinidades tintoriais distintas",
        "B": "Somente mitocôndrias vivas",
        "C": "A atividade elétrica neuronal",
        "D": "Exclusivamente lipídios neutros"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "Uma interpretação correta de micrografia deve considerar:",
      "opcoes": {
        "A": "Somente a cor das estruturas",
        "B": "Resolução, técnica e possíveis artefatos de preparação",
        "C": "Apenas o aumento anunciado",
        "D": "Que todas as células são idênticas"
      },
      "resposta_certa": "B"
    }
  ],
  [
    {
      "pergunta": "O retículo endoplasmático rugoso participa especialmente da:",
      "opcoes": {
        "A": "Síntese de proteínas destinadas à secreção e às membranas",
        "B": "Produção exclusiva de ATP",
        "C": "Duplicação de cromossomos",
        "D": "Digestão de bactérias fora da célula"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "O complexo de Golgi atua principalmente em:",
      "opcoes": {
        "A": "Fotossíntese humana",
        "B": "Modificação e distribuição de macromoléculas",
        "C": "Síntese do DNA mitocondrial",
        "D": "Formação de osteóns"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "A mitocôndria é especialmente importante para:",
      "opcoes": {
        "A": "Secretar muco diretamente",
        "B": "Produzir queratina",
        "C": "Produção aeróbica de ATP",
        "D": "Armazenar cromossomos no núcleo"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "Os lisossomos participam de:",
      "opcoes": {
        "A": "Produção dos microtúbulos",
        "B": "Formação de sinapses",
        "C": "Respiração aeróbica",
        "D": "Degradação intracelular e reciclagem de componentes"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "A função dos microtúbulos inclui:",
      "opcoes": {
        "A": "Organização do fuso mitótico e transporte intracelular",
        "B": "Produção de hemoglobina",
        "C": "Síntese direta de colesterol",
        "D": "Replicação extracelular do DNA"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "Na transcrição, ocorre a:",
      "opcoes": {
        "A": "Conversão de proteína em DNA",
        "B": "Produção de RNA a partir de um molde de DNA",
        "C": "Duplicação de organelas por mitose",
        "D": "Translação de mRNA em lipídios"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Os checkpoints do ciclo celular são mecanismos que:",
      "opcoes": {
        "A": "Eliminam a necessidade de reparo",
        "B": "Garantem que toda célula prolifere",
        "C": "Monitoram condições para progressão segura entre fases",
        "D": "Transformam todos os tecidos em epitélio"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "Na mitose, o resultado típico em células somáticas é:",
      "opcoes": {
        "A": "Formação de quatro gametas haploides",
        "B": "Fusão de dois núcleos",
        "C": "Produção exclusiva de células-tronco",
        "D": "Distribuição do material genético entre duas células-filhas"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "A apoptose pode ser definida como:",
      "opcoes": {
        "A": "Morte celular regulada por vias moleculares",
        "B": "Toda lesão tecidual decorrente de trauma",
        "C": "Divisão celular acelerada",
        "D": "Ruptura mecânica obrigatória da membrana"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "A sinalização celular envolve:",
      "opcoes": {
        "A": "Exclusivamente proteínas estruturais",
        "B": "Reconhecimento de sinais por receptores e resposta intracelular",
        "C": "Somente osmose",
        "D": "Apenas digestão de organelas"
      },
      "resposta_certa": "B"
    }
  ],
  [
    {
      "pergunta": "Uma característica típica do tecido epitelial é:",
      "opcoes": {
        "A": "Células justapostas com pouco material extracelular entre elas",
        "B": "Grandes espaços entre todas as células",
        "C": "Ausência de polaridade celular",
        "D": "Matriz mineralizada abundante"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "O epitélio simples pavimentoso é adequado para:",
      "opcoes": {
        "A": "Grande resistência ao atrito contínuo",
        "B": "Trocas por difusão em superfícies delgadas",
        "C": "Contração voluntária",
        "D": "Suporte ósseo rígido"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "As microvilosidades aumentam principalmente:",
      "opcoes": {
        "A": "A velocidade de mitose",
        "B": "O número de cromossomos",
        "C": "A superfície disponível para absorção",
        "D": "A rigidez da cartilagem"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "O fibroblasto está relacionado principalmente à:",
      "opcoes": {
        "A": "Produção de mielina",
        "B": "Transmissão sináptica",
        "C": "Contração cardíaca",
        "D": "Síntese e manutenção de componentes da matriz extracelular"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "O tecido conjuntivo frouxo caracteriza-se por:",
      "opcoes": {
        "A": "Matriz variável e suporte a vasos e células",
        "B": "Fibras musculares estriadas",
        "C": "Neurônios mielinizados",
        "D": "Camadas de queratinócitos exclusivamente"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "A cartilagem hialina apresenta:",
      "opcoes": {
        "A": "Vasos sanguíneos abundantes na matriz",
        "B": "Condrócitos em lacunas e matriz especializada",
        "C": "Osteóns concêntricos",
        "D": "Epitélio glandular pseudoestratificado"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "O tecido ósseo possui como característica:",
      "opcoes": {
        "A": "Ausência de matriz extracelular",
        "B": "Células sem metabolismo",
        "C": "Matriz mineralizada e remodelamento contínuo",
        "D": "Apenas células musculares"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "Os osteoclastos estão associados à:",
      "opcoes": {
        "A": "Produção de queratina",
        "B": "Produção de mielina",
        "C": "Secreção glandular",
        "D": "Reabsorção de tecido ósseo"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "O tecido adiposo branco atua em:",
      "opcoes": {
        "A": "Reserva energética e sinalização metabólica",
        "B": "Exclusivamente transmissão de impulsos",
        "C": "Formação de cartilagem articular",
        "D": "Contração muscular voluntária"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "Os eritrócitos humanos maduros são especializados em:",
      "opcoes": {
        "A": "Síntese de colágeno",
        "B": "Transporte de gases respiratórios",
        "C": "Formação de sinapses",
        "D": "Produção de matriz óssea"
      },
      "resposta_certa": "B"
    }
  ],
  [
    {
      "pergunta": "No músculo esquelético, a unidade funcional de contração é:",
      "opcoes": {
        "A": "O sarcômero",
        "B": "O néfron",
        "C": "O osteón",
        "D": "O alvéolo pulmonar"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "O músculo cardíaco apresenta:",
      "opcoes": {
        "A": "Contração sempre voluntária",
        "B": "Discos intercalares e células estriadas ramificadas",
        "C": "Ausência de junções celulares",
        "D": "Matriz óssea mineralizada"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "O tecido muscular liso é encontrado em:",
      "opcoes": {
        "A": "Tendões exclusivamente",
        "B": "Córtex ósseo",
        "C": "Paredes de vísceras e vasos sanguíneos",
        "D": "Axônios mielinizados"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "A principal função dos dendritos neuronais é:",
      "opcoes": {
        "A": "Mineralizar matriz extracelular",
        "B": "Degradar proteínas lisossomais",
        "C": "Contrair miofibrilas",
        "D": "Receber e integrar sinais sinápticos"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "As células da glia desempenham funções como:",
      "opcoes": {
        "A": "Sustentação, isolamento e regulação do microambiente neural",
        "B": "Produzir hemoglobina",
        "C": "Formar epitélio simples",
        "D": "Realizar fagocitose exclusiva do sangue"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "A mielina contribui para:",
      "opcoes": {
        "A": "Diminuir toda comunicação neural",
        "B": "Aumentar a velocidade de condução em muitos axônios",
        "C": "Produzir colágeno tipo I",
        "D": "Substituir as sinapses"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Na cicatrização, fibroblastos podem contribuir para:",
      "opcoes": {
        "A": "Desaparecimento de toda inflamação",
        "B": "Produção de mielina",
        "C": "Deposição e remodelamento de matriz extracelular",
        "D": "Transformação de todo tecido em cartilagem"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "A regeneração tecidual depende, entre outros fatores, de:",
      "opcoes": {
        "A": "Ausência absoluta de células",
        "B": "Presença exclusiva de colágeno",
        "C": "Alteração obrigatória do DNA",
        "D": "Tipo de tecido, células progenitoras e extensão da lesão"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "A fibrose caracteriza-se tipicamente por:",
      "opcoes": {
        "A": "Deposição excessiva de componentes da matriz durante reparação",
        "B": "Desenvolvimento de sinapses maduras",
        "C": "Produção fisiológica exclusiva de hemácias",
        "D": "Destruição seletiva do núcleo neuronal"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "Em uma análise histológica, a correlação estrutura-função exige:",
      "opcoes": {
        "A": "Interpretar apenas a cor da lâmina",
        "B": "Relacionar organização celular, matriz e função do tecido",
        "C": "Ignorar contexto biológico",
        "D": "Atribuir diagnóstico definitivo a uma imagem isolada"
      },
      "resposta_certa": "B"
    }
  ],
  [
    {
      "pergunta": "Qual estrutura delimita a célula e regula trocas com o meio?",
      "opcoes": {
        "A": "Nucléolo",
        "B": "Membrana plasmática",
        "C": "Ribossomo",
        "D": "Cromatina"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Na microscopia, resolução e aumento são:",
      "opcoes": {
        "A": "Exatamente o mesmo parâmetro",
        "B": "Independentes de técnicas",
        "C": "Propriedades distintas que afetam a interpretação",
        "D": "Sempre ilimitados"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "A via secretora de uma proteína costuma envolver:",
      "opcoes": {
        "A": "Retículo endoplasmático rugoso e complexo de Golgi",
        "B": "Mitocôndria e nucléolo apenas",
        "C": "Osteócito e cartilagem",
        "D": "Cílios e matriz óssea"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "A diferenciação celular consiste principalmente em:",
      "opcoes": {
        "A": "Eliminação de todas as organelas",
        "B": "Ausência total de regulação gênica",
        "C": "Transporte de água",
        "D": "Aquisição de características funcionais especializadas"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "O epitélio de revestimento apresenta como característica:",
      "opcoes": {
        "A": "Matriz mineralizada",
        "B": "Polaridade e junções celulares",
        "C": "Sarcomeros",
        "D": "Longos axônios"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "A cartilagem normalmente apresenta:",
      "opcoes": {
        "A": "Osteóns completos",
        "B": "Músculos estriados",
        "C": "Matriz especializada e condrócitos",
        "D": "Glândulas secretoras"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "Os osteoblastos estão associados principalmente à:",
      "opcoes": {
        "A": "Formação de matriz óssea",
        "B": "Transmissão de potencial de ação",
        "C": "Produção de bile",
        "D": "Transporte de oxigênio"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "As células gliais participam do funcionamento nervoso porque:",
      "opcoes": {
        "A": "São sinônimos de hemácias",
        "B": "Constituem exclusivamente epitélio",
        "C": "Secretam queratina",
        "D": "Contribuem para suporte e regulação neural"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "A cicatrização envolve frequentemente:",
      "opcoes": {
        "A": "Somente apoptose e nenhuma síntese",
        "B": "Inflamação, deposição de matriz e remodelamento",
        "C": "Exclusivamente transmissão sináptica",
        "D": "Apenas mitose de osteoclastos"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Uma conclusão metodologicamente correta a partir de micrografia isolada é:",
      "opcoes": {
        "A": "Sempre estabelecer diagnóstico clínico",
        "B": "Ignorar a coloração",
        "C": "Descrever evidências observáveis e reconhecer limitações",
        "D": "Afirmar causalidade sem informações adicionais"
      },
      "resposta_certa": "C"
    }
  ]
]

def executar():
    assert len(QUESTOES)==5 and all(len(x)==10 for x in QUESTOES)
    with psycopg2.connect(os.environ["DATABASE_URL"],cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as c:
            c.execute("SELECT id FROM disciplinas WHERE nome=%s ORDER BY id", (NOME,))
            encontrados=c.fetchall()
            if len(encontrados)>1:
                raise RuntimeError("Disciplina duplicada")
            if encontrados:
                did=encontrados[0]["id"]
            else:
                c.execute("INSERT INTO disciplinas(nome,carga_horaria) VALUES(%s,80) RETURNING id", (NOME,))
                did=c.fetchone()["id"]
            c.execute("UPDATE disciplinas SET carga_horaria=80 WHERE id=%s", (did,))
            c.execute("SELECT id FROM capitulos WHERE disciplina_id=%s ORDER BY id FOR UPDATE",(did,))
            ids=[x["id"] for x in c.fetchall()]
            if len(ids) not in (0,4):
                raise RuntimeError("Quantidade de capitulos preexistentes incompatível")
            for i,titulo in enumerate(CAPITULOS):
                if ids:
                    cid=ids[i]
                    c.execute("SELECT COUNT(*) AS n FROM notas WHERE disciplina_id=%s",(did,))
                    if c.fetchone()["n"]:
                        raise RuntimeError("Há notas reais nesta disciplina, sem atualização automática")
                    c.execute("UPDATE capitulos SET titulo=%s,video_url=%s,pdf_url=%s WHERE id=%s",
                              (titulo,MATERIAL_PROVISORIO,MATERIAL_PROVISORIO,cid))
                else:
                    c.execute("INSERT INTO capitulos(disciplina_id,titulo,video_url,pdf_url) VALUES(%s,%s,%s,%s) RETURNING id",
                              (did,titulo,MATERIAL_PROVISORIO,MATERIAL_PROVISORIO))
                    cid=c.fetchone()["id"]
                c.execute("SELECT id FROM provas WHERE capitulo_id=%s ORDER BY id",(cid,))
                prov=c.fetchall()
                if len(prov)>1:
                    raise RuntimeError("Provas duplicadas")
                payload=json.dumps(QUESTOES[i],ensure_ascii=False)
                if prov:
                    c.execute("UPDATE provas SET questoes_json=%s WHERE id=%s",(payload,prov[0]["id"]))
                else:
                    c.execute("INSERT INTO provas(capitulo_id,questoes_json) VALUES(%s,%s)",(cid,payload))
            c.execute("SELECT COUNT(*) AS n FROM questoes_finais WHERE disciplina_id=%s",(did,))
            cnt=c.fetchone()["n"]
            if cnt not in (0,10):
                raise RuntimeError("Banco de prova final preexistente não pode ser substituído")
            if cnt==0:
                for q in QUESTOES[4]:
                    o=q["opcoes"]
                    c.execute("""INSERT INTO questoes_finais
                        (disciplina_id,pergunta,opcao_a,opcao_b,opcao_c,opcao_d,resposta_correta)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (did,q["pergunta"],o["A"],o["B"],o["C"],o["D"],q["resposta_certa"]))
            c.execute("SELECT id,nome FROM alunos WHERE ra=%s", (TESTE_RA,))
            aluno=c.fetchone()
            if not aluno or "teste" not in (aluno["nome"] or "").lower():
                raise RuntimeError("RA de teste não encontrado ou não identificado como teste")
            aid=aluno["id"]
            c.execute("""INSERT INTO aluno_disciplina(aluno_id,disciplina_id)
                      VALUES(%s,%s) ON CONFLICT(aluno_id,disciplina_id) DO NOTHING""",(aid,did))
            c.execute("""INSERT INTO aluno_disciplina_datas(aluno_id,disciplina_id,data_inicio,data_fim_previsto)
                      VALUES(%s,%s,%s,%s) ON CONFLICT(aluno_id,disciplina_id) DO NOTHING""",
                      (aid,did,"01/01/2026","01/04/2026"))
            # Esse RA é uma identidade de testes. Nenhum pagamento real é criado.
            c.execute("SELECT id,status FROM situacao_financeira WHERE aluno_id=%s ORDER BY id DESC LIMIT 1",(aid,))
            financeiro=c.fetchone()
            if not financeiro:
                c.execute("""INSERT INTO situacao_financeira
                  (aluno_id,forma_pagamento,status,parcelas_total,parcelas_pagas,valor_total)
                  VALUES (%s,'simulacao','pago',1,1,0)""",(aid,))
            # Conferência pós escrita com dados mínimos e sem alterar notas.
            c.execute("""SELECT c.id, p.questoes_json FROM capitulos c
                     JOIN provas p ON p.capitulo_id=c.id
                     WHERE c.disciplina_id=%s ORDER BY c.id""",(did,))
            x=c.fetchall()
            if len(x)!=4 or any(len(json.loads(r["questoes_json"]))!=10 for r in x):
                raise RuntimeError("Capítulos ou questionários incompletos")
            c.execute("SELECT COUNT(*) AS n FROM questoes_finais WHERE disciplina_id=%s",(did,))
            if c.fetchone()["n"]!=10:
                raise RuntimeError("Avaliação final incompleta")
            print("[SIGEU BIOLOGIA TESTE VERIFICADO] disciplina_id=%s capitulos=4 questoes_unidade=40 final=10 aluno_teste=1" % did,flush=True)

if __name__=="__main__":
    executar()
