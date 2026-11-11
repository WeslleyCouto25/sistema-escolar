"""Evita UnboundLocalError de imports locais em migrações de produção."""
import ast
import pathlib
import unittest


class MigrationScopeTests(unittest.TestCase):
    def test_main_uses_global_connection_import_only(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "migrate.py").read_text(encoding="utf-8"))
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
        self.assertEqual(len(functions), 1)
        for node in ast.walk(functions[0]):
            if isinstance(node, ast.ImportFrom):
                imported_names = [alias.asname or alias.name for alias in node.names]
                self.assertNotIn("get_db_connection", imported_names)


if __name__ == "__main__":
    unittest.main()
