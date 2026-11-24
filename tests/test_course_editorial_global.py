import unittest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from pathlib import Path
import course_editorial as ce
import course_editorial_global as ge

def chapters(n=4):
    return [dict(id=i,titulo=f"Capítulo {i}",video_url="https://example.org/video.mp4",pdf_url="https://example.org/material.pdf",prova_id=i) for i in range(1,n+1)]

class GlobalEditorialTests(unittest.TestCase):
    def setUp(self):
        self.env=Environment(loader=FileSystemLoader("templates"),autoescape=select_autoescape(),undefined=StrictUndefined)
        self.env.globals.update(url_for=lambda endpoint,**kw: "/"+endpoint,session={})

    def render(self,catalog,rows):
        template=self.env.get_template("student/_editorial_chapter.html")
        html=[]
        for u,c in zip(catalog["units"],rows):
            html.append(template.render(unit=u,study=ce.empty_progress(),disciplina={"id":1},editorial=catalog,editorial_token="test",editorial_graded=[],editorial_emphasis=ce.emphasis,c=c))
        return "".join(html)

    def test_positiva_alias_and_render(self):
        for name in ["Fundamentos da Psicologia Positiva","Psicologia Positiva — 80H"]:
            c=ge.resolve_course(name,chapters())
            self.assertEqual(c["mode"],"full")
            self.assertEqual(self.render(c,chapters()).count('class="se-body"'),66)

    def test_transit_real_source_and_varied_layouts(self):
        c=ge.resolve_course("Psicologia e Cognição no Trânsito",chapters())
        self.assertEqual(c["mode"],"source")
        for unit in c["units"]:
            self.assertEqual(sum(b["type"]=="source_reading" for b in unit["blocks"]),9)
            self.assertGreaterEqual(len(unit["blocks"]),12)
            self.assertEqual(sum(b["type"]=="slides" for b in unit["blocks"]),2)
            self.assertTrue(ce.ready(unit,ce.empty_progress()))
        layouts=[tuple(b["type"] for b in u["blocks"]) for u in c["units"]]
        self.assertEqual(len(set(layouts)),4)
        html=self.render(c,chapters())
        self.assertIn("Mente e mobilidade",html)
        self.assertIn("Educação para o trânsito",html)
        self.assertEqual(html.count('class="se-body"'),108)

    def test_all_other_courses_use_editorial_without_fake_material(self):
        for n in [1,3,4,5]:
            rows=chapters(n)
            c=ge.resolve_course("Cálculo II",rows)
            self.assertEqual(c["mode"],"materials")
            self.assertEqual(len(c["units"]),n)
            self.assertTrue(all(ce.ready(u,ce.empty_progress()) for u in c["units"]))
            html=self.render(c,rows)
            self.assertEqual(html.count('class="sigeu-editorial"'),n)
            self.assertIn("https://example.org/video.mp4",html)
            self.assertIn("https://example.org/material.pdf",html)
            self.assertIn("ainda não foi publicado",html)

    def test_placeholder_and_media(self):
        self.assertEqual(ge.media_info("https://www.sigeu.com"),("",""))
        self.assertEqual(ge.media_info("https://example.org/test.webm")[1],"file")
        self.assertEqual(ge.media_info("javascript:alert(1)"),("",""))
        self.assertEqual(ge.media_info("https://youtu.be/abcdefghijk")[1],"iframe")

    def test_template_no_old_content_branch(self):
        text=Path("templates/disciplina.html").read_text(encoding="utf-8")
        self.env.parse(text)
        self.assertIn('include "student/_editorial_chapter.html"',text)
        self.assertNotIn("sigeu-legacy-content",text)
        self.assertNotIn('elif not editorial',text)

if __name__=="__main__":
    unittest.main()
