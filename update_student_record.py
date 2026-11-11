"""Atualização de cadastro existente por parâmetros privados do Render.

JSON da operação é removido do ambiente após a execução. Nenhum dado
pessoal é gravado no código-fonte ou nos logs.
"""
import json
import os
import re
from decimal import Decimal

from db_pool import get_db_connection
from academic_plans.course_chapters import COURSE_CHAPTERS


def atualizar_ficha_autorizada():
    raw = os.getenv("SIGEU_CORRECAO_ALUNO_JSON", "").strip()
    if not raw:
        return None
    data = json.loads(raw)
    fields = ("ra", "cpf", "email", "nome_pai", "nome_mae",
              "data_nascimento", "sexo", "naturalidade", "nacionalidade")
    if not isinstance(data, dict) or any(not data.get(field) for field in fields):
        raise ValueError("Dados da operacao incompletos")
    ra = str(data["ra"]).strip()
    cpf = re.sub(r"\D", "", str(data["cpf"]))
    if not re.fullmatch(r"\d{8}", ra) or not re.fullmatch(r"\d{11}", cpf):
        raise ValueError("Identificadores invalidos")
    if "@" not in str(data["email"]) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", str(data["data_nascimento"])
    ):
        raise ValueError("Contato ou data invalida")
    if data["sexo"] not in ("M", "F"):
        raise ValueError("Sexo invalido")
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(202611111)")
        cur.execute("SELECT id FROM alunos WHERE ra=%s FOR UPDATE", (ra,))
        aluno = cur.fetchone()
        if not aluno:
            raise RuntimeError("RA inexistente")
        aluno_id = aluno["id"]
        cur.execute("SELECT cpf FROM dados_pessoais WHERE aluno_id=%s FOR UPDATE", (aluno_id,))
        ficha = cur.fetchone()
        if not ficha or re.sub(r"\D", "", str(ficha["cpf"])) != cpf:
            raise RuntimeError("CPF nao corresponde ao cadastro")
        cur.execute("UPDATE alunos SET email=%s WHERE id=%s",
                    (str(data["email"]).strip().lower(), aluno_id))
        cur.execute("""
            UPDATE dados_pessoais SET
              nome_pai=%s, nome_mae=%s, data_nascimento=%s,
              sexo=%s, naturalidade=%s, nacionalidade=%s
            WHERE aluno_id=%s
        """, (data["nome_pai"], data["nome_mae"], data["data_nascimento"],
              data["sexo"], data["naturalidade"], data["nacionalidade"], aluno_id))
        titles = 0
        for course, names in COURSE_CHAPTERS.items():
            cur.execute("SELECT id FROM disciplinas WHERE nome=%s LIMIT 2", (course,))
            matches = cur.fetchall()
            if len(matches) != 1:
                raise RuntimeError("Disciplina ausente ou duplicada")
            course_id = matches[0]["id"]
            cur.execute("SELECT 1 FROM aluno_disciplina WHERE aluno_id=%s AND disciplina_id=%s",
                        (aluno_id, course_id))
            if not cur.fetchone():
                raise RuntimeError("Vinculo da disciplina ausente")
            cur.execute("SELECT id FROM capitulos WHERE disciplina_id=%s ORDER BY id",
                        (course_id,))
            chapters = cur.fetchall()
            if len(chapters) != 4:
                raise RuntimeError("Quantidade de capitulos divergente")
            for item, name in zip(chapters, names):
                cur.execute("UPDATE capitulos SET titulo=%s WHERE id=%s",
                            (name, item["id"]))
                titles += 1

        # Marcação administrativa de pagamento por ordem explícita do gestor.
        # Não constitui confirmação da operadora, recibo ou webhook.
        if data.get("pago_por_ordem_administrativa") is True:
            cur.execute("""
                SELECT id, status, parcelas_total, valor_total
                FROM situacao_financeira WHERE aluno_id=%s
                ORDER BY id DESC LIMIT 1 FOR UPDATE
            """, (aluno_id,))
            financeiro = cur.fetchone()
            if (not financeiro or int(financeiro["parcelas_total"] or 0) != 8
                or abs(Decimal(str(financeiro["valor_total"])) -
                       Decimal("959.84")) > Decimal("0.01")):
                raise RuntimeError("Condicoes contratuais nao conferem")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS anotacoes_baixa_administrativa (
                  operacao TEXT PRIMARY KEY,
                  aluno_id INTEGER NOT NULL REFERENCES alunos(id),
                  valor NUMERIC(12,2) NOT NULL,
                  status_anterior TEXT,
                  fonte TEXT NOT NULL,
                  confirmado_na_operadora BOOLEAN NOT NULL DEFAULT FALSE,
                  realizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                INSERT INTO anotacoes_baixa_administrativa
                (operacao, aluno_id, valor, status_anterior, fonte)
                VALUES (%s,%s,%s,%s,'declaracao_expressa_gestor')
                ON CONFLICT (operacao) DO NOTHING
            """, (str(data["operacao"]), aluno_id, 959.84, financeiro["status"]))
            cur.execute("""
                UPDATE situacao_financeira
                SET status='pago', parcelas_pagas=parcelas_total
                WHERE id=%s
            """, (financeiro["id"],))
        conn.commit()
        return {"alunos": 1, "titulos": titles,
                "status_financeiro": "pago" if data.get("pago_por_ordem_administrativa") else "inalterado"}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
