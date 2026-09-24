"""Optional, fail-open access to the user's native credential store."""

from __future__ import annotations

import contextlib
import getpass
import io
import json
import os
import subprocess
import sys
from typing import Any


SERVICE_NAME = "jevcompass/openrouter"
ACCOUNT_NAME = "api-key"
LOOKUP_TIMEOUT_SECONDS = 0.35
AUTH_TIMEOUT_SECONDS = 30.0

# Restrict secrets to the native stores supported by keyring. In particular,
# do not accept keyrings.alt, plaintext file stores, or third-party backends.
_TRUSTED_BACKEND_MODULES = frozenset({
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
    "keyring.backends.Windows",
})


def _trusted_backend() -> Any | None:
    try:
        import keyring
        backend = keyring.get_keyring()
    except Exception:
        return None
    if backend.__class__.__module__ not in _TRUSTED_BACKEND_MODULES:
        return None
    return backend


def _operate(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    backend = _trusted_backend()
    if backend is None:
        return {"ok": False, "secure_store_available": False}

    if action == "status":
        try:
            credential = backend.get_password(SERVICE_NAME, ACCOUNT_NAME)
        except Exception:
            return {"ok": False, "secure_store_available": False}
        return {
            "ok": True,
            "secure_store_available": True,
            "configured": isinstance(credential, str) and bool(credential),
        }

    if action == "get":
        try:
            credential = backend.get_password(SERVICE_NAME, ACCOUNT_NAME)
        except Exception:
            return {"ok": False}
        if not isinstance(credential, str) or not credential:
            return {"ok": True, "credential": ""}
        return {"ok": True, "credential": credential}

    if action == "set":
        credential = payload.get("credential")
        if not isinstance(credential, str) or not credential:
            return {"ok": False}
        try:
            backend.set_password(SERVICE_NAME, ACCOUNT_NAME, credential)
        except Exception:
            return {"ok": False}
        return {"ok": True, "secure_store_available": True}

    if action == "delete":
        try:
            backend.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        except Exception:
            return {"ok": False}
        return {"ok": True, "secure_store_available": True}

    return {"ok": False}


def _worker_main(action: str) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw else {}
        if not isinstance(payload, dict):
            payload = {}
        # Keep backend diagnostics away from the single machine-readable result.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = _operate(action, payload)
    except Exception:
        result = {"ok": False}
    sys.stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0


def _run_worker(
    action: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = LOOKUP_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    command = [sys.executable, "-m", "jevcompass.credentials", "--worker", action]
    kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "text": True,
        "timeout": timeout,
        "check": False,
    }
    if payload is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))

    try:
        completed = subprocess.run(command, **kwargs)
        if completed.returncode != 0 or not isinstance(completed.stdout, str):
            return None
        result = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None
    return result if isinstance(result, dict) else None


def resolve_api_key() -> str:
    """Resolve the key without letting optional keyring failures affect Codex."""
    environment_key = os.environ.get("OPENROUTER_API_KEY", "")
    if environment_key:
        return environment_key
    result = _run_worker("get")
    credential = result.get("credential") if result and result.get("ok") else None
    return credential if isinstance(credential, str) else ""


def credential_status() -> dict[str, Any]:
    """Return redacted credential availability for diagnostics."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return {
            "configured": True,
            "source": "environment",
            "secure_store_available": None,
        }
    result = _run_worker("status")
    available = bool(result and result.get("ok") and result.get("secure_store_available"))
    configured = bool(available and result.get("configured"))
    return {
        "configured": configured,
        "source": "system-keyring" if configured else "none",
        "secure_store_available": available,
    }


def auth_main(action: str) -> int:
    """Manage the keyring interactively; never accept a secret as an argument."""
    if action == "status":
        status = credential_status()
        if status["source"] == "environment":
            print("OpenRouter API key: configured through the environment.")
        elif status["configured"]:
            print("OpenRouter API key: configured in the system keyring.")
        elif status["secure_store_available"]:
            print("System keyring is ready; no OpenRouter API key is stored.")
        else:
            print("No usable system keyring is available; local-only advice remains available.")
        return 0

    if not sys.stdin.isatty() or not sys.stderr.isatty():
        print("This command requires an interactive terminal.", file=sys.stderr)
        return 2

    if action == "set":
        credential = getpass.getpass("OpenRouter API key: ")
        confirmation = getpass.getpass("Confirm OpenRouter API key: ")
        if not credential or credential != confirmation:
            print("The key was empty or the confirmation did not match.", file=sys.stderr)
            return 2
        result = _run_worker(
            "set",
            payload={"credential": credential},
            timeout=AUTH_TIMEOUT_SECONDS,
        )
        if not result or not result.get("ok"):
            print("Could not store the key in a supported system keyring.", file=sys.stderr)
            return 1
        print("OpenRouter API key stored in the system keyring.")
        return 0

    if action == "delete":
        confirmation = getpass.getpass("Type DELETE to remove the JevCompass key: ")
        if confirmation != "DELETE":
            print("Keyring entry was not removed.")
            return 0
        result = _run_worker("delete", timeout=AUTH_TIMEOUT_SECONDS)
        if not result or not result.get("ok"):
            print("Could not remove a key from a supported system keyring.", file=sys.stderr)
            return 1
        print("JevCompass keyring entry removed. An environment variable, if set, is unchanged.")
        return 0

    return 2


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        return 2
    return _worker_main(sys.argv[2])


if __name__ == "__main__":
    raise SystemExit(main())
