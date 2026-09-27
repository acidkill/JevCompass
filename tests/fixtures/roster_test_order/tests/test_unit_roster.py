import unittest

from roster import import_roster


class RosterTransformationTests(unittest.TestCase):
    def test_import_deduplicates_casefolded_email_preserving_first_seen(self):
        people = import_roster([
            {"email": " Zoe@Example.test ", "name": " First Zoe ", "team": " Ops "},
            {"email": "alice@example.test", "name": "Alice", "team": "Data"},
            {"email": "zoe@example.test", "name": "Later Zoe", "team": "Other"},
        ])
        self.assertEqual(
            [(person.email, person.name, person.team) for person in people],
            [
                ("zoe@example.test", "First Zoe", "Ops"),
                ("alice@example.test", "Alice", "Data"),
            ],
        )

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(import_roster([]), [])

    def test_required_fields_are_nonempty_strings(self):
        invalid_records = [
            {"email": "", "name": "Name", "team": "Team"},
            {"email": "a@example.test", "name": " ", "team": "Team"},
            {"email": "a@example.test", "name": "Name", "team": None},
            {"name": "Name", "team": "Team"},
        ]
        for record in invalid_records:
            with self.subTest(record=record), self.assertRaises(ValueError):
                import_roster([record])


if __name__ == "__main__":
    unittest.main()
