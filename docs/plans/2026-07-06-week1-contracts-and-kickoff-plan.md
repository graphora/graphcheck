# GraphCheck Week 1 — Contracts, Scaffold & Kickoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the GraphCheck repository (scaffold + governance) and ship the two frozen v0 contracts — `results.json` (SPEC-01) and the check YAML (SPEC-02) — with strict, tested Pydantic models, then hand the rest of the team their Week-1 work.

**Architecture:** A `src`-layout Python package (`graphcheck`) whose `contracts/` subpackage holds Pydantic v2 models that are the single source of truth for both contracts; JSON Schemas are *generated* from those models. A `packs/` subpackage exposes a `REGISTRY` that validates conformance `with` payloads. The CLI is a minimal Typer entry point only (the full surface is C6, Week 3). Governance (branch protection, CODEOWNERS, CI) is applied to the repo before any contract code merges via PR.

**Tech Stack:** Python 3.12+, hatchling build backend, uv (dev), Typer (CLI), Pydantic v2, PyYAML, ruff, pytest, pytest-cov, Hypothesis, pre-commit. Repo: `graphora/graphcheck` (private, GitHub Team).

## Global Constraints

Copied verbatim from `docs/design/2026-07-06-week1-contracts-and-kickoff.md`. Every task's requirements implicitly include this section.

- **No AI attribution anywhere** — no `Co-Authored-By`, no "Generated with…", nothing AI-attributed in commits, PRs, issues, docs, comments, or CHANGELOG.
- **Python 3.12+**; compatible through 3.13. `requires-python = ">=3.12"`.
- **Strict validation** — every Pydantic model uses `model_config = ConfigDict(extra="forbid")`. Unknown keys error loudly.
- **Contracts are the source of truth in Pydantic**; JSON Schema is generated and is **structural only** — derived invariants live in `model_validator`s, never in the schema.
- `results.json` `schema_version` is `"1.0"`, versioned independently of `graphcheck_version`.
- **Score weights** hard-coded: `error=3`, `warn=1`.
- **Exit codes are locked** (0/1/2/3) and derived by the precedence table (SPEC-01 rule 1); never change in v0.
- **Anti-slop:** no comments restating code; no abstractions without ≥3 callers; no "just in case" params; no swallowed exceptions; no `print` (use the logger); no un-issued TODOs.
- **Commits:** Conventional-commits style, imperative, no attribution footer. Branch off `development`; PR into `development`.
- **Default branch is `development`**; `main` holds release tags.

**Editing convention:** when a step introduces a new `import`, add it to the target file's existing **top-of-file import block** (kept sorted — ruff's `I` rules enforce this). Never place `import`/`from` statements mid-file; ruff fails on `E402`/`I001`. Where a step shows new imports, they belong in the module's import block, not literally where the code is appended.

---

## File Structure

```
graphcheck/
├── LICENSE                              Apache 2.0
├── README.md                            skeleton
├── CHANGELOG.md                         Keep a Changelog
├── CONTRIBUTING.md                      branch flow, DoD, decision rights, anti-slop, no-attribution
├── pyproject.toml                       PEP 621 · hatchling · deps · ruff + pytest config
├── uv.lock                              committed
├── .python-version                      3.12
├── .gitignore                           extend
├── .pre-commit-config.yaml              ruff
├── .github/
│   ├── workflows/ci.yml                 jobs: lint, test (3.12–3.13)
│   ├── CODEOWNERS
│   ├── pull_request_template.md         DoD checklist
│   └── ISSUE_TEMPLATE/deliverable.md
├── src/graphcheck/
│   ├── __init__.py                      __version__, PACK/ SCHEMA versions re-export
│   ├── cli.py                           minimal Typer: --version / --help
│   ├── contracts/
│   │   ├── __init__.py
│   │   ├── results.py                   SPEC-01 models + validators + derivations
│   │   ├── check.py                     SPEC-02 envelope models + suite loader
│   │   └── schemas.py                   JSON Schema generation entrypoints
│   └── packs/
│       └── __init__.py                  REGISTRY, PACK_VERSION, built-in `with` models
├── tests/contracts/
│   ├── test_results.py
│   ├── test_check_validation.py
│   └── fixtures/
│       ├── results.complete.json
│       ├── results.partial.json
│       ├── results.generated-only.json
│       ├── results.failed.json
│       ├── suite.valid.yml
│       └── suite.invalid-*.yml
├── docs/specs/
│   ├── SPEC-01-results-json.md
│   ├── SPEC-02-check-yaml.md
│   ├── results.schema.json              generated
│   ├── check.envelope.schema.json       frozen envelope
│   └── check.schema.json                generated combined
└── docs/week-1-kickoff.md
```

---

## Phase 1 — Repository scaffold & governance

### Task 1: Package scaffold, packaging, and minimal CLI

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore` (extend), `src/graphcheck/__init__.py`, `src/graphcheck/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `graphcheck.__version__` (str, `"0.1.0"`); `graphcheck.cli.app` (a `typer.Typer`); console entry point `graphcheck`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "graphcheck"
version = "0.1.0"
description = "Semantic observability for property graphs — pytest for knowledge graphs."
requires-python = ">=3.12"
license = "Apache-2.0"
readme = "README.md"
dependencies = [
    "typer>=0.12",
    "pydantic>=2.6",
    "pyyaml>=6.0",
]

[project.scripts]
graphcheck = "graphcheck.cli:app"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-cov>=5",
    "hypothesis>=6",
    "jsonschema>=4",
    "ruff>=0.6",
    "pre-commit>=3.7",
]

[tool.hatch.build.targets.wheel]
packages = ["src/graphcheck"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.pytest.ini_options]
testpaths = ["tests"]
# Coverage is enforced in CI (.github/workflows/ci.yml), NOT here — so the focused
# `pytest -k ...` red/green steps in this plan don't trip the package-wide 80% gate.
```

- [ ] **Step 2: Create `.python-version`**

```
3.12
```

- [ ] **Step 3: Extend `.gitignore`** (append to the existing file)

```
.venv/
dist/
*.egg-info/
.graphcheck/
profiles.yml
.coverage
.pytest_cache/
```

- [ ] **Step 4: Write the failing CLI test** — `tests/test_cli.py`

```python
from typer.testing import CliRunner

from graphcheck import __version__
from graphcheck.cli import app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_no_hidden_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
```

- [ ] **Step 5: Run it to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'graphcheck'`

- [ ] **Step 6: Create `src/graphcheck/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 7: Create `src/graphcheck/cli.py`** (minimal — the full surface is C6, Week 3)

```python
import typer

from graphcheck import __version__

