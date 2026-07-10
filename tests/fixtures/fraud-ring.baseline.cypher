// ============================================================================
// fraud-ring.cypher
// Fixture graph for GraphCheck v0 — see SCHEMA.md for the full contract.
//
// IDEMPOTENT VIA MERGE: every node and relationship below is created with
// MERGE, keyed on its `id` (or, for relationships, on the pair of node ids
// plus type). Running this file 1 time or 50 times in a row leaves the
// graph in the identical end state — nothing is ever wiped. This matters
// because other tests (e.g. C2's connector tests) may share the same
// database instance; a wipe would destroy their state too.
//
// Target: ~5,000 nodes, loads in under 10 seconds on a local Neo4j instance.
//
// DENSE SUB-CLUSTERS: rather than spreading CONTROLS/transaction edges
// evenly across all accounts, we carve out a handful of small, tight
// account rings (a "fraud ring" in the literal sense) where every account
// in the cluster controls and transacts with every other account in the
// same cluster. This is what makes the graph's problems visually obvious
// in the demo (§10 of the briefing) rather than diffuse.
//
// PLANTED DEFECTS — see section 3 for exact IDs. Only two defect types are
// in scope this week:
//   - 3 orphan Account nodes (no relationships at all)
//   - 1 cardinality violation (an Account owned by 2 Customers instead of 1)
// PII and drift defects are Janani's follow-up scope (see SCHEMA.md).
// ============================================================================

// ---- 0. Constraints (required for MERGE-by-id to be fast and safe) -------
CREATE CONSTRAINT customer_id IF NOT EXISTS FOR (c:Customer) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT account_id IF NOT EXISTS FOR (a:Account) REQUIRE a.id IS UNIQUE;
CREATE CONSTRAINT transaction_id IF NOT EXISTS FOR (t:Transaction) REQUIRE t.id IS UNIQUE;

// ---- 1. Customers (1,500) ------------------------------------------------
UNWIND range(1, 1500) AS i
MERGE (c:Customer {id: 'CUST-' + toString(i)})
ON CREATE SET
  c.name = 'Customer ' + toString(i),
  c.tax_id = toString(100000000 + i);

// ---- 1b. Planted PII --------------------------------------------
// Adds two PII properties to every Customer:
//   - email
//   - national_id
// national_id alternates between Singapore NRIC-style and
// Indian Aadhaar-style values so the PII pack has both
// formats to detect during testing
UNWIND range(1, 1500) AS i
MATCH (c:Customer {id: 'CUST-' + toString(i)})
SET c.email = 'customer' + toString(i) + '@example.com',
    c.national_id = CASE WHEN i % 2 = 0
      THEN 'S' + toString(1000000 + i) + 'D'
      ELSE toString(200000000000 + i) END;

// ---- 2. Accounts (2,500) — mostly 'checking'/'savings', some 'shell' -----
UNWIND range(1, 2500) AS i
MERGE (a:Account {id: 'ACC-' + toString(i)})
ON CREATE SET
  a.type = CASE WHEN i % 10 = 0 THEN 'shell'
                WHEN i % 2 = 0 THEN 'savings'
                ELSE 'checking' END,
  a.balance = (i * 137) % 50000;

// ---- 3. Transactions (1,000) ---------------------------------------------
UNWIND range(1, 1000) AS i
MERGE (t:Transaction {id: 'TXN-' + toString(i)})
ON CREATE SET
  t.amount = (i * 91) % 10000,
  t.ts = datetime({epochSeconds: 1750000000 + i * 3600});

// ---- 4. OWNS: EVERY Account (1-2500) gets exactly one owning Customer ----
// Cycles through the 1,500 customers so every account, not just the first
// 1,500, is guaranteed an owner. This is what makes "exactly one OWNS per
// Account" a real, verified invariant across the whole account range,
// rather than true for some accounts by luck. (An earlier version of this
// script only reliably owned accounts 1-1500 and left most of 1501-2500
// unowned — a real bug, not a design choice. Fixed here.)
UNWIND range(1, 2500) AS i
MATCH (a:Account {id: 'ACC-' + toString(i)})
MATCH (c:Customer {id: 'CUST-' + toString(((i - 1) % 1500) + 1)})
MERGE (c)-[:OWNS]->(a);

// Bonus CONTROLS (not OWNS, so the "exactly one owner" invariant is never
// at risk): about a third of customers also control a second account
// beyond what they own.
MATCH (c:Customer)
WHERE toInteger(split(c.id, '-')[1]) % 3 = 0
MATCH (a:Account {id: 'ACC-' + toString(((toInteger(split(c.id, '-')[1]) + 1499) % 2500) + 1)})
MERGE (c)-[:CONTROLS]->(a);

