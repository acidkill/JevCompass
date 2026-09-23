import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from jevcompass import catalog


class CatalogTests(unittest.TestCase):
    def test_catalog_is_curated_and_has_required_metadata(self):
        entries = catalog.load_catalog()
        self.assertGreaterEqual(len(entries), 19)
        required = {"id", "capability", "use_when", "avoid_when", "role", "cost", "privacy", "availability"}
        for entry in entries:
            self.assertTrue(required <= entry.keys(), entry["id"])
            self.assertTrue(entry["use_when"])
            self.assertTrue(entry["avoid_when"])
        ids = {entry["id"] for entry in entries}
        self.assertTrue({"serena", "smem", "sequential-thinking", "context7", "perplexity", "shell"} <= ids)
        self.assertNotIn("jev-use", ids)
        self.assertTrue({"create-plan", "kubernetes-gitops-workflow", "helm-chart-scaffolding", "python-packaging"} <= ids)
        self.assertFalse(any(identifier.startswith("tonis-") for identifier in ids))
        version = catalog.catalog_version()
        self.assertRegex(version, r"^sha256:[0-9a-f]{64}$")
        candidates = catalog.candidates("code", "any", limit=20)
        for item in candidates:
            self.assertIn(item["kind"], {"tool", "skill"})
            self.assertIsInstance(item["use_when"], str)
            self.assertIsInstance(item["avoid_when"], str)
            self.assertEqual(item["catalog_version"], version)

    def test_candidates_apply_task_role_domain_and_limit(self):
        fake = [
            {"id": "a", "task_kinds": ["code"], "role": "testing", "domains": ["software"], "availability": "available"},
            {"id": "b", "task_kinds": ["code"], "role": "research", "domains": ["software"], "availability": "available"},
            {"id": "c", "task_kinds": ["code"], "role": "planning", "domains": ["general"], "availability": "available"},
            {"id": "d", "task_kinds": ["code"], "role": "testing", "domains": ["software"], "availability": "unavailable"},
        ]
        with patch.object(catalog, "load_catalog", return_value=fake):
            self.assertEqual([x["id"] for x in catalog.candidates("CODE", "testing", "software")], ["a"])
            self.assertEqual(len(catalog.candidates("code", "any", limit=1)), 1)
            self.assertEqual([x["id"] for x in catalog.candidates("code", "any", "software")], ["a", "b", "c"])
            self.assertEqual(catalog.candidates("code", "testing", limit=0), [])

    def test_availability_validation_does_not_return_paths_or_config_values(self):
        fake_skills = {"jev-use": {"name": "jev-use", "description": "legacy advisory"}}
        with patch.object(catalog, "discover_installed_skills", return_value=fake_skills), \
                patch.object(catalog, "_configured_mcp_servers", return_value={"serena"}), \
                patch.object(catalog.shutil, "which", side_effect=lambda command: "/secret/path" if command in {"bash", "git"} else None):
            entries = catalog.load_catalog()
        by_id = {item["id"]: item for item in entries}
        self.assertEqual(by_id["serena"]["availability"], "configured")
        self.assertNotIn("jev-use", by_id)
        self.assertEqual(by_id["context7"]["availability"], "unavailable")
        self.assertNotIn("/secret/path", repr(entries))
        self.assertNotIn("availability_spec", repr(entries))

    def test_skill_discovery_is_bounded_metadata_only(self):
        with patch.object(catalog, "_SKILL_ROOTS", ()):
            self.assertEqual(catalog.discover_installed_skills(), {})

    def test_missing_bearer_token_marks_configured_mcp_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text('[mcp_servers.context7]\nurl = "https://example.invalid"\nbearer_token_env_var = "CTX_TEST_TOKEN"\n[mcp_servers.serena]\ncommand = "serena"\n')
            with patch.object(catalog, "_CONFIG", config), patch.dict(catalog.os.environ, {}, clear=True):
                self.assertEqual(catalog._configured_mcp_servers(), {"serena"})
            with patch.object(catalog, "_CONFIG", config), patch.dict(catalog.os.environ, {"CTX_TEST_TOKEN": "present"}, clear=True):
                self.assertEqual(catalog._configured_mcp_servers(), {"serena", "context7"})


if __name__ == "__main__":
    unittest.main()
