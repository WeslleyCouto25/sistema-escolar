"""Auditoria de implantação: somente leitura de cursos e renderização sintética.
Não cria matrícula, não altera notas e não imprime informações de alunos.
"""
from collections import Counter
from flask import render_template
from app import app, get_db_connection, _editorial_chapters
import course_editorial as ce
import course_editorial_global as global_ce

def smoke_editorial():
    counts = Counter()
    details = []
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id,nome FROM disciplinas ORDER BY id")
        courses = [dict(row) for row in cur.fetchall()]
        for course in courses:
            chapters = _editorial_chapters(cur, course["id"])
            if not chapters:
                counts["sem_capitulos"] += 1
                continue
            catalog = global_ce.resolve_course(course["nome"], chapters)
            mode = catalog["mode"]
            counts[mode] += 1
            assert len(catalog["units"]) == len(chapters)
            with app.test_request_context("/disciplina/%d" % course["id"]):
                html = render_template("disciplina.html", disciplina=course,
                    capitulos=chapters, capitulos_liberados=len(chapters),
                    plano_disponivel=False, editorial=catalog,
                    editorial_open=list(range(1,len(chapters)+1)),
                    editorial_graded=[],editorial_current=1,
                    editorial_progress={i:ce.empty_progress() for i in range(1,len(chapters)+1)},
                    editorial_token="smoke-only",editorial_emphasis=ce.emphasis,
                    editorial_concluida=False)
            assert html.count('class="sigeu-editorial"') == len(chapters), "Falha na renderização universal"
            assert "course_editorial.js" in html and "course_editorial.css" in html
            assert 'class="se-proof"' in html
            if global_ce.is_positiva(course["nome"]):
                assert mode == "full", "Psicologia Positiva não ativou catálogo autoral"
                assert html.count('class="se-body"') == 66
                details.append(("positiva",course["id"],len(chapters),html.count('class="se-body"')))
            if global_ce.is_transito(course["nome"]):
                assert mode == "source", "Psicologia do Trânsito não ativou apostilas"
                assert html.count('class="se-body"') >= 36
                details.append(("transito",course["id"],len(chapters),html.count('class="se-body"')))
        print("[SIGEU EDITORIAL GLOBAL SMOKE] disciplinas=%d modos=%s" % (len(courses),dict(counts)),flush=True)
        for kind,did,chapters,paragraphs in details:
            print("[SIGEU EDITORIAL COURSE VERIFIED] curso=%s disciplina=%s capitulos=%s paragrafos=%s" % (kind,did,chapters,paragraphs),flush=True)
    finally:
        conn.close()
