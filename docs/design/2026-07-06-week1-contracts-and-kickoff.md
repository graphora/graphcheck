# Week 1 design — contracts, scaffold, and team kickoff

*2026-07-06 · owner: Ezhil · reviewer: Ghilda/Keval*

This document records the decisions for Ezhil's Week 1 deliverables and the direction handed to the rest of the team. It explains *why* the contracts are shaped the way they are. The normative *what* lives in the SPEC files (`docs/specs/SPEC-01-results-json.md`, `docs/specs/SPEC-02-check-yaml.md`) that this design produces. Where this document and the briefing disagree, the briefing wins.

## Goal

Produce, this week, the four things Ezhil owns or drives:

1. Repository scaffold **and governance** (the "Day 1" init the whole team commits against): structure, linting, testing, CI, branch protection, and `development` as the default branch — so the team is adherent to the standards from day 1.
2. SPEC-01 — the `results.json` contract. Frozen for v0. Every other artifact renders from it.
3. SPEC-02 — the check YAML contract. Frozen for v0. Strict validation, generated JSON Schema.
4. Team direction — GitHub issues + a kickoff doc for the Connector (C2) and fixture-graph owners.

Sequencing: **scaffold + governance → contracts → team direction.** Repository setup and its guardrails land *before* any spec code is written (with server-side enforcement per the plan decision in the Governance section), so every subsequent contribution — including the contract code — flows through the same PR, review, and CI path. The contracts then unblock the conventions the team's Week 1 work references.

Two review gates precede implementation: Ezhil reviews this design (the spec), then reviews the implementation plan built from it, before any spec code is written.

## Decisions locked this session

| Decision | Choice | Rationale |
| --- | --- | --- |
| Tooling stack | `pyproject.toml` (PEP 621), hatchling backend, `uv` for dev, Typer CLI, ruff + pytest + Hypothesis, src-layout | Modern, minimal, distributes as a plain wheel for `pipx install graphcheck`. Typer serves the "glass-grade CLI, every error contains the fix, `--json` on every command" bar. |
| `results.json` structure | Hybrid: `run` header + `suites[]` + flat `checks[]` | Renderers group by `suite_id`; agents/CI iterate the flat list; future cloud ingests normalized check records keyed by `(suite_id, id)`. No consumer has to reshape. |
| Check YAML format | Pattern-keyed collections (`conformance:` / `competency:` / `drift:`) | Human-readable, matches the briefing's mental model. Loader normalizes to a flat internal list carrying `pattern`, so the engine still dispatches on one field. |
| verdict / severity | Collapse severity into verdict for failures | A failing `severity:error` check → `verdict:fail`; a failing `severity:warn` → `verdict:warn`. Exit codes derive from verdict, `severity` (for `errored`), and `run.status` (`failed`→3, `partial` floors to 2) — see the SPEC-01 rule 1 precedence table. `severity` stays as the declared-config field for auditability. |
| `results.json` JSON Schema | Include it (structural only) | Lets C5's writer structurally self-validate every run. JSON Schema covers **structure only** — the derived invariants (score/totals/exit_code, field-presence, coverage-status) live in the Pydantic validators, so schema-valid ≠ fully-valid; external consumers must not rely on the schema alone. |
| Validation library | Pydantic v2 as source of truth | `extra="forbid"` gives SPEC-02's "unknown keys error loudly" for free; `model_json_schema()` generates the published schema. One definition, no drift between validator and schema. |
| Team direction | GitHub issues (tracking) + `docs/week-1-kickoff.md` (narrative) | GitHub-native, matches the CI-owns-cadence, runnable-artifact culture. |
| Default branch | `development` (default, integration) + `main` (release/tags) | Daily PRs target `development`; `main` holds tagged releases (v0.1.0). Gitflow-lite, matches the briefing's release-tagging model. |
| Branch protection | **GitHub Team on the `graphora` org** → rulesets on `development` + `main` | `graphcheck` is org-owned, so Team (not personal Pro) is the plan that unlocks private-repo rulesets (verified: Free returns HTTP 403). Applied via `gh api` after the org upgrade. |
| Governance-as-code | CODEOWNERS + PR template (DoD checklist) + CONTRIBUTING.md | Makes "adherent from day 1" real — the reviewer model and §14.1 definition of done are enforced on every PR, not left to memory. |

## Repository scaffold and governance

