"""Calendario progressivo da disciplina: Capitulo 1 no D+3, 2 no D+6 etc.

Recebe a DATA DE MATRICULA (armazenada em aluno_disciplina_datas.data_inicio),
sem confundi-la com a data de emissao do contrato.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("America/Sao_Paulo")
MAX_CHAPTERS = 4


def chapter_release_date(enrollment_date: str, number: int) -> date:
    if not isinstance(number, int) or not 1 <= number <= MAX_CHAPTERS:
        raise ValueError("Numero de capitulo fora do intervalo 1..4")
    initial = datetime.strptime(enrollment_date, "%d/%m/%Y").date()
    return initial + timedelta(days=number * 3)


def chapter_is_released(enrollment_date: str, number: int, today: date | None = None) -> bool:
    date_today = today if today is not None else datetime.now(TIMEZONE).date()
    return date_today >= chapter_release_date(enrollment_date, number)


def released_chapters(enrollment_date: str, today: date | None = None) -> int:
    return sum(chapter_is_released(enrollment_date, chapter, today)
               for chapter in range(1, MAX_CHAPTERS + 1))
