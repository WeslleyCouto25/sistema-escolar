"""Leitura em lote das notas do dashboard, sem N+1 queries.

Preserva preferência por ID canônico de capítulo e compatibilidade
com notas legadas indexadas por número (1..4).
"""
from collections import defaultdict


def normalize_unit_grades(chapters, grades):
    chapters = list(chapters)
    by_id = {int(c["id"]): idx for idx, c in enumerate(chapters, 1)}
    selected = {}
    for row in grades:
        try:
            key = int(row.get("capitulo"))
        except (TypeError, ValueError):
            continue
        if key in by_id:
            ordinal = by_id[key]
            priority = 2
        elif 1 <= key <= len(chapters):
            ordinal = key
            priority = 1
        else:
            continue
        candidate = (priority, int(row.get("id") or 0))
        if ordinal not in selected or candidate >= selected[ordinal][0]:
            selected[ordinal] = (candidate, row)
    return [
        {
            "ordem": idx,
            "capitulo_id": int(ch["id"]),
            "titulo": ch.get("titulo") or "Unidade " + str(idx),
            "nota": (float(selected[idx][1]["nota"])
                     if idx in selected and selected[idx][1].get("nota") is not None
                     else None),
            "nota_id": int(selected[idx][1]["id"]) if idx in selected else None,
        }
        for idx, ch in enumerate(chapters, 1)
    ]


def dashboard_grades(cursor, aluno_id, discipline_ids):
    """Busca capítulos e notas com apenas duas consultas para todas disciplinas."""
    ids = list(dict.fromkeys(int(x) for x in discipline_ids))
    if not ids:
        return {}, []
    cursor.execute(
        "SELECT id,disciplina_id,titulo FROM capitulos "
        "WHERE disciplina_id=ANY(%s) ORDER BY disciplina_id,id",
        (ids,),
    )
    chapters = defaultdict(list)
    for c in cursor.fetchall():
        chapters[int(c["disciplina_id"])].append(dict(c))
    cursor.execute(
        "SELECT n.id,n.disciplina_id,n.capitulo,n.nota,d.nome AS disciplina_nome "
        "FROM notas n JOIN disciplinas d ON d.id=n.disciplina_id "
        "WHERE n.aluno_id=%s ORDER BY n.disciplina_id,n.capitulo",
        (aluno_id,),
    )
    grades = [dict(row) for row in cursor.fetchall()]
    by_course = defaultdict(list)
    for row in grades:
        by_course[int(row["disciplina_id"])].append(row)
    logical = {
        disc_id: normalize_unit_grades(chapters[disc_id], by_course[disc_id])
        for disc_id in ids
    }
    return logical, grades
