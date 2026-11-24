import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
import course_editorial as ce

class CourseEditorialTest(unittest.TestCase):
    def setUp(self):
        self.course = ce.get_course("Fundamentos da Psicologia Positiva")

    def test_catalog(self):
        self.assertEqual([len(u["paragraphs"]) for u in self.course["units"]], [15,16,17,18])
        self.assertEqual([len(u["blocks"]) for u in self.course["units"]], [15]*4)
        for i,u in enumerate(self.course["units"],1):
            self.assertTrue(all(len(p["body"].split()) >= 250 for p in u["paragraphs"]))
            self.assertTrue(u["video"].endswith(f"AULA_{i:02d}_PSICOLOGIA_POSITIVA_M6" + ("_EDITORIAL" if i<4 else "") + ".mp4"))
            required = ce.required_ids(u)
            self.assertFalse(ce.ready(u,ce.empty_progress()))
            self.assertTrue(ce.ready(u,{"recursos":list(required)}))
            for b in u["blocks"]:
                if b["type"] in ("slides","optional_video"):
                    self.assertNotIn(b["id"],required)
                if b["type"] == "video":
                    with self.assertRaises(ValueError): ce.validate_completion(u,b["id"],.89)
                    self.assertEqual(ce.validate_completion(u,b["id"],.9),b["id"])

    def test_unlock_and_time(self):
        self.assertEqual(ce.open_units(4,[]),[1])
        grades=[{"ordem":1,"nota":7}]
        self.assertEqual(ce.open_units(4,grades),[1,2])
        self.assertEqual(ce.open_units(1,grades),[1])
        t=datetime.now(timezone.utc)
        self.assertEqual(ce.credited_seconds(t,t+timedelta(seconds=15),595),600)
        self.assertEqual(ce.credited_seconds(t,t+timedelta(seconds=60),200),200)

    def test_render_all(self):
        env=Environment(loader=FileSystemLoader("templates"),autoescape=select_autoescape(),undefined=StrictUndefined)
        env.globals["url_for"]=lambda endpoint,**kwargs: "/"+endpoint
        template=env.get_template("student/_editorial_chapter.html")
        env.parse(Path("templates/disciplina.html").read_text(encoding="utf-8"))
        for u in self.course["units"]:
            html=template.render(unit=u,study=ce.empty_progress(),disciplina={"id":1},editorial=self.course,editorial_token="test",editorial_graded=[],editorial_emphasis=ce.emphasis,c={"prova_id":1})
            self.assertEqual(html.count('class="se-body"'),len(u["paragraphs"]))
            self.assertIn(u["video"],html)
        html=env.get_template("student/editorial_book.html").render(course=self.course,editorial=self.course,editorial_emphasis=ce.emphasis)
        self.assertIn("Psicologia Positiva",html)

if __name__ == "__main__":
    unittest.main()
