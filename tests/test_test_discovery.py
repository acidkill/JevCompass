"""Synthetic-repository coverage for local focused-test discovery."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from jevcompass.test_discovery import discover_test_candidates
from jevcompass.test_order import TestCandidate, rank_tests


class TestDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Synthetic Tests")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True)

    def write(self, relative, text=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def test_discovers_staged_unstaged_and_untracked_python_changes(self):
        self.write("src/alpha.py", "VALUE = 1\\n")
        self.write("src/beta.py", "VALUE = 1\\n")
        self.write("src/gamma.py", "VALUE = 1\\n")
        self.write("tests/test_alpha.py")
        self.write("tests/test_beta.py")
        self.write("tests/test_gamma.py")
        self.commit()

        self.write("src/alpha.py", "VALUE = 2\\n")
        self.git("add", "src/alpha.py")
        self.write("src/beta.py", "VALUE = 2\\n")
        self.write("src/gamma.py", "VALUE = 2\\n")
        candidates = discover_test_candidates(self.root)

        self.assertEqual(len(candidates), 3)
        self.assertEqual({candidate.kind for candidate in candidates}, {"unit"})
        self.assertEqual({candidate.relevance for candidate in candidates}, {0.85})
        self.assertEqual({candidate.command for candidate in candidates}, {
            "python -m unittest discover -s tests -p test_alpha.py -v",
            "python -m unittest discover -s tests -p test_beta.py -v",
            "python -m unittest discover -s tests -p test_gamma.py -v",
        })

    def test_unit_and_integration_candidates_create_real_remote_choice(self):
        self.write("src/widget.py", "VALUE = 1\n")
        self.write("tests/test_widget.py", "import unittest\n")
        self.write("tests/integration/test_widget.py", "import unittest\n")
        self.commit()
        self.write("src/widget.py", "VALUE = 2\n")
        candidates = discover_test_candidates(self.root)
        self.assertEqual({candidate.kind for candidate in candidates}, {"unit", "integration"})
        self.assertEqual(len(candidates), 2)

        class Client:
            def __init__(self):
                self.calls = []

            def decide(self, state, questions):
                self.calls.append((state, questions))
                return {"first": {"type": "choice", "choice": "t2", "confidence": 0.9}}

        client = Client()
        result = rank_tests("python", candidates, [], client)
        self.assertEqual(result.status, "remote-choice")
        self.assertEqual(len(client.calls), 1)
        self.assertNotIn("test_widget.py", repr(client.calls))

    def test_no_changes_and_unsupported_language_abstain(self):
        self.write("src/widget.py", "value = 1\\n")
        self.write("tests/test_widget.py")
        self.commit()
        self.assertEqual(discover_test_candidates(self.root), ())

        self.write("src/widget.js", "value = 2\\n")
        self.assertEqual(discover_test_candidates(self.root), ())

    def test_changed_test_file_is_selected_directly(self):
        path = self.write("tests/test_widget.py", "import unittest\\n")
        candidate, = discover_test_candidates(self.root)
        self.assertEqual(candidate.command,
                         "python -m unittest discover -s tests -p test_widget.py -v")
        self.assertEqual(candidate.relevance, 1.0)
        self.assertTrue(path.is_file())

    def test_ambiguous_mapping_and_unsafe_or_symlink_test_files_abstain(self):
        self.write("src/widget.py", "value = 1\\n")
        self.write("tests/test_widget.py")
        self.write("other/test_widget.py")
        self.write("tests/test_widget[unsafe].py")
        self.commit()
        self.write("src/widget.py", "value = 2\\n")
        outside = self.root.parent / (self.root.name + "-outside.py")
        outside.write_text("", encoding="utf-8")
        try:
            (self.root / "tests" / "test_link.py").symlink_to(outside)
            # The source mapping is ambiguous; symlink and unsafe names do not
            # create additional selectable candidates.
            self.assertEqual(discover_test_candidates(self.root), ())
        finally:
            outside.unlink(missing_ok=True)

    def test_bounded_scan_abstains_if_scan_would_be_incomplete(self):
        self.write("src/widget.py")
        self.write("tests/test_widget.py")
        self.write("tests/nested/test_extra.py")
        self.assertEqual(discover_test_candidates(self.root, max_scan_entries=1), ())

    def test_discovery_is_read_only_and_candidates_stay_local_to_ranker(self):
        self.write("src/widget.py")
        self.write("tests/test_widget.py")
        candidate, = discover_test_candidates(self.root)
        self.assertIsInstance(candidate, TestCandidate)

        class Client:
            def __init__(self):
                self.request = None

            def decide(self, state, questions):
                self.request = (state, questions)
                return {"first": {"type": "choice", "choice": "t1", "confidence": 0.99}}

        client = Client()
        rank_tests("python", [candidate, TestCandidate("integration", "private command")],
                   [], client)
        request_text = repr(client.request)
        self.assertNotIn(candidate.command, request_text)
        self.assertNotIn("tests/test_widget.py", request_text)
        self.assertFalse((self.root / ".git" / "index.lock").exists())


if __name__ == "__main__":
    unittest.main()
