# GraphCheck agent guide

This guide describes how an agent discovers and runs GraphCheck suites, consumes the
machine-readable result, and proposes new checks for human approval. The normative contracts are
[SPEC-01 (`results.json`)](specs/SPEC-01-results-json.md) and
[SPEC-02 (check YAML)](specs/SPEC-02-check-yaml.md); this guide is the operational summary.

## Agent and MCP surface

Start the stdio MCP server from a GraphCheck project or one of its child directories:

```console
graphcheck mcp serve
```

GraphCheck finds the project by walking upward to `graphcheck.yml`. The server deliberately exposes
exactly three tools:

| Tool | Input | Output and use |
| --- | --- | --- |
| `list_checks` | none | Returns configured suites without connecting to the database. Each check includes `id`, `kind`, resolved `severity`, resolved `tags`, and effective `generated`. Use a returned `suite` value as the input to `run_suite`. |
| `run_suite` | `suite` (required), `profile` (optional) | Runs one suite with the same read-only credential verification as the CLI, writes the normal run artifacts, and returns a validated SPEC-01 result. |
| `get_results` | `run_id` (optional, defaults to `latest`) | Loads and validates a persisted `results.json`. Pass a GraphCheck run ID, not a path. |

A reliable agent loop is:

1. Call `list_checks` and select an existing suite ID; do not guess it.
2. Call `run_suite` with that ID and, when needed, a configured profile name.
3. Interpret the returned `run.exit_code` and every selected check result as described below.
4. Use the returned `run.id` with `get_results` when the persisted artifact is needed later. Use
   `latest` only when another run cannot race with the lookup.

The MCP surface runs and reads suites; it does not create or approve them. An authoring agent writes
SPEC-02 files to the `checks` path configured in `graphcheck.yml` (default: `checks/`) through its
normal repository/file tools.

`run_suite` returns the same result model as `results.json`, even when checks fail. An MCP tool-call
error is different: the request itself could not produce or publish a usable result. Do not treat a
tool-call error as a clean run and do not synthesize a pass.

The equivalent CLI flow is `graphcheck run --suite <suite-id>` followed by reading
`.graphcheck/runs/<run-id>/results.json`. The convenience copy at
`.graphcheck/runs/latest/results.json` has the same race caveat as `get_results("latest")`.

## Consuming `results.json`

`results.json` is the decision-making contract. The HTML report is a rendering of it, not another
source of truth. Its top-level shape is:

```text
{ schema_version, run, score, totals, suites[], checks[] }
```

- `schema_version` versions this contract independently of the GraphCheck release. Reject an
  unsupported version rather than guessing at its meaning.
- `run` contains identity, timestamps, status, exit code, selection, redaction, target metadata,
  and a run-level error when setup failed.
- `score` is a severity-weighted score or `null` when no check executed. It is useful for reporting,
  but it does not replace the exit code or verdicts.
- `totals` is the tally of `checks[]`; `suites[]` contains the corresponding per-suite score and
  totals.
- `checks[]` contains exactly the selected check universe. A check excluded by suite or tag
  selection is absent, not skipped.

In Python, use GraphCheck's compatibility-aware validator rather than parsing and trusting a raw
dictionary:

```python
from pathlib import Path
from graphcheck.reporting import load_results

results = load_results(Path(".graphcheck/runs/latest/results.json"))
if results.run.exit_code != 0:
    # Route the run for remediation or review; inspect results.checks below.
    ...
```

Other consumers should validate against `docs/specs/results.schema.json` and implement the derived
invariants in SPEC-01. The JSON Schema is structural; it cannot express all status, totals, score,
field-presence, and exit-code rules.

### Run status is not the verdict

`run.status` describes execution coverage:

- `complete` means GraphCheck completed the selected universe. It does **not** mean all checks
  passed.
- `partial` means coverage was lost. `partial_reason` explains why.
- `failed` means the run could not be prepared or executed as a run. Inspect `run.error`.

The per-check `verdict` describes each outcome:

| Verdict | Meaning | Diagnostic field |
| --- | --- | --- |
| `pass` | The check executed and its assertion held. | `measured` and `expected` |
| `fail` | An `error`-severity assertion did not hold. | `evidence` |
| `warn` | A `warn`-severity assertion did not hold. | `evidence` |
| `errored` | GraphCheck attempted the check but could not compile, run, or evaluate it. | `error: {code, message, fix}` |
| `skipped` | GraphCheck did not attempt the check. | `skip_reason: generated | unsupported | not_run` |

`errored` is never a pass. Likewise, `skipped` proves nothing about the graph. Agents must not use
"no `fail` verdicts" as a success test because warnings, execution errors, partial runs, and an
entirely skipped selection are all non-clean outcomes.

### Exit-code contract

Use the stored `run.exit_code` as the overall automation decision. It is derived using the first
matching row:

| Exit | Meaning |
| --- | --- |
| `3` | `run.status` is `failed`; the run-level `run.error` contains the cause and fix. |
| `1` | At least one `fail`, or an `errored` check with `severity:error`. A partial-run `engine.timeout` is handled by exit `2` instead. |
| `2` | The run is partial, nothing was evaluated, or at least one `warn` or `severity:warn` error occurred. This is review/inconclusive, not success. |
| `0` | The run is complete, at least one check executed, and all executed checks passed; generated skips may coexist with those passes. |

