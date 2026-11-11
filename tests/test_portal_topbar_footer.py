"""Regressão visual não destrutiva: cabeçalho e rodapés do aluno."""
import pathlib
import re
import unittest
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
CSS = ROOT / "static/css/student_campus_2026.css"
FOOTER = TEMPLATES / "student/_campus_footer.html"


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            data = dict(attrs)
            self.hrefs.append(data.get("href", ""))


class PortalTopbarFooterTests(unittest.TestCase):
    def test_blue_topbar_overrides_legacy_theme(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertIn("linear-gradient(112deg,#08418f 0%,#0a60ca 56%,#0878cf 100%)", css)
        self.assertIn("header.topbar", css)
        self.assertIn("body.portal07.student-page footer.sigeu-campus-footer", css)

    def test_footer_internal_links_target_existing_dashboard_sections(self):
        footer = FOOTER.read_text(encoding="utf-8")
        dashboard = (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")
        parser = LinkParser()
        parser.feed(footer)
        self.assertNotIn("#", parser.hrefs)
        self.assertTrue(any(h.startswith("mailto:") for h in parser.hrefs))
        self.assertTrue(any(h.startswith("tel:") for h in parser.hrefs))
        for url in parser.hrefs:
            if url.startswith("/dashboard?section="):
                section = url.split("section=", 1)[1]
                self.assertIn(f'id="{section}-section"', dashboard, url)
        self.assertIn("sectionFromUrl", dashboard)
        self.assertIn("localStorage.getItem('activeSection')", dashboard)

    def test_shared_footer_present_without_overwriting_old_course_footers(self):
        for name in (
            "dashboard.html",
            "disciplina_concluida.html",
            "aluno/meus_documentos.html",
            "situacao_academica.html",
            "avaliacao_final.html",
        ):
            template = (TEMPLATES / name).read_text(encoding="utf-8")
            self.assertEqual(template.count("{% include 'student/_campus_footer.html' %}"), 1, name)
        for name in ("disciplina.html", "disciplina_alternativa.html"):
            template = (TEMPLATES / name).read_text(encoding="utf-8")
            self.assertIn("<footer>", template)
        discipline = (TEMPLATES / "disciplina.html").read_text(encoding="utf-8")
        self.assertIn("href=\"#chapter-{{ loop.index }}\"", discipline)
        self.assertNotIn('href="#"', discipline)

    def test_css_version_invalidates_old_browser_cache(self):
        for name in (
            "dashboard.html", "disciplina.html", "disciplina_concluida.html",
            "aluno/meus_documentos.html", "login.html",
            "disciplina_alternativa.html", "avaliacao_final.html",
            "situacao_academica.html",
        ):
            template = (TEMPLATES / name).read_text(encoding="utf-8")
            self.assertIn("student_campus_2026.css') }}?v=20261111b", template, name)


if __name__ == "__main__":
    unittest.main()
