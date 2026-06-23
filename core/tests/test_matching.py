from datetime import datetime, timedelta, timezone
from django.test import SimpleTestCase
from core.services.timespan import IdDates
from core.services import matching


def _iv(start_h, end_h):
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return IdDates("x", base + timedelta(hours=start_h), base + timedelta(hours=end_h))


class CompareTests(SimpleTestCase):
    def test_overlapping_windows(self):
        self.assertTrue(matching.compare(_iv(0, 2), _iv(1, 3)))

    def test_disjoint_windows(self):
        self.assertFalse(matching.compare(_iv(0, 1), _iv(2, 3)))


class AddDictTests(SimpleTestCase):
    def test_numeric_counts_sum(self):
        self.assertEqual(matching.add_dict({"Robin": "3"}, {"Robin": "2"}), {"Robin": "5"})

    def test_non_numeric_becomes_X(self):
        self.assertEqual(matching.add_dict({"Robin": "X"}, {"Robin": "2"}), {"Robin": "X"})

    def test_new_species_added(self):
        self.assertEqual(
            matching.add_dict({"Robin": "1"}, {"Jay": "2"}), {"Robin": "1", "Jay": "2"}
        )


class DescriptionTests(SimpleTestCase):
    def test_description_lines(self):
        self.assertEqual(matching.create_bird_description({"Robin": "3"}), "3 Robin\n")


class UpsertBlockTests(SimpleTestCase):
    def test_empty_description_gets_block_only(self):
        out = matching.upsert_block("", "3 Robin\n")
        self.assertTrue(out.startswith("<!-- roadrunner -->"))
        self.assertIn("3 Robin", out)

    def test_existing_text_is_preserved_and_block_appended(self):
        out = matching.upsert_block("My ride.", "3 Robin\n")
        self.assertTrue(out.startswith("My ride."))
        self.assertIn("<!-- roadrunner -->", out)

    def test_resync_replaces_block_not_duplicates(self):
        first = matching.upsert_block("My ride.", "3 Robin\n")
        second = matching.upsert_block(first, "5 Robin\n")
        self.assertEqual(second.count("<!-- roadrunner -->"), 1)
        self.assertIn("5 Robin", second)
        self.assertNotIn("3 Robin", second)

    def test_idempotent_same_input(self):
        once = matching.upsert_block("My ride.", "3 Robin\n")
        twice = matching.upsert_block(once, "3 Robin\n")
        self.assertEqual(once, twice)
