#!/usr/bin/env python3
"""Prospective local-advisor plan control; manual blind assessment required."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import time

import pilot_cli_core as core
from jevcompass.strategy import choose_strategies

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/ambiguous_contract_triage"
PROMPT = """Prepare an actionable investigation and conditional repair plan for this
fictional invoice calculator. Read the ordinary fixture evidence. Do not edit
files, run tests, access credentials or the network, or silently settle missing
requirements. Identify the minimum clarification needed before implementation,
dependencies, conditional edits and mandatory validation afterward. Return the
plan as your final response. Do not identify an experimental variant or advisor.
A plan is not proof of coding completion or executed validation."""


def snapshot(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("fixture contains a symlink")
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def private_write(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value)


def elapsed(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 2)


def run_arm(*, codex, model, effort, timeout, max_tokens, fixture, home, prompt):
    started = time.monotonic()
    environment = core._isolated_environment(
        home=home, isolated_python=home / "python", allow_openrouter_key=False)
    command = [
        codex, "exec", "--json", "--ephemeral", "--ignore-user-config",
        "--skip-git-repo-check", "--sandbox", "read-only", "--model", model,
        "-c", 'model_reasoning_effort="' + effort + '"', prompt,
    ]
    try:
        process = subprocess.Popen(
            command, cwd=fixture, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        lines, times, failure = core._collect_events(
            process, started=started, timeout=timeout, max_tokens=max_tokens)
    except OSError:
        return {"status": "failed", "failure": "codex_unavailable",
                "plan_elapsed_ms": elapsed(started), "token_usage": None}, None
    parsed = core.parse_event_stream(lines, start_monotonic=started, event_times=times)
    plan = None
    completed = False
    for line in lines:
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.completed":
            completed = True
        item = event.get("item")
        if (event.get("type") == "item.completed" and isinstance(item, dict)
                and item.get("type") == "agent_message" and isinstance(item.get("text"), str)):
            plan = item["text"]
    # Never persist raw streams, commands, errors or assistant commentary.
    usable = failure is None and completed and bool(plan and plan.strip())
    result = {
        "status": "completed" if usable else "failed",
        "failure": None if usable else "plan_incomplete",
        "plan_elapsed_ms": elapsed(started),
        "token_usage_status": parsed.get("token_usage_status", "unavailable"),
        "token_usage": parsed.get("token_usage"),
        "first_observed_tool_ms": parsed.get("first_tool_ms"),
        "first_useful_error_ms": None,
        "billing_cost_usd": None,
    }
    return result, plan if usable else None


def run_pair(*, output_dir: Path, model: str, effort="low", codex="codex",
             timeout=180, max_tokens=150000, seed=330):
    if not isinstance(model, str) or not model.strip():
        raise ValueError("explicit model required")
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("unsupported effort")
    if not 1 <= timeout <= 600 or not 1 <= max_tokens <= core.MAX_EVENT_TOKEN_BUDGET:
        raise ValueError("invalid bounded execution settings")
    original = snapshot(FIXTURE)
    started = time.monotonic()
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(output_dir, 0o700)
    labels = ["arm-a", "arm-b"]
    random.Random(seed).shuffle(labels)
    mapping = dict(zip(labels, ("baseline", "treatment")))
    private_write(output_dir / "arm-map.json", json.dumps(mapping))
    receipt = {
        "schema_version": 1, "status": "pending_manual_assessment",
        "fixture_sha256": original, "arms": {}, "remote_invocations": 0,
        "coding_completion_status": "not_evaluated",
        "blind_assessment_status": "pending",
        "timing_scope": "setup, sequential arms, immutable checks and plan capture; excludes receipt serialization",
    }
    with tempfile.TemporaryDirectory(prefix="jev-plan-pair-") as temporary:
        private_root = Path(temporary)
        os.chmod(private_root, 0o700)
        auth_source = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"
        auth_snapshot = private_root / "auth.json"
        if not core._copy_auth(auth_source, auth_snapshot):
            receipt["status"] = "failed"
            receipt["failure"] = "auth_unavailable"
        else:
            receipt["shared_setup_elapsed_ms"] = elapsed(started)
            for label in ("arm-a", "arm-b"):
                prep = time.monotonic()
                fixture = private_root / (label + "-fixture")
                home = private_root / (label + "-home")
                shutil.copytree(FIXTURE, fixture)
                if snapshot(fixture) != original:
                    raise ValueError("fixture parity failed")
                if not core._copy_auth(auth_snapshot, home / ".codex/auth.json"):
                    raise ValueError("arm authentication unavailable")
                prompt = PROMPT
                if mapping[label] == "treatment":
                    decision = choose_strategies(
                        "coding", ["existing_symbol", "behavior_change"],
                        contract_evidence="conflicting")
                    if decision.status != "no-remote-choice" or decision.usage is not None:
                        raise ValueError("local strategy contract violated")
                    if tuple(x.id.value for x in decision.recommendations) != ("define_contract_then_implement",):
                        raise ValueError("unexpected local strategy")
                    prompt += "\nOptional strategy: " + decision.recommendations[0].rationale
                prep_ms = elapsed(prep)
                result, plan = run_arm(
                    codex=codex, model=model, effort=effort, timeout=timeout,
                    max_tokens=max_tokens, fixture=fixture, home=home, prompt=prompt)
                result["preparation_elapsed_ms"] = prep_ms
                result["immutable_fixture_verified"] = snapshot(fixture) == original
                if not result["immutable_fixture_verified"]:
                    result["status"] = "failed"
                    result["failure"] = "fixture_mutated"
                    plan = None
                if plan:
                    private_write(output_dir / (label + "-plan.md"), plan)
                    result["plan_sha256"] = hashlib.sha256(plan.encode()).hexdigest()
                else:
                    result["plan_sha256"] = None
                    receipt["status"] = "failed"
                receipt["arms"][label] = result
    receipt["total_pair_elapsed_ms"] = elapsed(started)
    private_write(output_dir / "receipt.json", json.dumps(receipt, indent=2) + "\n")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", default="low", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-tokens", type=int, default=150000)
    parser.add_argument("--seed", type=int, default=330)
    args = parser.parse_args(argv)
    result = run_pair(**vars(args))
    print(json.dumps({"status": result["status"]}))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
