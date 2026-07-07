# Week 1 kickoff — contracts and connection

**Goal:** lock the two frozen contracts and get Neo4j connecting, so the engine (Week 2) has a stable target.

## Frozen contracts (read these first)

- `docs/specs/SPEC-01-results-json.md` — the `results.json` output contract. Everything renders from it.
- `docs/specs/SPEC-02-check-yaml.md` — the check YAML input contract.

Both are frozen for v0. Changing either is a cross-contract escalation to Ezhil (§13). The Pydantic models under `src/graphcheck/contracts/` are the source of truth; the JSON Schemas are generated.

## Sequencing

The contracts land first because the rest of Week 1 references them: the connector (C2) emits results shaped by SPEC-01, and the fixture graph is designed to trip the checks SPEC-02 expresses. Order: **scaffold + governance → contracts → connector + fixture**.

## Deliverables and owners

| Deliverable | Owner | Acceptance |
| --- | --- | --- |
| Repo scaffold + governance | Ezhil | Done — packaging, minimal CLI, CI (ruff + pytest matrix 3.12–3.13), Apache-2.0, CODEOWNERS, PR template, ruleset. |
| SPEC-01 `results.json` | Ezhil | Done — models + generated schema + fixtures + tests. |
| SPEC-02 check YAML | Ezhil | Done — envelope + loader + registry + generated schemas + tests. |
| `[C2]` Neo4j adapter + capability probe | Ghilda / Keval | `graphcheck debug` reports server version, edition, APOC presence; integration test passes against Neo4j 4.4 and 5.x containers. `graphcheck debug` is the one Week-1 CLI command; the rest of the surface is Week 3 (C6). |
| `[Fixture]` `fraud-ring.cypher` | Jayachandra / Janani | `tests/fixtures/fraud-ring.cypher` loads in under 10s; ~5K nodes; 3 orphans, 1 cardinality violation, PII in 2 properties, 1 induced drift. |

Tracked as GitHub issues on the **Week 1** milestone.

## How we work

- **Branches:** `development` is the default; PR into it. `main` holds release tags. Direct pushes are blocked by the ruleset.
- **Decision rights (§13):** reversible in under half a day → decide yourself; over two days or any cross-contract change → escalate to Ezhil.
- **Definition of done:** the PR template checklist — tests, coverage ≥ 80%, no `print`, no swallowed exceptions, CHANGELOG entry, design note.
- **Anti-slop:** no abstractions without three callers, no "just in case" params, no comments restating code.
- **Attribution:** none — plain human voice in commits, PRs, issues, docs.
- **Cadence:** daily three-sentence standup (shipped / shipping / one blocker); Friday demo + retro against the fixture graph.

## Kickoff message

> Week 1 is live. The two frozen contracts — `results.json` (SPEC-01) and check YAML (SPEC-02) — are merged under `docs/specs/`, with generated schemas and tests; build against those, don't reshape them. Your issues are on the Week 1 milestone: Ghilda/Keval on the Neo4j adapter + `graphcheck debug`, Jayachandra/Janani on the fraud-ring fixture. PR into `development`, keep CI green, three-sentence standup daily.
