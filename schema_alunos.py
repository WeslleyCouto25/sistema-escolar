"""Compatibilidade do cadastro de alunos com bases PostgreSQL antigas.

Migração estritamente aditiva e idempotente: nunca altera valores existentes,
não reescreve tabelas e não apaga registros acadêmicos.
"""

# Nomes e tipos são literais internos, não dados fornecidos pelo usuário.
CAMPOS_DADOS_PESSOAIS = (
    "nome_pai",
    "nome_mae",
    "naturalidade",
    "nacionalidade",
    "data_nascimento",
    "sexo",
    "estado_civil",
    "email_alternativo",
)


def garantir_campos_dados_pessoais(cursor):
    """Garante os campos usados pelas telas de listar/cadastrar/editar alunos."""
    for coluna in CAMPOS_DADOS_PESSOAIS:
        cursor.execute(
            f"ALTER TABLE dados_pessoais ADD COLUMN IF NOT EXISTS {coluna} TEXT"
        )