app = typer.Typer(
    name="graphcheck",
    help="Semantic observability for property graphs.",
    add_completion=False,
    no_args_is_help=True,
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"graphcheck {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """GraphCheck — the command surface lands in Week 3 (C6)."""
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv sync && uv run pytest tests/test_cli.py -v`
Expected: PASS (2 passed)

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .python-version .gitignore uv.lock src/graphcheck tests/test_cli.py
git commit -m "feat: scaffold graphcheck package with minimal CLI"
```

---

### Task 2: CI workflow with stable job names

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: required status-check **job** names `lint` and `test (3.12)`…`test (3.13)` (referenced by the ruleset in Task 4).

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [development, main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --group dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .

  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - name: Install uv and select the matrix Python
        uses: astral-sh/setup-uv@v5
        with:
          # Sets UV_PYTHON, which overrides the committed .python-version so uv
          # runs on the matrix interpreter (not the pinned 3.12).
          python-version: ${{ matrix.python-version }}
      - run: uv sync --group dev
      - name: Assert the interpreter matches the matrix (guards against a single pinned run)
        run: uv run python -c "import sys; want=tuple(int(p) for p in '${{ matrix.python-version }}'.split('.')); assert sys.version_info[:2]==want, sys.version"
      - run: uv run pytest --cov=graphcheck --cov-report=term-missing --cov-fail-under=80
```

- [ ] **Step 2: Verify the workflow parses**

Run: `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run ruff lint + pytest matrix on every PR"
```

---

### Task 3: Governance and project files

**Files:**
- Create: `LICENSE`, `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `.pre-commit-config.yaml`, `.github/CODEOWNERS`, `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/deliverable.md`

- [ ] **Step 1: Create `LICENSE`** — the full Apache License 2.0 text.

Run: `curl -fsSL https://www.apache.org/licenses/LICENSE-2.0.txt -o LICENSE`
Then edit the copyright line at the end to `Copyright 2026 Graphora`.

- [ ] **Step 2: Create `.github/CODEOWNERS`** (last-match-wins; owners do not accumulate)

```
*                             @ezhilvendhan
/docs/specs/                  @ghilda-graphora @kev-graphora
/src/graphcheck/contracts/    @ghilda-graphora @kev-graphora
/tests/contracts/             @ghilda-graphora @kev-graphora
/tests/fixtures/              @jayachandra-bit @jananik-graphora
```

- [ ] **Step 3: Create `.github/pull_request_template.md`** (the §14.1 definition of done)

```markdown
## What this changes


## Definition of done
- [ ] Reviewed by the named CODEOWNER
- [ ] Unit tests written and passing locally + CI
- [ ] Integration tests pass against the fixture graph (where applicable)
- [ ] Coverage ≥ 80% on owned code
- [ ] No `print` / `console.log`; structured logger only
- [ ] No swallowed exceptions (`except: pass`)
- [ ] No un-issued TODOs
- [ ] Design note in `docs/components/<name>.md` (where applicable)
- [ ] CHANGELOG.md entry
- [ ] No new third-party dependency without team review
- [ ] No agent meta-commentary or AI attribution anywhere
```

- [ ] **Step 4: Create `.github/ISSUE_TEMPLATE/deliverable.md`**

```markdown
---
name: Week deliverable
about: A scoped deliverable with acceptance criteria
labels: []
---

**Owner:**
**Milestone:**

## Acceptance criteria


## Decision rights
Owner decides libraries within the component; the named reviewer approves. Cross-contract changes escalate to Ezhil (§13).
```

- [ ] **Step 5: Create `CONTRIBUTING.md`**

```markdown
# Contributing to GraphCheck

## Branches
`development` is the default/integration branch — open PRs against it. `main` holds release tags (v0.1.0). Never push directly to either.

## Definition of done
See the PR template. Every box is required before merge.

## Decision rights (§13)
Reversible in < half a day → decide yourself. Reversible in > two days, or any cross-contract change (results.json, check YAML, exit codes, CLI surface) → escalate to Ezhil.

## Anti-slop
No abstractions without three callers. No "just in case" params. No comments restating code. No swallowed exceptions. No un-issued TODOs.

## Attribution
No AI attribution anywhere — commits, PRs, issues, docs, comments. Write in a plain human voice.
```

- [ ] **Step 6: Create `CHANGELOG.md`**

```markdown
# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- Repository scaffold: packaging, minimal CLI, CI, governance.
```

- [ ] **Step 7: Create `README.md`** (skeleton; the 10-minute quickstart is Week 4)

```markdown
# GraphCheck

Semantic observability for property graphs — like pytest for your knowledge graph. Point it at Neo4j, declare what should be true, and get a scored, evidence-linked report.

> v0 is under active development. A 10-minute quickstart lands with v0.1.0.

## Status
Week 1: contracts (`results.json`, check YAML) and repository scaffold. See `docs/design/` and `docs/specs/`.

## License
Apache-2.0.
```

- [ ] **Step 8: Create `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

- [ ] **Step 9: Verify ruff + pytest are clean**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest`
Expected: ruff passes; tests pass.

- [ ] **Step 10: Commit**

```bash
git add LICENSE README.md CHANGELOG.md CONTRIBUTING.md .pre-commit-config.yaml .github/CODEOWNERS .github/pull_request_template.md .github/ISSUE_TEMPLATE
git commit -m "chore: add license, governance, and contributor docs"
```

---

### Task 4: Bootstrap the repo — `development` default + ruleset

This task runs git/gh commands, not code. It establishes the branch model and turns on enforcement. It is the one direct push; everything after uses PRs.

**Files:** none (repo settings + the design/plan docs get committed here).

- [ ] **Step 1: Commit the design and plan docs onto the bootstrap**

```bash
git add docs/design docs/plans
git commit -m "docs: add Week 1 contracts design and implementation plan"
```

- [ ] **Step 2: Create and publish `development`, set it as default**

```bash
git branch development
git push -u origin main development
gh repo edit graphora/graphcheck --default-branch development
```

- [ ] **Step 3: Enable auto-delete of merged branches**

```bash
gh repo edit graphora/graphcheck --delete-branch-on-merge
```

- [ ] **Step 4: Create the ruleset on `development` and `main`**

Write `/tmp/ruleset.json`, then POST it. Repeat with `"~DEFAULT_BRANCH"` replaced by `refs/heads/main` for the second branch (or include both refs in `include`).

```bash
cat > /tmp/ruleset.json <<'JSON'
{
  "name": "protect-development",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH", "refs/heads/main"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "required_linear_history" },
    { "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "require_code_owner_review": true,
        "dismiss_stale_reviews_on_push": true,
        "required_review_thread_resolution": true
      }
    },
    { "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "lint" },
          { "context": "test (3.12)" },
          { "context": "test (3.13)" }
        ]
      }
    }
  ]
}
JSON
gh api --method POST /repos/graphora/graphcheck/rulesets --input /tmp/ruleset.json
```

- [ ] **Step 5: Verify the ruleset is active**

Run: `gh api /repos/graphora/graphcheck/rulesets --jq '.[].name'`
Expected: `protect-development` (HTTP 200, not 403).

- [ ] **Step 6: Confirm the default branch**

Run: `gh api /repos/graphora/graphcheck --jq '.default_branch'`
Expected: `development`

---

## Phase 2 — The frozen contracts (via PRs into `development`)

> Every task in this phase is developed on a feature branch and merged by PR. Start each with `git switch development && git pull && git switch -c <branch>`.

### Task 5: SPEC-01 — enums and leaf models

**Files:**
- Create: `src/graphcheck/contracts/__init__.py`, `src/graphcheck/contracts/results.py`
- Test: `tests/contracts/test_results.py`

**Interfaces:**
- Produces: enums `Verdict`, `Severity`, `Pattern`, `SkipReason`, `RunStatus`, `RedactionPolicy`; constant `WEIGHTS: dict[Severity, int]`; models `Evidence`, `EvidenceElement`, `Estimate`, `CheckError`; base `_Strict` (`extra="forbid"`). All consumed by Tasks 6–8.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from pydantic import ValidationError

from graphcheck.contracts.results import (
    CheckError,
    Estimate,
    Evidence,
    EvidenceElement,
    Severity,
    Verdict,
    WEIGHTS,
)


def test_weights_are_severity_keyed():
    assert WEIGHTS[Severity.ERROR] == 3
    assert WEIGHTS[Severity.WARN] == 1


def test_verdict_values():
    assert {v.value for v in Verdict} == {"pass", "fail", "warn", "errored", "skipped"}


def test_evidence_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        Evidence(
            message="x", elements=[], truncated=False, cap=50, total_count=0, bogus=1
        )


def test_check_error_shape():
    err = CheckError(code="c", message="m", fix="f")
    assert err.fix == "f"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/contracts/test_results.py -v`
Expected: FAIL — `ModuleNotFoundError: graphcheck.contracts.results`

- [ ] **Step 3: Create `src/graphcheck/contracts/__init__.py`** (empty file)

```python
```

- [ ] **Step 4: Create `src/graphcheck/contracts/results.py`** (enums + leaves)

```python
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = "1.0"


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    ERRORED = "errored"
    SKIPPED = "skipped"


class Severity(str, Enum):
    ERROR = "error"
    WARN = "warn"


class Pattern(str, Enum):
    CONFORMANCE = "conformance"
    DRIFT = "drift"
    COMPETENCY_SHAPE = "competency-shape"
    COMPETENCY_REGRESSION = "competency-regression"


class SkipReason(str, Enum):
    GENERATED = "generated"
    UNSUPPORTED = "unsupported"
    NOT_RUN = "not_run"


class RunStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class RedactionPolicy(str, Enum):
    NONE = "none"
    MASK = "mask"
    HASH = "hash"


WEIGHTS: dict[Severity, int] = {Severity.ERROR: 3, Severity.WARN: 1}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceElement(_Strict):
    kind: Literal["node", "rel"]
    id: str
    labels: list[str] | None = None
    type: str | None = None


class Evidence(_Strict):
    message: str
    elements: list[EvidenceElement]
    truncated: bool
    cap: int
    total_count: int


class Estimate(_Strict):
    sample_size: int
    population: int
    confidence: float
    ci: tuple[float, float] | None = None


class CheckError(_Strict):
    code: str
    message: str
    fix: str
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/contracts/test_results.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add src/graphcheck/contracts tests/contracts/test_results.py
git commit -m "feat(contracts): add results.json enums and leaf models"
```

---

### Task 6: SPEC-01 — `CheckResult` and the field-presence validator

**Files:**
- Modify: `src/graphcheck/contracts/results.py`
- Test: `tests/contracts/test_results.py`

**Interfaces:**
- Consumes: enums + leaves from Task 5.
- Produces: `CheckResult` model with property `executed: bool` (True iff `verdict != SKIPPED`). Consumed by Tasks 7–8.

- [ ] **Step 1: Write the failing tests**

```python
from graphcheck.contracts.results import CheckResult, Pattern, SkipReason


def _base(**over):
    """A valid record for the given verdict; override any field to make it invalid."""
    verdict = over.get("verdict", Verdict.PASS)
    data = dict(
        id="c1", suite_id="s", pattern=Pattern.CONFORMANCE, name="n",
        severity=Severity.ERROR, verdict=verdict, expected={},
    )
    if verdict is not Verdict.SKIPPED:
        data.update(started_at="t", duration_ms=5)
    if verdict in (Verdict.PASS, Verdict.FAIL, Verdict.WARN):
        data.update(compiled_query="RETURN 1", params={}, measured={})
    if verdict in (Verdict.FAIL, Verdict.WARN):
        data["evidence"] = Evidence(message="m", elements=[], truncated=False, cap=50, total_count=0)
    if verdict is Verdict.ERRORED:
        data["error"] = CheckError(code="c", message="m", fix="f")
    if verdict is Verdict.SKIPPED:
        data["skip_reason"] = SkipReason.GENERATED
    data.update(over)
    return data


def test_valid_record_for_each_verdict():
    for v in Verdict:
        CheckResult(**_base(verdict=v))  # must not raise


def test_fail_requires_evidence():
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.FAIL, evidence=None))


def test_pass_forbids_evidence():
    ev = Evidence(message="m", elements=[], truncated=False, cap=50, total_count=0)
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.PASS, evidence=ev))


def test_pass_requires_execution_fields():
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.PASS, measured=None))


def test_errored_requires_error_forbids_measured_and_is_executed():
    assert CheckResult(**_base(verdict=Verdict.ERRORED)).executed is True
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.ERRORED, error=None))
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.ERRORED, measured={"rows": 1}))


def test_attempted_check_requires_timing():
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.ERRORED, started_at=None))


def test_skipped_requires_skip_reason_and_null_execution_fields():
    assert CheckResult(**_base(verdict=Verdict.SKIPPED)).executed is False
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.SKIPPED, skip_reason=None))
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.SKIPPED, duration_ms=5))


def test_errored_and_skipped_forbid_estimate_object():
    est = Estimate(sample_size=10, population=100, confidence=0.95)
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.ERRORED, estimate=est))
    with pytest.raises(ValidationError):
        CheckResult(**_base(verdict=Verdict.SKIPPED, estimate=est))
```

Add the imports `CheckResult, Pattern, SkipReason` to the existing import block.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/contracts/test_results.py -k CheckResult -v`
Expected: FAIL — `ImportError: cannot import name 'CheckResult'`

- [ ] **Step 3: Append `CheckResult` to `results.py`**

First update the top-of-file import to `from pydantic import BaseModel, ConfigDict, model_validator`. Then append this class below the leaf models:

```python
class CheckResult(_Strict):
    id: str
    suite_id: str
    pattern: Pattern
    name: str
    provenance: str | None = None
    severity: Severity
    verdict: Verdict
    skip_reason: SkipReason | None = None
    started_at: str | None = None
    duration_ms: int | None = None
    compiled_query: str | None = None
    params: dict[str, object] | None = None
    measured: dict[str, object] | None = None
    expected: dict[str, object]
    estimate: Estimate | Literal[False] = False
    evidence: Evidence | None = None
    error: CheckError | None = None

    @property
    def executed(self) -> bool:
        return self.verdict is not Verdict.SKIPPED

    @model_validator(mode="after")
    def _field_presence(self) -> CheckResult:
        v = self.verdict
        run_outcomes = (Verdict.PASS, Verdict.FAIL, Verdict.WARN)

        # Only an attempted, measured check can be sampled; errored/skipped are never estimates.
        if v not in run_outcomes and self.estimate is not False:
            raise ValueError(f"{v.value} check {self.id!r} must have estimate=false")

        if v in (Verdict.FAIL, Verdict.WARN):
            if self.evidence is None:
                raise ValueError(f"{v.value} check {self.id!r} must carry evidence")
        elif self.evidence is not None:
            raise ValueError(f"{v.value} check {self.id!r} must not carry evidence")

        if v is Verdict.ERRORED:
            if self.error is None:
                raise ValueError(f"errored check {self.id!r} must carry error")
        elif self.error is not None:
            raise ValueError(f"non-errored check {self.id!r} must not carry error")

        if v is Verdict.SKIPPED:
            if self.skip_reason is None:
                raise ValueError(f"skipped check {self.id!r} must carry skip_reason")
            for field in ("started_at", "duration_ms", "compiled_query", "params", "measured"):
                if getattr(self, field) is not None:
                    raise ValueError(f"skipped check {self.id!r} must have null {field}")
            return self  # nothing executed; the checks below are for attempted checks

        if self.skip_reason is not None:
            raise ValueError(f"non-skipped check {self.id!r} must not carry skip_reason")

        # Every attempted check (pass/fail/warn/errored) has timing.
        if self.started_at is None or self.duration_ms is None:
            raise ValueError(f"attempted check {self.id!r} must carry started_at and duration_ms")

        if v in run_outcomes:
            for field in ("compiled_query", "params", "measured"):
                if getattr(self, field) is None:
                    raise ValueError(f"{v.value} check {self.id!r} must carry {field}")
        elif v is Verdict.ERRORED and self.measured is not None:
            raise ValueError(f"errored check {self.id!r} must not carry measured (it did not measure)")
        return self
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/contracts/test_results.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/graphcheck/contracts/results.py tests/contracts/test_results.py
git commit -m "feat(contracts): add CheckResult with field-presence invariants"
```

---

### Task 7: SPEC-01 — derivation helpers (score, totals, exit code)

**Files:**
- Modify: `src/graphcheck/contracts/results.py`
- Test: `tests/contracts/test_results.py`

**Interfaces:**
- Consumes: `CheckResult`, `Verdict`, `Severity`, `WEIGHTS`, `RunStatus`.
- Produces: `score_value(checks) -> int | None`; `totals(checks) -> dict`; `exit_code(status, checks) -> int`. Consumed by Task 8's validator.

- [ ] **Step 1: Write the failing tests**

```python
from graphcheck.contracts.results import RunStatus, exit_code, score_value, totals


def _chk(verdict, severity=Severity.ERROR, **over):
    from graphcheck.contracts.results import CheckResult, Pattern
    data = dict(id="x", suite_id="s", pattern=Pattern.CONFORMANCE, name="n",
                severity=severity, verdict=verdict, expected={})
    if verdict is not Verdict.SKIPPED:
        data.update(started_at="t", duration_ms=5)
    if verdict in (Verdict.PASS, Verdict.FAIL, Verdict.WARN):
        data.update(compiled_query="RETURN 1", params={}, measured={})
    if verdict in (Verdict.FAIL, Verdict.WARN):
        data["evidence"] = Evidence(message="m", elements=[], truncated=False, cap=50, total_count=0)
    if verdict is Verdict.ERRORED:
        data["error"] = CheckError(code="c", message="m", fix="f")
    if verdict is Verdict.SKIPPED:
        data["skip_reason"] = SkipReason.GENERATED
    data.update(over)
    return CheckResult(**data)


def test_score_matches_design_example():
    checks = [_chk(Verdict.FAIL), _chk(Verdict.PASS), _chk(Verdict.WARN, Severity.WARN)]
    assert score_value(checks) == 43  # 100 * 3 / (3+3+1)


def test_score_null_on_empty_denominator():
    assert score_value([_chk(Verdict.SKIPPED)]) is None
    assert score_value([]) is None


def test_totals_tally():
    checks = [_chk(Verdict.PASS), _chk(Verdict.FAIL), _chk(Verdict.SKIPPED)]
    assert totals(checks) == {"checks": 3, "pass": 1, "fail": 1, "warn": 0, "errored": 0, "skipped": 1}


def test_exit_code_precedence():
    assert exit_code(RunStatus.FAILED, []) == 3
    assert exit_code(RunStatus.COMPLETE, [_chk(Verdict.FAIL)]) == 1
    assert exit_code(RunStatus.PARTIAL, [_chk(Verdict.FAIL)]) == 1  # fail dominates partial
    assert exit_code(RunStatus.PARTIAL, [_chk(Verdict.PASS)]) == 2  # clean partial
    assert exit_code(RunStatus.COMPLETE, [_chk(Verdict.ERRORED, Severity.ERROR)]) == 1  # error-errored
    assert exit_code(RunStatus.COMPLETE, [_chk(Verdict.ERRORED, Severity.WARN)]) == 2   # warn-errored
    assert exit_code(RunStatus.COMPLETE, [_chk(Verdict.SKIPPED)]) == 2  # nothing evaluated
    assert exit_code(RunStatus.COMPLETE, []) == 2  # empty selection
    assert exit_code(RunStatus.COMPLETE, [_chk(Verdict.WARN, Severity.WARN)]) == 2
    assert exit_code(RunStatus.COMPLETE, [_chk(Verdict.PASS)]) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/contracts/test_results.py -k "score or totals or exit_code" -v`
Expected: FAIL — cannot import `score_value`

- [ ] **Step 3: Append the helpers to `results.py`**

First add `from collections import Counter` to the top-of-file import block. Then append:

```python
def score_value(checks: list[CheckResult]) -> int | None:
    denominator = sum(WEIGHTS[c.severity] for c in checks if c.executed)
    if denominator == 0:
        return None
    numerator = sum(WEIGHTS[c.severity] for c in checks if c.verdict is Verdict.PASS)
    return round(100 * numerator / denominator)


def totals(checks: list[CheckResult]) -> dict[str, int]:
    counts = Counter(c.verdict for c in checks)
    return {
        "checks": len(checks),
        "pass": counts[Verdict.PASS],
        "fail": counts[Verdict.FAIL],
        "warn": counts[Verdict.WARN],
        "errored": counts[Verdict.ERRORED],
        "skipped": counts[Verdict.SKIPPED],
    }


def exit_code(status: RunStatus, checks: list[CheckResult]) -> int:
    if status is RunStatus.FAILED:
        return 3
    hard = any(
        c.verdict is Verdict.FAIL
        or (c.verdict is Verdict.ERRORED and c.severity is Severity.ERROR)
        for c in checks
    )
    if hard:
        return 1
    nothing_evaluated = not any(c.executed for c in checks)
    soft = any(
        c.verdict is Verdict.WARN
        or (c.verdict is Verdict.ERRORED and c.severity is Severity.WARN)
        for c in checks
    )
    if status is RunStatus.PARTIAL or nothing_evaluated or soft:
        return 2
    return 0
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/contracts/test_results.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/graphcheck/contracts/results.py tests/contracts/test_results.py
git commit -m "feat(contracts): add score/totals/exit-code derivations"
```

---

### Task 8: SPEC-01 — `Run`, `Suite`, `Results` and the consistency validator

**Files:**
- Modify: `src/graphcheck/contracts/results.py`
- Test: `tests/contracts/test_results.py`

**Interfaces:**
- Consumes: everything from Tasks 5–7.
- Produces: `Results` (top-level model), `Run`, `Suite`, `Totals`, `Score`, `RunTarget`, `Capabilities`, `Selection`, `Redaction`. `Results` enforces: status shape, `partial_reason` iff, totals derivation, score (incl. per-suite null), exit-code precedence, coverage-status.

- [ ] **Step 1: Write the failing tests**

```python
def _run(**over):
    from graphcheck.contracts.results import RedactionPolicy
    data = dict(
        id="r", started_at="t", finished_at="t", graphcheck_version="0.1.0",
        pack_version="0.1.0", status=RunStatus.COMPLETE, exit_code=0,
        selection={"suites": [], "tags": [], "fail_fast": False},
        redaction={"policy": RedactionPolicy.NONE, "applied": False},
        target={"database": "neo4j", "server_version": "5", "edition": "community",
                "fingerprint": "sha256:x", "capabilities": {"apoc": True, "count_store": True}},
    )
    data.update(over)
    return data


def _results(checks, status=RunStatus.COMPLETE, **run_over):
    from graphcheck.contracts.results import Results, Score
    sc = score_value(checks)
    return Results(
        schema_version="1.0",
        run=_run(status=status, exit_code=exit_code(status, checks), **run_over),
        score=None if sc is None else Score(value=sc),
        totals=totals(checks),
        suites=[{"id": "s", "source_sha": "x", "score": sc, "totals": totals(checks)}],
        checks=checks,
    )


def test_consistent_results_validate():
    _results([_chk(Verdict.PASS)])


def test_wrong_totals_rejected():
    from graphcheck.contracts.results import Results, Score
    with pytest.raises(ValidationError):
        Results(schema_version="1.0", run=_run(exit_code=0), score=Score(value=100),
                totals={"checks": 9, "pass": 9, "fail": 0, "warn": 0, "errored": 0, "skipped": 0},
                suites=[], checks=[_chk(Verdict.PASS)])


def test_partial_reason_iff_partial():
    with pytest.raises(ValidationError):
        _results([_chk(Verdict.PASS)], partial_reason="stale")  # complete + reason
    with pytest.raises(ValidationError):
        _results([_chk(Verdict.SKIPPED, skip_reason=SkipReason.NOT_RUN)],
                 status=RunStatus.PARTIAL)  # partial needs partial_reason (None here)


def test_unsupported_skip_forces_partial():
    with pytest.raises(ValidationError):
        _results([_chk(Verdict.SKIPPED, skip_reason=SkipReason.UNSUPPORTED)])  # complete, should be partial


def test_generated_only_scores_null_and_exits_2():
    r = _results([_chk(Verdict.SKIPPED, skip_reason=SkipReason.GENERATED)])
    assert r.score is None
    assert r.run.exit_code == 2


def test_complete_requires_target():
    from graphcheck.contracts.results import Results, Score
    checks = [_chk(Verdict.PASS)]
    with pytest.raises(ValidationError):
        Results(schema_version="1.0", run=_run(target=None, exit_code=0),
                score=Score(value=100), totals=totals(checks),
                suites=[{"id": "s", "source_sha": "x", "score": 100, "totals": totals(checks)}],
                checks=checks)


def test_orphan_suite_id_rejected():
    with pytest.raises(ValidationError):
        _results([_chk(Verdict.PASS, suite_id="other")])  # suites[] only has "s"


def test_per_suite_totals_checked():
    from graphcheck.contracts.results import Results, Score
    checks = [_chk(Verdict.PASS)]
    with pytest.raises(ValidationError):
        Results(schema_version="1.0", run=_run(exit_code=0), score=Score(value=100),
                totals=totals(checks),
                suites=[{"id": "s", "source_sha": "x", "score": 100,
                         "totals": {"checks": 5, "pass": 5, "fail": 0, "warn": 0,
                                    "errored": 0, "skipped": 0}}],
                checks=checks)


def test_bogus_score_weights_rejected():
    from graphcheck.contracts.results import Score
    with pytest.raises(ValidationError):
        Score(value=50, weights={"error": 1, "warn": 1})


def test_duplicate_check_identity_rejected():
    with pytest.raises(ValidationError):
        _results([_chk(Verdict.PASS, id="dup"), _chk(Verdict.PASS, id="dup")])


def test_duplicate_suite_id_rejected():
    from graphcheck.contracts.results import Results, Score
    checks = [_chk(Verdict.PASS)]
    with pytest.raises(ValidationError):
        Results(schema_version="1.0", run=_run(exit_code=0), score=Score(value=100),
                totals=totals(checks),
                suites=[{"id": "s", "source_sha": "x", "score": 100, "totals": totals(checks)},
                        {"id": "s", "source_sha": "y", "score": 100, "totals": totals(checks)}],
                checks=checks)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/contracts/test_results.py -k "results or partial or unsupported or generated" -v`
Expected: FAIL — cannot import `Results`

- [ ] **Step 3: Append the top-level models to `results.py`**

First update the top-of-file import to `from pydantic import BaseModel, ConfigDict, Field, model_validator`. Then append:

```python
class Totals(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    checks: int
    passed: int = Field(alias="pass")
    fail: int
    warn: int
    errored: int
    skipped: int


class Score(_Strict):
    value: int
    method: Literal["weighted-by-severity"] = "weighted-by-severity"
    weights: dict[str, int] = Field(default_factory=lambda: {"error": 3, "warn": 1})

    @model_validator(mode="after")
    def _weights_locked(self) -> Score:
        if self.weights != {"error": 3, "warn": 1}:
            raise ValueError("score.weights are hard-coded in v0: {'error': 3, 'warn': 1}")
        return self


class Capabilities(_Strict):
    apoc: bool
    count_store: bool


class RunTarget(_Strict):
    database: str
    server_version: str
    edition: str
    fingerprint: str
    capabilities: Capabilities


class Selection(_Strict):
    suites: list[str]
    tags: list[str]
    fail_fast: bool


class Redaction(_Strict):
    policy: RedactionPolicy
    applied: bool


class Run(_Strict):
    id: str
    started_at: str
    finished_at: str
    graphcheck_version: str
    pack_version: str
    status: RunStatus
    partial_reason: str | None = None
    exit_code: int
    selection: Selection
    redaction: Redaction
    target: RunTarget | None = None
    error: CheckError | None = None


class Suite(_Strict):
    id: str
    source_sha: str
    score: int | None
    totals: Totals


class Results(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    run: Run
    score: Score | None
    totals: Totals
    suites: list[Suite]
    checks: list[CheckResult]

    @model_validator(mode="after")
    def _consistency(self) -> Results:
        status = self.run.status
        if status is RunStatus.FAILED:
            if self.run.error is None:
                raise ValueError("failed run must carry run.error")
            if self.checks or self.suites or self.score is not None:
                raise ValueError("failed run must have empty checks/suites and null score")
        elif self.run.error is not None:
            raise ValueError("non-failed run must not carry run.error")

        if status in (RunStatus.COMPLETE, RunStatus.PARTIAL) and self.run.target is None:
            raise ValueError("complete/partial run must carry run.target")

        if (self.run.partial_reason is not None) != (status is RunStatus.PARTIAL):
            raise ValueError("partial_reason must be non-null iff status is partial")

        expected_totals = totals(self.checks)
        if self.totals.model_dump(by_alias=True) != expected_totals:
            raise ValueError(f"totals must equal the tally of checks: {expected_totals}")

        expected_score = score_value(self.checks)
        if expected_score is None:
            if self.score is not None:
                raise ValueError("score must be null when no check executed")
        elif self.score is None or self.score.value != expected_score:
            raise ValueError(f"score.value must be {expected_score}")

        expected_exit = exit_code(status, self.checks)
        if self.run.exit_code != expected_exit:
            raise ValueError(f"exit_code must be {expected_exit}")

        has_gap = any(
            c.skip_reason in (SkipReason.UNSUPPORTED, SkipReason.NOT_RUN) for c in self.checks
        )
        if has_gap and status is not RunStatus.PARTIAL:
            raise ValueError("an unsupported/not_run skip requires run.status:partial")

        identities = [(c.suite_id, c.id) for c in self.checks]
        if len(identities) != len(set(identities)):
            raise ValueError("check identity (suite_id, id) must be unique across checks[]")
        if len({s.id for s in self.suites}) != len(self.suites):
            raise ValueError("suite ids must be unique in suites[]")

        by_suite: dict[str, list[CheckResult]] = {}
        for c in self.checks:
            by_suite.setdefault(c.suite_id, []).append(c)
        suite_ids = {s.id for s in self.suites}
        for suite_id in by_suite:
            if suite_id not in suite_ids:
                raise ValueError(f"check suite_id {suite_id!r} has no matching suites[] entry")
        for suite in self.suites:
            members = by_suite.get(suite.id, [])
            if suite.score != score_value(members):
                raise ValueError(f"suite {suite.id!r} score is inconsistent with its checks")
            if suite.totals.model_dump(by_alias=True) != totals(members):
                raise ValueError(f"suite {suite.id!r} totals are inconsistent with its checks")
        return self
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/contracts/test_results.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/graphcheck/contracts/results.py tests/contracts/test_results.py
git commit -m "feat(contracts): add Results model with consistency invariants"
```

- [ ] **Step 6: Open the PR**

```bash
git push -u origin <branch>
gh pr create --base development --title "SPEC-01: results.json Pydantic models + invariants" \
  --body "Implements the results.json contract per docs/design. Enums, CheckResult field-presence, derivations, and the Results consistency validator. Tests cover every invariant."
```

---

### Task 9: SPEC-01 — schema generation, fixtures, and round-trip tests

**Files:**
- Create: `src/graphcheck/contracts/schemas.py`, `docs/specs/results.schema.json`, `docs/specs/SPEC-01-results-json.md`, `tests/contracts/fixtures/results.{complete,partial,generated-only,failed}.json`
- Modify: `tests/contracts/test_results.py`

**Interfaces:**
- Consumes: `Results` from Task 8.
- Produces: `graphcheck.contracts.schemas.results_schema() -> dict`.

- [ ] **Step 1: Create the four fixtures** — comment-free JSON matching the illustrative examples in the design doc. `results.complete.json` is the 3-check example (score 43, exit 1); `results.failed.json` has `checks: []`, `score: null`, exit 3; `results.partial.json` has one `not_run` skip + `partial_reason`, exit 2; `results.generated-only.json` has one `generated` skip, `score: null`, exit 2. (Copy the design-doc JSON, strip `//` comments, replace `…`/`<ulid>` with real values, and make totals/score/exit self-consistent.)

- [ ] **Step 2: Write the failing round-trip + schema tests**

Add to the top import block of `test_results.py`: `import json`, `import jsonschema`, `from pathlib import Path`, `Results` (onto the existing `from graphcheck.contracts.results import ...` line), and `from graphcheck.contracts.schemas import SPECS_DIR, results_schema`. Then append:

```python
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ["complete", "partial", "generated-only", "failed"])
def test_fixture_validates_against_schema_and_round_trips(name):
    raw = json.loads((FIXTURES / f"results.{name}.json").read_text())
    jsonschema.validate(raw, results_schema())    # structural (JSON Schema)
    model = Results.model_validate(raw)            # + derived invariants (Pydantic)
    assert json.loads(model.model_dump_json(by_alias=True, exclude_none=False)) == raw


def test_committed_results_schema_is_current():
    committed = json.loads((SPECS_DIR / "results.schema.json").read_text())
    assert committed == results_schema()  # regenerate + recommit if this fails
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/contracts/test_results.py -k "fixture or schema" -v`
Expected: FAIL — cannot import `results_schema` (and fixtures may need value fixes)

- [ ] **Step 4: Create `src/graphcheck/contracts/schemas.py`**

```python
import json
from pathlib import Path

from graphcheck.contracts.results import Results

SPECS_DIR = Path(__file__).resolve().parents[3] / "docs" / "specs"


def results_schema() -> dict:
    return Results.model_json_schema(by_alias=True)


def write_results_schema() -> Path:
    path = SPECS_DIR / "results.schema.json"
    path.write_text(json.dumps(results_schema(), indent=2, sort_keys=True) + "\n")
    return path
```

- [ ] **Step 5: Generate the committed schema**

Run: `uv run python -c "from graphcheck.contracts.schemas import write_results_schema; print(write_results_schema())"`
Expected: writes `docs/specs/results.schema.json`

- [ ] **Step 6: Run tests; fix fixture values until green**

Run: `uv run pytest tests/contracts/test_results.py -v`
Expected: PASS — if a fixture fails validation, the error names the exact invariant to fix.

- [ ] **Step 7: Write `docs/specs/SPEC-01-results-json.md`** — the normative spec. Lift the `results.json` section of `docs/design/2026-07-06-week1-contracts-and-kickoff.md` verbatim (shape, the shape-by-status rules, the semantic rules, the field-presence table, the exit-code precedence table), and add a one-line pointer to the fixtures as the machine-valid artifacts.

- [ ] **Step 8: Commit and update the PR**

```bash
git add src/graphcheck/contracts/schemas.py docs/specs/results.schema.json docs/specs/SPEC-01-results-json.md tests/contracts/fixtures tests/contracts/test_results.py
git commit -m "feat(contracts): generate results schema + fixtures + spec doc"
git push
```

---

### Task 10: SPEC-02 — pack registry and built-in `with` model

**Files:**
- Create: `src/graphcheck/packs/__init__.py`
- Test: `tests/contracts/test_check_validation.py`

**Interfaces:**
- Produces: `PACK_VERSION: str`; `REGISTRY: dict[str, type[BaseModel]]`; `register(name)` decorator; built-in `completeness` `with` model. Consumed by Task 12.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from pydantic import ValidationError

from graphcheck.packs import PACK_VERSION, REGISTRY


def test_completeness_registered():
    assert "completeness" in REGISTRY
    model = REGISTRY["completeness"]
    ok = model.model_validate({"label": "Customer", "property": "tax_id", "threshold": 1.0})
    assert ok.threshold == 1.0


def test_with_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        REGISTRY["completeness"].model_validate({"label": "C", "property": "p", "bogus": 1})


def test_pack_version_is_a_string():
    assert isinstance(PACK_VERSION, str)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/contracts/test_check_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: graphcheck.packs`

- [ ] **Step 3: Create `src/graphcheck/packs/__init__.py`**

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

PACK_VERSION = "0.1.0"
REGISTRY: dict[str, type[BaseModel]] = {}


def register(name: str):
    def decorator(cls: type[BaseModel]) -> type[BaseModel]:
        REGISTRY[name] = cls
        return cls

    return decorator


class _WithBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


@register("completeness")
class CompletenessWith(_WithBase):
    label: str
    property: str
    threshold: float = 1.0
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/contracts/test_check_validation.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/graphcheck/packs tests/contracts/test_check_validation.py
git commit -m "feat(packs): add pack registry with built-in completeness check"
```

---

### Task 11: SPEC-02 — duplicate-key-rejecting YAML loader

**Files:**
- Create: `src/graphcheck/contracts/check.py`
- Test: `tests/contracts/test_check_validation.py`

**Interfaces:**
- Produces: `load_suite_yaml(text: str) -> dict` — raises on duplicate mapping keys. Consumed by Task 12.

- [ ] **Step 1: Write the failing test**

```python
from graphcheck.contracts.check import DuplicateKeyError, load_suite_yaml


def test_duplicate_keys_raise():
    text = "suite: s\nsuite: t\n"
    with pytest.raises(DuplicateKeyError):
        load_suite_yaml(text)


def test_normal_yaml_loads():
    assert load_suite_yaml("suite: s\n") == {"suite": "s"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/contracts/test_check_validation.py -k duplicate -v`
Expected: FAIL — cannot import `load_suite_yaml`

- [ ] **Step 3: Create `src/graphcheck/contracts/check.py`** (loader portion)

```python
from __future__ import annotations

import yaml


class DuplicateKeyError(ValueError):
    """A mapping key appeared more than once in a suite YAML file."""


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
    # We can't use safe_load directly because it silently keeps the last of
    # duplicate keys; the only reason for the subclass is the duplicate-key check.
    data = yaml.load(text, Loader=_NoDuplicatesLoader)  # noqa: S506 (SafeLoader subclass)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("a suite file must be a mapping at the top level")
    return data
```

> **Security note (for the reviewer):** `_NoDuplicatesLoader(yaml.SafeLoader)` inherits SafeLoader's constructors and overrides only the mapping constructor to raise on duplicate keys. It does **not** enable `!!python/object` or any arbitrary-type construction — it is equivalent to `safe_load` plus duplicate-key rejection. Do not replace the base with `yaml.Loader`/`FullLoader`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/contracts/test_check_validation.py -k "duplicate or normal" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/graphcheck/contracts/check.py tests/contracts/test_check_validation.py
git commit -m "feat(contracts): add duplicate-key-rejecting suite YAML loader"
```

---

### Task 12: SPEC-02 — envelope models, defaults, generated precedence, `with` validation

**Files:**
- Modify: `src/graphcheck/contracts/check.py`
- Test: `tests/contracts/test_check_validation.py`, `tests/contracts/fixtures/suite.valid.yml`, `tests/contracts/fixtures/suite.invalid-*.yml`

**Interfaces:**
- Consumes: `load_suite_yaml` (Task 11), `REGISTRY` (Task 10), `Pattern`/`Severity` (Task 5).
- Produces: `load_suite(text: str, *, source: str | None = None) -> Suite` where `Suite.checks: list[LoadedCheck]`. Each `LoadedCheck` has resolved `severity`, `tags`, `pattern`, `provenance`, effective `generated: bool`, **and `spec`** — the fully-validated pattern-specific payload (`ConformanceCheck` | `CompetencyCheck` | `DriftCheck`) the engine executes from, so nothing is discarded and the engine never reparses YAML. The suite id comes from the `suite:` key, else the `source` filename stem, else `ValueError`. `UnknownCheckError` raised on unknown `check`.
- **Loader vs engine boundary:** the loader validates, resolves config, and hands the engine a complete normalized `LoadedCheck` (payload on `.spec`), but does **not** execute or emit verdicts. `LoadedCheck.generated` is the parsed marker; translating `generated=True` into a SPEC-01 `CheckResult(verdict=skipped, skip_reason=generated)` at run time — and compiling `spec` to Cypher, resolving `$`-tokens, etc. — is the engine's (C1) responsibility, out of Week-1 scope.

- [ ] **Step 1: Create the fixtures**

`tests/contracts/fixtures/suite.valid.yml`:

```yaml
suite: customer-360
defaults: { severity: error, tags: [production] }

conformance:
  - id: cust-tax-id-present
    check: completeness
    with: { label: Customer, property: tax_id, threshold: 1.0 }
    tags: [pii, kyc]

competency:
  - id: cq-001
    question: "Which accounts does a customer control?"
    query: "MATCH (c:Customer {id:$id})-[:CONTROLS]->(a:Account) RETURN a.id AS account_id"
    params: { id: "$first-active-customer" }
    expect: { rows: { min: 1, max: 200 }, columns: [account_id], unique: true }

  - id: cq-001-regression
    question: "Known-good"
    query: "MATCH (c:Customer {id:$id})-[:CONTROLS]->(a:Account) RETURN a.id AS account_id"
    params: { id: "CUST-1042" }
    expect: { contains: ["ACC-9001"] }
```

`tests/contracts/fixtures/suite.invalid-unknown-key.yml`:

```yaml
suite: s
conformance:
  - id: x
    check: completeness
    with: { label: C, property: p }
    bogus: 1
```

`tests/contracts/fixtures/suite.invalid-unknown-check.yml`:

```yaml
suite: s
conformance:
  - id: x
    check: no_such_check
    with: {}
```

`tests/contracts/fixtures/suite.invalid-duplicate-key.yml`:

```yaml
suite: s
suite: t
```

`tests/contracts/fixtures/suite.invalid-bad-expect.yml`:

```yaml
suite: s
competency:
  - id: x
    question: q
    query: "RETURN 1"
    expect: { bogus: 1 }
```

- [ ] **Step 2: Write the failing tests**

```python
from pathlib import Path

from graphcheck.contracts.check import UnknownCheckError, load_suite
from graphcheck.contracts.results import Pattern, Severity

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return load_suite((FIX / name).read_text())


def test_valid_suite_loads_and_resolves_defaults():
    suite = _load("suite.valid.yml")
    by_id = {c.id: c for c in suite.checks}
    assert by_id["cust-tax-id-present"].severity is Severity.ERROR      # from defaults
    assert set(by_id["cust-tax-id-present"].tags) == {"production", "pii", "kyc"}  # union
    assert by_id["cq-001"].pattern is Pattern.COMPETENCY_SHAPE
    assert by_id["cq-001-regression"].pattern is Pattern.COMPETENCY_REGRESSION


def test_unknown_key_errors():
    with pytest.raises(ValidationError):
        _load("suite.invalid-unknown-key.yml")


def test_unknown_check_errors():
    with pytest.raises(UnknownCheckError):
        _load("suite.invalid-unknown-check.yml")


def test_duplicate_key_errors():
    with pytest.raises(DuplicateKeyError):
        _load("suite.invalid-duplicate-key.yml")


def test_unknown_expect_key_errors():
    with pytest.raises(ValidationError):
        _load("suite.invalid-bad-expect.yml")


def test_generated_file_marker_dominates_children():
    # File-level generated:true forces every check generated; a child generated:false cannot override.
    # (The engine, not the loader, later turns generated into a skipped CheckResult.)
    text = "suite: s\ngenerated: true\nconformance:\n  - id: x\n    check: completeness\n    with: {label: C, property: p}\n    generated: false\n"
    suite = load_suite(text)
    assert suite.checks[0].generated is True


def test_suite_name_defaults_to_source_stem():
    suite = load_suite("conformance: []\n", source="checks/customer-360.yml")
    assert suite.suite == "customer-360"


def test_suite_name_required_without_key_or_source():
    with pytest.raises(ValueError):
        load_suite("conformance: []\n")


def test_loaded_check_forbids_unknown_keys():
    from graphcheck.contracts.check import ConformanceCheck, LoadedCheck
    spec = ConformanceCheck(id="x", check="completeness", with_={"label": "C", "property": "p"})
    with pytest.raises(ValidationError):
        LoadedCheck(id="x", pattern=Pattern.CONFORMANCE, severity=Severity.ERROR,
                    tags=[], generated=False, spec=spec, bogus=1)


def test_conformance_requires_with():
    with pytest.raises(ValidationError):
        load_suite("suite: s\nconformance:\n  - id: x\n    check: completeness\n")


def test_conformance_with_defaults_are_normalized_onto_spec():
    suite = load_suite(
        "suite: s\nconformance:\n  - id: x\n    check: completeness\n    with: {label: C, property: p}\n"
    )
    assert suite.checks[0].spec.with_["threshold"] == 1.0  # pack default filled, not lost


def test_duplicate_check_id_in_suite_rejected():
    text = (
        "suite: s\ncompetency:\n"
        "  - id: dup\n    question: q\n    query: RETURN 1\n    expect: {unique: true}\n"
        "  - id: dup\n    question: q\n    query: RETURN 1\n    expect: {unique: true}\n"
    )
    with pytest.raises(ValueError):
        load_suite(text)
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/contracts/test_check_validation.py -v`
Expected: FAIL — cannot import `load_suite`

- [ ] **Step 4: Extend `check.py` — merge the imports into the top block (alongside `import yaml`), then add the classes below the loader**

```python
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from graphcheck.contracts.results import Pattern, Severity
from graphcheck.packs import REGISTRY


class UnknownCheckError(ValueError):
    """A conformance check references a `check` name not in the pack registry."""


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
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    check: str
    with_: dict = Field(alias="with")  # required — SPEC-02 freezes `check` + `with` for conformance


class RowBounds(_Strict):
    min: int | None = None
    max: int | None = None
    exactly: int | None = None


class Expect(_Strict):
    rows: RowBounds | None = None
    columns: list[str] | None = None
    unique: bool | None = None
    contains: list | None = None
    equals: list | None = None
    empty: bool | None = None


class CompetencyCheck(_Envelope):
    question: str
    query: str
    params: dict = {}
    expect: Expect


class DriftCheck(_Envelope):
    metric: str
    target: dict
    baseline: str = "latest"
    tolerance: dict


class LoadedCheck(_Strict):
    id: str
    pattern: Pattern
    severity: Severity                                     # resolved (check → defaults → error)
    tags: list[str]                                        # resolved (defaults ∪ check)
    provenance: str | None = None
    generated: bool                                        # effective (file OR check)
    spec: ConformanceCheck | CompetencyCheck | DriftCheck  # the validated payload the engine executes


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
        return LoadedCheck(id=env.id, pattern=pattern, severity=severity, tags=tags,
                           provenance=env.provenance, generated=generated, spec=env)

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
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/contracts/test_check_validation.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/graphcheck/contracts/check.py tests/contracts/test_check_validation.py tests/contracts/fixtures
git commit -m "feat(contracts): add check envelope, defaults, generated precedence, with validation"
```

---

### Task 13: SPEC-02 — envelope + combined schema generation, and the spec doc

**Files:**
- Modify: `src/graphcheck/contracts/schemas.py`
- Create: `docs/specs/check.envelope.schema.json`, `docs/specs/check.schema.json`, `docs/specs/SPEC-02-check-yaml.md`
- Modify: `tests/contracts/test_check_validation.py`

**Interfaces:**
- Consumes: `_SuiteFile` (envelope), `REGISTRY` + `PACK_VERSION`.
- Produces: `check_envelope_schema() -> dict`, `check_combined_schema() -> dict` (carries `x-pack-version`).

- [ ] **Step 1: Write the failing test**

Add to the top import block of `test_check_validation.py`: `import json`, `import jsonschema`, `from graphcheck.contracts.schemas import SPECS_DIR, check_combined_schema, check_envelope_schema`, and `from graphcheck.packs import PACK_VERSION`. (`load_suite_yaml` and `FIX` are already imported earlier in this file.) Then append:

```python
def test_envelope_schema_exposes_with_not_with_():
    props = check_envelope_schema()["$defs"]["ConformanceCheck"]["properties"]
    assert "with" in props and "with_" not in props


def test_envelope_schema_requires_with():
    conf = check_envelope_schema()["$defs"]["ConformanceCheck"]
    assert "with" in conf["required"]


def test_combined_schema_is_pack_versioned():
    assert check_combined_schema()["x-pack-version"] == PACK_VERSION


def test_combined_schema_validates_good_and_rejects_bad_with():
    schema = check_combined_schema()
    good = {"suite": "s", "conformance": [
        {"id": "x", "check": "completeness", "with": {"label": "C", "property": "p"}}]}
    jsonschema.validate(good, schema)  # must not raise
    bad = {"suite": "s", "conformance": [
        {"id": "x", "check": "completeness", "with": {"label": "C", "property": "p", "bogus": 1}}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_combined_schema_requires_with():
    schema = check_combined_schema()
    missing = {"suite": "s", "conformance": [{"id": "x", "check": "completeness"}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing, schema)


def test_valid_suite_fixture_validates_against_combined_schema():
    raw = load_suite_yaml((FIX / "suite.valid.yml").read_text())
    jsonschema.validate(raw, check_combined_schema())  # must not raise


def test_committed_check_schemas_are_current():
    envelope = json.loads((SPECS_DIR / "check.envelope.schema.json").read_text())
    combined = json.loads((SPECS_DIR / "check.schema.json").read_text())
    assert envelope == check_envelope_schema()
    assert combined == check_combined_schema()  # regenerate + recommit if this fails


def test_pack_with_models_are_ref_free():
    from graphcheck.packs import REGISTRY
    for name, model in REGISTRY.items():
        assert "$defs" not in model.model_json_schema(), f"{name} pack model must be flat for v0"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/contracts/test_check_validation.py -k schema -v`
Expected: FAIL — cannot import `check_combined_schema`

- [ ] **Step 3: Extend `schemas.py` — merge the imports into the top block, then append the functions**

```python
from graphcheck.contracts.check import _SuiteFile
from graphcheck.packs import PACK_VERSION, REGISTRY


def check_envelope_schema() -> dict:
    return _SuiteFile.model_json_schema(by_alias=True)


def check_combined_schema() -> dict:
    schema = _SuiteFile.model_json_schema(by_alias=True)
    schema["x-pack-version"] = PACK_VERSION
    defs = schema["$defs"]
    # Constrain each conformance item's `with` by its `check`: move the auto-generated
    # ConformanceCheck def aside, then redefine its $ref target as an allOf of the base
    # plus a oneOf over the registry. The conformance array already $refs ConformanceCheck,
    # so it picks up the constraint without touching the array schema.
    defs["ConformanceCheckBase"] = defs["ConformanceCheck"]
    branches = []
    for name, model in sorted(REGISTRY.items()):
        with_schema = model.model_json_schema()
        if "$defs" in with_schema:
            # A nested pack model emits internal #/$defs/... refs that dangle once inlined here.
            # v0 pack `with` models must be flat/ref-free; hoisting $defs is C3's job when needed.
            raise ValueError(
                f"pack `with` model {name!r} emits $defs; v0 requires flat, ref-free pack "
                f"schemas so they can be inlined. Hoist its $defs before registering it."
            )
        branches.append(
            {
                "properties": {"check": {"const": name}, "with": with_schema},
                "required": ["check", "with"],
            }
        )
    defs["WithByCheck"] = {"oneOf": branches}
    defs["ConformanceCheck"] = {
        "allOf": [
            {"$ref": "#/$defs/ConformanceCheckBase"},
            {"$ref": "#/$defs/WithByCheck"},
        ]
    }
    return schema


def write_check_schemas() -> None:
    (SPECS_DIR / "check.envelope.schema.json").write_text(
        json.dumps(check_envelope_schema(), indent=2, sort_keys=True) + "\n"
    )
    (SPECS_DIR / "check.schema.json").write_text(
        json.dumps(check_combined_schema(), indent=2, sort_keys=True) + "\n"
    )
```

- [ ] **Step 4: Generate the committed schemas**

Run: `uv run python -c "from graphcheck.contracts.schemas import write_check_schemas; write_check_schemas()"`
Expected: writes both files under `docs/specs/`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/contracts/test_check_validation.py -v`
Expected: PASS

- [ ] **Step 6: Write `docs/specs/SPEC-02-check-yaml.md`** — lift the SPEC-02 section from the design doc (the pattern-keyed shape, the six rules, the `with` pack-boundary subsection, the two-schema split), and point at `suite.valid.yml` as the machine-valid fixture.

- [ ] **Step 7: Commit and open/refresh the SPEC-02 PR**

```bash
git add src/graphcheck/contracts/schemas.py docs/specs/check.envelope.schema.json docs/specs/check.schema.json docs/specs/SPEC-02-check-yaml.md tests/contracts/test_check_validation.py
git commit -m "feat(contracts): generate check schemas (frozen envelope + combined) + spec doc"
git push -u origin <branch>
gh pr create --base development --title "SPEC-02: check YAML models, loader, registry + schemas" \
  --body "Implements the check YAML contract per docs/design. Duplicate-key loader, strict envelope, defaults resolution, generated precedence, REGISTRY-driven with validation, and the frozen-envelope/combined schema split."
```

---

## Phase 3 — Team direction

### Task 14: Week-1 kickoff doc

**Files:**
- Create: `docs/week-1-kickoff.md`

- [ ] **Step 1: Write `docs/week-1-kickoff.md`** — goal; links to the two frozen contracts (`docs/specs/SPEC-01…`, `SPEC-02…`); the sequencing/dependency note (contracts unblock C2's result emission and the fixture's defect design); per-owner deliverables + acceptance criteria (copy the §12 table rows for C2 and the fixture); the §13 decision-rights reminder; the §14 DoD + anti-slop rules; the no-AI-attribution rule; the standup/Friday-demo rhythm; and a ready-to-post three-sentence `#general` kickoff message. Write in a plain human voice, no attribution.

- [ ] **Step 2: Commit (on a docs branch → PR)**

```bash
git switch development && git pull && git switch -c docs/week-1-kickoff
git add docs/week-1-kickoff.md
git commit -m "docs: add Week 1 kickoff and team direction"
git push -u origin docs/week-1-kickoff
gh pr create --base development --title "docs: Week 1 kickoff" --body "Team direction, sequencing, DoD, and the kickoff message."
```

---

### Task 15: GitHub milestone, labels, and issues

This task uses `gh`, not code. It creates the trackable work for the rest of the team.

- [ ] **Step 1: Create the milestone and labels**

```bash
gh api --method POST /repos/graphora/graphcheck/milestones -f title="Week 1" \
  -f description="Contracts, connector, fixture graph"
gh label create week-1 --color 0e8a16 --description "Week 1 deliverable" || true
gh label create connector --color 1d76db || true
gh label create fixture --color fbca04 || true
```

- [ ] **Step 2: Create the C2 connector issue**

```bash
gh issue create --title "[C2] Neo4j adapter + capability probe" \
  --assignee ghilda-graphora --assignee kev-graphora \
  --milestone "Week 1" --label week-1 --label connector \
  --body "Owner: Ghilda/Keval.

Acceptance (§12): \`graphcheck debug\` reports server version, edition, and APOC presence; integration test passes against Neo4j 4.4 and 5.x containers.

\`graphcheck debug\` is the one Week-1 CLI command (carved out of C6); the rest of the CLI is Week 3. Emit results consistent with SPEC-01 (docs/specs/SPEC-01-results-json.md). Decision rights: you choose the driver/libraries within C2; Ezhil reviews. DoD: see the PR template."
```

- [ ] **Step 3: Create the fixture-graph issue**

```bash
gh issue create --title "[Fixture] fraud-ring.cypher" \
  --assignee jayachandra-bit --assignee jananik-graphora \
  --milestone "Week 1" --label week-1 --label fixture \
  --body "Owner: Jayachandra/Janani.

Acceptance (§12): Cypher at \`tests/fixtures/fraud-ring.cypher\` loads in under 10 seconds; ~5K nodes; contains 3 orphans, 1 cardinality violation, PII in 2 properties, and 1 induced drift. DoD: see the PR template."
```

- [ ] **Step 4: Create tracking issues for Ezhil's own deliverables**

```bash
for t in "[Scaffold] repo + governance" "[SPEC-01] results.json contract" "[SPEC-02] check YAML contract"; do
  gh issue create --title "$t" --assignee ezhilvendhan --milestone "Week 1" --label week-1 \
    --body "Owner: Ezhil. Tracked for Week 1 visibility. See docs/design/2026-07-06-week1-contracts-and-kickoff.md."
done
```

- [ ] **Step 5: Verify**

Run: `gh issue list --milestone "Week 1"`
Expected: five issues listed with the right assignees.

---

## Self-Review

**Spec coverage** — every design section maps to a task: scaffold+deps → T1; CI → T2; governance files → T3; branch/ruleset → T4; SPEC-01 enums/leaves → T5, CheckResult → T6, derivations → T7, Results validator → T8, schema/fixtures/doc → T9; SPEC-02 registry → T10, loader → T11, envelope/defaults/generated/with → T12, schemas+doc → T13; kickoff doc → T14; issues+milestone → T15. No design section is unimplemented.

**Placeholders** — none. Docs-heavy steps (SPEC markdown, kickoff) instruct "lift section X from the design doc verbatim," which is a concrete action, not a TODO.

**Type consistency** — names are stable across tasks: `Verdict/Severity/Pattern/SkipReason/RunStatus/RedactionPolicy`, `WEIGHTS`, `score_value`/`totals`/`exit_code`, `CheckResult.executed`, `Results`, `load_suite_yaml`/`load_suite`, `REGISTRY`/`PACK_VERSION`, `results_schema`/`check_combined_schema`. The `with` Python-keyword collision is handled with the `with_` alias in `ConformanceCheck`.

**Known follow-ups (out of Week-1 scope, noted not silently dropped):** the twelve conformance `with` models beyond `completeness` are C3's Week-2 work (registry grows, combined schema regenerates); `graphcheck debug` is C2's; the fixture graph is Jayachandra/Janani's.
