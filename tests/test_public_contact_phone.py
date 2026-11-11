"""Proteção contra divergência de telefone em links públicos SIGEU.

Não inspeciona telefones pessoais de alunos nem documentos emitidos.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APPROVED = "5521990020262"
TEMPLATES = ROOT / "templates"
MARKETING = TEMPLATES / "seo_public"
TARGETS = [
    TEMPLATES / "index.html",
    TEMPLATES / "_public_header.html",
    TEMPLATES / "_public_footer.html",
    TEMPLATES / "disciplinas_isoladas.html",
    TEMPLATES / "disciplina.html",
    TEMPLATES / "disciplina_alternativa.html",
    TEMPLATES / "suporte.html",
    TEMPLATES / "student/_campus_footer.html",
]
LINK_PATTERNS = [
    re.compile(r"wa\.me/(\d{11,15})", re.I),
    re.compile(r"api\.whatsapp\.com/send\?phone=\+?(\d{11,15})", re.I),
    re.compile(r"tel:\+?(\d{11,15})", re.I),
]


class PublicContactTests(unittest.TestCase):
    def test_public_contact_numbers_are_consistent(self):
        public_files = list(MARKETING.rglob("*.html"))
        public_files += list((MARKETING / "solucoes_digitais/assets").glob("*.js"))
        public_files += TARGETS
        seen = set()
        for path in public_files:
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("5541985015076", source, str(path))
            self.assertNotIn("5521980075685", source, str(path))
            self.assertNotIn("551133020501", source, str(path))
            for pattern in LINK_PATTERNS:
                for match in pattern.finditer(source):
                    self.assertEqual(
                        match.group(1),
                        APPROVED,
                        f"Contato divergente em {path}: {match.group(0)}",
                    )

    def test_campaign_phone_configuration_defaults_to_rio(self):
        controller = (ROOT / "sigeu_solucoes_digitais.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("SIGEU_WHATSAPP_NUMERO", "5521990020262")', controller)
        script = (MARKETING / "solucoes_digitais/assets/instituto-sigeu-academico.js").read_text(encoding="utf-8")
        self.assertIn("5521990020262", script)


if __name__ == "__main__":
    unittest.main()