```
graphcheck/
├── LICENSE                     Apache 2.0
├── README.md                   skeleton now; 10-minute quickstart lands Week 4
├── CHANGELOG.md                Keep a Changelog format, semver from day 1
├── CONTRIBUTING.md             branch flow, DoD, decision rights, anti-slop, no-AI-attribution
├── pyproject.toml              PEP 621 · hatchling · deps · ruff + pytest config inline
├── uv.lock                     committed
├── .python-version             3.12
├── .gitignore                  extend: profiles.yml, .graphcheck/, .venv, dist/
├── .pre-commit-config.yaml     ruff format + ruff check, run locally before push
├── .github/
│   ├── workflows/ci.yml        ruff + pytest on every PR (matrix 3.12–3.13)
│   ├── CODEOWNERS              path → required reviewer, enforced by the ruleset
│   ├── pull_request_template.md   the §14.1 definition-of-done checklist
│   └── ISSUE_TEMPLATE/deliverable.md   owner · acceptance criteria · DoD
├── src/graphcheck/
│   ├── __init__.py             __version__
│   ├── cli.py                  minimal Typer app: --version / --help only
│   └── contracts/              SPEC-01/02 Pydantic models + validators (Ezhil's code this week)
├── tests/
│   ├── contracts/              validator tests + machine-valid fixtures/ (round-trip, invariants)
│   └── fixtures/               fraud-ring.cypher lands here (Jayachandra/Janani)
├── docs/
│   ├── specs/                  SPEC-01, SPEC-02, generated JSON Schemas
│   ├── components/             per-component design notes (§14.1.8)
│   ├── prompts/                per-component agent prompts (§16)
│   ├── design/                 this document
│   └── week-1-kickoff.md       team direction
├── checks/                     example suite
└── examples/                   example CQ library (Week 3)
```

**CLI boundary.** The full command surface (C6) belongs to Ghilda/Keval in Week 3. Two things are allowed earlier: the scaffold ships a minimal Typer entry point (`graphcheck --version`, `--help`) so the package installs and CI can import it; and **`graphcheck debug`** is carved out as the *one* Week-1 command — it is C2's diagnostic entry point (owned by Ghilda/Keval per the §12 acceptance criterion, added in the C2 PR, not in Ezhil's scaffold). Every other command waits for C6. This resolves the apparent conflict between "no CLI until Week 3" and the Week-1 `graphcheck debug` acceptance criterion: `debug` is a deliberate, single exception owned by the connector.

**Dependencies added this week.** Runtime: `typer`, `pydantic>=2`, `pyyaml`. Dev (a `[dependency-groups]` / `--group dev`): `pytest`, `pytest-cov` (the CI `--cov` gate needs it), `hypothesis`, `ruff`, `pre-commit`. Everything else (Neo4j driver, etc.) is a per-component choice owned by that component's owner.

### Governance from day 1

The intent is to make the standards binding rather than aspirational. **How binding they can be depends on a GitHub plan decision (below): `graphora/graphcheck` is a private repo on the Free plan, where neither repository rulesets nor classic branch protection are available — verified, the rulesets API returns HTTP 403, "Upgrade to GitHub Pro or make this repository public."**

Regardless of that decision, these land in the bootstrap commit and cost nothing:

- **Branches.** `development` is created and set as the repository default; `main` is retained for release tags (v0.1.0). Feature branches → PR into `development`.
- **CI** (`.github/workflows/ci.yml`) on every PR and on pushes to `development`/`main`. Stable **job** names, because required status checks match job/check names, not step names:
  - `lint` — `ruff check` + `ruff format --check`
  - `test (3.12)` / `test (3.13)` — a matrix; each runs `pytest --cov=graphcheck --cov-report=term-missing --cov-fail-under=80` (coverage is enforced *inside* the test job, since the DoD requires ≥ 80%).
- **CODEOWNERS** (lean, to paths that exist; grows as component directories land). CODEOWNERS applies the **last matching pattern only — owners do not accumulate across lines** — so each path lists every required reviewer explicitly:
  - `*` → `@ezhilvendhan`
  - `/docs/specs/`, `/src/graphcheck/contracts/` → `@ghilda-graphora @kev-graphora` (the SPEC-01/02 reviewers per §11.2; Ezhil authors these and GitHub excludes a PR's author from its own required review — see "CODEOWNERS ownership of contracts" below)
  - `/tests/fixtures/` → `@jayachandra-bit @jananik-graphora`
- **PR template** carries the §14.1 definition-of-done checklist (tests; coverage ≥ 80%; no `print`; no swallowed exceptions; design note; CHANGELOG entry) plus an anti-slop line.
- **CONTRIBUTING.md** states the branch flow, the §13 decision-rights rule, the §14 DoD and anti-slop rules, and the no-AI-attribution rule in one place newcomers read first.

**Enforcement mechanism — decided: GitHub Team on the `graphora` org.** `graphcheck` is org-owned, so the plan that governs it is the *organization's* plan — **GitHub Team** (~US$4/user/month), not personal GitHub Pro (Pro applies only to a user account's own repos and would not affect this org repo). Upgrading the `graphora` org to Team enables rulesets + branch protection while keeping the repo private. The org upgrade is a billing action Ezhil performs; the ruleset application is a setup step and is verified by re-checking the rulesets API (currently HTTP 403 on Free, expected to succeed once Team is active).

Once `graphora` is on Team, the setup step applies a **ruleset** via `gh api` to both `development` and `main`: require a pull request; require 1 approving review; require review from Code Owners; require the `lint` and `test (3.12)`–`test (3.13)` status checks to pass; require the branch to be up to date; require conversation resolution; block force-pushes and deletions.

