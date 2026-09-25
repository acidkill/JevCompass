"""Command-line interface for JevCompass."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import tomllib
from typing import Any

from . import __version__, advisor
from .catalog import catalog_snapshot, catalog_version, register_local_skill
from .paths import codex_profile_id, resolve_codex_home
from .decisions import DecisionsClient, DecisionsError, model_status
from .installer import (
    SPAWN_ADVICE_MATCHER,
    SUBAGENT_MATCHER,
    _is_legacy_gate,
    _is_product_hook,
    install,
)
from .credentials import auth_main, credential_status


CATEGORIES = tuple(advisor.CATALOG_TASKS)
DOMAINS = ("general", "software", "python", "web", "shell", "kubernetes", "codex", "security")
ROLES = ("primary", "planner", "explorer", "worker")


def _recommend(category: str, domain: str, role: str) -> int:
    event_name = "UserPromptSubmit" if role in {"primary", "planner"} else "SubagentStart"
    output = advisor.select_advice(event_name, category, domain, role)
    if output:
        print(output["hookSpecificOutput"]["additionalContext"])
    else:
        print("No recommendation available; continue with the normal Codex workflow.")
    return 0


def _selection_capacity(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Report local catalog choice capacity for representative vanilla tasks."""
    cases = (
        ("source_review_general", "source-review", "general"),
        ("codebase_software", "codebase", "software"),
        ("coding_python", "code", "python"),
    )
    result: dict[str, Any] = {}
    for label, task, domain in cases:
        counts = {"tool": 0, "skill": 0}
        candidate_ids: list[str] = []
        inherited = {"python": "software", "web": "software"}.get(domain)
        for item in entries:
            if item.get("availability") != "available" or task not in item.get("task_kinds", ()):
                continue
            domains = item.get("domains", ())
            if not any(value in domains for value in (domain, "general", inherited) if value):
                continue
            kind = item.get("kind")
            if kind in counts:
                counts[kind] = min(counts[kind] + 1, 3)
                candidate_ids.append(item.get("id", ""))
        total = sum(counts.values())
        result[label] = {
            "available_tools": counts["tool"],
            "available_skills": counts["skill"],
            "mode": (
                "decision_candidates" if max(counts.values()) >= 2
                else "low_signal_skip" if candidate_ids == ["exec_command"]
                else "local_candidates" if total else "silent"
            ),
        }
    return {"ok": True, "examples": result,
            "note": "Local availability only; active-session tool access and advice usefulness are unverified."}


def _hook_observation() -> dict[str, Any]:
    """Return bounded, redacted last statuses per hook from the metric tail."""
    allowed_events = ("UserPromptSubmit", "SubagentStart", "PreToolUse")
    allowed_statuses = {
        "cache", "jev", "local", "skip", "insufficient-candidates", "low-signal-skip",
        "classification-skip", "role-skip", "collab-plan", "collab-unavailable",
    }
    empty = {"observed": False, "event": None, "log_modified_age_seconds": None,
             "status": "unavailable", "recent_by_event": {}}
    try:
        current_profile = codex_profile_id()
        stat = advisor.LOG_PATH.stat()
        with advisor.LOG_PATH.open("rb") as stream:
            stream.seek(max(0, stat.st_size - 65536))
            lines = stream.read(65536).splitlines()
    except OSError:
        return empty

    statuses: dict[str, str] = {}
    latest_event: str | None = None
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or record.get("profile") != current_profile:
            continue
        event, status = record.get("event"), record.get("status")
        if event in allowed_events and isinstance(status, str) and status in allowed_statuses:
            if latest_event is None:
                latest_event = event
            statuses.setdefault(event, status)
            if len(statuses) == len(allowed_events):
                break
    if latest_event is None:
        return empty
    return {
        "observed": True,
        "event": latest_event,
        "log_modified_age_seconds": max(0, int(time.time() - stat.st_mtime)),
        "status": statuses[latest_event],
        "recent_by_event": {event: statuses[event] for event in allowed_events if event in statuses},
    }


