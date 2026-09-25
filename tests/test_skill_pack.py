from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jevcompass import cli
from jevcompass import advisor
from jevcompass.catalog import candidates, load_catalog
from jevcompass.skill_pack import SKILL_NAMES, install_skills


class SkillPackTests(unittest.TestCase):
    def test_dry_run_install_and_repeat_preserve_hook_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            codex = home / "codex"
            codex.mkdir()
            hooks = codex / "hooks.json"
            hooks.write_text('{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"smem"}]}]}}')
            original = hooks.read_bytes()
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex)}):
                self.assertIn("4 new", install_skills(dry_run=True))
                self.assertFalse((codex / "skills").exists())
                self.assertIn("Installed 4", install_skills())
                self.assertIn("no files changed", install_skills())
                self.assertEqual(hooks.read_bytes(), original)
                entries = {item["id"]: item for item in load_catalog()}
                planning = candidates(task_kind="planning", role="any", domain="general", limit=20)
                choices = advisor._questions(planning)
                self.assertTrue({"jevcompass-plan-implementation", "jevcompass-plan-cutover"}
                                <= set(choices["skill"]["criteria"]))
                for name in SKILL_NAMES:
                    path = codex / "skills" / name / "SKILL.md"
                    self.assertTrue(path.is_file())
                    self.assertIn(f"name: {name}", path.read_text())
                    self.assertEqual(entries[name]["availability"], "available")

    def test_conflict_is_preflighted_without_partial_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conflicting = root / SKILL_NAMES[-1]
            conflicting.mkdir()
            (conflicting / "SKILL.md").write_text("user-owned skill")
            with self.assertRaisesRegex(ValueError, "conflicts"):
                install_skills(root=root)
            self.assertFalse((root / SKILL_NAMES[0]).exists())
            self.assertEqual((conflicting / "SKILL.md").read_text(), "user-owned skill")

    def test_symlink_destination_and_root_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            owned = base / "owned"
            owned.mkdir()
            (base / "linked").symlink_to(owned, target_is_directory=True)
            with self.assertRaises(ValueError):
                install_skills(root=base / "linked")
            (owned / SKILL_NAMES[0]).symlink_to(base, target_is_directory=True)
            with self.assertRaises(ValueError):
                install_skills(root=owned)

    def test_cli_skill_install_is_explicit_and_nonblocking(self):
        with tempfile.TemporaryDirectory() as directory:
            codex = Path(directory) / "codex"
            codex.mkdir()
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex)}):
                self.assertEqual(cli.main(["skills", "install", "--dry-run"]), 0)
                self.assertFalse((codex / "skills").exists())
                self.assertEqual(cli.main(["skills", "install"]), 0)
                self.assertFalse((codex / "hooks.json").exists())
                self.assertEqual(cli.main(["skills", "install"]), 0)


if __name__ == "__main__":
    unittest.main()
