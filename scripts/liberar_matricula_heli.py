"""Ajuste pontual e idempotente de acesso administrativo de matrícula já paga.
Não altera pagamento, data de matrícula, notas ou demais alunos.
Executar apenas sob SIGEU_LIBERAR_MATRICULA_HELI=1.
"""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import psycopg2
from psycopg2.extras import RealDictCursor

def executar():
    with psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, nome FROM alunos
                WHERE nome ILIKE '%%heli%%'
                ORDER BY id
            """)
            alunos = cur.fetchall()
            if len(alunos) != 1:
                print("[SIGEU HELI ACESSO] candidato_univoco=nao quantidade=%s, sem alteracoes" % len(alunos), flush=True)
                return
            aid = alunos[0]["id"]
            cur.execute("""
                SELECT ad.disciplina_id, dd.data_inicio,
                  (SELECT sf.status FROM situacao_financeira sf
                   WHERE sf.aluno_id=ad.aluno_id ORDER BY id DESC LIMIT 1) AS pagamento
                FROM aluno_disciplina ad
                LEFT JOIN aluno_disciplina_datas dd
                 ON dd.aluno_id=ad.aluno_id AND dd.disciplina_id=ad.disciplina_id
                WHERE ad.aluno_id=%s
            """, (aid,))
            cursos = cur.fetchall()
            if not cursos or any(x["pagamento"] != "pago" for x in cursos):
                print("[SIGEU HELI ACESSO] matricula_ou_pagamento_nao_confirmado, sem alteracoes", flush=True)
                return
            # A confirmação é administrativa, não uma nova quitação financeira.
            hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
            count=0
            for d in cursos:
                inicio=hoje-timedelta(days=3)
                if d["data_inicio"]:
                    try:
                        inicio = min(inicio, datetime.strptime(d["data_inicio"], "%d/%m/%Y").date())
                    except ValueError:
                        pass
                cur.execute("""
                    INSERT INTO acessos_disciplina_admin
                        (aluno_id,disciplina_id,estado,data_limite,inicio_calendario)
                    VALUES (%s,%s,'liberado',NULL,%s)
                    ON CONFLICT (aluno_id,disciplina_id) DO UPDATE SET
                        estado='liberado',data_limite=NULL,inicio_calendario=EXCLUDED.inicio_calendario,
                        atualizado_em=CURRENT_TIMESTAMP
                """, (aid,d["disciplina_id"],inicio))
                count+=1
            print("[SIGEU HELI ACESSO VERIFICADO] disciplinas_liberadas=%s, matricula_preservada=sim" % count, flush=True)

if __name__=="__main__":
    executar()
