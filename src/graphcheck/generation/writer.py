from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from graphcheck.contracts.check import load_suite
from graphcheck.errors import GraphCheckError
from graphcheck.generation.proposals import ValidatedCandidate, serialize_validated_suite


@dataclass(frozen=True)
class WrittenSuite:
    path: Path
    suite_id: str
    text: str


class GeneratedSuiteWriter:
    """Exclusively publish a validated suite without overwriting any existing file."""

    def __init__(
        self,
        checks_dir: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        link: Callable[[str | bytes | os.PathLike, str | bytes | os.PathLike], None] = os.link,
        fsync: Callable[[int], None] = os.fsync,
    ) -> None:
        self._checks_dir = checks_dir
        self._clock = clock or (lambda: datetime.now(UTC))
        self._link = link
        self._fsync = fsync

    def write(self, candidates: list[ValidatedCandidate]) -> WrittenSuite:
        if not candidates:
            raise GraphCheckError(
                "generate.no_valid_candidates",
                "No valid generated candidates were available to write.",
                "Review the logged reasons and retry with clearer domain docs or another model.",
            )
        try:
            self._checks_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise _write_failed() from exc

        timestamp = self._clock()
        timestamp = (
            timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)
        )
        while True:
            suite_id = f"generated-{timestamp:%Y%m%dT%H%M%S.%fZ}"
            destination = self._checks_dir / f"{suite_id}.yml"
            if destination.exists():
                timestamp += timedelta(microseconds=1)
                continue
            try:
                text = serialize_validated_suite(suite_id, candidates)
            except Exception as exc:
                raise GraphCheckError(
                    "generate.write_invalid",
                    "The assembled generated suite failed final validation.",
                    "Report a GraphCheck bug and retry after upgrading.",
                ) from exc

            temporary: Path | None = None
            published = False
            try:
                descriptor, temporary_name = tempfile.mkstemp(
                    dir=self._checks_dir,
                    prefix=f".{suite_id}.",
                    suffix=".tmp",
                )
                temporary = Path(temporary_name)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(text.encode("utf-8"))
                    stream.flush()
                    self._fsync(stream.fileno())
                try:
                    self._link(temporary, destination)
                except FileExistsError:
                    temporary.unlink(missing_ok=True)
                    timestamp += timedelta(microseconds=1)
                    continue
                published = True
                temporary.unlink()
                temporary = None
            except OSError as exc:
                if published:
                    destination.unlink(missing_ok=True)
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                raise _write_failed() from exc

            try:
                final_text = destination.read_text(encoding="utf-8")
                load_suite(final_text, source=str(destination))
            except Exception as exc:
                destination.unlink(missing_ok=True)
                raise GraphCheckError(
                    "generate.write_invalid",
                    "The published generated suite failed its loader assertion.",
                    "Report a GraphCheck bug and retry after upgrading.",
                ) from exc
            return WrittenSuite(path=destination, suite_id=suite_id, text=final_text)


def _write_failed() -> GraphCheckError:
    return GraphCheckError(
        "generate.write_failed",
        "The generated suite could not be written.",
        "Check the configured checks path and filesystem permissions, then retry.",
    )
