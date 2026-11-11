import unittest
from datetime import date, timedelta
from chapter_release import chapter_is_released, chapter_release_date, released_chapters


class TestChapterRelease(unittest.TestCase):
    def test_schedule_starts_three_days_after_enrollment(self):
        expected = (date(2026, 11, 14), date(2026, 11, 17),
                    date(2026, 11, 20), date(2026, 11, 23))
        for number, release in enumerate(expected, 1):
            self.assertEqual(chapter_release_date("11/11/2026", number), release)

    def test_prior_to_d3_no_chapter_available(self):
        for today in [date(2026,11,11), date(2026,11,12), date(2026,11,13)]:
            self.assertEqual(released_chapters("11/11/2026", today), 0)

    def test_each_chapter_appears_only_on_its_release_day(self):
        for n in range(1, 5):
            day = chapter_release_date("11/11/2026", n)
            self.assertFalse(chapter_is_released("11/11/2026", n, day - timedelta(days=1)))
            self.assertTrue(chapter_is_released("11/11/2026", n, day))
            self.assertEqual(released_chapters("11/11/2026", day), n)

    def test_invalid_chapter_numbers_denied(self):
        for number in [0,-1,5,10]:
            with self.assertRaises(ValueError):
                chapter_release_date("11/11/2026", number)


if __name__ == "__main__":
    unittest.main()
