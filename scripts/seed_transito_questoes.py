"""Atualizacao pontual da disciplina existente de Psicologia e Cognicao no Transito.
Executada apenas com SIGEU_SEED_TRANSITO_QUESTOES=1 no migrate.py.
Atualiza exclusivamente capitulos e provas da disciplina existente; nao altera alunos.
"""
import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor

RAIZ = "https://pub-1371be77f091443ba1bcf8b5247fbc21.r2.dev/PsicoTransito/"
PROVISORIO = "https://www.sigeu.com"
ARQUIVOS = [
 ("Transito1.mp4", "Aula_1_Apostila_LaTeX.pdf"),
 (None, "Aula_2_Apostila_LaTeX.pdf"),
 (None, "Aula_3_Apostila_LaTeX.pdf"),
 (None, "Aula_4_Apostila_LaTeX.pdf"),
]
QUESTOES = [
  [
    {
      "pergunta": "A Psicologia do Trânsito investiga principalmente:",
      "opcoes": {
        "A": "Somente motores de veículos",
        "B": "Processos psicológicos e comportamentais na mobilidade",
        "C": "Apenas engenharia estrutural",
        "D": "Exclusivamente multas"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Considerar o trânsito como sistema sociotécnico significa analisar:",
      "opcoes": {
        "A": "Apenas condutores",
        "B": "Somente sinalização",
        "C": "Pessoas, veículos, infraestrutura, regras e contextos",
        "D": "Somente agentes públicos"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "A atenção seletiva permite:",
      "opcoes": {
        "A": "Priorizar estímulos relevantes em meio a distratores",
        "B": "Processar igualmente todos os estímulos",
        "C": "Eliminar a possibilidade de erros",
        "D": "Dirigir sem perceber o ambiente"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "A percepção resulta sobretudo de:",
      "opcoes": {
        "A": "Uma cópia literal e infalível do mundo",
        "B": "Reflexos motores sem cognição",
        "C": "Ausência de interpretação",
        "D": "Organização e interpretação de informações sensoriais"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "Consciência situacional envolve:",
      "opcoes": {
        "A": "Apenas reconhecer placas",
        "B": "Perceber, compreender e antecipar acontecimentos da via",
        "C": "Exclusivamente calcular distâncias",
        "D": "Somente memorizar itinerários"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "O uso do celular na direção pode prejudicar a segurança por:",
      "opcoes": {
        "A": "Melhorar a vigilância",
        "B": "Aumentar a visão periférica",
        "C": "Competir pelos recursos de atenção",
        "D": "Eliminar impulsividade"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "No trânsito, vigilância designa a capacidade de:",
      "opcoes": {
        "A": "Monitorar continuamente eventos relevantes",
        "B": "Recordar acontecimentos infantis",
        "C": "Manter aceleração constante",
        "D": "Desconsiderar estímulos repetidos"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "Experiências anteriores influenciam a percepção porque:",
      "opcoes": {
        "A": "Percepção independe de memória",
        "B": "A percepção é sempre objetiva",
        "C": "Todos interpretam sinais igualmente",
        "D": "Expectativas orientam a interpretação dos estímulos"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "Uma contribuição da Psicologia do Trânsito é:",
      "opcoes": {
        "A": "Substituir integralmente engenharia de tráfego",
        "B": "Fundamentar ações preventivas considerando fatores humanos",
        "C": "Atribuir todos os sinistros ao indivíduo",
        "D": "Dispensar educação viária"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Qual perspectiva é mais adequada à mobilidade segura?",
      "opcoes": {
        "A": "Somente punição do condutor",
        "B": "Exclusivamente habilidade motora",
        "C": "Integração de fatores individuais, ambientais e sociais",
        "D": "Ausência de fiscalização"
      },
      "resposta_certa": "C"
    }
  ],
  [
    {
      "pergunta": "Funções executivas contribuem para a condução porque permitem:",
      "opcoes": {
        "A": "Planejar, inibir impulsos e ajustar decisões",
        "B": "Eliminar emoções",
        "C": "Ignorar estímulos externos",
        "D": "Substituir a percepção"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "Memória de trabalho é a capacidade de:",
      "opcoes": {
        "A": "Registrar ilimitadamente fatos passados",
        "B": "Esquecer informações irrelevantes",
        "C": "Eliminar distrações por completo",
        "D": "Manter e manipular informações por curto intervalo"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "Aprendizagem observacional ocorre quando:",
      "opcoes": {
        "A": "Só há reflexos inatos",
        "B": "Comportamentos são aprendidos ao observar modelos",
        "C": "A memória não participa",
        "D": "Não há influência social"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Na teoria do comportamento planejado, a intenção depende sobretudo de:",
      "opcoes": {
        "A": "Apenas genética",
        "B": "Somente velocidade média",
        "C": "Atitudes, normas subjetivas e controle percebido",
        "D": "Apenas punições"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "Um hábito arriscado automatizado seria:",
      "opcoes": {
        "A": "Verificar a distância de segurança",
        "B": "Observar pedestres na travessia",
        "C": "Reduzir a velocidade na chuva",
        "D": "Consultar notificações do celular sem avaliar o perigo"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "Avaliar o risco de atravessar uma avenida exige:",
      "opcoes": {
        "A": "Estimar velocidades, distâncias e condições da via",
        "B": "Apenas acompanhar outro pedestre",
        "C": "Acreditar na própria sorte",
        "D": "Ignorar visibilidade e fluxo"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "Uma heurística cognitiva pode produzir erros porque:",
      "opcoes": {
        "A": "Garante precisão de julgamentos",
        "B": "Simplifica decisões e pode introduzir vieses",
        "C": "Anula influências sociais",
        "D": "É idêntica à memória de trabalho"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Autorregulação no trânsito significa:",
      "opcoes": {
        "A": "Aumentar velocidade para cumprir metas",
        "B": "Negar qualquer falha pessoal",
        "C": "Monitorar e ajustar o comportamento para reduzir riscos",
        "D": "Desconsiderar consequências"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "O excesso de confiança pode levar o condutor a:",
      "opcoes": {
        "A": "Fazer estimativas sempre precisas",
        "B": "Eliminar riscos da via",
        "C": "Obedecer automaticamente todas as normas",
        "D": "Superestimar a própria capacidade e subestimar perigos"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "Qual estratégia favorece aprendizagem de comportamentos seguros?",
      "opcoes": {
        "A": "Informação, prática orientada, feedback e condições adequadas",
        "B": "Repetição de ameaças sem contextualização",
        "C": "Somente memorização de leis",
        "D": "Desconsiderar o ambiente"
      },
      "resposta_certa": "A"
    }
  ],
  [
    {
      "pergunta": "O estresse intenso ao dirigir pode:",
      "opcoes": {
        "A": "Garantir decisões mais rápidas e corretas",
        "B": "Prejudicar atenção, julgamento e autorregulação",
        "C": "Eliminar a agressividade",
        "D": "Aumentar sempre a memória"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "A fadiga está relacionada principalmente a:",
      "opcoes": {
        "A": "Melhora da vigilância",
        "B": "Maior controle dos impulsos",
        "C": "Redução da atenção sustentada e do tempo de resposta",
        "D": "Eliminação dos microsonos"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "Por que o sono insuficiente é fator de risco viário?",
      "opcoes": {
        "A": "Pode comprometer alerta e desempenho psicomotor",
        "B": "Aumenta sempre a criatividade",
        "C": "Elimina a sonolência em viagens longas",
        "D": "Substitui a necessidade de pausas"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "No contexto viário, agressividade pode ser intensificada por:",
      "opcoes": {
        "A": "Somente características genéticas",
        "B": "Exclusivamente pelo veículo",
        "C": "Ausência de qualquer emoção",
        "D": "Frustração, interpretações hostis e condições contextuais"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "A condução sob efeito de álcool pode comprometer:",
      "opcoes": {
        "A": "Apenas a visão noturna",
        "B": "Julgamento, coordenação e tempo de reação",
        "C": "Somente a audição",
        "D": "Apenas a memória de longo prazo"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Uma distração cognitiva acontece quando:",
      "opcoes": {
        "A": "O motorista reduz o volume do rádio",
        "B": "O motorista ajusta o banco antes de sair",
        "C": "O pensamento se afasta da tarefa mesmo com olhos voltados para a via",
        "D": "O motorista observa uma sinalização obrigatória"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "Ergonomia e fatores humanos procuram:",
      "opcoes": {
        "A": "Adaptar sistemas às capacidades e limitações humanas",
        "B": "Exigir que todos se adaptem sem suporte",
        "C": "Tratar só de design estético",
        "D": "Eliminar treinamento"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "Na abordagem Sistema Seguro, é fundamental:",
      "opcoes": {
        "A": "Assumir que ninguém comete erros",
        "B": "Priorizar exclusivamente multas",
        "C": "Desconsiderar vulnerabilidades",
        "D": "Projetar vias e sistemas tolerantes a erros humanos"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "A prevenção de sinistros deve considerar:",
      "opcoes": {
        "A": "Somente o comportamento individual",
        "B": "Pessoas, infraestrutura, velocidade, veículos e resposta pós-sinistro",
        "C": "Apenas equipamentos eletrônicos",
        "D": "Somente fiscalização eventual"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Uma medida efetiva para reduzir a distração digital é:",
      "opcoes": {
        "A": "Responder mensagens em baixa velocidade",
        "B": "Usar redes sociais em semáforos",
        "C": "Evitar manuseio do celular e configurar recursos antes da viagem",
        "D": "Confiar integralmente na capacidade de multitarefa"
      },
      "resposta_certa": "C"
    }
  ],
  [
    {
      "pergunta": "A resiliência da mobilidade urbana envolve:",
      "opcoes": {
        "A": "Exclusivamente aumentar a velocidade",
        "B": "Considerar acessibilidade e segurança de diferentes usuários",
        "C": "Eliminar trajetos a pé",
        "D": "Priorizar somente veículos particulares"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Uma política de mobilidade inclusiva deve:",
      "opcoes": {
        "A": "Tratar todos os usuários como fisicamente idênticos",
        "B": "Ignorar obstáculos nas calçadas",
        "C": "Remover barreiras para pessoas com diferentes necessidades",
        "D": "Restringir circulação de idosos"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "A avaliação psicológica no trânsito deve ser:",
      "opcoes": {
        "A": "Realizada por profissional habilitado com métodos e critérios pertinentes",
        "B": "Baseada apenas em impressão informal",
        "C": "Substituída por opinião de terceiros",
        "D": "Empregada sem consentimento e sem ética"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "O SATEPSI está relacionado principalmente a:",
      "opcoes": {
        "A": "Licenciamento de automóveis",
        "B": "Fiscalização de velocidade",
        "C": "Treinamento de motociclistas",
        "D": "Avaliação da qualidade técnico-científica de testes psicológicos"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "Em avaliação psicológica, validade indica:",
      "opcoes": {
        "A": "O custo do instrumento",
        "B": "O suporte das evidências às interpretações pretendidas dos escores",
        "C": "A duração obrigatória de aplicação",
        "D": "A ausência de erro em qualquer contexto"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Uma abordagem ética de avaliação psicológica exige:",
      "opcoes": {
        "A": "Divulgar resultados publicamente",
        "B": "Aplicar testes sem contextualização",
        "C": "Respeitar sigilo, finalidade, direitos e limites técnicos",
        "D": "Definir resultados previamente"
      },
      "resposta_certa": "C"
    },
    {
      "pergunta": "A educação para o trânsito deve procurar:",
      "opcoes": {
        "A": "Promover compreensão de riscos e comportamentos de convivência",
        "B": "Apenas decorar artigos de lei",
        "C": "Culpabilizar exclusivamente pedestres",
        "D": "Substituir a infraestrutura"
      },
      "resposta_certa": "A"
    },
    {
      "pergunta": "O Plano Nacional de Redução de Mortes e Lesões no Trânsito é conhecido como:",
      "opcoes": {
        "A": "SATEPSI",
        "B": "PERMA",
        "C": "SUS",
        "D": "Pnatrans"
      },
      "resposta_certa": "D"
    },
    {
      "pergunta": "Uma política de segurança centrada no cidadão deve:",
      "opcoes": {
        "A": "Ignorar desigualdades territoriais",
        "B": "Considerar pedestres, ciclistas, pessoas com deficiência e outros grupos vulneráveis",
        "C": "Concentrar decisões exclusivamente em motoristas",
        "D": "Dispensar participação social"
      },
      "resposta_certa": "B"
    },
    {
      "pergunta": "Uma avaliação de campanha educativa deve:",
      "opcoes": {
        "A": "Usar apenas número de cartazes",
        "B": "Assumir sucesso sem indicadores",
        "C": "Mensurar resultados, limitações e possíveis efeitos em diferentes públicos",
        "D": "Ignorar mudanças de comportamento"
      },
      "resposta_certa": "C"
    }
  ]
]

def executar():
    db = os.environ["DATABASE_URL"]
    if len(QUESTOES) != 4 or any(len(grupo) != 10 for grupo in QUESTOES):
        raise RuntimeError("Quantidade incorreta de questoes (esperado 4 x 10)")
    conn = psycopg2.connect(db, cursor_factory=RealDictCursor)
    try:
        with conn:
            with conn.cursor() as cur:
                # Não criar disciplina nova nem modificar outra disciplina.
                cur.execute("SELECT id,nome FROM disciplinas WHERE nome ILIKE %s", ("%Psicologia%Trânsito%",))
                achadas = cur.fetchall()
                if len(achadas) != 1:
                    raise RuntimeError("Disciplina de transito nao identificada de forma univoca: " + str(len(achadas)))
                disc = achadas[0]
                cur.execute("SELECT id,titulo FROM capitulos WHERE disciplina_id=%s ORDER BY id FOR UPDATE", (disc["id"],))
                caps = cur.fetchall()
                if len(caps) != 4:
                    raise RuntimeError("A disciplina deve ter exatamente 4 capitulos; encontrados " + str(len(caps)))
                for idx, cap in enumerate(caps):
                    video, pdf = ARQUIVOS[idx]
                    video_url = RAIZ + video if video else PROVISORIO
                    pdf_url = RAIZ + pdf
                    cur.execute(
                        "UPDATE capitulos SET video_url=%s,pdf_url=%s WHERE id=%s AND disciplina_id=%s",
                        (video_url, pdf_url, cap["id"], disc["id"]))
                    cur.execute("SELECT id FROM provas WHERE capitulo_id=%s ORDER BY id FOR UPDATE", (cap["id"],))
                    provas = cur.fetchall()
                    if len(provas) > 1:
                        raise RuntimeError("Provas duplicadas no capitulo " + str(idx+1))
                    payload = json.dumps(QUESTOES[idx], ensure_ascii=False)
                    if provas:
                        cur.execute("UPDATE provas SET questoes_json=%s WHERE id=%s", (payload, provas[0]["id"]))
                    else:
                        cur.execute("INSERT INTO provas(capitulo_id,questoes_json) VALUES(%s,%s)", (cap["id"], payload))
                cur.execute("""SELECT c.id,c.video_url,c.pdf_url,p.questoes_json
                      FROM capitulos c JOIN provas p ON p.capitulo_id=c.id
                      WHERE c.disciplina_id=%s ORDER BY c.id""", (disc["id"],))
                verificados = cur.fetchall()
                assert len(verificados) == 4
                for idx,row in enumerate(verificados):
                    q = json.loads(row["questoes_json"])
                    assert len(q) == 10
                    assert all(set(x["opcoes"]) == {"A","B","C","D"} and x["resposta_certa"] in "ABCD" for x in q)
                    assert row["pdf_url"] == RAIZ + ARQUIVOS[idx][1]
                    assert row["video_url"] == (RAIZ + ARQUIVOS[idx][0] if ARQUIVOS[idx][0] else PROVISORIO)
        print("[SIGEU TRANSITO IMPORTADO] disciplina_id=%s capitulos=4 questoes=40 videos=1+3_provisorios pdfs=4" % disc["id"], flush=True)
    finally:
        conn.close()

if __name__ == "__main__":
    executar()
