import unittest
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
import course_editorial as ce
import course_editorial_global as ge

class DashboardCourseTests(unittest.TestCase):
    def test_fragment_uses_same_curriculum_without_shell(self):
        env=Environment(loader=FileSystemLoader("templates"),autoescape=select_autoescape(),undefined=StrictUndefined)
        env.globals["url_for"]=lambda endpoint,**kwargs: "/"+endpoint
        rows=[dict(id=i,titulo=f"Capítulo {i}",video_url="https://example.org/a.mp4",pdf_url="https://example.org/a.pdf",prova_id=i) for i in range(1,5)]
        catalog=ge.resolve_course("Fundamentos da Psicologia Positiva",rows)
        html=env.get_template("student/_course_view.html").render(disciplina={"id":9,"nome":catalog["name"]},capitulos=rows,capitulos_liberados=4,plano_disponivel=True,editorial=catalog,editorial_open=[1,2],editorial_graded=[1],editorial_current=2,editorial_progress={i:ce.empty_progress() for i in range(1,5)},editorial_token="test",editorial_emphasis=ce.emphasis,editorial_concluida=False)
        self.assertIn('data-course-id="9"',html)
        self.assertEqual(html.count('class="se-body"'),31)
        self.assertEqual(html.count('class="sigeu-editorial"'),2)
        self.assertIn('data-course-chapter="3"',html)
        self.assertIn('aria-disabled="true"',html)
        for forbidden in ('<footer','<body','<html','class="sidebar"','<script'):
            self.assertNotIn(forbidden,html)
    def test_shell_contract(self):
        html=Path("templates/dashboard.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('id="sigeu-course-panel"'),1)
        self.assertIn("{% include 'student/_campus_footer.html' %}",html)
        self.assertIn("window.SigeuDashboardCourse.close()",html)
        self.assertIn("js/dashboard_course.js",html)
        js=Path("static/js/student_campus_2026.js").read_text()
        self.assertIn("window.SigeuDashboardCourse.open(destination)",js)
        self.assertNotIn("window.location.assign(destination)",js)
        css=Path("static/css/dashboard_course.css").read_text()
        self.assertIn("overflow-wrap:anywhere!important",css)
        self.assertIn("white-space:normal!important",css)

if __name__=="__main__": unittest.main()