**CODEOWNERS ownership of contracts (decided).** Contract paths (`/docs/specs/`, `/src/graphcheck/contracts/`) list `@ghilda-graphora @kev-graphora` — the SPEC-01/02 reviewers named in §11.2. Ezhil authors the initial contracts (GitHub excludes a PR's author from its own required review) and retains authority over *post-freeze* contract changes through §13 escalation and org-admin review, so he is not added as a code owner on those lines. Note the mechanics: "require review from Code Owners" is satisfied by approval from **any one** listed owner of a changed path — it does not force approval from every listed owner — so adding `@ezhilvendhan` would auto-request him and let his approval count, but would not *hard-require* it. Forcing his specific sign-off on contract changes remains the §13 process gate, not a CODEOWNERS guarantee.

**Bootstrap ordering note.** The first scaffold + governance commit cannot itself go through a PR (no protected branch exists yet), so it is pushed directly to establish `development`; the ruleset is enabled immediately afterward, once the org is on Team. Every subsequent change — the contracts included — uses the PR flow.

## SPEC-01 — `results.json`

`results.json` is the contract every other artifact (HTML, MCP, future cloud) renders from. It is frozen for v0. Its own `schema_version` is versioned independently of `graphcheck_version` and is bumped only on a contract change.

```jsonc
{
  "schema_version": "1.0",
  "run": {
    "id": "run_<ulid>",
    "started_at": "2026-07-06T09:00:00Z",     // ISO 8601, UTC, always
    "finished_at": "2026-07-06T09:02:41Z",
    "graphcheck_version": "0.1.0",
    "pack_version": "0.1.0",                  // built-in conformance pack version used (reproducibility + `with` schema pin)
    "status": "complete",                     // complete | partial | failed
    "partial_reason": null,                   // set when status=partial (e.g. time budget hit)
    "exit_code": 1,                           // 0/1/2/3, recorded not just returned
    "selection": { "suites": ["customer-360"], "tags": ["production"], "fail_fast": false },
    "redaction": { "policy": "none", "applied": false },   // policy ∈ none|mask|hash (enum frozen now); v0 implements none only
    "target": {                               // present for complete|partial; null for failed
      "database": "neo4j",
      "server_version": "5.18.0",
      "edition": "community",
      "fingerprint": "sha256:…",              // stable hash of schema + counts
      "capabilities": { "apoc": true, "count_store": true }
    },
    "error": null                             // non-null only when status=failed: { code, message, fix }
  },
  // score & totals are DERIVED from checks[]; this example is internally consistent
  // (one error/fail, one error/pass, one warn/warn): weights error=3 warn=1 →
  // 100 × 3 / (3+3+1) = round(42.857) = 43. exit_code 1 because a fail is present.
  "score": { "value": 43, "method": "weighted-by-severity", "weights": { "error": 3, "warn": 1 } },
  "totals": { "checks": 3, "pass": 1, "fail": 1, "warn": 1, "errored": 0, "skipped": 0 },
  "suites": [
    { "id": "customer-360", "source_sha": "…", "score": 43,
      "totals": { "checks": 3, "pass": 1, "fail": 1, "warn": 1, "errored": 0, "skipped": 0 } }
  ],
  "checks": [
    {
      "id": "cq-001",
      "suite_id": "customer-360",
      "pattern": "competency-shape",          // conformance | drift | competency-shape | competency-regression
      "name": "Which accounts does a customer control…",
      "provenance": "KYC requirement §4.2",
      "severity": "error",                    // declared severity (config)
      "verdict": "fail",                      // outcome: pass | fail | warn | errored | skipped
      "skip_reason": null,                    // set only when verdict=skipped: generated | unsupported | not_run
      "started_at": "2026-07-06T09:01:12Z",
      "duration_ms": 142,
      "compiled_query": "MATCH (c:Customer {id:$customer_id})-[:CONTROLS*1..4]->(a:Account) RETURN a.id AS account_id",
                                              // string once compiled; null if the check errored before compiling
      "params": { "customer_id": "CUST-1042" },   // only literal-value surface; subject to run.redaction
      "measured": { "rows": 5000 },
      "expected": { "rows": { "min": 1, "max": 200 }, "unique": true },
      "estimate": false,                      // false = exact; else { sample_size, population, confidence, ci: [lo, hi] }
      "evidence": {                           // mandatory when verdict ∈ {fail, warn}; element IDs + labels only, no property values
        "message": "5000 rows exceeds max 200",
        "elements": [ { "kind": "node", "id": "4:abc:12", "labels": ["Account"] } ],
        "truncated": true, "cap": 50, "total_count": 5000
      },
      "error": null                           // populated only when verdict=errored: { code, message, fix }
    },
    {
      "id": "cust-tax-id-present", "suite_id": "customer-360", "pattern": "conformance",
      "name": "Customer.tax_id is present", "provenance": null,
      "severity": "error", "verdict": "pass", "skip_reason": null,
      "started_at": "2026-07-06T09:01:13Z", "duration_ms": 61,
      "compiled_query": "MATCH (c:Customer) RETURN count(c) AS total, count(c.tax_id) AS present",
      "params": {}, "measured": { "coverage": 1.0 }, "expected": { "threshold": 1.0 },
      "estimate": false, "evidence": null, "error": null   // evidence null: passing checks carry none
    },
    {
      "id": "account-no-orphans", "suite_id": "customer-360", "pattern": "conformance",
      "name": "Accounts are connected to a Customer", "provenance": null,
      "severity": "warn", "verdict": "warn", "skip_reason": null,
      "started_at": "2026-07-06T09:01:14Z", "duration_ms": 55,
      "compiled_query": "MATCH (a:Account) WHERE NOT (a)<-[:CONTROLS]-(:Customer) RETURN a.id",
      "params": {}, "measured": { "orphans": 2 }, "expected": { "orphans": 0 },
      "estimate": false,
      "evidence": {                           // warn also requires evidence
        "message": "2 Account nodes have no controlling Customer",
        "elements": [ { "kind": "node", "id": "4:abc:88", "labels": ["Account"] },
                      { "kind": "node", "id": "4:abc:91", "labels": ["Account"] } ],
        "truncated": false, "cap": 50, "total_count": 2
      },
      "error": null
    }
  ]
}
```

### Shape by run status

The top-level shape is conditional on `run.status`, enforced by a Pydantic `model_validator` (JSON Schema alone cannot express it):

- **`complete`** — `run.target`, `totals`, `suites`, `checks` present; `run.error` is null. The `score` key is always present but **nullable**: a number when ≥ 1 check executed, and `null` when none did — including a `complete` generated-only run or an empty selection (`checks: []`). See rule 6.
- **`partial`** — as complete (same nullable-`score` rule), plus `run.partial_reason` is non-null. `totals` is always derived from `checks[]` (invariant below), so a resolved-but-unexecuted check is still emitted as a `skipped` record (`skip_reason:"not_run"`) rather than vanishing — coverage stays countable and `totals` stays consistent. Coverage lost to a suite that could not be **loaded/parsed** (its individual checks are unknowable) is described in `run.partial_reason`, not folded into `totals`.
- **`failed`** — the run could not execute (bad config, no connection). `run.error` is `{ code, message, fix }`, `run.target` may be null, `score` is null, `suites` and `checks` are `[]`. Exit code 3.

  In short: `score` is a present-but-nullable key in every status; it is `null` exactly when the score denominator is empty (no check executed), and a number otherwise. And `run.partial_reason` is non-null **iff** `run.status` is `partial` — it is `null` for `complete` and `failed`, so a stale reason never leaks into a non-partial run. The `model_validator` enforces both.

A failed run:

```jsonc
{
  "schema_version": "1.0",
  "run": {
    "id": "run_<ulid>", "started_at": "…", "finished_at": "…", "graphcheck_version": "0.1.0", "pack_version": "0.1.0",
    "status": "failed", "partial_reason": null, "exit_code": 3,
    "selection": { "suites": [], "tags": [], "fail_fast": false },
    "redaction": { "policy": "none", "applied": false },
    "target": null,
    "error": { "code": "connection.auth",
               "message": "Neo4j rejected the credentials for bolt://localhost:7687",
               "fix": "Check the password in profiles.yml, or run `graphcheck debug` to diagnose." }
  },
  "score": null,
  "totals": { "checks": 0, "pass": 0, "fail": 0, "warn": 0, "errored": 0, "skipped": 0 },
  "suites": [],
  "checks": []
}
```

> The two blocks above are **illustrative `jsonc`** — they carry `//` comments and `…`/`<ulid>` placeholders for readability, so they are **not** valid JSON as printed. The machine-valid artifacts the round-trip tests load are separate committed fixtures — `tests/contracts/fixtures/results.complete.json` and `results.failed.json` — identical in shape but comment-free with real values. The spec doc and the fixtures are kept in sync by the test that validates the fixtures against `results.schema.json`.

### Semantic rules (these encode the accuracy contract)

1. **verdict encodes outcome + severity for assertion failures.** A failing `severity:error` check → `verdict:fail`; a failing `severity:warn` check → `verdict:warn`. Two distinct "did-not-pass-normally" cases are kept separate by *when* the check stopped:
   - **attempted but failed to compile or execute** (query parse error, timeout, runtime error) → `verdict:errored` — keeps its declared `severity`, carries `error:{code,message,fix}`, counts in the score denominator with zero credit, and does **not** make the run partial (it was attempted and produced a definite result).
   - **could not even be attempted** because the connection lacks a required capability, detected at **preflight** by the capability probe (e.g. the check needs APOC and APOC is absent) → `verdict:skipped` with `skip_reason:"unsupported"` — a coverage gap, excluded from the score, and marking the run `partial`.

   Rule of thumb: *preflight capability gaps are `skipped:unsupported`; failures once a check is attempted are `errored`.* `severity` stays a separate declared-config field so every finding is auditable.

   The exit code is the **first** matching row (order matters — the numeric codes are not a severity scale, so precedence is stated explicitly):

   | Order | Condition | Exit |
   | --- | --- | --- |
   | 1 | `run.status:failed` (the run could not execute) | **3** |
   | 2 | any check `verdict:fail`, or (`verdict:errored` and `severity:error`) | **1** |
   | 3 | `run.status:partial`; **or nothing was evaluated** (the universe is empty — selection matched no checks — or every check in it is `skipped`); or any check `verdict:warn`, or (`verdict:errored` and `severity:warn`) | **2** |
   | 4 | otherwise (a `complete` run with ≥ 1 executed check, all `pass` / `skipped`) | **0** |

   So a partial run that also has a hard failure exits **1** (the failure dominates the incompleteness); a partial run that is otherwise clean exits **2** (incomplete coverage never reads as success); an error-severity `errored` exits **1**, keeping `errored ≠ passed` at the CI gate, not only in the score. Row 3 also closes the **nothing-evaluated hole** — any run whose score denominator is empty evaluated nothing, so it exits **2** with `score:null` (rule 6). Two ways to get there: (a) every check in a non-empty universe is `skipped` (a generated-only suite awaiting approval, or all preflight-`unsupported`); (b) an **empty selection** — `--select`/`--suite` matched no checks, so `checks: []`. CI never reads either as a pass. Note (b) is *not* `partial`: an empty selection is a `complete` run over an empty universe (the user asked for a subset and got exactly it — there was no unintended loss), it just evaluated nothing, so it lands on row 3 via the "nothing evaluated" clause, not the `partial` clause. A run becomes `partial` only from *unintended* coverage loss within a non-empty selected universe: a time budget, an aborted run (`skip_reason:"not_run"`), or a preflight-`unsupported` check.

2. **Evidence is mandatory on `fail` and `warn`.** A finding without `evidence.elements` (or an explicit, capped, `truncated`-labeled list) is a bug. `compiled_query` is present once a check compiles and is `null` if it errored before compiling; it keeps `$param` placeholders — literal values live only in `params`.

3. **`errored` carries the fix.** When `verdict:errored`, the record carries `error: { code, message, fix }`. A `status:failed` run carries the same shape at `run.error`. This is the structured form of "every error message contains the fix."

4. **Estimates are labeled.** `estimate` is `false` for exact results, or an object `{ sample_size, population, confidence, ci: [lo, hi] }` for sampled results. A sample is never presented as exact.

5. **`checks[]` is the selected universe; `skipped` records why; `totals` derive from `checks[]`.** `checks[]` contains exactly the checks matching the active selection — the checks in the selected suites whose tags match `--select` (no filter ⇒ every loaded check). A check that does not match the selection is simply **absent** from the run, not recorded as skipped; `run.selection` documents the scope so a reader knows `totals` are scoped to it. A `skipped` check (which *is* in the universe) carries `skip_reason` (`generated` | `unsupported` | `not_run`) and is excluded from the score denominator. `totals` is a pure tally of `checks[]`: each verdict count in `totals` equals the count of `checks[]` with that verdict — nothing is counted that is not also a record. This gives reporters and CI one unambiguous universe for `--suite`/tag filtering.

6. **Score makes `errored` hurt, and is computed from `checks[]`.** With `w(error)=3`, `w(warn)=1` (weight by `check.severity`, not by verdict): `score = round(100 × Σ_{c | c.verdict=pass} w(c.severity) / Σ_{c | c.verdict ∈ {pass, fail, warn, errored}} w(c.severity))`. `skipped` is excluded from both sums; **an empty denominator (no check executed) makes `score` `null`** — "nothing was evaluated", never 100 (and that run exits 2, per the rule-1 table). The **same rule applies per suite**: `suites[].score` is computed identically over that suite's checks and is likewise present-but-nullable — `null` when the suite executed no check. `errored` sits in the denominator with zero credit. The score is derived from `checks[]`, not from aggregate `totals`. Weights are **hard-coded in v0** (not configurable — YAGNI until a second caller needs it). Because JSON Schema cannot express this arithmetic, `score`, per-suite `totals`, and `exit_code` are enforced by Pydantic model validators and unit tests over the derived invariants — not by the JSON Schema.

7. **Redaction is in the frozen contract from day 1.** `run.redaction.policy` is an enum fixed now — `none | mask | hash` — so v1 cloud ingestion can require `mask` or `hash` without a schema change. v0 **implements `none` only** and provides no configuration surface to change it, so `run.redaction.policy` is always `none` in v0 output (local-only, honoring "no data leaves the environment"). The enum reserves `mask`/`hash` for v1, when a **run-level config** (a CLI flag or `graphcheck.yml` — not a check-YAML field) will set them; a v0 binary asked for `mask`/`hash` rejects with a clear "reserved until v1" error rather than silently ignoring it. `params` is the only place literal input values are serialized; `evidence.elements` carry element IDs plus labels/types (never property values); `compiled_query` keeps `$param` placeholders.

8. **Partial runs are first-class and never look successful.** `run.status:partial` carries a `partial_reason` whenever coverage was *unintentionally* incomplete (time budget, aborted run, unreadable suite, a preflight-`unsupported` check). It is never silently truncated; per the rule-1 precedence it **never exits 0** — an otherwise-clean partial exits **2**, unless a hard failure among the checks it *did* run dominates (exit **1**).

### Field presence by verdict

Every check record always carries its identity/config fields (`id`, `suite_id`, `pattern`, `name`, `provenance`, `severity`, `verdict`, `expected` — the last being the declared assertion, known without executing). The remaining fields are execution-derived and are populated by `verdict`; a Pydantic `model_validator` enforces this table:

| Field | pass | fail / warn | errored | skipped |
| --- | --- | --- | --- | --- |
| `skip_reason` | null | null | null | **set** |
| `started_at`, `duration_ms` | set | set | set | **null** |
| `compiled_query` | set | set | set if it compiled, else null | **null** |
| `params` (resolved) | set | set | set if resolved, else null | **null** |
| `measured` | set | set | null | **null** |
| `estimate` | set | set | `false` | `false` |
| `evidence` | null | **set** | null | null |
| `error` | null | null | **set** | null |

A `skipped` check never executed, so every execution-derived field is null — only its declared config remains, and `expected` still shows what it *would* have asserted. An `errored` check *was* attempted, so timing is present but `measured` is null and `error` is set.

### Deliverables

- `docs/specs/SPEC-01-results-json.md` — the normative spec (the illustrative jsonc examples above appear here too).
- `docs/specs/results.schema.json` — JSON Schema generated from the Pydantic model (structural validation only).
- `tests/contracts/fixtures/results.{complete,partial,generated-only,failed}.json` — the **machine-valid** example artifacts (comment-free, real values): a mixed `complete` run; a `partial` run (with a `not_run` skip); a `generated-only` run (skipped check records, non-zero `totals.skipped`, `score:null`, exit 2); and a `failed` run. The **empty-selection** shape (`checks: []`, all-zero `totals`, `score:null`, exit 2) is a *different* JSON shape that shares only the score-null/exit-2 invariant — it, along with the other fine-grained edge cases (each field-presence null case, the coverage-status mapping, per-suite `score:null`), is built as an **in-memory model instance** rather than a fixture apiece.
- `src/graphcheck/contracts/results.py` — the Pydantic model (source of truth), with `model_validator`s for: the status-conditional run shape; the `partial_reason` **iff invariant** (non-null iff `status:partial`); the derived invariants (top-level `score` **and per-suite `suites[].score`**, both incl. the null-on-empty-denominator case, plus `totals` and `exit_code`); the **field-presence table by verdict** (evidence/error/skip_reason and execution-field nulls); and the **coverage-status invariant** — any check with `skip_reason ∈ {unsupported, not_run}` ⇒ `run.status:partial`, while `skip_reason:generated` does **not** force partial.
- `tests/contracts/test_results.py` — round-trips the **fixture files**, validates them against `results.schema.json`, and asserts every invariant directly: the score formula (incl. empty denominator ⇒ `score:null`, for the top-level *and* per-suite score); the `partial_reason` iff (null for `complete`/`failed`); the **exit-code precedence table** (including partial+fail → 1, error-severity `errored` → 1, and nothing-evaluated — all-`skipped` *and* empty-selection `checks:[]` — → 2); totals consistency; the field-presence table by verdict with its null cases; and the coverage-status invariant (`unsupported`/`not_run` ⇒ `partial`; `generated`-only and empty-selection stay `complete` but exit 2 with `score:null`).

## SPEC-02 — check YAML

A suite file under `checks/` uses pattern-keyed collections. Every level forbids unknown keys.

```yaml
suite: customer-360
defaults: { severity: error, tags: [production] }   # optional, applied to all checks in file

conformance:
  - id: cust-tax-id-present
    check: completeness            # selects the pack-owned schema that validates `with`
    with: { label: Customer, property: tax_id, threshold: 1.0 }
    tags: [pii, kyc]

competency:
  - id: cq-001
    question: "Which accounts does a customer control, directly or via intermediaries?"
    provenance: "KYC §4.2"
    query: |
      MATCH (c:Customer {id:$customer_id})-[:CONTROLS*1..4]->(a:Account)
      RETURN a.id AS account_id
    params: { customer_id: "$first-active-customer" }   # graph-relative token → zero-config
    expect: { rows: { min: 1, max: 200 }, columns: [account_id], unique: true }

  - id: cq-001-regression
    question: "CUST-1042 historically controls ACC-9001 and ACC-9002"
    provenance: "regression — caught a path bug 2026-Q1"
    query: |
      MATCH (c:Customer {id:$customer_id})-[:CONTROLS*1..4]->(a:Account)
      RETURN a.id AS account_id
    params: { customer_id: "CUST-1042" }
    expect: { contains: ["ACC-9001", "ACC-9002"] }      # regression overlay — opt-in

drift:
  - id: customer-count-stable
    metric: node_count
    target: { label: Customer }
    baseline: latest               # named snapshot under .graphcheck/baselines/ (default latest, or a pinned <date>)
    tolerance: { max_drop_pct: 10 }
    severity: warn
```

### Rules

1. **`generated: true` is inert, and a file marker dominates.** The marker is allowed at two scopes — top-level (the suite file) and per-check. Effective state is monotonic: `generated(check) = file.generated OR check.generated`. A child **cannot** un-generate a file marked generated; you approve a generated file by deleting its top-level marker (after which any per-check markers still gate individual checks). Any check whose effective state is generated is reported as `verdict:skipped` with `skip_reason:"generated"` (the field SPEC-01 defines) — the *loader* records the `generated` marker; the *engine* (C1) emits the `skipped` result at run time — until a human removes the marker. This enforces "generate proposes, human approves" in the pipeline, not in policy. **Validation precedes the marker:** a generated check is still fully schema-validated (duplicate-key rejection, envelope, and `with` via the registry) and only *then* marked generated — the marker does not short-circuit validation. This keeps every suite file a strictly-valid artifact, so removing a marker never surfaces a hidden schema error.

2. **Suite-level keys and `defaults`.** A suite file's allowed top-level keys are `suite` (id; defaults to the filename stem), `generated`, `defaults`, and the three pattern collections `conformance` / `competency` / `drift`; `extra='forbid'` applies at this level too, so an unknown top-level key errors. `defaults` accepts `severity` and `tags` only. Resolution: a check's effective `severity` = `check.severity` → `defaults.severity` → the built-in fallback `error` (an unspecified check is treated as blocking — fail-closed); a check's effective `tags` = the union of `defaults.tags` and `check.tags`.

3. **Strictness is two layers.** (a) A duplicate-key-rejecting YAML loader — a `SafeLoader` subclass that raises on a repeated mapping key — runs *before* Pydantic, because PyYAML's default `safe_load` silently keeps the last of duplicate keys and would let a `severity: error` / `severity: warn` collision pass unnoticed. (b) Pydantic `extra="forbid"` at every level rejects unknown keys. Together, duplicate keys *and* unknown keys are loud errors — which is what SPEC-02's acceptance test asserts.

4. **Envelope is frozen; `with` is a versioned pack payload.** SPEC-02 freezes the common per-check envelope vocabulary: `id`, `severity`, `tags`, `provenance`, `generated` (all patterns); `question`, `query`, `params`, `expect` (`rows` / `columns` / `unique` / `contains` / `equals` / `empty`) (competency); `check`, `with` (conformance); `metric`, `target`, `baseline`, `tolerance` (drift). (`defaults` is suite-level, not a per-check field — rule 2.) **A conformance check has no top-level `target`/`threshold`/etc — all of its type-specific configuration, `target` included, lives under the single `with:` key**, validated against a pack-owned schema selected by `check`. `target` is a top-level field only for drift. This keeps the pack boundary crisp: for conformance, everything outside the common vocabulary is inside `with`. The concrete validation mechanism is the next subsection. `additionalProperties:false` holds on the envelope *and* within each `with` variant, so strictness is preserved at the boundary; what is *not* frozen is the *set* of `with` variants — it grows via pack releases, consistent with the governing rule ("new capabilities as packs, not subsystems").

5. **Competency pattern is derived.** A competency check is classified `competency-regression` iff its `expect` contains value assertions (`contains` or `equals`); otherwise `competency-shape`. The engine writes the result into `pattern` in `results.json`. Shape and regression share one `competency:` list precisely so an SME can pin a regression overlay beside the shape check without moving it. Regression overlays require SME-supplied values and are opt-in; the canonical competency test uses shape, cardinality, and uniqueness only, so it works on an unfamiliar graph zero-config.

6. **Drift checks resolve a baseline.** `baseline` is **optional and defaults to `latest`** — a named snapshot under `.graphcheck/baselines/` (or a pinned `<date>`) produced by `graphcheck baseline set`. The current value is read live from the graph and compared against the baseline within `tolerance`. A drift check whose resolved baseline is missing (e.g. none has been pinned yet) → `verdict:errored` (never a silent pass).

### The `with` pack boundary, concretely

The envelope cannot enumerate the twelve conformance checks and still stay frozen, so `with` is validated through a registry, not a hard-coded union:

- **Registry.** `graphcheck.packs.REGISTRY: dict[str, type[pydantic.BaseModel]]` maps a conformance `check` name (e.g. `completeness`) to a strict (`extra="forbid"`) Pydantic model for its `with` payload. The built-in conformance pack registers its entries on import and exposes a `PACK_VERSION` constant.
- **Loading.** For a conformance check, the loader looks up `check` in `REGISTRY`. An unknown `check` is a loud error (`"unknown check type: …"`). A known one validates its `with` dict against the registered model (strict). So `check.py` validates strict `with` payloads without SPEC-02 enumerating them.
- **Two schema artifacts — one frozen, one generated.** `docs/specs/check.envelope.schema.json` is the **frozen** SPEC-02 surface: the envelope + suite-level structure, with `with` left as an opaque object. Separately, `docs/specs/check.schema.json` is a **generated build artifact** — the envelope combined with a `oneOf` of `{ "check": {const: <name>}, "with": <model_json_schema> }`, one branch per registry entry (each `with` schema from `model_json_schema()`) — stamped with the `PACK_VERSION` it was generated against, and regenerated whenever the pack changes.
- **Only `check.py` and the combined schema are full validators.** Because the envelope schema leaves `with` opaque, validating a suite against it *alone* will **not** catch unknown keys inside a `with` payload — it is a structural check of the common vocabulary only, never a strict pass on its own. Strict validation of `with` comes only from `check.py` (via the `REGISTRY`) or the generated combined `check.schema.json`. The loader always uses `check.py`; the combined schema is for external tools.
- **Version pin.** v0 ships exactly one built-in conformance pack, bundled with the release, so its `PACK_VERSION` tracks the graphcheck version — there is nothing to negotiate. `results.json.run.pack_version` records it for reproducibility and to pin which `with` schemas were in force. A declared `requires_pack` constraint is deferred until third-party packs exist (out of v0 scope).
- **Week 1 vs Week 2 (why this is not a freeze violation).** What SPEC-02 freezes in Week 1 is the *envelope schema + the protocol* (the `REGISTRY` interface, the generation step, the `pack_version` field) — none of which change in Week 2. The *combined* `check.schema.json` is explicitly a pack-versioned generated artifact: when C3 adds the twelve `with` models in Week 2, the pack version bumps and the combined schema regenerates. That is by design, not a post-freeze change to the frozen surface. In Week 1 the registry holds only the entries needed to validate the example suite; the combined schema shipped then is correct for `PACK_VERSION 0.1.0` and grows with the pack.

### Deliverables

- `docs/specs/SPEC-02-check-yaml.md` — the normative spec: the envelope, suite-level keys + `defaults` resolution, the `generated` precedence rule, the `with`/registry boundary, the two-schema split, and the baseline contract.
- `docs/specs/check.envelope.schema.json` — the **frozen** envelope + suite-structure schema (`with` left opaque). This is SPEC-02's frozen surface.
- `docs/specs/check.schema.json` — the **generated** combined schema (envelope + `oneOf` over the registry), stamped with `PACK_VERSION`; regenerated as the pack grows.
- `src/graphcheck/contracts/check.py` — the envelope Pydantic models, the duplicate-key-rejecting loader, and the `REGISTRY`-driven `with` validation (registry *entries* are C3's Week-2 work).
- `tests/contracts/fixtures/suite.valid.yml` + a set of `suite.invalid-*.yml` (unknown key, duplicate key, unknown `check`) — the machine-valid/invalid suite fixtures the tests load.
- `tests/contracts/test_check_validation.py` — loads those fixtures and proves: unknown keys error; duplicate keys error; a `generated:true` file marks all its checks `skipped` and a child cannot override it; `severity` fallback resolves (`check` → `defaults` → `error`); an unknown `check` errors; `suite.valid.yml` validates against the combined schema.

## Team direction

### GitHub issues (milestone `Week 1`)

| Title | Assignees | Acceptance (from §12) |
| --- | --- | --- |
| `[C2] Neo4j adapter + capability probe` | ghilda-graphora, kev-graphora | `graphcheck debug` reports server version, edition, APOC presence; integration test passes against Neo4j 4.4 and 5.x containers |
| `[Fixture] fraud-ring.cypher` | jayachandra-bit, jananik-graphora | Cypher at `tests/fixtures/fraud-ring.cypher` loads in under 10 seconds; ~5K nodes; 3 orphans, 1 cardinality violation, PII in 2 properties, 1 induced drift |

Tracking issues are also opened for Ezhil's own deliverables (scaffold, SPEC-01, SPEC-02) so the whole week is visible on the milestone. Each issue links the frozen contracts, states the decision-rights boundary (owners choose libraries within their component; Ezhil reviews), and carries the per-component definition of done (§14.1).

### Kickoff doc

`docs/week-1-kickoff.md`: goal, links to the two frozen contracts, the sequencing/dependency graph, per-owner deliverables and acceptance criteria, the §13 decision-rights reminder, the §14 definition-of-done and anti-slop rules, the no-AI-attribution rule, and the standup / Friday-demo rhythm. It ends with a ready-to-post `#general` kickoff message in the three-sentence house style.

## Ownership and definition of done

Per §14.1, each deliverable is done only when: reviewed by the named reviewer and merged; unit tests written and passing on CI; 80%+ line coverage on owned code; no `print`, no swallowed exceptions, no un-issued TODOs; a design note in `docs/components/`; a CHANGELOG entry; demoable against the fixture graph. The contracts (SPEC-01, SPEC-02) are additionally **frozen** at end of Week 1 — changes after that are cross-component escalations to Ezhil (§13).
