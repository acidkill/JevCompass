"""Command-line interface for JevCompass."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any

from . import __version__, advisor
from .catalog import catalog_version, load_catalog
from .decisions import DecisionsClient, DecisionsError, configured_model, model_available
from .installer import install


CATEGORIES = tuple(advisor.CATALOG_TASKS)
DOMAINS = ("general", "software", "python", "web", "kubernetes")
ROLES = ("primary", "planner", "explorer", "worker", "luna_worker")


def _recommend(category: str, domain: str, role: str) -> int:
    event_name = "UserPromptSubmit" if role in {"primary", "planner"} else "SubagentStart"
    output = advisor.select_advice(event_name, category, domain, role)
    if output:
        print(output["hookSpecificOutput"]["additionalContext"])
    else:
        print("No recommendation available; continue with the normal Codex workflow.")
    return 0


def doctor(*, test_jev: bool = False) -> dict[str, Any]:
    entries = load_catalog()
    codex_home = Path.home() / ".codex"
    hooks_path = codex_home / "hooks.json"
    hook_command = advisor.hook_command()
    checks: dict[str, Any] = {
        "python": {"ok": sys.version_info >= (3, 11), "version": platform.python_version(), "required": ">=3.11"},
        "openrouter_key": {"ok": bool(os.environ.get("OPENROUTER_API_KEY")), "configured": bool(os.environ.get("OPENROUTER_API_KEY"))},
        "jev_model": {"ok": model_available(), "id": configured_model(), "check": "public model metadata"},
        "catalog": {
            "ok": bool(entries) and not any(item["id"].startswith("tonis-") for item in entries),
            "curated_entries": len(entries),
            "version": catalog_version(),
        },
        "hooks_json": {"ok": False, "registered_advisory_hooks": [], "pretool_jev_gate": False},
    }
    try:
        config = json.loads(hooks_path.read_text(encoding="utf-8"))
        hooks = config.get("hooks", {})
        registered = []
        for event_name in ("UserPromptSubmit", "SubagentStart"):
            if any(handler.get("command") == hook_command
                   for group in hooks.get(event_name, [])
                   for handler in group.get("hooks", [])):
                registered.append(event_name)
        pretool = any(
            "jev" in handler.get("command", "").lower()
            for group in hooks.get("PreToolUse", [])
            for handler in group.get("hooks", [])
        )
        checks["hooks_json"] = {
            "ok": registered == ["UserPromptSubmit", "SubagentStart"] and not pretool,
            "registered_advisory_hooks": registered,
            "pretool_jev_gate": pretool,
        }
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if test_jev:
        try:
            answer = DecisionsClient().decide(
                {"task_kind": "testing", "role": "planner", "domain": "software"},
                {"tool": {"type": "choice", "instructions": "Choose the direct test tool.",
                          "criteria": {"pytest": "Tests Python code", "browser": "Tests browser interactions"}}},
            )
            choice = answer.get("tool", {})
            checks["live_decision"] = {"ok": isinstance(choice, dict) and
                                        choice.get("choice") in {"pytest", "browser"},
                                        "billed_request": True}
        except DecisionsError:
            checks["live_decision"] = {"ok": False, "billed_request": True}
    checks["ok"] = all(check["ok"] for check in checks.values() if isinstance(check, dict) and "ok" in check)
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jevcompass", description="Privacy-first tool and skill advice for Codex")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hook", help="Handle a Codex hook event from stdin")
    recommend = sub.add_parser("recommend", help="Request advice using allowlisted metadata only")
    recommend.add_argument("--category", required=True, choices=CATEGORIES)
    recommend.add_argument("--domain", required=True, choices=DOMAINS)
    recommend.add_argument("--role", choices=ROLES, default="primary")
    doctor_parser = sub.add_parser("doctor", help="Check local runtime, catalog, and hook registration")
    doctor_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    doctor_parser.add_argument("--test-jev", action="store_true", help="Send one synthetic, billed Jev request")
    installer = sub.add_parser("install", help="Merge the two advisory hooks")
    installer.add_argument("--dry-run", action="store_true", help="Validate planned changes without writing files")
    args = parser.parse_args(argv)
    if args.command == "hook":
        return advisor.hook_main()
    if args.command == "recommend":
        return _recommend(args.category, args.domain, args.role)
    if args.command == "doctor":
        result = doctor(test_jev=args.test_jev)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"JevCompass {__version__}: {'PASS' if result['ok'] else 'CHECK REQUIRED'}")
            for name, check in result.items():
                if name != "ok":
                    print(f"- {name}: {'PASS' if check['ok'] else 'CHECK'}")
            print("Configured MCP names and skill metadata never prove a tool is connected in this session.")
        return 0 if result["ok"] else 1
    if args.command == "install":
        try:
            result = install(dry_run=args.dry_run)
        except (OSError, ValueError, RuntimeError) as error:
            print(f"JevCompass install failed: {error}", file=sys.stderr)
            return 1
        print(result)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
