"""Catalog integration for the optional phase-aware coding workflow skill."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jevcompass import advisor, catalog


SKILL_ID = "jevcompass-coding-workflow"


class CodingWorkflowCatalogTests(unittest.TestCase):
    def _entry_spec(self):
        data = json.loads((Path(catalog.__file__).with_name("catalog_data.json"))
                          .read_text(encoding="utf-8"))
        return next(item for item in data["entries"] if item["id"] == SKILL_ID)

    def _isolated_catalog(self, root: Path):
        return (
            patch.object(catalog, "_skill_roots", return_value=(root,)),
            patch.object(catalog, "_configured_mcp_servers", return_value=set()),
            patch.object(catalog, "_codex_shell_available", return_value=False),
            patch.object(catalog, "_ci_test_runner", return_value=None),
            patch.object(catalog, "_read_local_catalog", return_value=[]),
            patch.object(catalog, "_inside_git_checkout", return_value=False),
            patch.object(catalog.shutil, "which", return_value=None),
        )

    def test_curated_entry_uses_valid_phase_and_privacy_metadata(self):
        entry = self._entry_spec()
        required = {
            "id", "capability", "use_when", "avoid_when", "role", "cost",
            "privacy", "availability", "task_kinds", "domains",
        }
        self.assertTrue(required <= entry.keys())
        self.assertTrue(entry["use_when"])
        self.assertTrue(entry["avoid_when"])
        self.assertEqual(entry["role"], "guidance")
        self.assertEqual(entry["cost"], "low")
        self.assertEqual(entry["availability"], {
            "kind": "skill",
            "name": SKILL_ID,
        })
        self.assertEqual(entry["task_kinds"], ["code", "debug", "testing"])
        self.assertEqual(entry["domains"], ["software"])
        self.assertIn("allowlisted coarse task metadata", entry["privacy"])

    def test_only_actual_install_enables_relevant_tasks_and_irrelevant_work_stays_excluded(self):
        bundled_skill = (Path(catalog.__file__).parent / "bundled_skills" /
                         SKILL_ID / "SKILL.md")
        self.assertTrue(bundled_skill.is_file())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patches = self._isolated_catalog(root)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                unavailable = next(item for item in catalog.load_catalog()
                                   if item["id"] == SKILL_ID)
                self.assertEqual(unavailable["availability"], "unavailable")
                for category in ("coding", "debugging", "testing"):
                    task_kind = advisor.CATALOG_TASKS[category]
                    self.assertNotIn(
                        SKILL_ID,
                        {item["id"] for item in catalog.candidates(
                            task_kind, "any", "software", limit=30,
                        )},
                    )

                installed = root / SKILL_ID / "SKILL.md"
                installed.parent.mkdir()
                installed.write_text(
                    "---\nname: jevcompass-coding-workflow\n"
                    "description: Optional phase-aware coding workflow.\n---\n",
                    encoding="utf-8",
                )

                available = next(item for item in catalog.load_catalog()
                                 if item["id"] == SKILL_ID)
                self.assertEqual(available["availability"], "available")
                for category in ("coding", "debugging", "testing"):
                    task_kind = advisor.CATALOG_TASKS[category]
                    choices = catalog.candidates(
                        task_kind, "any", "software", limit=30,
                    )
                    selected = next(item for item in choices if item["id"] == SKILL_ID)
                    self.assertEqual(selected["kind"], "skill")
                    self.assertEqual(selected["role"], "guidance")
                    self.assertEqual(selected["availability"], "available")
                    self.assertIn("only allowlisted", selected["privacy"])

                irrelevant_tasks = (
                    advisor.CATALOG_TASKS["documentation"],
                    "research",
                    "ops",
                    "planning",
                )
                for task_kind in irrelevant_tasks:
                    with self.subTest(task_kind=task_kind):
                        self.assertNotIn(
                            SKILL_ID,
                            {item["id"] for item in catalog.candidates(
                                task_kind, "any", "software", limit=30,
                            )},
                        )
                self.assertNotIn(
                    SKILL_ID,
                    {item["id"] for item in catalog.candidates(
                        "code", "planner", "software", limit=30,
                    )},
                )


if __name__ == "__main__":
    unittest.main()
