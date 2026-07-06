# SPEC-02 — check YAML

*Frozen for v0.* A suite file under `checks/` declares the checks GraphCheck runs. It uses pattern-keyed collections; every level forbids unknown keys.

**Source of truth:** the Pydantic models + loader at `src/graphcheck/contracts/check.py`. **Machine-valid example:** `tests/contracts/fixtures/suite.valid.yml`.

## Shape

```yaml
suite: customer-360                       # optional; defaults to the filename stem
defaults: { severity: error, tags: [production] }   # optional; severity + tags only

conformance:
  - id: cust-tax-id-present
    check: completeness                   # selects the pack-owned schema that validates `with`
    with: { label: Customer, property: tax_id, threshold: 1.0 }
    tags: [pii, kyc]

competency:
  - id: cq-001
    question: "Which accounts does a customer control?"
    query: "MATCH (c:Customer {id:$id})-[:CONTROLS]->(a:Account) RETURN a.id AS account_id"
    params: { id: "$first-active-customer" }        # graph-relative token -> zero-config
    expect: { rows: { min: 1, max: 200 }, columns: [account_id], unique: true }

  - id: cq-001-regression
    query: "..."
    expect: { contains: ["ACC-9001"] }              # regression overlay -> opt-in

drift:
  - id: customer-count-stable
    metric: node_count
    target: { label: Customer }
    baseline: latest                                # optional; defaults to latest
    tolerance: { max_drop_pct: 10 }
    severity: warn
```

## Rules

1. **`generated: true` is inert, and a file marker dominates.** Allowed at file and per-check scope; effective state is monotonic (`file OR check`) — a child cannot un-generate a generated file. The loader records the marker; the engine (C1) emits the resulting `skipped` / `skip_reason:"generated"` result at run time. A generated check is still fully validated first.
2. **Suite-level keys:** `suite`, `generated`, `defaults`, and `conformance` / `competency` / `drift`. `defaults` accepts `severity` and `tags` only. Resolution: `severity` = check → defaults → `error` (fail-closed); `tags` = union of defaults + check.
3. **Strictness is two layers:** a duplicate-key-rejecting `SafeLoader` subclass (PyYAML's `safe_load` silently keeps the last duplicate) runs before Pydantic, and `extra="forbid"` rejects unknown keys — so duplicate *and* unknown keys are loud errors.
4. **Envelope frozen; `with` is a versioned pack payload.** The frozen per-check envelope: `id`, `severity`, `tags`, `provenance`, `generated` (all); `question`, `query`, `params`, `expect` (competency); `check`, `with` (conformance, both required); `metric`, `target`, `baseline`, `tolerance` (drift). A conformance check's type-specific config lives entirely under `with`, validated against a pack-owned schema selected by `check`.
5. **Competency pattern is derived:** `expect` with `contains`/`equals` → `competency-regression`; else `competency-shape`.
6. **Drift baseline** is optional, defaults to `latest`; a missing resolved baseline errors at run time (never a silent pass).

## The `with` pack boundary

`with` is validated through a registry, not a hard-coded union:

- **Registry:** `graphcheck.packs.REGISTRY: dict[str, type[BaseModel]]` maps a `check` name to a strict (`extra="forbid"`) model for its `with` payload; the built-in pack exposes `PACK_VERSION`.
- **Loading:** the loader looks up `check` in `REGISTRY` (unknown → loud error), validates `with` against the model, and keeps the normalized result (so pack defaults like `threshold=1.0` survive onto the loaded `spec.with_`).
- **Two schema artifacts:** `docs/specs/check.envelope.schema.json` is the **frozen** envelope (with `with` opaque — *not* a full validator on its own). `docs/specs/check.schema.json` is a **generated** combined schema (envelope + a `oneOf` over the registry, stamped with `x-pack-version`), regenerated as the pack grows. Full `with` validation comes only from `check.py` or the combined schema. v0 pack models must be flat (ref-free); the generator rejects any that emit `$defs`.
- **Week 1 vs Week 2:** SPEC-02 freezes the envelope + the protocol (registry interface, generation step, `pack_version`). The twelve `with` models are C3's Week-2 work.

## Loader output

`load_suite(text, *, source=None) -> Suite`. Each `LoadedCheck` carries resolved `severity`/`tags`/`pattern`/`provenance`, effective `generated`, and **`spec`** — the fully-validated pattern payload the engine executes from (nothing discarded). Duplicate check ids within a suite are rejected.

## Deliverables

- `src/graphcheck/packs/__init__.py` — the pack registry + built-in `completeness`.
- `src/graphcheck/contracts/check.py` — the duplicate-key loader, strict envelope + `Expect` models, defaults/generated resolution, and `REGISTRY`-driven `with` validation.
- `docs/specs/check.envelope.schema.json` (frozen) + `docs/specs/check.schema.json` (generated, pack-versioned).
- `tests/contracts/fixtures/suite.valid.yml` + `suite.invalid-*.yml`, and `tests/contracts/test_check_validation.py`.
