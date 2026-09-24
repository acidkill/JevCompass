"""Keep the synthetic workflow pilot fixture separate from evaluator answers."""
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).parent
FIXTURE = ROOT / "fixtures" / "workflow_review"
EXPECTED = ROOT / "evaluation" / "w02_expected.md"


class WorkflowFixtureTests(unittest.TestCase):
    def test_w02_fixture_can_be_copied_without_evaluator_key(self):
        self.assertTrue(EXPECTED.is_file())
        self.assertFalse((FIXTURE / "expected-result.md").exists())
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "fixture"
            shutil.copytree(FIXTURE, copied)
            self.assertTrue((copied / "AGENTS.md").is_file())
            self.assertTrue((copied / "repo-map.md").is_file())
            self.assertTrue((copied / "clients/fjordly-labs/drafts/proposal-draft.md").is_file())
            self.assertFalse(any("expected" in path.name.lower() for path in copied.rglob("*")))

    def test_w02_fixture_distinguishes_current_from_legacy_rate(self):
        canonical = (FIXTURE / "canonical-pricing.md").read_text(encoding="utf-8")
        legacy = (FIXTURE / "legacy-pricing.md").read_text(encoding="utf-8")
        draft = (FIXTURE / "clients/fjordly-labs/drafts/proposal-draft.md").read_text(encoding="utf-8")
        self.assertIn("NOK 12,500", canonical)
        self.assertIn("NOK 9,800", legacy)
        self.assertIn("NOK 9,800", draft)
        self.assertIn("source-system count not confirmed", draft)
        self.assertNotIn("NOK 12,500", draft)


if __name__ == "__main__":
    unittest.main()
