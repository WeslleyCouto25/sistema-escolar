"""Regressões dos campos do cadastro administrativo de alunos.

Executa sem credenciais e sem acesso ao banco de produção:
    python -m unittest discover -s tests -p 'test_schema_alunos.py'
"""
import unittest

from schema_alunos import CAMPOS_DADOS_PESSOAIS, garantir_campos_dados_pessoais


class CursorFalso:
    def __init__(self):
        self.comandos = []

    def execute(self, sql):
        self.comandos.append(sql)


class TestCadastroAlunosSchema(unittest.TestCase):
    def test_colunas_exigidas_por_listagem_e_edicao(self):
        esperadas = {
            "nome_pai", "nome_mae", "naturalidade", "nacionalidade",
            "data_nascimento", "sexo", "estado_civil", "email_alternativo",
        }
        self.assertEqual(set(CAMPOS_DADOS_PESSOAIS), esperadas)

    def test_migracao_so_adiciona_campos_ausentes(self):
        cursor = CursorFalso()
        garantir_campos_dados_pessoais(cursor)
        self.assertEqual(len(cursor.comandos), 8)
        for coluna, sql in zip(CAMPOS_DADOS_PESSOAIS, cursor.comandos):
            self.assertEqual(
                sql,
                f"ALTER TABLE dados_pessoais ADD COLUMN IF NOT EXISTS {coluna} TEXT",
            )
            self.assertNotIn("DROP ", sql)
            self.assertNotIn("DELETE ", sql)

    def test_migracao_reexecutavel(self):
        cursor = CursorFalso()
        garantir_campos_dados_pessoais(cursor)
        primeira = cursor.comandos[:]
        garantir_campos_dados_pessoais(cursor)
        self.assertEqual(cursor.comandos, primeira + primeira)


if __name__ == "__main__":
    unittest.main()
