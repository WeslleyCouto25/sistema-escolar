import unittest
from pathlib import Path

from academic_plans.course_chapters import COURSE_CHAPTERS


class TestPlanosInstitucionais(unittest.TestCase):
    def test_dois_planos_e_oito_titulos(self):
        self.assertEqual(len(COURSE_CHAPTERS), 2)
        self.assertEqual(sum(map(len, COURSE_CHAPTERS.values())), 8)
        for name, chapters in COURSE_CHAPTERS.items():
            self.assertTrue(name)
            self.assertEqual(len(chapters), 4)
            self.assertEqual(len(set(chapters)), 4)
            self.assertTrue(all(len(ch) >= 35 for ch in chapters))

    def test_planos_todos_conteudos_original(self):
        base = Path(__file__).resolve().parents[1] / "academic_plans"
        for slug, title in (
            ("transito", "Psicologia e Cognição no Trânsito"),
            ("positiva", "Fundamentos da Psicologia Positiva"),
        ):
            data = (base / f"source_{slug}.txt").read_text(encoding="utf-8")
            self.assertGreater(len(data), 12000)
            self.assertIn(title, data)
            self.assertIn("EMENTA", data)
            self.assertIn("METODOLOGIA", data)
            self.assertIn("AVALIAÇÃO DA APRENDIZAGEM", data)
            self.assertIn("REFERÊNCIAS", data)


if __name__ == "__main__":
    unittest.main()
