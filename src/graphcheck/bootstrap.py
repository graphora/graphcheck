"""Lightweight console entry point with a standard-library-only version fast path."""

from __future__ import annotations

import sys

from graphcheck import __version__


def cli() -> None:
    if sys.argv[1:] == ["--version"]:
        print(f"graphcheck {__version__}")
        return
    from graphcheck.telemetry.consent import resolve_consent

    consent = resolve_consent()
    from graphcheck.cli import cli as typer_cli

    typer_cli(consent=consent)
