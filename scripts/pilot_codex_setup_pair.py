"""Supplemental isolated Codex CLI pairs (outside core-20 denominator).

Only fixed metadata is written to stdout. Prompts, transcripts, source, paths, auth,
and environment values stay out of receipts. C03 forwards a key only after explicit opt-in.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import secrets
import shlex
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
CASE_PROMPTS["C02"] = (
    CASE_PROMPTS["C01"] + " Before your first tool call, report the JevCompass advice ID "
    "and only the candidate IDs if an advisory is present; otherwise report exactly "
    "NO JEVCOMPASS ADVISORY."
)
CASE_PROMPTS["C03"] = (
    "Review the synthetic Python file counter.py for correctness and report the highest-priority "
    "finding with its line number. Before your first tool call, report the JevCompass advice ID "
    "and only candidate IDs you received; if no advisory is present, report exactly "
    "NO JEVCOMPASS ADVISORY. The fixture contains no real project or client data."
)
DEFAULT_TIMEOUT = 90
MAX_TIMEOUT = 300
MAX_SKILL_BYTES = 2 * 1024 * 1024
EXPECTED_INSTALLED_VERSION = "0.1.12"
REVIEW_SKILL_LAYOUTS = (
    ("developer-essentials", "1.0.4", "code-review-excellence"),
    ("security-scanning", "1.3.2", "security-requirement-extraction"),
)
CASE_IDS = ("C01", "R10")
SELECTABLE_CASE_IDS = ("C01", "C02", "C03", "R10")

_spec = importlib.util.spec_from_file_location("pilot_cli_core_for_setup_pair", CORE_SCRIPT)
if _spec is None or _spec.loader is None:
    raise RuntimeError("CLI pilot helpers are unavailable")
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)


def _check_installed_version(python: Path) -> None:
    """Prove the explicit interpreter resolves the tested release, not this checkout."""
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE") if key in os.environ}
    try:
        result = subprocess.run(
            [str(python), "-I", "-c",
             "from importlib.metadata import version; print(version('jevcompass'))"],
            env=env, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("installed JevCompass version could not be verified") from error
    if result.returncode != 0 or result.stdout.strip() != EXPECTED_INSTALLED_VERSION:
        raise RuntimeError("installed JevCompass version does not match the tested release")


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


def _review_skill_sources(auth_root: Path) -> tuple[tuple[Path, str], ...]:
    """Return the two curated skill sources and their isolated plugin-cache destinations."""
    cache = auth_root / "plugins" / "cache" / "claude-code-workflows"
    result = []
    for package, version, skill in REVIEW_SKILL_LAYOUTS:
        source = cache / package / version / "skills" / skill
        if source.is_symlink() or not source.is_dir() or not (source / "SKILL.md").is_file():
            raise FileNotFoundError("curated review skills are unavailable")
        result.append((source, f"claude-code-workflows/{package}/{version}/skills/{skill}"))
    return tuple(result)


def _write_review_fixture(destination: Path) -> None:
    """Create a tiny fictional Python review target inside the private temporary pair."""
    destination.mkdir(mode=0o700, parents=True)
    (destination / "counter.py").write_text(
        "def increment(value: int) -> int:\n"
        "    return value - 1\n",
        encoding="utf-8",
    )

def _prepare_profile(
    *, home: Path, skill_source: Path, skill_sha256: str, treatment: bool,
    auth_source: Path | None, installed_python: Path | None = None,
    candidate_skills: tuple[tuple[Path, str], ...] = (),
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
        installed_candidates = []
        for source, relative_dest in candidate_skills:
            destination = codex_home / "plugins" / "cache" / Path(relative_dest)
            destination.parent.mkdir(mode=0o700, parents=True)
            digest = _skill_digest(source)
            _install_skill(source, destination, digest)
            codex_skill = codex_home / "skills" / destination.name
            codex_skill.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _install_skill(source, codex_skill, digest)
            installed_candidates.append(destination.name)
    except (OSError, ValueError, RuntimeError):
        return {"failure": "skill_setup_failed"}
    isolated_python = home / "python"
    if treatment:
        try:
            if installed_python is None:
                _core._install_treatment_hooks(home, isolated_python)
            else:
                env = _core._isolated_environment(home=home, isolated_python=isolated_python)
                env.pop("PYTHONPATH", None)
                completed = subprocess.run(
                    [str(installed_python), "-m", "jevcompass", "install"],
                    env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=15, check=False,
                )
                if completed.returncode != 0:
                    return {"failure": "hooks_setup_failed"}
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            return {"failure": "hooks_setup_failed"}
    return {
        "auth_copied": auth_copied,
        "skill_installed": True,
        "skill_sha256": skill_sha256,
        "candidate_skills_installed": installed_candidates,
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


def _c03_action_telemetry(
    lines: list[str], *, event_times: list[float], start_monotonic: float,
    fixture: Path, home: Path,
) -> dict[str, Any]:
    """Record only successful completed reads of the exact synthetic C03 targets.

    Recognized commands are direct cat/head/tail/sed/nl reads, optionally as
    exactly two such commands joined by &&, with one exact bash/sh -lc wrapper
    allowed around the whole command. Other shell operators/expansion, listing
    and search commands, mentions in assistant text, started/failed command
    items, and nonzero exits cannot count as reads. Receipts contain fixed IDs and bounded
    timings only; command text and paths are never returned.
    """
    candidate_ids = tuple(skill for _, _, skill in REVIEW_SKILL_LAYOUTS)
    targets: dict[str, set[str]] = {
        "counter": {os.path.normcase(os.path.abspath(fixture / "counter.py"))},
    }
    candidate_targets: dict[str, set[str]] = {skill: set() for skill in candidate_ids}
    for package, version, skill in REVIEW_SKILL_LAYOUTS:
        candidate_targets[skill].update({
            os.path.normcase(os.path.abspath(
                home / ".codex" / "skills" / skill / "SKILL.md"
            )),
            os.path.normcase(os.path.abspath(
                home / ".codex" / "plugins" / "cache" / "claude-code-workflows"
                / package / version / "skills" / skill / "SKILL.md"
            )),
        })
    observed_reads: dict[str, float] = {}

    def file_operands(command: str, *, allow_wrapper: bool = True) -> list[str] | None:
        def tokenize(value: str) -> list[str] | None:
            try:
                lexer = shlex.shlex(value, posix=True, punctuation_chars=";&|()<>")
                lexer.whitespace_split = True
                lexer.commenters = ""
                return list(lexer)
            except ValueError:
                return None

        def direct_operands(argv: list[str]) -> list[str] | None:
            if not argv:
                return None
            utility = Path(argv[0]).name
            if utility not in {"cat", "head", "tail", "sed", "nl"}:
                return None
            if any(
                any(char in token for char in ";&|<>()") or "`" in token or "$" in token
                for token in argv
            ):
                return None
            args = argv[1:]
            if not args:
                return None
            if utility in {"cat", "head", "tail"}:
                operands: list[str] = []
                i = 0
                after_options = False
                while i < len(args):
                    token = args[i]
                    if token == "--" and not after_options:
                        after_options = True
                    elif not after_options and token.startswith("-"):
                        if utility == "cat":
                            return None
                        if token in {"-n", "-c", "--lines", "--bytes"}:
                            i += 1
                            if i >= len(args):
                                return None
                        elif token.startswith(("--lines=", "--bytes=")):
                            pass
                        elif token.startswith(("-n", "-c")) and token[2:].isdigit():
                            pass
                        elif utility == "head" and token[1:].isdigit():
                            pass
                        else:
                            return None
                    else:
                        operands.append(token)
                    i += 1
                return operands
            if utility == "nl":
                operands = []
                i = 0
                after_options = False
                while i < len(args):
                    token = args[i]
                    if token == "--" and not after_options:
                        after_options = True
                    elif not after_options and token == "-ba":
                        pass
                    elif not after_options and token == "-b":
                        i += 1
                        if i >= len(args) or args[i] != "a":
                            return None
                    elif not after_options and token.startswith("-"):
                        return None
                    else:
                        operands.append(token)
                    i += 1
                return operands
            # sed: consume options and their values, then its script, then file operands.
            operands = []
            i = 0
            script_seen = False
            script_from_option = False
            after_options = False
            while i < len(args):
                token = args[i]
                if not after_options and token == "--":
                    after_options = True
                elif not after_options and token in {"-e", "--expression", "-f", "--file"}:
                    i += 1
                    if i >= len(args):
                        return None
                    script_from_option = True
                elif not after_options and token.startswith("-"):
                    if token not in {"-n", "-E", "-r", "--regexp-extended"}:
                        return None
                elif not script_seen and not script_from_option:
                    script_seen = True
                else:
                    operands.append(token)
                i += 1
            return operands

        argv = tokenize(command)
        if not argv:
            return None
        executable = Path(argv[0]).name
        if executable in {"bash", "sh"}:
            if not allow_wrapper or len(argv) != 3 or argv[1] != "-lc":
                return None
            return file_operands(argv[2], allow_wrapper=False)

        conjunctions = [index for index, token in enumerate(argv) if token == "&&"]
        if conjunctions:
            if len(conjunctions) != 1:
                return None
            split_at = conjunctions[0]
            left = direct_operands(argv[:split_at])
            right = direct_operands(argv[split_at + 1:])
            if not left or not right:
                return None
            return [*left, *right]
        return direct_operands(argv)


    for order, line in enumerate(lines):
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        exit_code = item.get("exit_code")
        if isinstance(exit_code, bool) or exit_code != 0:
            continue
        raw_command = item.get("command")
        command = (
            raw_command if isinstance(raw_command, str)
            else " ".join(raw_command)
            if isinstance(raw_command, list) and all(isinstance(part, str) for part in raw_command)
            else None
        )
        if command is None:
            continue
        operands = file_operands(command)
        if not operands:
            continue
        candidates = {os.path.normcase(os.path.abspath(fixture / operand)) for operand in operands}
        matched: list[str] = []
        if candidates & targets["counter"]:
            matched.append("counter")
        matched.extend(skill for skill in candidate_ids if candidates & candidate_targets[skill])
        if not matched:
            continue
        if order >= len(event_times):
            continue
        observed = event_times[order]
        if isinstance(observed, bool) or not isinstance(observed, (int, float)) or not math.isfinite(observed):
            continue
        elapsed_ms = round(max(0.0, min(MAX_TIMEOUT * 1000, (observed - start_monotonic) * 1000)), 2)
        for target in matched:
            observed_reads.setdefault(target, elapsed_ms)

    return {
        "counter_read_ms": observed_reads.get("counter"),
        "candidate_skill_reads": [
            {"id": skill, "elapsed_ms": observed_reads[skill]}
            for skill in candidate_ids if skill in observed_reads
        ],
    }


def _run_live_arm(
    *, codex: str, model: str, reasoning_effort: str, fixture: Path, home: Path,
    skill_source: Path, skill_sha256: str, case_id: str, timeout: int,
    treatment: bool, auth_source: Path, answer_sink: list[str] | None = None,
    installed_python: Path | None = None,
    candidate_skills: tuple[tuple[Path, str], ...] = (),
    allow_openrouter_key: bool = False,
) -> dict[str, Any]:
    profile = _prepare_profile(
        home=home, skill_source=skill_source, skill_sha256=skill_sha256,
        treatment=treatment, auth_source=auth_source,
        installed_python=installed_python, candidate_skills=candidate_skills,
    )
    if "failure" in profile:
        return _safe_failure(profile["failure"])
    isolated_python = profile["isolated_python"]
    env = _core._isolated_environment(home=home, isolated_python=isolated_python)
    if installed_python is not None:
        env.pop("PYTHONPATH", None)
    if allow_openrouter_key and treatment and case_id == "C03":
        env["OPENROUTER_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
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
        "candidate_skills_installed": profile["candidate_skills_installed"],
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
    if case_id == "C03":
        result["agent_reported_candidate_ids"] = parsed["agent_reported_candidate_ids"]
        result["action_telemetry"] = _c03_action_telemetry(
            lines, event_times=event_times, start_monotonic=started,
            fixture=fixture, home=home,
        )
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
    elif answer_sink is not None:
        answer = _final_answer(lines)
        if answer is not None:
            answer_sink.append(answer)
    return result


def _mock_arm(*, case_id: str, treatment: bool, skill_sha256: str) -> dict[str, Any]:
    """Return deterministic synthetic metadata; no Codex or network process is started."""
    advice = treatment and case_id in {"C01", "C02", "C03"}
    category = "review" if case_id == "C03" else "codex-setup" if case_id in {"C01", "C02"} else "none"
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
                "category": category,
                "status": "local" if advice else "skipped",
                "duration_ms": 0.25,
                "trace": "abcdef12" if advice else None,
            }]
            if treatment else []
        ),
        "routine_no_advice": case_id == "R10" and treatment,
    }


def _final_answer(lines: list[str]) -> str | None:
    """Select the last completed assistant message, excluding raw tool output."""
    answer = None
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        kind, text_value, _ = _core.extract_event(event)
        if kind == "assistant" and text_value:
            answer = text_value
    return answer


def _write_blind_answers(directory: Path, entries: list[dict[str, str]]) -> tuple[int, int]:
    """Store only validated synthetic answers; keep arm mapping in a separate private file."""
    absolute = Path(os.path.abspath(directory))
    if absolute == ROOT or ROOT in absolute.parents or absolute.exists():
        raise ValueError("blind output must be a new directory outside the repository")
    if not absolute.name.startswith("jevcompass-"):
        raise ValueError("blind output directory must start with jevcompass-")
    validated = []
    rejected = 0
    for entry in entries:
        answer = entry["answer"]
        fixture_root = entry.get("fixture_root")
        if fixture_root:
            answer = answer.replace(fixture_root, "<fixture>")
        answer = _core.TRACE_RE.sub("", answer).strip()
        try:
            answer = _core._validate_quality_text(
                answer, _core.MAX_BLIND_ANSWER_BYTES, "synthetic answer",
            )
            if not answer or any(prompt in answer for prompt in CASE_PROMPTS.values()):
                raise ValueError("blind answer is empty or contains a full prompt")
        except ValueError:
            rejected += 1
            continue
        validated.append({**entry, "answer": answer})
    fd, _ = _core._private_directory_fd(absolute)
    try:
        mapping = []
        for entry in validated:
            token = secrets.token_hex(12)
            _core._write_new_file_at(fd, f"{token}.txt", entry["answer"].encode("utf-8"), 0o600)
            mapping.append({"token": token, "case": entry["case"], "arm": entry["arm"]})
        _core._write_new_file_at(
            fd, "mapping.json", json.dumps(mapping, sort_keys=True).encode("utf-8"), 0o600,
        )
    finally:
        os.close(fd)
    return len(validated), rejected


def run_pair(
    *, mode: str, model: str | None, timeout: int = DEFAULT_TIMEOUT,
    reasoning_effort: str = "medium", codex: str | None = None,
    rng: Any = None, auth_root: Path | None = None, skill_source: Path | None = None,
    blind_dir: Path | None = None, cases: tuple[str, ...] = CASE_IDS,
    installed_python: Path | None = None, allow_openrouter_key: bool = False,
    review_skill_sources: tuple[tuple[Path, str], ...] | None = None,
) -> dict[str, Any]:
    if mode not in {"run", "mock", "dry-run"}:
        raise ValueError("mode must be run, mock, or dry-run")
    if not cases or len(set(cases)) != len(cases) or any(case not in SELECTABLE_CASE_IDS for case in cases):
        raise ValueError("cases must be unique selected pilot IDs")
    if timeout < 1 or timeout > MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if reasoning_effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("reasoning effort is invalid")
    if mode == "run" and not model:
        raise ValueError("an explicit Codex model is required")
    if blind_dir is not None and mode != "run":
        raise ValueError("blind answer capture requires a live run")
    if allow_openrouter_key and (mode != "run" or cases != ("C03",)):
        raise ValueError("OpenRouter key forwarding is limited to a live C03-only pair")
    if mode == "run" and "C03" in cases and not allow_openrouter_key:
        raise ValueError("a live C03 pair requires explicit OpenRouter key forwarding opt-in")
    if mode == "run" and "C03" in cases and installed_python is None:
        raise ValueError("a live C03 pair requires the explicit installed v0.1.12 interpreter")
    if allow_openrouter_key and not os.environ.get("OPENROUTER_API_KEY"):
        raise ValueError("OpenRouter API key is unavailable")
    if installed_python is not None:
        if not installed_python.is_absolute() or not installed_python.is_file():
            raise ValueError("installed Python must be an existing absolute file")
        _check_installed_version(installed_python)
    if not FIXTURE.is_dir():
        raise FileNotFoundError("synthetic Codex setup fixture is unavailable")

    source_home = auth_root or _source_auth_root()
    skill_path = skill_source or _source_skill_root(source_home)
    skill_sha256 = _skill_digest(skill_path)
    if "C03" in cases:
        review_skills = review_skill_sources or _review_skill_sources(source_home)
        expected_ids = tuple(skill for _, _, skill in REVIEW_SKILL_LAYOUTS)
        actual_ids = tuple(Path(relative).name for _, relative in review_skills)
        if actual_ids != expected_ids:
            raise ValueError("review skill sources must match the two curated candidates")
    else:
        review_skills = ()
    executable = codex or (shutil.which("codex") if mode == "run" else None)
    if mode == "run" and not executable:
        raise RuntimeError("Codex CLI is unavailable")
    auth_source = source_home / "auth.json"
    if mode == "run" and not auth_source.is_file():
        raise RuntimeError("Codex auth.json is unavailable")

    order = list(cases)
    (rng or random.SystemRandom()).shuffle(order)
    result: dict[str, Any] = {
        "pilot": "jevcompass-codex-setup-supplement",
        "status": "completed",
        "mode": mode,
        "case_order": order,
        "model": model if mode == "run" else None,
        "reasoning_effort": reasoning_effort,
        "timeout_seconds_per_arm": timeout,
        "openrouter_key_forwarded": bool(allow_openrouter_key),
        "advisor_source": "installed-release" if installed_python is not None else "checkout",
        "advisor_version": EXPECTED_INSTALLED_VERSION if installed_python is not None else None,
        "sandbox": "read-only",
        "core_20_denominator_included": False,
        "receipt_scope": "redacted metadata only; no prompts, answers, source, paths, auth, or secrets",
        "cases": {},
    }
    blind_entries: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="jevcompass-codex-setup-") as temporary:
        work = Path(temporary)
        for case_id in order:
            source_fixture = FIXTURE
            if case_id == "C03":
                source_fixture = work / case_id / "synthetic-review-fixture"
                _write_review_fixture(source_fixture)
            arm_order = ["baseline", "treatment"]
            (rng or random.SystemRandom()).shuffle(arm_order)
            arms: dict[str, tuple[Path, Path]] = {}
            digests = []
            skill_digests = []
            candidate_skill_digests = tuple(_skill_digest(source) for source, _ in review_skills)
            for label in ("baseline", "treatment"):
                home = work / case_id / f"{label}-home"
                home.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fixture_copy = work / case_id / f"{label}-fixture"
                digests.append(_core.copy_fixture(source_fixture, fixture_copy))
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
                        installed_python=installed_python, candidate_skills=review_skills,
                    )
                    arm_result = {
                        "status": "not_run",
                        "execution": "not_started",
                        "model_called": False,
                        "auth_copied": False,
                        "skill_installed": "failure" not in profile,
                        "skill_sha256": skill_sha256,
                        "candidate_skills_installed": profile.get("candidate_skills_installed", []),
                        "candidate_skill_digests": list(candidate_skill_digests),
                        "hooks_configured": treatment and "failure" not in profile,
                    }
                elif mode == "mock":
                    profile = _prepare_profile(
                        home=home, skill_source=skill_path, skill_sha256=skill_sha256,
                        treatment=treatment, auth_source=None,
                        installed_python=installed_python, candidate_skills=review_skills,
                    )
                    arm_result = (
                        _safe_failure(profile["failure"])
                        if "failure" in profile
                        else _mock_arm(case_id=case_id, treatment=treatment, skill_sha256=skill_sha256)
                    )
                    if "failure" not in profile and case_id == "C03":
                        arm_result["candidate_skills_installed"] = profile["candidate_skills_installed"]
                        arm_result["candidate_skill_digests"] = list(candidate_skill_digests)
                else:
                    assert executable and model
                    captured_answer: list[str] | None = [] if blind_dir is not None else None
                    arm_result = _run_live_arm(
                        codex=executable, model=model, reasoning_effort=reasoning_effort,
                        fixture=fixture_copy, home=home, skill_source=skill_path,
                        skill_sha256=skill_sha256, case_id=case_id, timeout=timeout,
                        treatment=treatment, auth_source=auth_source,
                        answer_sink=captured_answer, installed_python=installed_python,
                        candidate_skills=review_skills,
                        allow_openrouter_key=allow_openrouter_key,
                    )
                    if captured_answer:
                        blind_entries.append({
                            "case": case_id, "arm": label, "answer": captured_answer[-1],
                            "fixture_root": str(fixture_copy),
                        })
                arm_results[label] = arm_result
            result["cases"][case_id] = {
                "arm_order": arm_order,
                "fixture_sha256": digests[0],
                "fixture_copies_identical": True,
                "skill_copies_identical": skill_digests[0] == skill_digests[1],
                "candidate_skill_digests": list(candidate_skill_digests),
                "candidate_skill_copies_identical": True,
                "fresh_jev_cache": case_id == "C03",
                "pair_scope": "delivery smoke; treatment-only remote key, not efficacy comparison" if case_id == "C03" else "supplemental paired probe",
                "routine_negative_control": case_id == "R10",
                "arms": arm_results,
            }
        if any(
            arm.get("status") == "failed"
            for case in result["cases"].values()
            for arm in case["arms"].values()
        ):
            result["status"] = "failed"
        if blind_dir is not None:
            accepted, rejected = _write_blind_answers(blind_dir, blind_entries)
            result["blind_answer_count"] = accepted
            result["blind_answer_rejected_count"] = rejected
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="prepare isolated profiles without launching Codex")
    mode.add_argument("--mock", action="store_true", help="exercise pairing with synthetic metadata only")
    parser.add_argument("--model", help="same explicit Codex model for both arms (required for live execution)")
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh"), default="medium")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"per-arm timeout, maximum {MAX_TIMEOUT}s")
    parser.add_argument("--blind-dir", type=Path, help="new private directory outside the repository for blinded synthetic answers")
    parser.add_argument("--case", action="append", choices=SELECTABLE_CASE_IDS,
                        help="select one or more cases; default is C01 and R10")
    parser.add_argument("--installed-python", type=Path,
                        help="absolute interpreter path of installed JevCompass 0.1.12 release")
    parser.add_argument(
        "--allow-openrouter-key", action="store_true",
        help="explicitly forward OPENROUTER_API_KEY to the treatment arm of a live C03-only synthetic pair",
    )
    args = parser.parse_args(argv)
    selected_mode = "dry-run" if args.dry_run else "mock" if args.mock else "run"
    if selected_mode == "run" and not args.model:
        parser.error("--model is required for a live pair")
    try:
        result = run_pair(
            mode=selected_mode, model=args.model, timeout=args.timeout,
            reasoning_effort=args.reasoning_effort, blind_dir=args.blind_dir,
            cases=tuple(args.case) if args.case else CASE_IDS,
            installed_python=args.installed_python,
            allow_openrouter_key=args.allow_openrouter_key,
        )
    except FileNotFoundError as error:
        code = "skill_unavailable" if "skill" in str(error) else "fixture_unavailable"
        print(json.dumps({"pilot": "jevcompass-codex-setup-supplement", **_safe_failure(code)}))
        return 1
    except RuntimeError as error:
        message = str(error)
        code = (
            "auth_unavailable" if "auth" in message else
            "openrouter_key_unavailable" if "OpenRouter" in message else
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
