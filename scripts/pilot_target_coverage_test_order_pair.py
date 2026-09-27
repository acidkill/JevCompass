#!/usr/bin/env python3
"""Run a bounded matched pair with explicit, equal target-coverage metadata.

The frozen checkout fixture is copied to a private temporary staging directory;
only that copy's test-options.json receives the reviewed coverage targets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

import pilot_cross_layer_test_order_pair as cross_layer

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "cross_layer_test_order"
BASELINE_PROMPT = (
    "Implement the documented checkout service fee. Read README.md, CONTRACT.md, "
    "TESTING.md, and test-options.json. Change only checkout/service.py. After "
    "your source change, choose one focused candidate from test-options.json "
    "using your own judgment about the task and its coverage/runtime metadata, "
    "then run it. Always run the required full suite: "
    f"{cross_layer.REQUIRED_COMMAND}. Do not access external networks or "
    "services unless a JevCompass ranking instruction explicitly authorizes "
    "that rank request. Report command exit statuses without source text."
)
TARGET_RANKING = (
    "\n\nAfter a meaningful edit to checkout/service.py has completed, and "
    "before starting any focused candidate, request an advisory order using "
    "the same local test-options.json by running exactly:\n"
    f"{cross_layer.RANK_COMMAND}\n"
    "The rank command does not execute tests. Its recommendation is optional "
    "and nonbinding: use your judgment to choose either declared focused "
    "candidate, whether the returned order is valid or not. If it abstains, "
    "fails, or returns invalid data, choose locally using the task and "
    "candidate metadata. Then run the required full suite. Do not call Jev "
    "before changing checkout/service.py."
)
TARGETS_BY_ID = {
    "unit": ["internal_logic_changed"],
    "integration": [
        "boundary_mapping_changed",
        "internal_logic_changed",
        "public_contract_changed",
    ],
}
_ALLOWED_SIGNALS = {
    "internal_logic_changed", "boundary_mapping_changed", "public_contract_changed",
}
_REVIEWED_SHA256 = {
    "README.md": "5f7e9eedaffa6a076b072d413456a03903bfb35448d8406c1b19c0ad0819e6e3",
    "CONTRACT.md": "a58b3723cb50f32b68de748c42b0b52b51f05eb38aa55dc044b895a7d2aa18d4",
    "TESTING.md": "e085b58c31ea651eb9df3317fa7c8683ac31742584a2b2890a877e11d5add5e7",
    "test-options.json": "303f55f544e96977d87164c041a780251080e7664dc0eb0605e17333f37f16f0",
    "checkout/service.py": "6153c27ac5d0aa9a7855530059d597e7f710d41488b39b61e257705fd6f5fb3f",
    "tests/test_unit_checkout.py": "a1afeabb09793f251d8b4fa0116a03548981c558a120e2ba9775e1c149915590",
    "tests/test_integration_checkout.py": "e21b9fb91ca939899c6009520f70cc27b1959ebd428d00957bb8e402bf341977",
}


def _strict_json(text: str) -> Any:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate-json-key")
            value[key] = item
        return value

    return json.loads(text, object_pairs_hook=pairs)


def _verify_fixture_evidence(source: Path) -> None:
    for relative, expected_digest in _REVIEWED_SHA256.items():
        path = source / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
            raise ValueError("reviewed fixture evidence unavailable")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_digest:
            raise ValueError("fixture differs from reviewed target evidence")
    evidence = {
        "tests/test_unit_checkout.py": (
            "quote_checkout(25)", "(25, 1, 26)", "test_nonnegative_integer_validation",
        ),
        "tests/test_integration_checkout.py": (
            "--subtotal-cents", "json.loads(run.stdout)",
            "\"service_fee_cents\": 25", "\"total_cents\": 1275",
        ),
    }
    for relative, markers in evidence.items():
        path = source / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 * 1024:
            raise ValueError("coverage evidence unavailable")
        text = path.read_text(encoding="utf-8")
        if any(marker not in text for marker in markers):
            raise ValueError("frozen fixture no longer supports target metadata")


def _prepare_enriched_fixture(destination: Path, *, source: Path = FIXTURE) -> Path:
    """Create an enriched private fixture copy without modifying the frozen source."""
    if not source.is_dir() or source.is_symlink():
        raise ValueError("synthetic fixture unavailable")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("synthetic fixture contains symbolic links")
    _verify_fixture_evidence(source)
    if any("expected" in path.name.lower() for path in source.rglob("*")):
        raise ValueError("synthetic fixture contains evaluator material")
    shutil.copytree(source, destination)
    options_path = destination / "test-options.json"
    if options_path.is_symlink() or not options_path.is_file() or options_path.stat().st_size > 32 * 1024:
        raise ValueError("test options unavailable")
    value = _strict_json(options_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("test options invalid")
    signals = value.get("signals")
    if (not isinstance(signals, list) or
            any(not isinstance(signal, str) or signal not in _ALLOWED_SIGNALS for signal in signals)):
        raise ValueError("test signals invalid")
    value["signals"] = sorted(set(signals) | {"boundary_mapping_changed"})
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 2:
        raise ValueError("test candidates invalid")
    by_id = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("test candidate invalid")
        identifier = candidate.get("id")
        if identifier in by_id or identifier not in TARGETS_BY_ID:
            raise ValueError("test candidate ids invalid")
        by_id[identifier] = candidate
    if set(by_id) != set(TARGETS_BY_ID):
        raise ValueError("test candidates do not match the frozen contract")
    for identifier, expected_command in (
        ("unit", cross_layer.UNIT_COMMAND),
        ("integration", cross_layer.INTEGRATION_COMMAND),
    ):
        candidate = by_id[identifier]
        if (candidate.get("kind") != identifier
                or candidate.get("command") != expected_command):
            raise ValueError("test candidate command mismatch")
        candidate["coverage_targets"] = TARGETS_BY_ID[identifier]
    required = value.get("required")
    if required != [{"id": "full", "command": cross_layer.REQUIRED_COMMAND}]:
        raise ValueError("required suite does not match the frozen contract")
    options_path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n",
                            encoding="utf-8")
    os.chmod(options_path, 0o600)
    return destination


def run_pair(**kwargs: Any) -> dict[str, Any]:
    """Run the existing cross-layer pair using a staged enriched fixture."""
    with tempfile.TemporaryDirectory(prefix="jev-target-coverage-") as temporary:
        enriched = _prepare_enriched_fixture(Path(temporary) / "fixture")
        kwargs["fixture_source"] = enriched
        kwargs["baseline_prompt"] = BASELINE_PROMPT
        kwargs["treatment_prompt_suffix"] = TARGET_RANKING
        kwargs["track_post_change_rank_phase"] = True
        kwargs["baseline_first_candidate_policy"] = "either"
        kwargs["advice_policy"] = "nonbinding"
        return cross_layer.run_pair(**kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="authorize execution of Codex CLI arms")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--timeout", type=int, default=cross_layer.engine.DEFAULT_TIMEOUT)
    parser.add_argument(
        "--max-tokens", type=int,
        help=("optional per-arm observed usage ceiling; detected from completed-turn "
              "usage events, not a provider per-request hard cap"),
    )
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-openrouter-key", action="store_true",
                        help=cross_layer.engine.OPENROUTER_KEY_HELP)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("pass --live to run model-backed CLI arms")
    codex = shutil.which(args.codex)
    if not codex:
        print(json.dumps({"status": "failed", "failure": "codex_unavailable"}))
        return 2
    try:
        receipt = run_pair(
            codex=codex, model=args.model, reasoning_effort=args.reasoning_effort,
            timeout=args.timeout, seed=args.seed, output_dir=args.output_dir,
            allow_openrouter_key=args.allow_openrouter_key,
            max_tokens=args.max_tokens,
        )
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({"status": "failed", "failure": "runner_setup_failed"}))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
