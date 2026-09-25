"""Supplemental C01/R10 isolated Codex CLI paired pilot (outside core-20 denominator).

Only fixed metadata is written to stdout. Prompts, transcripts, source, paths, auth,
and environment values stay out of receipts. This runner never forwards an OpenRouter key.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "codex_setup"
CORE_SCRIPT = ROOT / "scripts" / "pilot_cli_core.py"
CASE_PROMPTS = {
    "C01": (
        "Plan how to configure Codex Desktop hooks and Codex CLI skills for fictional "
        "OrbitNote using only the synthetic repository files. Explain the two advisory "
        "triggers, install and trust checks, and what remains unverified. Do not edit host "
        "files or contact network services; cite the fixture files for each step."
    ),
    "R10": "Check whether docs/verification.md exists in the synthetic fixture.",
}
DEFAULT_TIMEOUT = 90
MAX_TIMEOUT = 300
MAX_SKILL_BYTES = 2 * 1024 * 1024
CASE_IDS = ("C01", "R10")

_spec = importlib.util.spec_from_file_location("pilot_cli_core_for_setup_pair", CORE_SCRIPT)
if _spec is None or _spec.loader is None:
    raise RuntimeError("CLI pilot helpers are unavailable")
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)


def _skill_digest(source: Path) -> str:
    if source.is_symlink() or not source.is_dir() or not (source / "SKILL.md").is_file():
        raise FileNotFoundError("stock openai-docs skill is unavailable")
    digest = hashlib.sha256()
    total = 0
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("stock openai-docs skill is not a plain directory")
        if path.is_file():
            size = path.stat().st_size
            total += size
            if total > MAX_SKILL_BYTES:
                raise ValueError("stock openai-docs skill exceeds the size limit")
            relative = path.relative_to(source).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _install_skill(source: Path, destination: Path, expected_digest: str) -> None:
    shutil.copytree(source, destination)
    if _skill_digest(destination) != expected_digest:
        raise RuntimeError("stock openai-docs skill copy verification failed")


def _source_auth_root() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def _source_skill_root(auth_root: Path) -> Path:
    user_skill = auth_root / "skills" / "openai-docs"
    if user_skill.is_dir():
        return user_skill
    return auth_root / "skills" / ".system" / "openai-docs"


def _safe_failure(code: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "failure": code,
        "execution": "not_started",
    }


def _prepare_profile(
    *, home: Path, skill_source: Path, skill_sha256: str, treatment: bool,
    auth_source: Path | None,
) -> dict[str, Any]:
    home.mkdir(mode=0o700)
    codex_home = home / ".codex"
    codex_home.mkdir(mode=0o700)
    auth_copied = False
    if auth_source is not None:
        try:
            auth_copied = _core._copy_auth(auth_source, codex_home / "auth.json")
        except OSError:
            auth_copied = False
        if not auth_copied:
            return {"failure": "auth_unavailable"}
    skill_dest = codex_home / "skills" / "openai-docs"
    skill_dest.parent.mkdir(mode=0o700, parents=True)
    try:
        _install_skill(skill_source, skill_dest, skill_sha256)
    except (OSError, ValueError, RuntimeError):
        return {"failure": "skill_setup_failed"}
    isolated_python = home / "python"
    if treatment:
        try:
            _core._install_treatment_hooks(home, isolated_python)
        except (OSError, RuntimeError, ValueError):
            return {"failure": "hooks_setup_failed"}
    return {
        "auth_copied": auth_copied,
        "skill_installed": True,
        "skill_sha256": skill_sha256,
        "hooks_configured": treatment,
        "home": home,
        "codex_home": codex_home,
        "isolated_python": isolated_python,
    }


def _command(*, codex: str, model: str, reasoning_effort: str, prompt: str) -> list[str]:
    return [
        codex, "-a", "never", "exec", "--json", "--ephemeral",
        "--sandbox", "read-only", "--skip-git-repo-check",
        "--dangerously-bypass-hook-trust", "--model", model,
        "--config", f"model_reasoning_effort={reasoning_effort}", prompt,
    ]


def _run_live_arm(
    *, codex: str, model: str, reasoning_effort: str, fixture: Path, home: Path,
    skill_source: Path, skill_sha256: str, case_id: str, timeout: int,
    treatment: bool, auth_source: Path,
) -> dict[str, Any]:
    profile = _prepare_profile(
        home=home, skill_source=skill_source, skill_sha256=skill_sha256,
        treatment=treatment, auth_source=auth_source,
    )
    if "failure" in profile:
        return _safe_failure(profile["failure"])
    isolated_python = profile["isolated_python"]
    env = _core._isolated_environment(home=home, isolated_python=isolated_python)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            _command(
                codex=codex, model=model, reasoning_effort=reasoning_effort,
                prompt=CASE_PROMPTS[case_id],
            ),
            cwd=fixture, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        lines, event_times, failure = _core._collect_events(
            process, started=started, timeout=timeout, preserve_on_failure=True,
        )
    except OSError:
        return _safe_failure("codex_unavailable")
    parsed = _core.parse_event_stream(
        lines, start_monotonic=started, event_times=event_times,
        known_candidate_ids=_core._known_catalog_ids(),
    )
    result: dict[str, Any] = {
        "status": "completed" if not failure and process.returncode == 0 else "failed",
        "execution": "completed" if not failure and process.returncode == 0 else "failed",
        "exit_code": process.returncode,
        "model_called": True,
        "auth_copied": profile["auth_copied"],
        "skill_installed": True,
        "skill_sha256": skill_sha256,
        "hooks_configured": treatment,
        "event_count": parsed["event_count"],
        "partial_event_stream": bool(failure),
        "first_assistant_ms": (
            parsed["first_assistant"]["elapsed_ms"] if parsed["first_assistant"] else None
        ),
        "first_tool_ms": parsed["first_tool"]["elapsed_ms"] if parsed["first_tool"] else None,
        "first_tool_name": parsed["first_tool"]["tool"] if parsed["first_tool"] else None,
        "first_action": parsed["first_action"],
        "advice_id_before_first_tool": parsed["advice_id_before_first_tool"],
    }
    if treatment:
        result["advice_id"] = parsed["advice_id"]
        metrics = _core.read_safe_metrics(
            home / ".local" / "state" / "jevcompass" / "advisor.jsonl"
        )
        result["advisor_metrics"] = metrics
        result["advice_metric"] = _core.correlate_advice(parsed, metrics)
    if failure:
        result["failure"] = failure
    elif process.returncode != 0:
        result["failure"] = "codex_nonzero_exit"
    return result


def _mock_arm(*, case_id: str, treatment: bool, skill_sha256: str) -> dict[str, Any]:
    """Return deterministic synthetic metadata; no Codex or network process is started."""
    advice = treatment and case_id == "C01"
    return {
        "status": "not_run",
        "execution": "mocked",
        "model_called": False,
        "auth_copied": False,
        "skill_installed": True,
        "skill_sha256": skill_sha256,
        "hooks_configured": treatment,
        "event_count": 2 if advice else 1,
        "advice_id_before_first_tool": advice,
        "advice_id": "abcdef12" if advice else None,
        "advisor_metrics": (
            [{
                "event": "UserPromptSubmit",
                "category": "codex-setup" if advice else "none",
                "status": "local" if advice else "skipped",
                "duration_ms": 0.25,
                "trace": "abcdef12" if advice else None,
            }]
            if treatment else []
        ),
        "routine_no_advice": case_id == "R10" and treatment,
    }


def run_pair(
    *, mode: str, model: str | None, timeout: int = DEFAULT_TIMEOUT,
    reasoning_effort: str = "medium", codex: str | None = None,
    rng: Any = None, auth_root: Path | None = None, skill_source: Path | None = None,
) -> dict[str, Any]:
    if mode not in {"run", "mock", "dry-run"}:
        raise ValueError("mode must be run, mock, or dry-run")
    if timeout < 1 or timeout > MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if reasoning_effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("reasoning effort is invalid")
    if mode == "run" and not model:
        raise ValueError("an explicit Codex model is required")
    if not FIXTURE.is_dir():
        raise FileNotFoundError("synthetic Codex setup fixture is unavailable")

    source_home = auth_root or _source_auth_root()
    skill_path = skill_source or _source_skill_root(source_home)
    skill_sha256 = _skill_digest(skill_path)
    executable = codex or (shutil.which("codex") if mode == "run" else None)
    if mode == "run" and not executable:
        raise RuntimeError("Codex CLI is unavailable")
    auth_source = source_home / "auth.json"
    if mode == "run" and not auth_source.is_file():
        raise RuntimeError("Codex auth.json is unavailable")

    order = list(CASE_IDS)
    (rng or random.SystemRandom()).shuffle(order)
    result: dict[str, Any] = {
        "pilot": "jevcompass-codex-setup-supplement",
        "status": "completed",
        "mode": mode,
        "case_order": order,
        "model": model if mode == "run" else None,
        "reasoning_effort": reasoning_effort,
        "timeout_seconds_per_arm": timeout,
        "openrouter_key_forwarded": False,
        "sandbox": "read-only",
        "core_20_denominator_included": False,
        "receipt_scope": "redacted metadata only; no prompts, answers, source, paths, auth, or secrets",
        "cases": {},
    }
    with tempfile.TemporaryDirectory(prefix="jevcompass-codex-setup-") as temporary:
        work = Path(temporary)
        for case_id in order:
            arm_order = ["baseline", "treatment"]
            (rng or random.SystemRandom()).shuffle(arm_order)
            arms: dict[str, tuple[Path, Path]] = {}
            digests = []
            skill_digests = []
            for label in ("baseline", "treatment"):
                home = work / case_id / f"{label}-home"
                home.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fixture_copy = work / case_id / f"{label}-fixture"
                digests.append(_core.copy_fixture(FIXTURE, fixture_copy))
                arms[label] = (home, fixture_copy)
                skill_digests.append(skill_sha256)
            if digests[0] != digests[1]:
                raise RuntimeError("paired fixture copies differ")
            if skill_digests[0] != skill_digests[1]:
                raise RuntimeError("paired skill copies differ")
            arm_results: dict[str, Any] = {}
            for label in arm_order:
                treatment = label == "treatment"
                home, fixture_copy = arms[label]
                if mode == "dry-run":
                    profile = _prepare_profile(
                        home=home, skill_source=skill_path, skill_sha256=skill_sha256,
                        treatment=treatment, auth_source=None,
                    )
                    arm_result = {
                        "status": "not_run",
                        "execution": "not_started",
                        "model_called": False,
                        "auth_copied": False,
                        "skill_installed": "failure" not in profile,
                        "skill_sha256": skill_sha256,
                        "hooks_configured": treatment and "failure" not in profile,
                    }
                elif mode == "mock":
                    profile = _prepare_profile(
                        home=home, skill_source=skill_path, skill_sha256=skill_sha256,
                        treatment=treatment, auth_source=None,
                    )
                    arm_result = (
                        _safe_failure(profile["failure"])
                        if "failure" in profile
                        else _mock_arm(case_id=case_id, treatment=treatment, skill_sha256=skill_sha256)
                    )
                else:
                    assert executable and model
                    arm_result = _run_live_arm(
                        codex=executable, model=model, reasoning_effort=reasoning_effort,
                        fixture=fixture_copy, home=home, skill_source=skill_path,
                        skill_sha256=skill_sha256, case_id=case_id, timeout=timeout,
                        treatment=treatment, auth_source=auth_source,
                    )
                arm_results[label] = arm_result
            result["cases"][case_id] = {
                "arm_order": arm_order,
                "fixture_sha256": digests[0],
                "fixture_copies_identical": True,
                "skill_copies_identical": skill_digests[0] == skill_digests[1],
                "routine_negative_control": case_id == "R10",
                "arms": arm_results,
            }
        if any(
            arm.get("status") == "failed"
            for case in result["cases"].values()
            for arm in case["arms"].values()
        ):
            result["status"] = "failed"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="prepare isolated profiles without launching Codex")
    mode.add_argument("--mock", action="store_true", help="exercise pairing with synthetic metadata only")
    parser.add_argument("--model", help="same explicit Codex model for both arms (required for live execution)")
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh"), default="medium")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"per-arm timeout, maximum {MAX_TIMEOUT}s")
    args = parser.parse_args(argv)
    selected_mode = "dry-run" if args.dry_run else "mock" if args.mock else "run"
    if selected_mode == "run" and not args.model:
        parser.error("--model is required for a live pair")
    try:
        result = run_pair(
            mode=selected_mode, model=args.model, timeout=args.timeout,
            reasoning_effort=args.reasoning_effort,
        )
    except FileNotFoundError as error:
        code = "skill_unavailable" if "skill" in str(error) else "fixture_unavailable"
        print(json.dumps({"pilot": "jevcompass-codex-setup-supplement", **_safe_failure(code)}))
        return 1
    except RuntimeError as error:
        message = str(error)
        code = (
            "auth_unavailable" if "auth" in message else
            "codex_unavailable" if "CLI" in message else
            "model_required" if "model" in message else "setup_failed"
        )
        print(json.dumps({"pilot": "jevcompass-codex-setup-supplement", **_safe_failure(code)}))
        return 1
    except (OSError, ValueError):
        print(json.dumps({"pilot": "jevcompass-codex-setup-supplement", **_safe_failure("setup_failed")}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
