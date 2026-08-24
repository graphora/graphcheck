#!/usr/bin/env python3
"""Validate Action inputs, resolve artifacts, and create a temporary CI profile."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

UPLOAD_MODES = {"always", "on-failure", "never"}


def main() -> int:
    try:
        _validate_inputs()
        config = _project_config()
        artifacts = config.get("artifacts", ".graphcheck")
        if (
            not isinstance(artifacts, str)
            or not artifacts
            or "\n" in artifacts
            or "\r" in artifacts
        ):
            raise ValueError("artifacts must be a non-empty, single-line path")
        _append_environment("GRAPHCHECK_ARTIFACTS_DIR", artifacts)
        _generate_profile()
    except Exception as exc:
        print(
            f"::error title=GraphCheck Action configuration::{_command_value(str(exc))}",
            file=sys.stderr,
        )
        return 1
    return 0


def _validate_inputs() -> None:
    concurrency = os.environ.get("GC_CONCURRENCY", "")
    if concurrency and (
        not concurrency.isascii() or not concurrency.isdecimal() or int(concurrency) < 1
    ):
        raise ValueError("concurrency must be a positive integer when provided")
    upload_mode = os.environ.get("GC_UPLOAD_ARTIFACTS", "always")
    if upload_mode not in UPLOAD_MODES:
        raise ValueError("upload-artifacts must be one of: always, on-failure, never")


def _project_config() -> dict[str, object]:
    path = Path("graphcheck.yml")
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("graphcheck.yml must contain a mapping")
    return loaded


def _generate_profile() -> None:
    path = Path("profiles.yml")
    if path.exists():
        print("profiles.yml already exists, using it as-is.")
        return
    profile = os.environ["GC_PROFILE"]
    data = {
        "default": profile,
        "profiles": {
            profile: {
                "uri": os.environ["GC_URI"],
                "user": os.environ["GC_USER"],
                "password": None,
                "password_env": "NEO4J_PASSWORD",
                "database": os.environ["GC_DATABASE"],
            }
        },
    }
    path.write_text(
        yaml.safe_dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8"
    )
    _append_file(os.environ.get("GITHUB_OUTPUT"), "generated_profiles=true\n")
    print("Generated profiles.yml")


def _append_environment(name: str, value: str) -> None:
    destination = os.environ.get("GITHUB_ENV")
    if not destination:
        raise ValueError("GITHUB_ENV is not set")
    _append_file(destination, f"{name}={value}\n")


def _append_file(path: str | None, value: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(value)


def _command_value(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


if __name__ == "__main__":
    raise SystemExit(main())