// ---- 5. CONTROLS: baseline chainable control edges (feeds CQ*1..4) -------
// Direct Customer -> Account control (roughly 1 in 5 customers control an
// account beyond what they own)
MATCH (c:Customer)
WHERE toInteger(split(c.id, '-')[1]) % 5 = 0
MATCH (a:Account {id: 'ACC-' + toString(2000 + (toInteger(split(c.id, '-')[1]) % 400))})
MERGE (c)-[:CONTROLS]->(a);

// Account -> Account control chains through shell accounts (1-3 hops),
// spread across the whole graph (the "background" control traffic)
MATCH (shell:Account {type: 'shell'})
WITH shell, toInteger(split(shell.id, '-')[1]) AS n
MATCH (target:Account {id: 'ACC-' + toString((n + 7) % 2500 + 1)})
WHERE target.id <> shell.id
MERGE (shell)-[:CONTROLS]->(target);

// ---- 5b. DENSE SUB-CLUSTERS: 5 tight fraud rings of 8 accounts each ------
// Ring k occupies accounts ACC-2401..ACC-2408 shifted by k*8, all already
// created in step 2 (they sit in the 2401-2440 range). Every account in a
// ring points to every other account in the same ring via CONTROLS, and a
// dedicated Customer "ringleader" controls the whole cluster directly.
// This is what a genuinely dense fraud ring looks like: a clique, not a chain.
UNWIND range(0, 4) AS ring_num
MERGE (leader:Customer {id: 'CUST-RING-LEADER-' + toString(ring_num)})
ON CREATE SET leader.name = 'Ring Leader ' + toString(ring_num), leader.tax_id = '999' + toString(ring_num)
WITH ring_num, leader, [j IN range(1, 8) | 2401 + (ring_num * 8) + (j - 1)] AS ring_account_nums
UNWIND ring_account_nums AS acc_num
MATCH (a:Account {id: 'ACC-' + toString(acc_num)})
MERGE (leader)-[:CONTROLS]->(a)
WITH ring_num, ring_account_nums, a, acc_num
UNWIND ring_account_nums AS other_num
WITH a, acc_num, other_num
WHERE other_num <> acc_num
MATCH (b:Account {id: 'ACC-' + toString(other_num)})
MERGE (a)-[:CONTROLS]->(b);

// ---- 6. SENT / RECEIVED_BY: wire transactions between accounts ----------
UNWIND range(1, 1000) AS i
MATCH (t:Transaction {id: 'TXN-' + toString(i)})
MATCH (sender:Account {id: 'ACC-' + toString((i * 3) % 2500 + 1)})
MATCH (receiver:Account {id: 'ACC-' + toString((i * 7) % 2500 + 1)})
WHERE sender.id <> receiver.id
MERGE (sender)-[:SENT]->(t)
MERGE (t)-[:RECEIVED_BY]->(receiver);

// ============================================================================
// 3. PLANTED DEFECTS — documented IDs (this week's scope only)
// ============================================================================

// --- Defect A, B, C: 3 orphan Account nodes (zero relationships) ---------
// IDs: ACC-ORPHAN-0001, ACC-ORPHAN-0002, ACC-ORPHAN-0003
// MERGE-by-id means re-running this file never creates duplicates and
// never accidentally connects these to anything else in the graph (they
// use a distinct id namespace, so no MATCH clause above can ever touch them).
MERGE (o1:Account {id: 'ACC-ORPHAN-0001'}) ON CREATE SET o1.type = 'checking', o1.balance = 100;
MERGE (o2:Account {id: 'ACC-ORPHAN-0002'}) ON CREATE SET o2.type = 'savings',  o2.balance = 250;
MERGE (o3:Account {id: 'ACC-ORPHAN-0003'}) ON CREATE SET o3.type = 'shell',    o3.balance = 0;

// --- Defect D: 1 cardinality violation -------------------------------------
// ID: ACC-CARD-0001, owned by TWO customers (CUST-CARD-0001 and
// CUST-CARD-0002) instead of exactly one. This breaks the "exactly one
// OWNS per Account" invariant documented in SCHEMA.md.
MERGE (a:Account {id: 'ACC-CARD-0001'}) ON CREATE SET a.type = 'checking', a.balance = 5000
MERGE (c1:Customer {id: 'CUST-CARD-0001'}) ON CREATE SET c1.name = 'Cardinality Violator A', c1.tax_id = '000000001'
MERGE (c2:Customer {id: 'CUST-CARD-0002'}) ON CREATE SET c2.name = 'Cardinality Violator B', c2.tax_id = '000000002'
MERGE (c1)-[:OWNS]->(a)
MERGE (c2)-[:OWNS]->(a);

// ============================================================================
// End of seed. Expect ~5,011 nodes total: 5,000 base (1,500 Customers +
// 2,500 Accounts + 1,000 Transactions) + 3 orphan accounts + 1 cardinality
// account + 2 cardinality customers + 5 ring-leader customers (one per
// dense sub-cluster, added in step 5b) = 5,011.
// ============================================================================