def doctor(*, test_jev: bool = False) -> dict[str, Any]:
    entries, catalog_counts = catalog_snapshot()
    codex_home_source = "environment" if os.environ.get("CODEX_HOME") else "default"
    hooks_path = resolve_codex_home() / "hooks.json"
    hook_command = advisor.hook_command()
    hooks_feature = {"ok": True, "base_config": "not-disabled"}
    try:
        config_data = tomllib.loads((resolve_codex_home() / "config.toml").read_text(encoding="utf-8"))
        features = config_data.get("features", {})
        if isinstance(features, dict) and features.get("hooks", features.get("codex_hooks")) is False:
            hooks_feature = {"ok": False, "base_config": "disabled"}
    except FileNotFoundError:
        pass
    except (OSError, tomllib.TOMLDecodeError, TypeError, AttributeError):
        hooks_feature = {"ok": False, "base_config": "unreadable"}
    key_status = credential_status()
    key_configured = key_status["configured"]
    model_check_status = model_status()
    checks: dict[str, Any] = {
        "python": {"ok": sys.version_info >= (3, 11), "version": platform.python_version(), "required": ">=3.11"},
        "codex_home": {"ok": True, "source": codex_home_source},
        "hooks_feature": hooks_feature,
        "openrouter_key": {"ok": key_configured, **key_status},
        "jev_model": {
            "ok": model_check_status != "missing",
            "status": model_check_status,
            "available": model_check_status == "available",
            "source": "environment" if os.environ.get("JEVCOMPASS_MODEL") else "default",
            "check": "public model metadata",
        },
        "catalog": {
            "ok": bool(entries) and not any(item["id"].startswith("tonis-") for item in entries),
            **catalog_counts,
            "version": catalog_version(),
        },
        "selection_capacity": _selection_capacity(entries),
        "hooks_json": {
            "ok": False,
            "registered_advisory_hooks": [],
            "pretool_jev_gate": False,
            "spawn_advice": {"registered": False, "safe": True},
            "malformed_product_pretool": False,
        },
        "hook_observation": _hook_observation(),
    }
    try:
        config = json.loads(hooks_path.read_text(encoding="utf-8"))
        hooks = config.get("hooks", {})
        registered = []
        for event_name in ("UserPromptSubmit", "SubagentStart"):
            if any(handler.get("command") == hook_command
                   for group in hooks.get(event_name, [])
                   if event_name != "SubagentStart" or group.get("matcher") == SUBAGENT_MATCHER
                   for handler in group.get("hooks", [])):
                registered.append(event_name)
        pretool_groups = hooks.get("PreToolUse", [])
        pretool = any(
            _is_legacy_gate(handler.get("command", ""))
            for group in pretool_groups if isinstance(group, dict)
            for handler in group.get("hooks", [])
            if isinstance(group.get("hooks", []), list) and isinstance(handler, dict)
        )
        product_pretool = [
            (group, handler)
            for group in pretool_groups if isinstance(group, dict)
            for handler in group.get("hooks", [])
            if isinstance(group.get("hooks", []), list)
            and isinstance(handler, dict) and _is_product_hook(handler.get("command", ""))
        ]
        canonical_spawn = any(
            group.get("matcher") == SPAWN_ADVICE_MATCHER
            and handler.get("command") == hook_command
            and handler.get("timeout") == 2
            for group, handler in product_pretool
        )
        malformed_product_pretool = any(
            group.get("matcher") != SPAWN_ADVICE_MATCHER
            or handler.get("command") != hook_command
            or handler.get("timeout") != 2
            for group, handler in product_pretool
        )
        checks["hooks_json"] = {
            "ok": registered == ["UserPromptSubmit", "SubagentStart"]
            and not pretool and not malformed_product_pretool,
            "registered_advisory_hooks": registered,
            "pretool_jev_gate": pretool,
            "spawn_advice": {"registered": canonical_spawn, "safe": not malformed_product_pretool},
            "malformed_product_pretool": malformed_product_pretool,
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
    # Remote selection credentials are optional; their status is diagnostic only.
    checks["ok"] = all(
        check["ok"] for name, check in checks.items()
        if name != "openrouter_key" and isinstance(check, dict) and "ok" in check
    )
    return checks


def _display_test_order(result: Any, as_json: bool) -> int:
    payload = {
        "status": result.status,
        "ordered": [{"id": item.candidate_id, "kind": item.kind.value,
                     "command": item.command} for item in result.ordered_candidates],
        "required": [{"id": item.required_id, "command": item.command}
                     for item in result.required],
        "executed": False,
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Focused checks in suggested order ({result.status}; none executed):")
        for index, item in enumerate(result.ordered_candidates, start=1):
            print(f"{index}. {item.command}")
        print("Required repository checks (always run):")
        for item in result.required:
            print(f"- {item.command}")
        if not result.required:
            print("- None supplied; confirm repository-required validation separately.")
    return 0


def _discover_tests_cli(repo: str, required_commands: list[str], as_json: bool) -> int:
    """Discover local Python focused checks and keep caller-provided gates."""
    from .test_discovery import discover_test_candidates
    from .test_order import RequiredTest, rank_tests

    if any(not 1 <= len(command) <= 512 or "\n" in command for command in required_commands):
        print("Invalid required test command; no command was executed.", file=sys.stderr)
        return 2
    candidates = discover_test_candidates(repo)
    required = [RequiredTest(command, f"required-{index}")
                for index, command in enumerate(required_commands, start=1)]
    result = rank_tests("python", candidates, required)
    return _display_test_order(result, as_json)


def _rank_tests_cli(source: str, as_json: bool) -> int:
    """Order explicit local commands; never execute them or send them to Jev."""
    from pathlib import Path
    from .test_order import rank_tests

    try:
        if source == "-":
            raw = sys.stdin.buffer.read(65_537)
        else:
            path = Path(source).expanduser()
            if path.stat().st_size > 65_536:
                raise ValueError("input-too-large")
            raw = path.read_bytes()
        if len(raw) > 65_536:
            raise ValueError("input-too-large")
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"surface", "candidates", "required"}:
            raise ValueError("invalid-shape")
        candidates, required = data["candidates"], data["required"]
        if (not isinstance(candidates, list) or not 1 <= len(candidates) <= 8
                or not isinstance(required, list) or len(required) > 8):
            raise ValueError("invalid-list-size")
        for item in candidates + required:
            if (not isinstance(item, dict) or not isinstance(item.get("command"), str)
                    or not 1 <= len(item["command"]) <= 512):
                raise ValueError("invalid-command")
        result = rank_tests(data["surface"], candidates, required)
    except (OSError, ValueError, TypeError):
        print("Invalid local test metadata; no command was executed.", file=sys.stderr)
        return 2
    return _display_test_order(result, as_json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jevcompass", description="Privacy-first tool and skill advice for Codex")
    parser.add_argument("--version", action="version", version=f"JevCompass {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hook", help="Handle a Codex hook event from stdin")
    recommend = sub.add_parser("recommend", help="Request advice using allowlisted metadata only")
    recommend.add_argument("--category", required=True, choices=CATEGORIES)
    recommend.add_argument("--domain", required=True, choices=DOMAINS)
    recommend.add_argument("--role", choices=ROLES, default="primary")
    triage_parser = sub.add_parser("triage", help="Rank diagnostic steps from allowlisted failure metadata")
    triage_parser.add_argument("--exit-code", required=True, type=int, help="Observed failing test process exit code")
    from .triage import FailureKind, HypothesisId, ImportObservation
    triage_parser.add_argument("--kind", action="append", required=True,
                               choices=tuple(item.value for item in FailureKind))
    triage_parser.add_argument("--hypothesis", action="append", required=True,
                               choices=tuple(item.value for item in HypothesisId))
    triage_parser.add_argument("--import-observation", action="append",
                               choices=tuple(item.value for item in ImportObservation),
                               help="Allowlisted local import-spec observation; may be repeated")
    triage_parser.add_argument("--json", action="store_true", help="Print machine-readable result")
    strategy_parser = sub.add_parser("strategy", help="Choose a coding strategy from allowlisted signals")
    strategy_sub = strategy_parser.add_subparsers(dest="strategy_action", required=True)
    strategy_choose = strategy_sub.add_parser("choose", help="Get up to two reviewed pretask strategies")
    strategy_choose.add_argument("--kind", required=True, choices=("coding", "debugging", "testing", "review"))
    strategy_choose.add_argument("--signal", action="append", required=True,
                                 choices=("failing_test", "dependency_change", "existing_symbol", "behavior_change",
                                          "unclear_contract", "regression_risk", "data_flow"))
    strategy_choose.add_argument("--json", action="store_true", help="Print machine-readable result")
    tests_parser = sub.add_parser("tests", help="Order focused tests after a code change without running them")
    tests_sub = tests_parser.add_subparsers(dest="tests_action", required=True)
    tests_discover = tests_sub.add_parser("discover", help="Discover changed Python tests and rank their order")
    tests_discover.add_argument("--repo", default=".", help="Local Git repository root (default: current directory)")
    tests_discover.add_argument("--required", action="append", required=True,
                                help="Repository-required validation command; repeat for multiple gates")
    tests_discover.add_argument("--json", action="store_true", help="Print machine-readable result")
    tests_rank = tests_sub.add_parser("rank", help="Rank candidate tests from local JSON metadata")
    tests_rank.add_argument("--input", required=True, help="Local JSON file, or - for stdin")
    tests_rank.add_argument("--json", action="store_true", help="Print machine-readable local result")
    doctor_parser = sub.add_parser("doctor", help="Check local runtime, catalog, and hook registration")
    doctor_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    doctor_parser.add_argument("--test-jev", action="store_true", help="Send one synthetic, billed Jev request")
    installer = sub.add_parser("install", help="Merge JevCompass advisory hooks")
    installer.add_argument("--dry-run", action="store_true", help="Validate planned changes without writing files")
    spawn_group = installer.add_mutually_exclusive_group()
    spawn_group.add_argument(
        "--spawn-advice", action="store_true", default=None,
        help="Opt in to nonblocking advice before agent spawn tools",
    )
    spawn_group.add_argument(
        "--disable-spawn-advice", action="store_false", dest="spawn_advice",
        help="Remove the JevCompass spawn advice hook",
    )
    skills = sub.add_parser("skills", help="Manage optional bundled Codex skills")
    skills_sub = skills.add_subparsers(dest="skills_action", required=True)
    skills_install = skills_sub.add_parser("install", help="Install two reviewed, local workflow skills")
    skills_install.add_argument("--dry-run", action="store_true", help="Validate without writing skill files")
    skills_add = skills_sub.add_parser("add", help="Register approved generic metadata for an installed skill")
    skills_add.add_argument("name", help="Installed skill identifier (lowercase slug)")
    for field in ("capability", "use-when", "avoid-when"):
        skills_add.add_argument(f"--{field}", required=True)
    skills_add.add_argument("--category", choices=CATEGORIES, required=True,
                            help="Task category where this skill is useful")
    skills_add.add_argument("--domain", choices=DOMAINS, required=True)
    skills_add.add_argument("--approve-remote-metadata", action="store_true",
                            help="Confirm these generic fields may be sent to OpenRouter for ranking")
    auth = sub.add_parser("auth", help="Manage the OpenRouter key in the system keyring")
    auth_sub = auth.add_subparsers(dest="auth_action", required=True)
    auth_sub.add_parser("status", help="Show redacted credential availability")
    auth_sub.add_parser("set", help="Store a key using a hidden interactive prompt")
    auth_sub.add_parser("delete", help="Remove the keyring entry after confirmation")
    args = parser.parse_args(argv)
    if args.command == "hook":
        return advisor.hook_main()
    if args.command == "recommend":
        return _recommend(args.category, args.domain, args.role)
    if args.command == "triage":
        from .triage import triage_failure
        result = triage_failure(
            tuple(FailureKind(item) for item in args.kind),
            tuple(HypothesisId(item) for item in args.hypothesis),
            args.exit_code,
            import_observations=tuple(
                ImportObservation(item) for item in (args.import_observation or ())
            ),
        )
        payload = {"observed_exit_status": result.observed_exit_status,
                   "test_failed": result.test_failed, "status": result.status,
                   "steps": [{"id": step.id.value, "title": step.title,
                              "instruction": step.instruction} for step in result.steps],
                   "executed": False}
        if args.json:
            print(json.dumps(payload))
        else:
            print(f"Observed test exit: {result.observed_exit_status}; diagnostic order ({result.status}):")
            for step in result.steps:
                print(f"- {step.id.value}: {step.instruction}")
            if not result.steps:
                print("No safe diagnostic choice; inspect the original failure locally.")
        return 0
    if args.command == "strategy":
        from .strategy import choose_strategies
        result = choose_strategies(args.kind, args.signal)
        payload = {"status": result.status,
                   "strategies": [{"id": item.id.value, "rationale": item.rationale}
                                  for item in result.recommendations]}
        if args.json:
            print(json.dumps(payload))
        else:
            print(f"Suggested coding strategy ({result.status}):")
            for item in result.recommendations:
                print(f"- {item.id.value}: {item.rationale}")
            if not result.recommendations:
                print("No relevant strategy; continue with the normal Codex workflow.")
        return 0
    if args.command == "tests":
        if args.tests_action == "discover":
            return _discover_tests_cli(args.repo, args.required, args.json)
        return _rank_tests_cli(args.input, args.json)
    if args.command == "doctor":
        result = doctor(test_jev=args.test_jev)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"JevCompass {__version__}: {'PASS' if result['ok'] else 'CHECK REQUIRED'}")
            home_source = result["codex_home"]["source"]
            home_label = "CODEX_HOME override" if home_source == "environment" else "default ~/.codex"
            print(f"- Codex config home: {home_label} (path hidden)")
            python_check = result["python"]
            print(f"- Python: {'PASS' if python_check['ok'] else 'CHECK'} {python_check['version']} (requires {python_check['required']})")
            key_check = result["openrouter_key"]
            key_source = key_check["source"]
            key_status = {
                "environment": "configured through the environment",
                "system-keyring": "configured in the system keyring",
                "none": "not configured",
            }.get(key_source, "not configured")
            print(f"- OpenRouter API key: {key_status}; remote Jev choices need it, while local single-candidate advice can still work without it")
            model_check = result["jev_model"]
            model_status = {
                "available": "metadata confirms Decisions support",
                "missing": "model is not listed with Decisions support",
                "unavailable": "metadata check unavailable; remote advice is unverified",
            }[model_check["status"]]
            model_source = "JEVCOMPASS_MODEL override" if model_check["source"] == "environment" else "default model"
            print(f"- Jev model metadata: {model_status} ({model_source})")
            catalog_check = result["catalog"]
            print(
                "- Catalog: "
                f"{catalog_check['curated_entries']} reviewed entries; "
                f"{catalog_check['available_tools']} local tools; "
                f"{catalog_check['available_skills']} reviewed skills installed; "
                f"{catalog_check['configured_mcp_servers']} MCP servers configured; "
                f"{catalog_check['unavailable_entries']} reviewed entries unavailable"
            )
            capacity = result["selection_capacity"]["examples"]
            modes = ", ".join(f"{label}: {item['mode'].replace('_', ' ')}"
                              for label, item in capacity.items())
            print(f"- Local choice capacity: {modes}")
            print("  This reflects discovered candidates, not active-session access or measured usefulness.")
            coding_python = capacity["coding_python"]
            if coding_python["mode"] == "low_signal_skip":
                print("  Python coding has low local choice capacity. To inspect the optional bundled skills, run `jevcompass skills install --dry-run`; installation is opt-in and never automatic.")
            hooks_check = result["hooks_json"]
            registered = ", ".join(hooks_check["registered_advisory_hooks"]) or "none"
            print(f"- Hooks registered: {registered}; legacy Jev PreToolUse gate {'present' if hooks_check['pretool_jev_gate'] else 'absent'}")
            spawn = hooks_check["spawn_advice"]
            print(f"- Optional spawn advice: {'registered' if spawn['registered'] else 'not registered'}")
            if not spawn["safe"]:
                print("  Malformed JevCompass PreToolUse hook registration needs repair.")
            observation = result["hook_observation"]
            if observation["observed"]:
                print(f"- Hook invocation metric: {observation['event']}; log modified {observation['log_modified_age_seconds']}s ago; status {observation['status']}")
                statuses = observation['recent_by_event']
                print("- Recent metric status by hook: " + ", ".join(
                    f"{event}={statuses.get(event, 'not observed in metric tail')}"
                    for event in ("UserPromptSubmit", "SubagentStart", "PreToolUse")))
            else:
                print("- Hook invocation metric: no safe record observed; status unavailable")
            print(f"- Hooks feature in base config: {result['hooks_feature']['base_config']} (active host policy and trust need separate verification)")
            if "live_decision" in result:
                print(f"- Synthetic Jev request: {'PASS' if result['live_decision']['ok'] else 'CHECK'} (billed)")
            print("MCP configuration and installed skill metadata do not prove tools are callable in this Codex session.")
            print("Private integration and skill names, configuration values, and local paths are withheld.")
        return 0 if result["ok"] else 1
    if args.command == "skills":
        if args.skills_action == "add":
            preview = {"id": args.name, "capability": args.capability,
                       "use_when": args.use_when, "avoid_when": args.avoid_when,
                       "category": args.category, "domain": args.domain}
            print("Metadata eligible for OpenRouter ranking: " + json.dumps(preview, sort_keys=True))
            if not args.approve_remote_metadata:
                print("No changes made. Review the fields, then repeat with --approve-remote-metadata.")
                return 0
            try:
                register_local_skill(args.name, args.capability, args.use_when, args.avoid_when,
                                     [advisor.CATALOG_TASKS[args.category]], [args.domain])
            except (OSError, ValueError) as error:
                print(f"JevCompass skill registration failed: {error}", file=sys.stderr)
                return 1
            print("Registered generic metadata for an installed skill. Read its SKILL.md before use.")
            return 0
        from .skill_pack import install_skills

        try:
            print(install_skills(dry_run=args.dry_run))
        except (OSError, ValueError, RuntimeError, UnicodeError) as error:
            print(f"JevCompass skill install failed: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "auth":
        return auth_main(args.auth_action)
    if args.command == "install":
        try:
            result = install(dry_run=args.dry_run, spawn_advice=args.spawn_advice)
        except (OSError, ValueError, RuntimeError) as error:
            print(f"JevCompass install failed: {error}", file=sys.stderr)
            return 1
        print(result)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
