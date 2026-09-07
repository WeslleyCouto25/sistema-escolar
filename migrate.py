"""Migrações idempotentes executadas antes do Gunicorn, nunca dentro de uma requisição HTTP."""
import os

from app import init_db, init_contratos_db, init_pagamentos_db, init_documentos_integrados_db, get_db_connection


def table_exists(cursor, name):
    cursor.execute("SELECT to_regclass(%s) AS reg", (f"public.{name}",))
    row = cursor.fetchone()
    return bool(row and row.get("reg"))


def ensure_extra_schema():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Um processo por vez faz a parte complementar da migração.
        cur.execute("SELECT pg_advisory_lock(834729151)")

        # Tabelas de disciplinas alternativas: algumas instalações antigas já as possuem,
        # mas uma instalação limpa precisa nascer completa antes das rotas serem acessadas.
        # CREATE IF NOT EXISTS não altera nem apaga estruturas já existentes.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS disciplinas_alternativas (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                mural TEXT,
                data_criacao TEXT,
                ativa INTEGER DEFAULT 1
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aluno_disciplina_alternativa (
                id SERIAL PRIMARY KEY,
                aluno_id INTEGER NOT NULL,
                disciplina_id INTEGER NOT NULL,
                data_matricula TEXT,
                UNIQUE (aluno_id, disciplina_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS anexos_disciplina_alternativa (
                id SERIAL PRIMARY KEY,
                aluno_id INTEGER NOT NULL,
                disciplina_id INTEGER NOT NULL,
                nome_arquivo TEXT,
                url_arquivo TEXT,
                descricao TEXT,
                data_envio TEXT,
                status TEXT DEFAULT 'pendente',
                nota REAL,
                feedback TEXT,
                data_correcao TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS notas_finais_alternativas (
                id SERIAL PRIMARY KEY,
                aluno_id INTEGER NOT NULL,
                disciplina_id INTEGER NOT NULL,
                nota_final REAL,
                status TEXT,
                data_realizacao TEXT
            )
        """)

        # Funil público de contratação de unidade curricular.
        # Guarda apenas dados do processo e metadados dos documentos; os arquivos ficam no R2.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS solicitacoes_matricula_publica (
                id SERIAL PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL DEFAULT 'previa',
                disciplina_digitada TEXT,
                disciplina_confirmada TEXT,
                curso_area TEXT,
                departamento TEXT,
                carga_horaria INTEGER,
                ementa_sugerida TEXT,
                plano_html TEXT,
                plano_dados_json TEXT,
                plano_documento_id INTEGER,
                valor_total NUMERIC(12,2),
                nome TEXT,
                email TEXT,
                cpf TEXT,
                telefone TEXT,
                endereco TEXT,
                cidade TEXT,
                estado TEXT,
                cep TEXT,
                aluno_id INTEGER,
                cobranca_id INTEGER,
                contrato_id INTEGER,
                disciplina_id INTEGER,
                documentos_json TEXT DEFAULT '[]',
                observacao_aluno TEXT,
                observacao_mew TEXT,
                aceite_termos BOOLEAN DEFAULT FALSE,
                data_criacao TEXT,
                data_plano TEXT,
                data_aceite TEXT,
                data_pagamento TEXT,
                data_documentos TEXT,
                data_aprovacao TEXT,
                data_liberacao TEXT
            )
        """)

        # Cloudflare R2: banco guarda apenas chave/metadados. Campos antigos ficam para leitura legada.
        for sql in [
            "ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS assinatura_r2_key TEXT",
            "ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS assinatura_mime TEXT",
            "ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS foto_assinatura_r2_key TEXT",
            "ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS foto_assinatura_mime TEXT",
            "ALTER TABLE contratos_alunos ADD COLUMN IF NOT EXISTS pdf_assinado_r2_key TEXT",
            "ALTER TABLE solicitacoes_documentos_integrados ADD COLUMN IF NOT EXISTS arquivo_r2_key TEXT",
            "ALTER TABLE projetos_finais ADD COLUMN IF NOT EXISTS arquivo_r2_key TEXT",
            "ALTER TABLE projetos_finais ADD COLUMN IF NOT EXISTS arquivo_atividade_r2_key TEXT",
            "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS arquivo_r2_key TEXT",
            "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS arquivo_nome TEXT",
            "ALTER TABLE documentos_autenticados ADD COLUMN IF NOT EXISTS arquivo_mime TEXT",
            "ALTER TABLE solicitacoes_matricula_publica ADD COLUMN IF NOT EXISTS plano_dados_json TEXT",
            "ALTER TABLE solicitacoes_matricula_publica ADD COLUMN IF NOT EXISTS plano_documento_id INTEGER",
            "ALTER TABLE solicitacoes_matricula_publica ADD COLUMN IF NOT EXISTS pedido_token TEXT",
            "ALTER TABLE solicitacoes_matricula_publica ADD COLUMN IF NOT EXISTS item_ordem INTEGER",
            "ALTER TABLE solicitacoes_matricula_publica ADD COLUMN IF NOT EXISTS solicitacao_extra TEXT",
            "ALTER TABLE solicitacoes_matricula_publica ADD COLUMN IF NOT EXISTS solicitacao_extra_respondida BOOLEAN DEFAULT FALSE",
            "ALTER TABLE solicitacoes_matricula_publica ADD COLUMN IF NOT EXISTS data_solicitacao_extra TEXT",
            "ALTER TABLE solicitacoes_matricula_publica ADD COLUMN IF NOT EXISTS docente_id INTEGER",
            "ALTER TABLE solicitacoes_matricula_publica ADD COLUMN IF NOT EXISTS docente_nome TEXT",
        ]:
            cur.execute(sql)

        # Solicitações antigas tornam-se pedidos unitários automaticamente.
        if table_exists(cur, "solicitacoes_matricula_publica"):
            cur.execute("UPDATE solicitacoes_matricula_publica SET pedido_token=token WHERE COALESCE(TRIM(pedido_token),'')='' ")
            cur.execute("UPDATE solicitacoes_matricula_publica SET item_ordem=1 WHERE item_ordem IS NULL")
            cur.execute("UPDATE solicitacoes_matricula_publica SET solicitacao_extra_respondida=TRUE WHERE COALESCE(solicitacao_extra_respondida,FALSE)=FALSE AND (cobranca_id IS NOT NULL OR data_aceite IS NOT NULL OR data_pagamento IS NOT NULL)")

        if table_exists(cur, "anexos_disciplina_alternativa"):
            cur.execute("ALTER TABLE anexos_disciplina_alternativa ADD COLUMN IF NOT EXISTS r2_key TEXT")
            cur.execute("ALTER TABLE anexos_disciplina_alternativa ADD COLUMN IF NOT EXISTS content_type TEXT")
            cur.execute("ALTER TABLE anexos_disciplina_alternativa ADD COLUMN IF NOT EXISTS feedback TEXT")

        # Índices das consultas mais frequentes. Só cria se a tabela existir.
        indexes = {
            "aluno_disciplina": [
                ("idx_ad_aluno", "aluno_id"), ("idx_ad_disciplina", "disciplina_id")],
            "aluno_disciplina_datas": [
                ("idx_add_aluno_disc", "aluno_id, disciplina_id")],
            "dados_pessoais": [("idx_dp_aluno", "aluno_id")],
            "situacao_financeira": [("idx_sf_aluno_id", "aluno_id, id DESC")],
            "notas": [("idx_notas_aluno_disc_cap", "aluno_id, disciplina_id, capitulo")],
            "notas_finais": [("idx_nf_aluno_disc", "aluno_id, disciplina_id")],
            "capitulos": [("idx_cap_disc", "disciplina_id")],
            "provas": [("idx_provas_cap", "capitulo_id")],
            "questoes_finais": [("idx_qf_disc", "disciplina_id")],
            "contratos_alunos": [("idx_contratos_aluno_id", "aluno_id, id DESC")],
            "pagamentos_mercadopago": [("idx_mp_aluno_id", "aluno_id, id DESC")],
            "solicitacoes_material": [("idx_sm_aluno", "aluno_id")],
            "solicitacoes_declaracoes": [("idx_sd_aluno", "aluno_id")],
            "documentos_enviados": [("idx_de_aluno_status", "aluno_id, status")],
            "documentos_autenticados": [("idx_da_codigo", "codigo"), ("idx_da_aluno", "aluno_id")],
            "solicitacoes_documentos_integrados": [
                ("idx_sdi_aluno_status", "aluno_id, status"), ("idx_sdi_status_id", "status, id DESC")],
            "projetos_finais": [("idx_pf_aluno_disc", "aluno_id, disciplina_id")],
            "disciplina_docente": [("idx_dd_disc", "disciplina_id, id DESC")],
            "anexos_disciplina_alternativa": [("idx_ada_aluno_disc", "aluno_id, disciplina_id")],
            "solicitacoes_matricula_publica": [
                ("idx_smp_status_id", "status, id DESC"),
                ("idx_smp_aluno", "aluno_id"),
                ("idx_smp_cobranca", "cobranca_id"),
                ("idx_smp_pedido", "pedido_token, id")
            ],
        }
        for table, specs in indexes.items():
            if table_exists(cur, table):
                for name, columns in specs:
                    cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})")

        conn.commit()
    finally:
        try:
            cur.execute("SELECT pg_advisory_unlock(834729151)")
            conn.commit()
        except Exception:
            pass
        conn.close()


def main():
    init_db()
    init_contratos_db()
    init_pagamentos_db()
    init_documentos_integrados_db()
    ensure_extra_schema()
    print("[MIGRATE] Banco conferido. DDL fora das requisições HTTP.")


if __name__ == "__main__":
    main()
