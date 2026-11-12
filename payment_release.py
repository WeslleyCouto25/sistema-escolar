"""Regra de acesso ao AVA: pagamento confirmado + validação de 24 horas.

O horário fica no PostgreSQL (TIMESTAMPTZ) e não depende de cookies.
Capítulos continuam liberados em D+3, D+6, D+9 e D+12 da matrícula.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("America/Sao_Paulo")
VALIDATION_HOURS = 24
NOTICE = (
    "A disciplina é liberada 24 horas após a confirmação do pagamento. "
    "Esse período é destinado à validação interna da documentação acadêmica."
)


def ensure_payment_release_schema(cursor):
    """DDL aditiva e idempotente, sem apagar ou suspender matrículas antigas."""
    cursor.execute("""
        ALTER TABLE situacao_financeira
        ADD COLUMN IF NOT EXISTS pagamento_confirmado_em TIMESTAMPTZ
    """)
    cursor.execute("""
        CREATE OR REPLACE FUNCTION sigeu_carimbar_pagamento()
        RETURNS trigger AS $$
        BEGIN
          IF NEW.status = 'pago' THEN
            IF TG_OP = 'INSERT' THEN
              IF NEW.pagamento_confirmado_em IS NULL THEN
                NEW.pagamento_confirmado_em := CURRENT_TIMESTAMP;
              END IF;
            ELSIF OLD.status IS DISTINCT FROM 'pago' THEN
              IF NEW.pagamento_confirmado_em IS NULL THEN
                NEW.pagamento_confirmado_em := CURRENT_TIMESTAMP;
              END IF;
            END IF;
          ELSIF TG_OP = 'UPDATE' AND OLD.status IS DISTINCT FROM NEW.status THEN
            NEW.pagamento_confirmado_em := NULL;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    cursor.execute("""
        DROP TRIGGER IF EXISTS sigeu_carimbar_pagamento_trg
        ON situacao_financeira
    """)
    cursor.execute("""
        CREATE TRIGGER sigeu_carimbar_pagamento_trg
        BEFORE INSERT OR UPDATE OF status ON situacao_financeira
        FOR EACH ROW EXECUTE FUNCTION sigeu_carimbar_pagamento()
    """)
    # Exceção administrativa por matrícula/disciplina: não altera datas oficiais.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS acessos_disciplina_admin (
            aluno_id INTEGER NOT NULL REFERENCES alunos(id) ON DELETE CASCADE,
            disciplina_id INTEGER NOT NULL REFERENCES disciplinas(id) ON DELETE CASCADE,
            estado VARCHAR(12) NOT NULL CHECK (estado IN ('liberado', 'bloqueado')),
            data_limite DATE,
            inicio_calendario DATE,
            atualizado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (aluno_id, disciplina_id)
        )
    """)
    # A baixa administrativa anterior preserva a hora real da declaração,
    # evitando iniciar um novo prazo quando o sistema muda de versão.
    cursor.execute("""
        DO $$
        BEGIN
          IF to_regclass('public.anotacoes_baixa_administrativa') IS NOT NULL THEN
            EXECUTE '
              UPDATE situacao_financeira sf
                 SET pagamento_confirmado_em = r.realizado_em
                FROM (
                  SELECT aluno_id, MAX(realizado_em) AS realizado_em
                  FROM anotacoes_baixa_administrativa
                  GROUP BY aluno_id
                ) r
               WHERE sf.aluno_id = r.aluno_id
                 AND sf.status = ''pago''
                 AND sf.pagamento_confirmado_em IS NULL
            ';
          END IF;
        END $$;
    """)


def evaluate_course_access(status, confirmed_at, enrollment_date, now=None):
    """Apenas lógica de calendário; dados pessoais não fazem parte do resultado."""
    now = now or datetime.now(ZONE)
    if now.tzinfo is None:
        raise ValueError("Relógio deve conter fuso horário")
    common = {"prazo_horas": VALIDATION_HOURS, "aviso": NOTICE}
    if status != "pago":
        return {
            **common, "acesso_permitido": False, "motivo": "pagamento_pendente",
            "titulo": "Matrícula em validação",
            "mensagem": "O pagamento ainda não consta como confirmado. " + NOTICE,
        }
    if confirmed_at is not None:
        if confirmed_at.tzinfo is None:
            raise ValueError("Data de pagamento sem fuso horário")
        release_at = confirmed_at + timedelta(hours=VALIDATION_HOURS)
        if now < release_at:
            return {
                **common, "acesso_permitido": False, "motivo": "prazo_validacao",
                "titulo": "Validação de documentação",
                "mensagem": NOTICE + " Previsão: " +
                release_at.astimezone(ZONE).strftime("%d/%m/%Y às %H:%M") + ".",
                "liberacao_prevista": release_at.astimezone(ZONE).isoformat(),
            }
    # Matrículas antigas que já estavam pagas antes desta atualização:
    # não retirar acesso retroativamente por ausência de carimbo histórico.
    if enrollment_date:
        try:
            start_date = datetime.strptime(enrollment_date, "%d/%m/%Y").date()
        except (ValueError, TypeError):
            return {
                **common, "acesso_permitido": False, "motivo": "matricula_invalida",
                "titulo": "Matrícula em conferência",
                "mensagem": "A secretaria precisa conferir a data da matrícula.",
            }
        if now.astimezone(ZONE).date() < start_date:
            return {
                **common, "acesso_permitido": False, "motivo": "matricula_futura",
                "titulo": "Matrícula programada",
                "mensagem": "O período letivo começa em " +
                start_date.strftime("%d/%m/%Y") + ". " + NOTICE,
            }
    return {
        **common, "acesso_permitido": True, "motivo": "liberado",
        "titulo": "Disciplina disponível",
        "mensagem": "Sua matrícula está apta para acesso. " +
        "Os capítulos seguem seu calendário acadêmico de liberação.",
    }


def get_course_access(cursor, aluno_id, disciplina_id, now=None):
    cursor.execute("""
        SELECT ad.id, d.data_inicio, sf.status, sf.pagamento_confirmado_em,
               ac.estado AS controle_estado, ac.data_limite AS controle_limite
        FROM aluno_disciplina ad
        LEFT JOIN aluno_disciplina_datas d
          ON d.aluno_id = ad.aluno_id AND d.disciplina_id = ad.disciplina_id
        LEFT JOIN LATERAL (
            SELECT status, pagamento_confirmado_em
            FROM situacao_financeira
            WHERE aluno_id = ad.aluno_id ORDER BY id DESC LIMIT 1
        ) sf ON TRUE
        LEFT JOIN acessos_disciplina_admin ac
          ON ac.aluno_id=ad.aluno_id AND ac.disciplina_id=ad.disciplina_id
        WHERE ad.aluno_id=%s AND ad.disciplina_id=%s
    """, (aluno_id, disciplina_id))
    row = cursor.fetchone()
    if not row:
        return {
            "acesso_permitido": False, "motivo": "nao_matriculado",
            "titulo": "Disciplina não matriculada",
            "mensagem": "Esta disciplina não está vinculada à sua matrícula.",
            "prazo_horas": VALIDATION_HOURS, "aviso": NOTICE,
        }
    now = now or datetime.now(ZONE)
    local_today = now.astimezone(ZONE).date()
    estado = row.get("controle_estado")
    limite = row.get("controle_limite")
    if estado == "bloqueado":
        return {
            "acesso_permitido": False, "motivo": "bloqueio_administrativo",
            "titulo": "Acesso suspenso",
            "mensagem": "O acesso a esta disciplina foi suspenso pela secretaria acadêmica.",
            "prazo_horas": VALIDATION_HOURS, "aviso": NOTICE,
        }
    if estado == "liberado":
        if limite is not None and local_today > limite:
            return {
                "acesso_permitido": False, "motivo": "liberacao_encerrada",
                "titulo": "Período de acesso encerrado",
                "mensagem": "O prazo autorizado para acesso a esta disciplina terminou em " +
                            limite.strftime("%d/%m/%Y") + ".",
                "prazo_horas": VALIDATION_HOURS, "aviso": NOTICE,
            }
        if row.get("status") == "pago":
            return {
                "acesso_permitido": True, "motivo": "liberacao_administrativa",
                "titulo": "Disciplina disponível",
                "mensagem": "Acesso autorizado após conferência administrativa da matrícula.",
                "prazo_horas": VALIDATION_HOURS, "aviso": NOTICE,
            }
    return evaluate_course_access(
        row.get("status"), row.get("pagamento_confirmado_em"),
        row.get("data_inicio"), now,
    )


def effective_chapter_start(cursor, aluno_id, disciplina_id, matricula_inicio):
    """Antecipação só do calendário de conteúdo; mantém matrícula original."""
    cursor.execute("""
        SELECT inicio_calendario, data_limite
        FROM acessos_disciplina_admin
        WHERE aluno_id=%s AND disciplina_id=%s AND estado='liberado'
    """, (aluno_id, disciplina_id))
    row = cursor.fetchone()
    if row and row.get("inicio_calendario"):
        today = datetime.now(ZONE).date()
        if row.get("data_limite") is None or today <= row["data_limite"]:
            return row["inicio_calendario"].strftime("%d/%m/%Y")
    return matricula_inicio
