import unittest
from decimal import Decimal

from initial_enrollments import (
    COURSES, INITIAL_COHORT_ID, _cpf_valido, validate_payload,
)


def cpf_teste(prefix):
    base = list(prefix)
    for length in (9, 10):
        digit = (sum(int(base[i]) * (length + 1 - i) for i in range(length)) * 10) % 11
        base.append(str(digit if digit < 10 else 0))
    return "".join(base)


class TestInitialEnrollments(unittest.TestCase):
    def setUp(self):
        self.people = [
            dict(nome="Pessoa Exemplo Um", cpf=cpf_teste("000000001"),
                 ra="10001001", email="p1@example.invalid",
                 disciplinas=list(COURSES[:2]),
                 valor_total="959.84", parcelas=8, meio_pagamento="picpay",
                 numero_contrato="CONTRATO-EXEMPLO-1"),
            dict(nome="Pessoa Exemplo Dois", cpf=cpf_teste("000000002"),
                 ra="10001002", email="p2@example.invalid",
                 disciplinas=[COURSES[2]],
                 valor_total="506.92", parcelas=1, meio_pagamento="qr_code",
                 numero_contrato=None),
        ]
        self.payload = {"operation": INITIAL_COHORT_ID, "alunos": self.people}

    def test_fixture_cpf_valido(self):
        self.assertTrue(all(_cpf_valido(x["cpf"]) for x in self.people))

    def test_two_students_three_courses(self):
        out = validate_payload(self.payload)
        self.assertEqual(len(out), 2)
        self.assertEqual(sum(len(x["disciplinas"]) for x in out), 3)
        self.assertEqual(out[0]["valor_total"], Decimal("959.84"))
        self.assertEqual(out[1]["valor_total"], Decimal("506.92"))

    def test_wrong_amount_rejected(self):
        self.people[1]["valor_total"] = "509.00"
        with self.assertRaises(ValueError):
            validate_payload(self.payload)

    def test_duplicate_ra_rejected(self):
        self.people[1]["ra"] = self.people[0]["ra"]
        with self.assertRaises(ValueError):
            validate_payload(self.payload)

    def test_inconsistent_disciplines_rejected(self):
        self.people[1]["disciplinas"] = [COURSES[1]]
        with self.assertRaises(ValueError):
            validate_payload(self.payload)

    def test_missing_identification_rejected(self):
        self.people[0]["cpf"] = None
        with self.assertRaises(ValueError):
            validate_payload(self.payload)


if __name__ == "__main__":
    unittest.main()
