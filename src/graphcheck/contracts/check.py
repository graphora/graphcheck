from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from graphcheck.contracts.results import Pattern, Severity
from graphcheck.packs import REGISTRY


class DuplicateKeyError(ValueError):
    """A mapping key appeared more than once in a suite YAML file."""


class UnknownCheckError(ValueError):
    """A conformance check references a `check` name not in the pack registry."""


class _NoDuplicatesLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateKeyError(f"duplicate key {key!r} at {key_node.start_mark}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDuplicatesLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def load_suite_yaml(text: str) -> dict:
    # SAFETY: _NoDuplicatesLoader subclasses SafeLoader, so this is as safe as
    # yaml.safe_load — it never constructs arbitrary Python (no !!python/object).
    # safe_load can't be used directly because it silently keeps the last of
    # duplicate keys; the only reason for the subclass is the duplicate-key check.
    data = yaml.load(text, Loader=_NoDuplicatesLoader)  # noqa: S506 (SafeLoader subclass)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("a suite file must be a mapping at the top level")
    return data


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Defaults(_Strict):
    severity: Severity | None = None
    tags: list[str] = []


class _Envelope(_Strict):
    id: str
    severity: Severity | None = None
    tags: list[str] = []
    provenance: str | None = None
    generated: bool = False


class ConformanceCheck(_Envelope):
    # No populate_by_name: the external key is the frozen `with` alias only, never `with_`.
    model_config = ConfigDict(extra="forbid")
    check: str
    with_: dict = Field(alias="with")  # required — SPEC-02 freezes `check` + `with` for conformance


class RowBounds(_Strict):
    min: int | None = None
    max: int | None = None
    exactly: int | None = None

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
        raise ValueError("suite name required: no `suite:` key and no source filename")
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
            raise ValueError(f"duplicate check id {lc.id!r} in suite {suite_id!r}")
        seen.add(lc.id)

    return Suite(suite=suite_id, checks=checks)
