from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from graphcheck.contracts.results import Pattern, Severity
from graphcheck.contracts.scalars import JsonSchemaInteger
from graphcheck.packs import REGISTRY
from graphcheck.yaml_loader import DuplicateKeyError as DuplicateKeyError
from graphcheck.yaml_loader import load_yaml_mapping


class UnknownCheckError(ValueError):
    """A conformance check references a `check` name not in the pack registry."""


class SuiteValidationError(ValueError):
    """A suite file is syntactically valid YAML but invalid as a GraphCheck suite."""


def load_suite_yaml(text: str) -> dict:
    try:
        return load_yaml_mapping(text, description="a suite file")
    except DuplicateKeyError:
        raise
    except ValueError as exc:
        raise SuiteValidationError(str(exc)) from exc


def _parse_severity(value: object) -> object:
    if type(value) is str:
        return Severity(value)
    return value


type SuiteSeverity = Annotated[Severity | None, BeforeValidator(_parse_severity)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Defaults(_Strict):
    severity: SuiteSeverity = None
    tags: list[str] = []


class _Envelope(_Strict):
    id: str
    severity: SuiteSeverity = None
    tags: list[str] = []
    provenance: str | None = None
    generated: bool = False


class ConformanceCheck(_Envelope):
    # No populate_by_name: the external key is the frozen `with` alias only, never `with_`.
    check: str
    with_: dict = Field(alias="with")  # required — SPEC-02 freezes `check` + `with` for conformance


class RowBounds(_Strict):
    min: JsonSchemaInteger | None = None
    max: JsonSchemaInteger | None = None
    exactly: JsonSchemaInteger | None = None

    @model_validator(mode="after")
    def _bounds_are_meaningful_and_consistent(self) -> RowBounds:
        values = {"min": self.min, "max": self.max, "exactly": self.exactly}
        if all(value is None for value in values.values()):
            raise ValueError("rows must declare at least one of min, max, or exactly")
        for name, value in values.items():
            if value is not None and value < 0:
                raise ValueError(f"rows.{name} must be non-negative")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("rows.min must not exceed rows.max")
        if self.exactly is not None:
            if self.min is not None and self.exactly < self.min:
                raise ValueError("rows.exactly must not be less than rows.min")
            if self.max is not None and self.exactly > self.max:
                raise ValueError("rows.exactly must not exceed rows.max")
        return self

    def permits(self, count: int) -> bool:
        if self.exactly is not None and count != self.exactly:
            return False
        if self.min is not None and count < self.min:
            return False
        return self.max is None or count <= self.max

    def permits_nonempty(self) -> bool:
        if self.exactly is not None:
            return self.exactly > 0
        return self.max is None or self.max > 0


class Expect(_Strict):
    rows: RowBounds | None = None
    columns: list[str] | None = None
    unique: bool | None = None
    contains: list | None = None
    equals: list | None = None
    empty: bool | None = None

    @model_validator(mode="after")
    def _assertions_are_meaningful_and_consistent(self) -> Expect:
        assertions = (
            self.rows,
            self.columns,
            self.unique,
            self.contains,
            self.equals,
            self.empty,
        )
        if all(assertion is None for assertion in assertions):
            raise ValueError("expect must declare at least one assertion")
        if self.contains == []:
            raise ValueError("expect.contains must not be empty")

        if self.rows is not None:
            if self.empty is True and not self.rows.permits(0):
                raise ValueError("expect.empty=true conflicts with expect.rows")
            if self.empty is False and not self.rows.permits_nonempty():
                raise ValueError("expect.empty=false conflicts with expect.rows")
            if self.equals is not None and not self.rows.permits(len(self.equals)):
                raise ValueError("expect.equals length conflicts with expect.rows")
            if self.contains and not self.rows.permits_nonempty():
                raise ValueError("expect.contains conflicts with expect.rows")

        if self.empty is True:
            if self.contains:
                raise ValueError("expect.empty=true conflicts with expect.contains")
            if self.equals is not None and self.equals:
                raise ValueError("expect.empty=true conflicts with expect.equals")
        elif self.empty is False and self.equals == []:
            raise ValueError("expect.empty=false conflicts with empty expect.equals")

        if (
            self.contains is not None
            and self.equals is not None
            and any(value not in self.equals for value in self.contains)
        ):
            raise ValueError("expect.contains must be a subset of expect.equals")
        return self


class CompetencyCheck(_Envelope):
    question: str
    query: str
    params: dict = {}
    expect: Expect

    @model_validator(mode="after")
    def _content_is_valid(self) -> CompetencyCheck:
        if not self.question.strip():
            raise ValueError("competency question must not be blank")
        if not self.query.strip():
            raise ValueError("competency query must not be blank")
        if any(not isinstance(key, str) for key in self.params):
            raise ValueError("competency params keys must be strings")
        return self


class DriftCheck(_Envelope):
    metric: str
    target: dict
    baseline: str = "latest"
    tolerance: dict

    @model_validator(mode="after")
    def _content_is_valid(self) -> DriftCheck:
        if not self.metric.strip():
            raise ValueError("drift metric must not be blank")
        if not self.baseline.strip():
            raise ValueError("drift baseline must not be blank")
        if not self.tolerance:
            raise ValueError("drift tolerance must not be empty")
        if any(not isinstance(key, str) for key in self.target):
            raise ValueError("drift target keys must be strings")
        if any(not isinstance(key, str) for key in self.tolerance):
            raise ValueError("drift tolerance keys must be strings")
        return self


class LoadedCheck(_Strict):
    id: str
    pattern: Pattern
    severity: Severity  # resolved (check -> defaults -> error)
    tags: list[str]  # resolved (defaults + check)
    provenance: str | None = None
    generated: bool  # effective (file OR check)
    spec: ConformanceCheck | CompetencyCheck | DriftCheck  # validated payload the engine executes


class Suite(_Strict):
    suite: str
    checks: list[LoadedCheck]


class _SuiteFile(_Strict):
    suite: str | None = None  # optional in the file; falls back to the source filename stem
    generated: bool = False
    defaults: Defaults = Defaults()
    conformance: list[ConformanceCheck] = []
    competency: list[CompetencyCheck] = []
    drift: list[DriftCheck] = []


def _competency_pattern(expect: Expect) -> Pattern:
    if expect.contains is not None or expect.equals is not None:
        return Pattern.COMPETENCY_REGRESSION
    return Pattern.COMPETENCY_SHAPE


def load_suite(text: str, *, source: str | None = None) -> Suite:
    raw = load_suite_yaml(text)
    parsed = _SuiteFile.model_validate(raw)
    suite_id = parsed.suite or (Path(source).stem if source else None)
    if not suite_id:
        raise SuiteValidationError("suite name required: no `suite:` key and no source filename")
    defaults = parsed.defaults
    checks: list[LoadedCheck] = []

    def resolve(env: _Envelope, pattern: Pattern) -> LoadedCheck:
        severity = env.severity or defaults.severity or Severity.ERROR
        tags = list(dict.fromkeys([*defaults.tags, *env.tags]))
        generated = parsed.generated or env.generated
        return LoadedCheck(
            id=env.id,
            pattern=pattern,
            severity=severity,
            tags=tags,
            provenance=env.provenance,
            generated=generated,
            spec=env,
        )

    for c in parsed.conformance:
        if c.check not in REGISTRY:
            raise UnknownCheckError(f"unknown check type: {c.check!r}")
        # Validate `with` against the pack model AND keep the normalized result, so pack
        # defaults (e.g. completeness threshold=1.0) survive onto spec.with_ for the engine.
        c.with_ = REGISTRY[c.check].model_validate(c.with_).model_dump()
        checks.append(resolve(c, Pattern.CONFORMANCE))
    for c in parsed.competency:
        checks.append(resolve(c, _competency_pattern(c.expect)))
    for c in parsed.drift:
        checks.append(resolve(c, Pattern.DRIFT))

    seen: set[str] = set()
    for lc in checks:
        if lc.id in seen:
            raise SuiteValidationError(f"duplicate check id {lc.id!r} in suite {suite_id!r}")
        seen.add(lc.id)

    return Suite(suite=suite_id, checks=checks)
