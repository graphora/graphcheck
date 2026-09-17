# Fraud-Ring Fixture - Schema & Cardinality Contract

Owner: Jayachandra/Janani
Consumers: C1 (Engine tests), C2 (Connector integration tests), C3 (conformance checks), Example CQ library

This is the schema the fixture graph implements. Any check or CQ written
against `seed.cypher` should assume the shape below and nothing else.
If the shape changes, this file changes first, then the seed script.

## Node types

| Label | Key property | Other properties | Approx count |
|---|---|---|---|
| `Customer` | `id` (e.g. `CUST-1`) | `name`, `tax_id` | ~1,500 |
| `Account` | `id` (e.g. `ACC-1`) | `type` (`checking`|`savings`|`shell`), `balance` | ~2,500 |
| `Transaction` | `id` (e.g. `TXN-1`) | `amount`, `ts` | ~1,000 |

Total: approximately 5,000 nodes.

## Relationship types and intended cardinality

| Relationship | Direction | Intended cardinality | Meaning |
|---|---|---|---|
| `OWNS` | `(Customer)-[:OWNS]->(Account)` | **At most 1 owning Customer per Account** | Direct legal ownership |
| `CONTROLS` | `(Customer)-[:CONTROLS]->(Account)` and `(Account)-[:CONTROLS]->(Account)` | 0..N, chainable 1..4 hops | Effective control, possibly via shell accounts. This is what the canonical CQ (`cq-001`, see briefing) walks with `CONTROLS*1..4` |
| `SENT` | `(Account)-[:SENT]->(Transaction)` | Exactly 1 sending Account per Transaction | Transaction origin |
| `RECEIVED_BY` | `(Transaction)-[:RECEIVED_BY]->(Account)` | Exactly 1 receiving Account per Transaction | Transaction destination |

## Invariants this fixture is built to test

Each invariant below is tested independently, with its own disjoint set of
planted defect IDs. No defect ID is shared across invariants.

1. **No orphans**: every `Account` should have at least one relationship
   of any kind (`OWNS`, `CONTROLS`, `SENT`, or `RECEIVED_BY`).
   Planted violations (exactly 3, zero relationships each):
   `ACC-ORPHAN-0001`, `ACC-ORPHAN-0002`, `ACC-ORPHAN-0003`.

2. **Cardinality**: every `Account` should have at most one incoming
   `OWNS` relationship. This invariant is checked as "owner_count > 1",
   scoped only to accounts with multiple owners - zero-owner accounts
   are out of scope for this check and belong to invariant 1 instead.
   Planted violation (exactly 1, owned by 2 customers):
   `ACC-CARD-0001` (owned by `CUST-CARD-0001` and `CUST-CARD-0002`).

   Note: in this fixture's current seed data, every Account in the
   ACC-1..ACC-2500 range receives exactly one OWNS edge, so there are no
   zero-owner accounts outside the orphan set above. If a future fixture
   variant ever adds an account with zero OWNS but some other
   relationship (so it is not a true orphan), that account would need
   its own explicit invariant, since it would not be caught by either
   check as currently scoped.

3. **PII presence**: every base Customer has three synthetic PII-shaped
   fields: `tax_id`, `email` (`customerN@example.com`), and `national_id`
   (alternating Singapore NRIC-style and Indian Aadhaar-style values, so
   the PII pack has both formats to detect). See manifest.yml `pii_fields`
   for the full declared list.

4. **Drift**: `seed-drifted.cypher` is a second, real fixture state
   representing a documented 12% Customer-count reduction since the
   baseline was captured (1,500 to 1,320, a drop of 180). Accounts,
   Transactions, planted defects, and PII fields are unchanged between
   the two states. See manifest.yml `drift` for the exact pinned figures.
   A conformance check should flag this drop as meaningful drift.

## Why this shape

- `OWNS` allows at most one owning Customer per Account - the deliberate
  cardinality violation (`ACC-CARD-0001`) breaks exactly this rule, because
  it is the simplest, most demo-legible invariant to violate and explain.
- `CONTROLS` is intentionally chainable so the CQ pattern in the main
  briefing (`Customer -[:CONTROLS*1..4]-> Account`) has real multi-hop paths
  to walk, including through `shell`-type accounts.
- `SENT`/`RECEIVED_BY` around a `Transaction` node (rather than a direct
  `Account -> Account` edge) exists so transaction-level properties (`amount`,
  `ts`) have somewhere to live, and so hub-outlier detection (C3) has a
  degree distribution to look at on `Transaction`.

**Naming note:** informally this relationship is sometimes called
`TRANSACTED_WITH`. We use `SENT`/`RECEIVED_BY` via an intermediate
`Transaction` node instead of a single direct edge, for the reason above.
Functionally equivalent; flagging so nobody re-derives this from scratch.