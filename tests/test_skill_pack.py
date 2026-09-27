from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jevcompass import cli
from jevcompass import advisor
from jevcompass.catalog import candidates, load_catalog
from jevcompass import skill_pack
from jevcompass.skill_pack import SKILL_NAMES, install_skills


LEGACY_FOCUSED_TESTS = "---\nname: jevcompass-focused-tests\ndescription: Select and run focused tests after a code change, then complete repository-required validation.\n---\n\n# Focused tests after a change\n\n1. Read the applicable repository instructions and test configuration. Check the project docs or CI for required checks; do not assume a familiar command is correct for this repository.\n2. Inspect the change and its callers, interfaces, and affected behavior. Find existing tests that exercise those paths, then choose the smallest relevant test selection that can detect a regression.\n3. If coverage evidence already identifies the first check, run it directly: do not issue a JevCompass command merely to confirm your selection. For a serialization or boundary-mapping change, prefer the test that directly asserts the changed public output when alternatives only cover unchanged calculations and no measured runtime tradeoff remains. If multiple materially different candidates remain plausible, optional JevCompass ranking can help; use only verified coarse coverage/runtime metadata and preserve unknown facts. Skip ranking when preparing or invoking it is more work than making the choice locally. Advice does not execute tests or replace evidence.\n4. Run that focused selection first. Use the repository's documented runner and report the exact command and its real exit status.\n5. Copy the exact required test command from local CI or project instructions, including its flags, and run it after the focused check. Do not treat a focused pass as a substitute for a mandatory gate.\n6. Report what ran, what passed or failed, and any checks not run with the reason. A started command is not a passing check; never infer success from partial output or an earlier run with different inputs.\n\nAvoid broad test runs unrelated to the change when they are not required. If the change has no testable behavior, say why tests are unnecessary and still follow any required repository checks.\n"


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
                self.assertIn(f"{len(SKILL_NAMES)} new", install_skills(dry_run=True))
                self.assertFalse((codex / "skills").exists())
                self.assertIn(f"Installed {len(SKILL_NAMES)}", install_skills())
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
                    if name != "jevcompass-coding-workflow":
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
                self.assertEqual(cli.main(["skills", "install", "--dry-run", "--refresh"]), 0)
                self.assertFalse((codex / "skills").exists())
                self.assertEqual(cli.main(["skills", "install"]), 0)
                self.assertFalse((codex / "hooks.json").exists())
                self.assertEqual(cli.main(["skills", "install"]), 0)


    def _install_legacy_focused_skill(self, root: Path) -> bytes:
        skill_dir = root / "jevcompass-focused-tests"
        skill_dir.mkdir(parents=True)
        previous = LEGACY_FOCUSED_TESTS.encode("utf-8")
        self.assertEqual(
            hashlib.sha256(previous).hexdigest(),
            "2089cf0f10e781919ebcb09d32498e095abf751e1de029a7651593b692cc7911",
        )
        self.assertIn(
            hashlib.sha256(previous).hexdigest(),
            skill_pack._REFRESHABLE_SHA256["jevcompass-focused-tests"],
        )
        (skill_dir / "SKILL.md").write_bytes(previous)
        return previous

    def test_refresh_only_accepts_known_historical_version_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = self._install_legacy_focused_skill(root)
            installed = root / "jevcompass-focused-tests" / "SKILL.md"
            with self.assertRaisesRegex(ValueError, "conflicts"):
                install_skills(root=root)

            plan = install_skills(root=root, dry_run=True, refresh=True)
            self.assertIn("4 new, 1 refreshable", plan)
            self.assertIn("no files changed", plan)
            self.assertEqual(installed.read_bytes(), previous)
            backup_root = root / skill_pack._BACKUP_ROOT_NAME
            self.assertFalse(backup_root.exists())

            result = install_skills(root=root, refresh=True)
            self.assertIn("refreshed 1", result)
            current = skill_pack._bundled_content("jevcompass-focused-tests").encode("utf-8")
            self.assertEqual(installed.read_bytes(), current)
            digest = hashlib.sha256(previous).hexdigest()
            backup = skill_pack._backup_path(root, "jevcompass-focused-tests", digest)
            self.assertEqual(backup.read_bytes(), previous)
            self.assertIn("no files changed", install_skills(root=root, refresh=True))
            self.assertEqual(backup.read_bytes(), previous)

    def test_refresh_refuses_custom_skill_without_partial_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = root / "jevcompass-focused-tests"
            custom.mkdir()
            (custom / "SKILL.md").write_text("user-edited skill", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "conflicts"):
                install_skills(root=root, refresh=True)
            self.assertEqual((custom / "SKILL.md").read_text(), "user-edited skill")
            self.assertFalse((root / "jevcompass-regression-review").exists())
            self.assertFalse((root / skill_pack._BACKUP_ROOT_NAME).exists())

    def test_refresh_refuses_tampered_backup_before_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = self._install_legacy_focused_skill(root)
            digest = hashlib.sha256(previous).hexdigest()
            backup = skill_pack._backup_path(root, "jevcompass-focused-tests", digest)
            backup.parent.mkdir(parents=True)
            backup.write_text("not a verified backup", encoding="utf-8")
            destination = root / "jevcompass-focused-tests" / "SKILL.md"
            with self.assertRaisesRegex(ValueError, "backup destination conflicts"):
                install_skills(root=root, refresh=True)
            self.assertEqual(destination.read_bytes(), previous)
            self.assertFalse((root / "jevcompass-regression-review").exists())

    def test_refresh_rolls_back_replaced_skill_if_later_install_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = self._install_legacy_focused_skill(root)
            real_create = skill_pack._create_new_file

            def fail_new_skill(path, content, *, mode):
                if path.parent.name != "jevcompass-focused-tests":
                    raise OSError("injected write failure")
                return real_create(path, content, mode=mode)

            with mock.patch.object(skill_pack, "_create_new_file", side_effect=fail_new_skill):
                with self.assertRaisesRegex(OSError, "injected write failure"):
                    install_skills(root=root, refresh=True)
            installed = root / "jevcompass-focused-tests" / "SKILL.md"
            self.assertEqual(installed.read_bytes(), previous)
            self.assertFalse((root / "jevcompass-regression-review").exists())
            self.assertFalse((root / skill_pack._BACKUP_ROOT_NAME).exists())

    def test_refresh_keeps_backup_if_rollback_cannot_restore_old_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = self._install_legacy_focused_skill(root)
            real_replace = skill_pack.os.replace
            real_create = skill_pack._create_new_file
            calls = 0

            def fail_restore(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected restore failure")
                return real_replace(source, destination)

            def fail_new_skill(path, content, *, mode):
                if path.parent.name != "jevcompass-focused-tests":
                    raise OSError("injected later install failure")
                return real_create(path, content, mode=mode)

            with mock.patch.object(skill_pack.os, "replace", side_effect=fail_restore):
                with mock.patch.object(skill_pack, "_create_new_file", side_effect=fail_new_skill):
                    with self.assertRaisesRegex(RuntimeError, "rollback was incomplete"):
                        install_skills(root=root, refresh=True)
            backup = skill_pack._backup_path(
                root, "jevcompass-focused-tests", hashlib.sha256(previous).hexdigest(),
            )
            self.assertEqual(backup.read_bytes(), previous)
            self.assertEqual(calls, 2)

if __name__ == "__main__":
    unittest.main()
    unittest.main()