This is a `0/1/2/3` contract, not a boolean convention where every nonzero value means the same
thing. Preserve all four values in wrappers and agent policy.

### Evidence and remediation

Every `fail` and `warn` has `evidence`:

```json
{
  "message": "Human-readable finding",
  "elements": [{"kind": "node", "id": "123", "labels": ["Customer"], "type": null}],
  "truncated": false,
  "cap": 50,
  "total_count": 1
}
```

Evidence elements are pointers, not graph records. `kind` is `node`, `rel`, or `aggregate`;
aggregate IDs name a measurement scope rather than a Neo4j element. When `truncated` is true,
`elements` is only the capped sample and `total_count` is the full finding count. Do not infer that
the listed elements are exhaustive.

For `errored`, use the structured `error.code`, `message`, and `fix`; absence of evidence is
expected because the assertion did not complete. For `skipped`, branch on `skip_reason`:

- `generated`: intentionally inert pending human approval; it does not by itself make the run
  partial.
- `unsupported`: a required target capability was unavailable; the run is partial.
- `not_run`: execution stopped before this selected check ran, for example under fail-fast; the run
  is partial.

If `estimate` is an object rather than `false`, report its sample size, population, confidence, and
confidence interval with the result. Do not present a sampled finding as an exact full-graph count.

## Authoring checks in SPEC-02 format

Suites are `.yml` or `.yaml` files beneath the configured checks directory. They have one optional
suite ID (which defaults to the filename stem), optional defaults, and one or more of the three
check collections:

```yaml
suite: customer-health
generated: true
defaults:
  severity: error
  tags: [production]

conformance:
  - id: customer-tax-id-present
    check: completeness
    with:
      label: Customer
      property: tax_id
      threshold: 1.0

competency:
  - id: customers-can-be-counted
    question: Can customers be counted?
    query: MATCH (c:Customer) RETURN count(c) AS count
    expect:
      rows: { exactly: 1 }
      columns: [count]

drift:
  - id: customer-count-stable
    metric: node_count
    target: { label: Customer }
    baseline: latest
    tolerance: { max_drop_pct: 10 }
    severity: warn
```

The collections have different contracts:

- `conformance`: `id`, `check`, and `with` are required. `check` selects an installed pack schema,
  and every type-specific argument belongs under `with`.
- `competency`: `id`, nonblank `question`, read-only `query`, and nonempty `expect` are required;
  `params` is optional. Shape assertions use `rows`, `columns`, `unique`, or `empty`. `contains` and
  `equals` create regression checks.
- `drift`: `id`, `metric`, `target`, and nonempty `tolerance` are required. `baseline` defaults to
  `latest`, but that baseline must exist when the check runs.

All check kinds also accept `severity`, `tags`, `provenance`, and `generated`. Severity resolves
from check to defaults to `error`; tags are the ordered union of suite defaults and check tags.
Check IDs must be unique across all three collections in a suite.

SPEC-02 is strict. Duplicate YAML keys, unknown fields, unknown check names, invalid `with`
parameters, and most scalar coercions are rejected. Use
[`check.schema.json`](specs/check.schema.json) to discover the installed conformance names,
parameters, defaults, and constraints; the smaller `check.envelope.schema.json` deliberately leaves
`with` opaque. Non-Python authors must also implement SPEC-02's semantic rules after JSON Schema
validation.

### Programmatic authoring and validation

An authoring program should construct data, serialize it with a safe YAML emitter, validate the
finished text, and only then publish it. Agent-authored content must set `generated: true` before
that validation and write:

```python
from pathlib import Path
import yaml
from graphcheck.contracts.check import load_suite

path = Path("checks/agent-proposal.yml")
payload = {
    "suite": "agent-proposal",
    "generated": True,
    "conformance": [
        {
            "id": "customer-id-unique",
            "check": "uniqueness",
            "with": {"label": "Customer", "property": "customer_id"},
        }
    ],
}
text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
load_suite(text, source=str(path))
path.write_text(text, encoding="utf-8")
```

Do not invent graph rules, pack names, or parameters. Derive proposals from the supplied graph
schema/profile and business documentation, prefer focused checks, parameterize competency queries,
and omit rules that the source material does not support.

## Generated checks require human approval

Every check created or materially rewritten by an agent must remain effectively
`generated: true`. Prefer the file-level marker for an entirely generated suite. The rule is
monotonic: a file-level `generated: true` makes every child generated, and a child
`generated: false` cannot override it.

Generated checks are fully parsed and validated, appear as `generated: true` in `list_checks`, and
appear in a selected run as `verdict: skipped` with `skip_reason: generated`. GraphCheck submits no
query for them. If every selected check is generated, nothing was evaluated and the run exits `2`.

Only a human reviewer activates a check after verifying its source assumptions, scope, severity,
parameters, and (for competency checks) read-only query. Approval is represented by removing the
effective marker:

- To approve the whole generated suite, the human removes its file-level `generated: true` and any
  per-check generated markers.
- To approve only some checks, the human removes the file-level marker, leaves
  `generated: true` on each unapproved check, and leaves approved checks unmarked.

An agent may propose or apply this diff after explicit human approval, but must not activate a
check on its own or reinterpret a generated skip as approval.
