"""Credential-isolated supervisor bridge for profile-driven triage decisions.

The agent subprocess gets no Jev/OpenRouter key. A runner observes the configured
focused-test failure, starts this one-shot loopback bridge, and installs the exact
Python shim built here. The supervisor validates only fixed enum metadata, calls
the production triage function, writes a redacted typed receipt, then returns a
catalog-authored result to the child. Bridged Codex network egress must be scoped
separately; this module does not claim localhost-only egress.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Sequence


MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 64 * 1024
MAX_USAGE = 10**12
_OBSERVATION_ENUMS = {
    "import": ("ImportObservation", "import_observations"),
    "assertion": ("AssertionObservation", "assertion_observations"),
    "timeout": ("TimeoutObservation", "timeout_observations"),
}
_OBSERVATION_FLAGS = {
    "import": "--import-observation",
    "assertion": "--assertion-observation",
    "timeout": "--timeout-observation",
}


def _strict_json(raw: str) -> Any:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite JSON number")
        return number

    return json.loads(raw, object_pairs_hook=unique_pairs, parse_float=finite_float,
                      parse_constant=lambda _value: (_ for _ in ()).throw(
                          ValueError("non-finite JSON")))


@dataclass(frozen=True)
class ProfileTriageSpec:
    """Supervisor-validated enum contract; values contain no source or free text."""

    failure_kinds: tuple[str, ...]
    hypotheses: tuple[str, ...]
    allowed_observations: Mapping[str, tuple[str, ...]]
    rank_hypotheses: bool = False
    diagnostic_costs: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from jevcompass.triage import DiagnosticCost, FailureKind, HypothesisId

        if (not self.failure_kinds or not self.hypotheses
                or any(not isinstance(value, str) for value in self.failure_kinds)
                or any(not isinstance(value, str) for value in self.hypotheses)
                or len(set(self.failure_kinds)) != len(self.failure_kinds)
                or len(set(self.hypotheses)) != len(self.hypotheses)
                or any(value not in {item.value for item in FailureKind}
                       for value in self.failure_kinds)
                or any(value not in {item.value for item in HypothesisId}
                       for value in self.hypotheses)
                or len(self.failure_kinds) > 4 or len(self.hypotheses) > 8):
            raise ValueError("invalid profile triage enum contract")
        if type(self.rank_hypotheses) is not bool:
            raise ValueError("rank_hypotheses must be boolean")
        # Plain strings only: these values are embedded verbatim in the shim.
        if not isinstance(self.diagnostic_costs, Mapping):
            raise ValueError("invalid profile diagnostic costs")
        allowed_costs = {item.value for item in DiagnosticCost}
        for hypothesis, cost in self.diagnostic_costs.items():
            if (type(hypothesis) is not str or type(cost) is not str
                    or hypothesis not in self.hypotheses
                    or cost not in allowed_costs):
                raise ValueError("invalid profile diagnostic costs")
        if not isinstance(self.allowed_observations, Mapping):
            raise ValueError("invalid profile observation allowlist")
        for group, values in self.allowed_observations.items():
            if group not in _OBSERVATION_ENUMS:
                raise ValueError("unknown observation group")
            enum_name, _ = _OBSERVATION_ENUMS[group]
            enum_type = getattr(__import__("jevcompass.triage", fromlist=[enum_name]), enum_name)
            allowed = {item.value for item in enum_type}
            if (not isinstance(values, tuple) or len(values) > len(allowed)
                    or any(not isinstance(value, str) or value not in allowed for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError("invalid profile observation enum allowlist")


class _ObservedDecisionClient:
    """Lazy DecisionsClient subclass counting actual outbound transport calls."""

    def __new__(cls, factory: Callable[[], Any], counter: list[int]):
        from jevcompass.decisions import DecisionsClient

        class Observed(DecisionsClient):
            def __init__(self) -> None:
                self._factory = factory
                self._counter = counter
                self._delegate = None

            def decide_with_usage(self, state: dict[str, Any], questions: dict[str, Any]):
                if self._delegate is None:
                    delegate = self._factory()
                    if not isinstance(delegate, DecisionsClient):
                        raise TypeError("decision client factory returned an unsupported client")
                    original = delegate.transport

                    def counted_transport(url: str, body: bytes, api_key: str, timeout: float):
                        self._counter[0] += 1
                        return original(url, body, api_key, timeout)

                    delegate.transport = counted_transport
                    self._delegate = delegate
                return self._delegate.decide_with_usage(state, questions)

        return Observed()


def _safe_usage(value: Any) -> dict[str, int | float | None] | None:
    if value is None:
        return None
    inputs, outputs, cost = value.input_tokens, value.output_tokens, value.cost_usd
    if (isinstance(inputs, bool) or not isinstance(inputs, int) or not 0 <= inputs <= MAX_USAGE
            or isinstance(outputs, bool) or not isinstance(outputs, int)
            or not 0 <= outputs <= MAX_USAGE):
        raise ValueError("invalid decision usage")
    if cost is not None and (
        isinstance(cost, bool) or not isinstance(cost, (int, float))
        or not math.isfinite(float(cost)) or not 0 <= cost <= MAX_USAGE
    ):
        raise ValueError("invalid decision usage")
    return {"input_tokens": inputs, "output_tokens": outputs,
            "cost_usd": float(cost) if cost is not None else None}


def _private_write(path: Path, payload: Mapping[str, Any]) -> None:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.stat().st_mode & 0o077:
        raise OSError("receipt parent must be a private real directory")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
               + "\n").encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise OSError("typed receipt exceeds size limit")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


class ProfileTriageBridge:
    """One request after a runner-confirmed focused failure; response is catalog-only."""

    def __init__(
        self, spec: ProfileTriageSpec, *, receipt_path: Path,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.spec = spec
        self.receipt_path = receipt_path
        self.client_factory = client_factory
        self._lock = threading.Lock()
        self._request_count = 0
        self._observed_exit: int | None = None
        self._focused_seen = False
        self._state = "not_requested"
        self._decision_receipt: dict[str, Any] | None = None
        self._server = self._make_server()
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.05}, daemon=True)

    def _make_server(self) -> ThreadingHTTPServer:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"
            server_version = "JevProfileTriageBridge"
            sys_version = ""

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_POST(self) -> None:
                bridge._handle(self)

            def do_GET(self) -> None:
                bridge._handle(self)

            def do_PUT(self) -> None:
                bridge._handle(self)

            def do_DELETE(self) -> None:
                bridge._handle(self)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server.block_on_close = False
        return server

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/triage"

    def __enter__(self) -> "ProfileTriageBridge":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._thread.is_alive():
            self._server.shutdown()
            self._thread.join(timeout=2.0)
        self._server.server_close()

    def observe_focused_failure(self, exit_status: int, *, failure_confirmed: bool) -> bool:
        """Called by the runner only for the first focused-command completion."""
        if type(failure_confirmed) is not bool:
            raise ValueError("failure confirmation must be boolean")
        with self._lock:
            if self._focused_seen:
                return False
            self._focused_seen = True
            if (failure_confirmed and isinstance(exit_status, int)
                    and not isinstance(exit_status, bool) and exit_status != 0):
                self._observed_exit = exit_status
                return True
            self._state = "not_eligible"
            return False

    def receipt(self) -> dict[str, Any]:
        with self._lock:
            return {
                "request_count": min(self._request_count, 2),
                "bridge_request_count": min(self._request_count, 2),
                "state": self._state,
                "observed_focused_failure": self._observed_exit is not None,
                "decision": dict(self._decision_receipt) if self._decision_receipt else None,
            }

    def _send(self, handler: BaseHTTPRequestHandler, status: int, payload: Mapping[str, Any]) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_RESPONSE_BYTES:
            status, encoded = 502, b'{"status":"unavailable"}'
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(encoded)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(encoded)
            handler.close_connection = True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.client_address[0] != "127.0.0.1":
            self._send(handler, 403, {"status": "unavailable"})
            return
        with self._lock:
            self._request_count += 1
            number, observed_exit = self._request_count, self._observed_exit
            if number > 1:
                self._state = "duplicate"
            elif observed_exit is None:
                self._state = "premature"
        if number > 1:
            self._send(handler, 429, {"status": "unavailable"})
            return
        if observed_exit is None:
            self._write_rejection("premature")
            self._send(handler, 409, {"status": "unavailable"})
            return
        try:
            request = self._read_request(handler)
            args = self._validated_request(request, observed_exit)
            payload, receipt = self._decide(args, observed_exit)
            _private_write(self.receipt_path, receipt)
            with self._lock:
                self._state = "completed"
                self._decision_receipt = receipt
            self._send(handler, 200, payload)
        except (ValueError, TypeError, OSError, UnicodeError, json.JSONDecodeError):
            self._write_rejection("invalid_request")
            self._send(handler, 400, {"status": "unavailable"})
        except Exception:
            # Never forward provider, transport, or exception text to the child.
            self._write_rejection("provider_or_bridge_error")
            self._send(handler, 200, self._local_fallback(observed_exit))

    def _read_request(self, handler: BaseHTTPRequestHandler) -> Any:
        if (handler.command != "POST" or handler.path != "/triage"
                or handler.headers.get("Transfer-Encoding") is not None):
            raise ValueError("invalid request")
        lengths = handler.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or handler.headers.get_content_type() != "application/json":
            raise ValueError("invalid request")
        length = int(lengths[0])
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ValueError("invalid request")
        raw = handler.rfile.read(length)
        if len(raw) != length:
            raise ValueError("short request")
        return _strict_json(raw.decode("utf-8"))

    def _validated_request(self, request: Any, observed_exit: int) -> dict[str, Any]:
        if not isinstance(request, dict) or set(request) != {
            "observed_exit_status", "failure_kinds", "hypotheses", "observations",
            "rank_hypotheses", "diagnostic_costs",
        }:
            raise ValueError("invalid enum-only request")
        if (type(request["observed_exit_status"]) is not int
                or request["observed_exit_status"] != observed_exit
                or request["failure_kinds"] != list(self.spec.failure_kinds)
                or request["hypotheses"] != list(self.spec.hypotheses)
                or type(request["rank_hypotheses"]) is not bool
                or request["rank_hypotheses"] != self.spec.rank_hypotheses):
            raise ValueError("request does not match profile")
        raw_observations = request["observations"]
        if not isinstance(raw_observations, dict) or set(raw_observations) - set(self.spec.allowed_observations):
            raise ValueError("invalid observation groups")
        observations: dict[str, tuple[str, ...]] = {}
        for group, values in raw_observations.items():
            allowed = set(self.spec.allowed_observations[group])
            if (not isinstance(values, list) or len(values) > len(allowed)
                    or any(not isinstance(value, str) or value not in allowed for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError("invalid observation enum")
            observations[group] = tuple(values)
        raw_costs = request["diagnostic_costs"]
        if (not isinstance(raw_costs, dict)
                or any(type(key) is not str or type(value) is not str
                       for key, value in raw_costs.items())
                or raw_costs != dict(self.spec.diagnostic_costs)):
            raise ValueError("invalid diagnostic cost request")
        return {
            "observations": observations,
            "diagnostic_costs": dict(raw_costs),
        }

    def _decide(self, args: dict[str, Any], observed_exit: int):
        from jevcompass.decisions import DecisionsClient
        from jevcompass.triage import (
            CATALOG, AssertionObservation, DiagnosticCost, FailureKind, HypothesisId,
            ImportObservation, TriageDecisionReason, TimeoutObservation,
            TriageResult, triage_failure,
        )

        counter = [0]
        def factory():
            return self.client_factory() if self.client_factory is not None else DecisionsClient()

        client = _ObservedDecisionClient(factory, counter)
        enum_groups = {
            "import": (ImportObservation, "import_observations"),
            "assertion": (AssertionObservation, "assertion_observations"),
            "timeout": (TimeoutObservation, "timeout_observations"),
        }
        triage_observations = {
            enum_groups[group][1]: tuple(enum_groups[group][0](value) for value in values)
            for group, values in args["observations"].items()
        }
        diagnostic_costs = {
            HypothesisId(key): DiagnosticCost(value)
            for key, value in args["diagnostic_costs"].items()
        }
        triage_options = dict(triage_observations)
        if diagnostic_costs:
            triage_options["diagnostic_costs"] = diagnostic_costs
        result = triage_failure(
            tuple(FailureKind(value) for value in self.spec.failure_kinds),
            tuple(HypothesisId(value) for value in self.spec.hypotheses),
            observed_exit,
            client=client,
            rank_hypotheses=self.spec.rank_hypotheses,
            **triage_options,
        )
        if not isinstance(result, TriageResult) or result.observed_exit_status != observed_exit:
            raise ValueError("invalid production triage result")
        reason = result.decision_reason
        if reason is not None and not isinstance(reason, TriageDecisionReason):
            raise ValueError("invalid decision reason")
        allowed_hypotheses = set(self.spec.hypotheses)
        step_ids = [step.id.value for step in result.steps]
        if (len(step_ids) > 2 or len(set(step_ids)) != len(step_ids)
                or any(identifier not in allowed_hypotheses for identifier in step_ids)):
            raise ValueError("invalid diagnostic choice")
        order = [item.value for item in result.hypothesis_order]
        if (len(order) > 4 or len(set(order)) != len(order)
                or any(item not in allowed_hypotheses or item == "confirm_behavior_contract"
                       for item in order)):
            raise ValueError("invalid causal hypothesis order")
        ranking_status = result.hypothesis_ranking_status
        if ranking_status not in {"complete", "incomplete", "not_established"}:
            raise ValueError("invalid hypothesis ranking status")
        if not self.spec.rank_hypotheses and (order or ranking_status != "not_established"):
            raise ValueError("unexpected hypothesis ranking")
        if ranking_status == "complete" and len(order) < 2:
            raise ValueError("complete ranking must order multiple causes")
        if result.status not in {"remote-choice", "no-remote-choice"}:
            raise ValueError("invalid triage status")
        if result.status == "remote-choice" and (
            not step_ids or reason is not TriageDecisionReason.ACCEPTED
        ):
            raise ValueError("invalid accepted choice")
        if result.status != "remote-choice" and reason is TriageDecisionReason.ACCEPTED:
            raise ValueError("accepted reason requires remote choice")
        usage = _safe_usage(result.decision_usage)
        source = (
            "cached_preferred_next_step" if result.cache_hit
            else "remote_preferred_next_step"
        ) if reason is TriageDecisionReason.ACCEPTED else (
            "locally_resolved_guidance" if reason is TriageDecisionReason.LOCAL_RESOLUTION
            else "unranked_local_fallback"
        )
        steps = []
        for index, identifier in enumerate(step_ids):
            entry = CATALOG[HypothesisId(identifier)].step
            steps.append({
                "id": identifier, "title": entry.title, "instruction": entry.instruction,
                "selection_source": source if index == 0 else "unranked_local_fallback",
            })
        usage_status = "reported" if usage is not None else (
            "not_invoked" if counter[0] == 0 else "not_reported"
        )
        if type(result.cache_hit) is not bool:
            raise ValueError("invalid cache status")
        receipt = {
            "schema_version": 1,
            "status": result.status,
            "bridge_request_count": 1,
            "observed_exit_status": observed_exit,
            "test_failed": True,
            "executed": False,
            "decision_reason": reason.value if reason is not None else None,
            "diagnostic_step_ids": step_ids,
            "diagnostic_choice_id": step_ids[0] if step_ids else None,
            "diagnostic_selection_source": source if step_ids else "none",
            "hypothesis_order": order,
            "hypothesis_ranking_status": ranking_status,
            "cache_hit": result.cache_hit is True,
            "provider_transport_call_count": counter[0],
            "decision_usage_status": usage_status,
            "decision_usage": usage,
        }
        payload = {
            "status": result.status,
            "observed_exit_status": observed_exit,
            "test_failed": True,
            "executed": False,
            "decision_reason": receipt["decision_reason"],
            "cache_hit": receipt["cache_hit"],
            "hypothesis_ranking_status": ranking_status,
            "steps": steps,
            "decision_usage": usage,
        }
        if self.spec.rank_hypotheses:
            payload["hypothesis_order"] = order
        return payload, receipt

    def _local_fallback(self, observed_exit: int) -> dict[str, Any]:
        from jevcompass.triage import CATALOG, HypothesisId

        steps = [CATALOG[HypothesisId(value)].step for value in self.spec.hypotheses[:2]]
        return {
            "status": "no-remote-choice", "observed_exit_status": observed_exit,
            "test_failed": True, "executed": False, "decision_reason": "provider_error",
            "cache_hit": False, "hypothesis_ranking_status": "not_established",
            "steps": [{
                "id": step.id.value, "title": step.title, "instruction": step.instruction,
                "selection_source": "unranked_local_fallback",
            } for step in steps],
            "decision_usage": None,
        }

    def _write_rejection(self, state: str) -> None:
        with self._lock:
            self._state = state
            if self._decision_receipt is not None:
                return
        receipt = {
            "schema_version": 1, "status": "rejected", "request_state": state,
            "bridge_request_count": min(self._request_count, 2),
            "observed_exit_status": self._observed_exit,
            "provider_transport_call_count": 0,
            "diagnostic_choice_id": None, "hypothesis_order": [],
            "hypothesis_ranking_status": "not_established", "cache_hit": False,
            "decision_usage_status": "not_invoked", "decision_usage": None,
        }
        try:
            _private_write(self.receipt_path, receipt)
        except FileExistsError:
            pass
        except OSError:
            pass
        with self._lock:
            self._decision_receipt = receipt


def command_for(spec: ProfileTriageSpec, exit_status: int,
                observations: Mapping[str, Sequence[str]] | None = None) -> tuple[str, ...]:
    """Return the canonical safe CLI argv for the validated profile."""
    if isinstance(exit_status, bool) or not isinstance(exit_status, int) or exit_status == 0:
        raise ValueError("triage bridge requires a nonzero observed exit")
    observed = observations or {}
    if set(observed) - set(spec.allowed_observations):
        raise ValueError("unknown observation group")
    args = ["python", "-m", "jevcompass", "triage", "--exit-code", str(exit_status)]
    for kind in spec.failure_kinds:
        args.extend(("--kind", kind))
    for hypothesis in spec.hypotheses:
        args.extend(("--hypothesis", hypothesis))
    for hypothesis, cost in spec.diagnostic_costs.items():
        args.extend(("--diagnostic-cost", f"{hypothesis}={cost}"))
    for group in ("import", "assertion", "timeout"):
        values = tuple(observed.get(group, ()))
        allowed = set(spec.allowed_observations.get(group, ()))
        if (len(set(values)) != len(values)
                or any(not isinstance(value, str) or value not in allowed for value in values)):
            raise ValueError("invalid observation enum")
        for value in values:
            args.extend((_OBSERVATION_FLAGS[group], value))
    if spec.rank_hypotheses:
        args.append("--rank-hypotheses")
    args.append("--json")
    return tuple(args)


def write_python_shim(directory: Path, bridge: ProfileTriageBridge,
                      real_python: str = sys.executable) -> Path:
    """Write a private exact-command shim; unknown commands execute real Python."""
    if (not isinstance(real_python, str) or not real_python
            or not bridge.url.startswith("http://127.0.0.1:")):
        raise ValueError("invalid bridge shim configuration")
    bindir = directory / "bin"
    bindir.mkdir(mode=0o700, parents=True, exist_ok=True)
    shim = bindir / "python"
    groups = {key: list(value) for key, value in bridge.spec.allowed_observations.items()}
    costs = dict(bridge.spec.diagnostic_costs)
    source = f'''#!/usr/bin/env python3
import json, os, sys
from urllib.request import Request, urlopen
REAL = {real_python!r}
URL = {bridge.url!r}
KINDS = {list(bridge.spec.failure_kinds)!r}
HYPOTHESES = {list(bridge.spec.hypotheses)!r}
OBSERVATIONS = {groups!r}
RANK = {bridge.spec.rank_hypotheses!r}
COSTS = {costs!r}
FLAGS = {dict(_OBSERVATION_FLAGS)!r}
ARGS = sys.argv[1:]
def fallback():
    os.execv(REAL, [REAL, *ARGS])
def pairs(flag, values):
    result = []
    for value in values:
        result.extend((flag, value))
    return result
prefix = ["-m", "jevcompass", "triage", "--exit-code"]
if len(ARGS) < 6 or ARGS[:4] != prefix:
    fallback()
try:
    code = int(ARGS[4])
    if code == 0:
        fallback()
    tail = ARGS[5:]
    expected = pairs("--kind", KINDS) + pairs("--hypothesis", HYPOTHESES)
    if tail[:len(expected)] != expected:
        fallback()
    tail = tail[len(expected):]
    supplied_costs = {{}}
    while tail and tail[0] == "--diagnostic-cost":
        if len(tail) < 2:
            fallback()
        name, separator, level = tail[1].partition("=")
        if (not separator or name not in COSTS or level != COSTS[name]
                or name in supplied_costs):
            fallback()
        supplied_costs[name] = level
        tail = tail[2:]
    if supplied_costs != COSTS:
        fallback()
    observations = {{group: [] for group in OBSERVATIONS}}
    while tail and tail[0] != "--json" and tail[0] != "--rank-hypotheses":
        if len(tail) < 2:
            fallback()
        flag, value = tail[0], tail[1]
        group = next((name for name, item in FLAGS.items() if item == flag), None)
        if group is None or value not in OBSERVATIONS.get(group, []) or value in observations[group]:
            fallback()
        observations[group].append(value)
        tail = tail[2:]
    if RANK:
        if not tail or tail[0] != "--rank-hypotheses":
            fallback()
        tail = tail[1:]
    elif tail and tail[0] == "--rank-hypotheses":
        fallback()
    if tail != ["--json"]:
        fallback()
    body = json.dumps({{
        "observed_exit_status": code, "failure_kinds": KINDS,
        "hypotheses": HYPOTHESES, "observations": observations,
        "rank_hypotheses": RANK, "diagnostic_costs": supplied_costs,
    }}, separators=(",", ":")).encode()
    req = Request(URL, data=body, headers={{"Content-Type": "application/json"}}, method="POST")
    with urlopen(req, timeout=2.0) as response:
        data = response.read({MAX_RESPONSE_BYTES + 1})
    if len(data) <= {MAX_RESPONSE_BYTES}:
        sys.stdout.buffer.write(data + (b"\\n" if not data.endswith(b"\\n") else b""))
        raise SystemExit(0)
except SystemExit:
    raise
except Exception:
    pass
fallback()
'''
    fd = os.open(shim, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o700)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            shim.unlink()
        except OSError:
            pass
        raise
    return bindir
