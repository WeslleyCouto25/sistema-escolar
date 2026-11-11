import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from payment_release import NOTICE, evaluate_course_access, VALIDATION_HOURS
from dashboard_grades import normalize_unit_grades

ZONE = ZoneInfo("America/Sao_Paulo")
NOW = datetime(2026, 11, 11, 16, 0, tzinfo=ZONE)


class AccessTests(unittest.TestCase):
    def test_pending_payment_is_blocked(self):
        state = evaluate_course_access("pendente", None, "11/11/2026", NOW)
        self.assertFalse(state["acesso_permitido"])
        self.assertEqual(state["motivo"], "pagamento_pendente")
        self.assertIn("24 horas", state["mensagem"])

    def test_paid_but_within_24h_is_blocked(self):
        state = evaluate_course_access("pago", NOW - timedelta(hours=23), "11/11/2026", NOW)
        self.assertFalse(state["acesso_permitido"])
        self.assertEqual(state["motivo"], "prazo_validacao")
        self.assertIn("documentação", state["mensagem"])

    def test_at_exact_24h_is_released(self):
        state = evaluate_course_access("pago", NOW - timedelta(hours=24), "11/11/2026", NOW)
        self.assertTrue(state["acesso_permitido"])
        self.assertEqual(state["prazo_horas"], VALIDATION_HOURS)

    def test_future_enrollment_date_still_blocks(self):
        state = evaluate_course_access("pago", NOW - timedelta(days=3), "15/11/2026", NOW)
        self.assertFalse(state["acesso_permitido"])
        self.assertEqual(state["motivo"], "matricula_futura")

    def test_legacy_confirmed_enrollment_not_retroactively_blocked(self):
        state = evaluate_course_access("pago", None, "01/11/2026", NOW)
        self.assertTrue(state["acesso_permitido"])

    def test_no_naive_datetimes(self):
        with self.assertRaises(ValueError):
            evaluate_course_access("pago", datetime(2026,11,11,0), "11/11/2026", NOW)


class GradeTests(unittest.TestCase):
    def test_prefers_canonical_note_over_legacy(self):
        chapters = [{"id": 101, "titulo": "Capítulo 1"}, {"id": 102, "titulo": "Capítulo 2"}]
        grades = [
            {"id": 1, "capitulo": 1, "nota": 6.0},
            {"id": 2, "capitulo": 101, "nota": 9.0},
            {"id": 3, "capitulo": 2, "nota": 8.0},
        ]
        result = normalize_unit_grades(chapters, grades)
        self.assertEqual([x["nota"] for x in result], [9.0, 8.0])

    def test_preserves_missing_notes(self):
        result = normalize_unit_grades([{"id": 20, "titulo": "Tema"}], [])
        self.assertIsNone(result[0]["nota"])
        self.assertEqual(result[0]["capitulo_id"], 20)


if __name__ == "__main__":
    unittest.main()
