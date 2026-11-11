"""Matriculas iniciais autorizadas via configuracao privada do Render.

Nao colocar nome, CPF, email, endereco ou senha de aluno no repositorio.
SIGEU_INITIAL_ENROLLMENTS_JSON deve ser definido temporariamente no ambiente,
executado UMA vez no deploy e limpo apos verificar os registros.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from werkzeug.security import generate_password_hash

from db_pool import get_db_connection

SCHEDULE_TZ = ZoneInfo("America/Sao_Paulo")
COURSES = (
    "Fundamentos da Psicologia Positiva",
    "Psicologia e Cognição no Trânsito",
    "Cálculo Diferencial e Integral II",
)
INITIAL_COHORT_ID = "sigeu-2026-11-11-iniciais-v1"


def _digits(v):
    return re.sub(r"\D", "", str(v or ""))


def _cpf_valido(value):
    value = _digits(value)
    if len(value) != 11 or len(set(value)) == 1:
        return False
    for size in (9, 10):
        n = (sum(int(value[i]) * (size + 1 - i) for i in range(size)) * 10) % 11
        if (n if n < 10 else 0) != int(value[size]):
            return False
    return True


def validate_payload(payload):
    """Confere forma e valores; o log de validacao jamais inclui PII."""
    if not isinstance(payload, dict) or payload.get("operation") != INITIAL_COHORT_ID:
        raise ValueError("Operacao de cadastro nao reconhecida")
    people = payload.get("alunos")
    if not isinstance(people, list) or len(people) != 2:
        raise ValueError("Exatamente dois alunos necessarios")
    ras = set()
    cpfs = set()
    planned = []
    for person in people:
        if not isinstance(person, dict):
            raise ValueError("Cadastro nao estruturado")
        name = str(person.get("nome") or "").strip()
        ra = _digits(person.get("ra"))
        cpf = _digits(person.get("cpf"))
        email = str(person.get("email") or "").strip().lower()
        assignments = person.get("disciplinas")
        amount = Decimal(str(person.get("valor_total") or "0"))
        portions = person.get("parcelas")
        pay_method = person.get("meio_pagamento")
        if not (5 <= len(name) <= 180) or len(ra) != 8 or not _cpf_valido(cpf):
            raise ValueError("Identificacao de aluno invalida")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Email invalido")
        if ra in ras or cpf in cpfs:
            raise ValueError("Cadastro repetido na operacao")
        if amount <= 0 or amount.as_tuple().exponent < -2:
            raise ValueError("Valor financeiro invalido")
        if not isinstance(portions, int) or portions not in (1, 8):
            raise ValueError("Parcelamento inesperado")
        if pay_method not in ("picpay", "qr_code"):
            raise ValueError("Meio de pagamento nao suportado")
        if (portions, pay_method, amount) not in (
            (8, "picpay", Decimal("959.84")),
            (1, "qr_code", Decimal("506.92")),
        ):
            raise ValueError("Condicao comercial divergente dos contratos")
        if not isinstance(assignments, list) or len(set(assignments)) != len(assignments):
            raise ValueError("Vinculo academico invalido")
        if not assignments or any(x not in COURSES for x in assignments):
            raise ValueError("Disciplina fora do escopo")
        if len(assignments) != (2 if pay_method == "picpay" else 1):
            raise ValueError("Total de disciplinas nao confere")
        # Numero de contrato nao informando nao vira numero inventado.
        number = person.get("numero_contrato")
        if number is not None and (not isinstance(number, str) or len(number) > 80):
            raise ValueError("Referencia contratual invalida")
        if person.get("endereco") and len(str(person["endereco"])) > 300:
            raise ValueError("Endereço acima do limite")
        ras.add(ra)
        cpfs.add(cpf)
        planned.append({
            **person, "nome": name, "ra": ra, "cpf": cpf, "email": email,
            "valor_total": amount, "parcelas": portions,
        })
    if set(x for p in planned for x in p["disciplinas"]) != set(COURSES):
        raise ValueError("As tres disciplinas contratadas devem estar representadas")
    return planned


def _upsert_course(cur, title):
    cur.execute(
        "SELECT id, COALESCE(carga_horaria, 80) AS carga_horaria "
        "FROM disciplinas WHERE LOWER(TRIM(nome))=LOWER(TRIM(%s)) "
        "ORDER BY id LIMIT 2",
        (title,),
    )
    existing = cur.fetchall()
    if len(existing) > 1:
        raise RuntimeError("Nome de disciplina duplicado no banco")
    if existing:
        if int(existing[0]["carga_horaria"]) != 80:
            raise RuntimeError("Carga horaria divergente: ajuste manual necessario")
        course_id = existing[0]["id"]
    else:
        cur.execute(
            "INSERT INTO disciplinas (nome,carga_horaria) VALUES (%s,80) RETURNING id",
            (title,),
        )
        course_id = cur.fetchone()["id"]
    cur.execute("SELECT id,titulo FROM capitulos WHERE disciplina_id=%s ORDER BY id", (course_id,))
    chapters = cur.fetchall()
    if len(chapters) > 4:
        raise RuntimeError("Disciplina ja possui mais de quatro capitulos: nao alterar")
    # Preserva capítulos e materiais existentes; somente preenche ausentes.
    for n in range(len(chapters) + 1, 5):
        cur.execute(
            "INSERT INTO capitulos (disciplina_id,titulo,video_url,pdf_url) "
            "VALUES (%s,%s,NULL,NULL)",
            (course_id, f"Capítulo {n}"),
        )
    return course_id


def _find_signed_contract(cur, entry):
    number = entry.get("numero_contrato")
    if number:
        cur.execute(
            "SELECT id,cpf,status,pago_em,referencia_pagamento,valor_centavos "
            "FROM cobrancas_avulsas WHERE numero_contrato=%s LIMIT 2", (number,),
        )
    else:
        cur.execute(
            "SELECT id,cpf,status,pago_em,referencia_pagamento,valor_centavos "
            "FROM cobrancas_avulsas WHERE regexp_replace(cpf,'[^0-9]','','g')=%s "
            "AND valor_centavos=%s ORDER BY id DESC LIMIT 2",
            (entry["cpf"], int(entry["valor_total"] * 100)),
        )
    matches = cur.fetchall()
    if len(matches) > 1:
        raise RuntimeError("Contratos candidatos ambiguos para o aluno")
    if not matches:
        return None, False
    contract = matches[0]
    if _digits(contract["cpf"]) != entry["cpf"]:
        raise RuntimeError("CPF nao coincide com o contrato")
    if int(contract["valor_centavos"]) != int(entry["valor_total"] * 100):
        raise RuntimeError("Contrato existente com valor divergente")
    # Nao considerar assinatura ou geracao de checkout como quitacao.
    confirmed_paid = (
        contract["status"] == "pago"
        and contract["pago_em"] is not None
        and bool(contract["referencia_pagamento"])
    )
    return contract["id"], confirmed_paid


def _ensure_student(cur, entry, course_ids, enrolled_on):
    ra = entry["ra"]
    cpf = entry["cpf"]
    cur.execute("SELECT id,nome,ra FROM alunos WHERE ra=%s FOR UPDATE", (ra,))
    existing = cur.fetchone()
    cur.execute(
        "SELECT dp.aluno_id,a.ra FROM dados_pessoais dp "
        "JOIN alunos a ON a.id=dp.aluno_id "
        "WHERE regexp_replace(dp.cpf,'[^0-9]','','g')=%s LIMIT 2",
        (cpf,),
    )
    matches = cur.fetchall()
    if len(matches) > 1 or (matches and matches[0]["ra"] != ra):
        raise RuntimeError("CPF ja vinculado a outro aluno")
    if existing:
        if existing["nome"].strip().casefold() != entry["nome"].casefold():
            raise RuntimeError("RA ja cadastrado com outro nome")
        student_id = existing["id"]
    else:
        # Senha aleatoria nao recuperavel, NUNCA CPF e nunca gravada no log.
        # No primeiro acesso, o administrador devera definir uma senha temporaria
        # segura no cadastro antes de liberar as credenciais ao estudante.
        password_hash = generate_password_hash(secrets.token_urlsafe(40))
        cur.execute(
            "INSERT INTO alunos (nome,email,ra,senha) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            (entry["nome"], entry["email"], ra, password_hash),
        )
        student_id = cur.fetchone()["id"]
    cur.execute("SELECT id FROM dados_pessoais WHERE aluno_id=%s", (student_id,))
    if cur.fetchone() is None:
        cur.execute("""
            INSERT INTO dados_pessoais
              (aluno_id,cpf,telefone,endereco,cidade,estado,cep,
               rg,curso_referencia,nome_pai,nome_mae,naturalidade,nacionalidade,
               data_nascimento,sexo,estado_civil,email_alternativo)
            VALUES
              (%s,%s,%s,%s,%s,%s,%s,
               NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)
        """, (student_id, entry["cpf"], entry.get("telefone") or None,
              entry.get("endereco") or None, entry.get("cidade") or None,
              entry.get("estado") or None, entry.get("cep") or None))
    else:
        cur.execute(
            "SELECT cpf FROM dados_pessoais WHERE aluno_id=%s", (student_id,)
        )
        stored = cur.fetchone()
        if stored["cpf"] and _digits(stored["cpf"]) != cpf:
            raise RuntimeError("CPF do aluno existente diverge")
    contract_id, paid = _find_signed_contract(cur, entry)
    cur.execute(
        "SELECT id,status FROM situacao_financeira WHERE aluno_id=%s ORDER BY id DESC LIMIT 1",
        (student_id,),
    )
    if cur.fetchone() is None:
        cur.execute("""
            INSERT INTO situacao_financeira
              (aluno_id,forma_pagamento,status,parcelas_total,parcelas_pagas,valor_total)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (student_id, entry["meio_pagamento"],
              "pago" if paid else "pendente",
              entry["parcelas"], entry["parcelas"] if paid else 0,
              float(entry["valor_total"])))
    # Sem abrir a plataforma antes da conciliacao financeira.
    for title in entry["disciplinas"]:
        course_id = course_ids[title]
        cur.execute(
            "INSERT INTO aluno_disciplina (aluno_id,disciplina_id) "
            "VALUES (%s,%s) ON CONFLICT (aluno_id,disciplina_id) DO NOTHING",
            (student_id, course_id),
        )
        start = enrolled_on.strftime("%d/%m/%Y")
        finish = (enrolled_on + timedelta(days=60)).strftime("%d/%m/%Y")
        cur.execute(
            "INSERT INTO aluno_disciplina_datas "
            "(aluno_id,disciplina_id,data_inicio,data_fim_previsto) "
            "VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (aluno_id,disciplina_id) DO NOTHING",
            (student_id, course_id, start, finish),
        )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS matriculas_origem_contrato (
          aluno_id INTEGER PRIMARY KEY REFERENCES alunos(id),
          cobranca_avulsa_id BIGINT NULL REFERENCES cobrancas_avulsas(id),
          numero_contrato TEXT NULL,
          origem TEXT NOT NULL,
          criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("""
        INSERT INTO matriculas_origem_contrato
          (aluno_id,cobranca_avulsa_id,numero_contrato,origem)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT (aluno_id) DO NOTHING
    """, (student_id, contract_id, entry.get("numero_contrato"), INITIAL_COHORT_ID))
    return student_id


def provision_initial_enrollments():
    raw = os.environ.get("SIGEU_INITIAL_ENROLLMENTS_JSON", "").strip()
    if not raw:
        return None
    entries = validate_payload(json.loads(raw))
    enrolled_on = datetime.now(SCHEDULE_TZ).date()
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(587419516)")
        courses = {title: _upsert_course(cur, title) for title in COURSES}
        student_ids = [
            _ensure_student(cur, e, courses, enrolled_on) for e in entries
        ]
        if len(set(student_ids)) != 2:
            raise RuntimeError("IDs de alunos duplicados")
        # Assercoes no banco antes de consolidar a operacao.
        for entry, student_id in zip(entries, student_ids):
            cur.execute(
                "SELECT COUNT(*) AS total FROM aluno_disciplina WHERE aluno_id=%s",
                (student_id,),
            )
            if int(cur.fetchone()["total"]) < len(entry["disciplinas"]):
                raise RuntimeError("Matricula incompleta")
        conn.commit()
        return {"alunos": 2, "disciplinas": 3, "vinculos": 3}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    result = provision_initial_enrollments()
    if result:
        print("[SIGEU INITIAL ENROLLMENTS VERIFIED] "
              "alunos=%s disciplinas=%s vinculos=%s" %
              (result["alunos"], result["disciplinas"], result["vinculos"]), flush=True)
