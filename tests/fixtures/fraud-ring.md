# Fraud Ring Fixture Manifest

## Purpose

This fixture graph is the primary verification dataset for GraphCheck Week 1.

It represents a realistic banking fraud graph with intentionally planted defects, sample PII, and an induced drift example. These are used to verify that GraphCheck can correctly detect data quality issues.

---

# Graph Summary

## Node Labels

- Customer
- Account
- Transaction

## Relationship Types

- OWNS
- CONTROLS
- SENT
- RECEIVED_BY

---

# Fixture Graph Counts

## Baseline Graph

File

- fraud-ring.baseline.cypher

Expected Counts

- Customers: **1507**
- Accounts: **2504**
- Transactions: **1000**

---

## Current Graph

File

- fraud-ring.cypher

Expected Counts

- Customers: **1327**
- Accounts: **2504**
- Transactions: **1000**

---

# Planted Defects

## 1. Orphan Accounts

Exactly three Account nodes have no relationships.

IDs

- ACC-ORPHAN-0001
- ACC-ORPHAN-0002
- ACC-ORPHAN-0003

Expected Result

GraphCheck should detect exactly **3 orphan accounts**.

---

## 2. Cardinality Violation

Account

- ACC-CARD-0001

is intentionally owned by two different customers.

Customers

- CUST-CARD-0001
- CUST-CARD-0002

Expected Result

GraphCheck should detect exactly **1 cardinality violation**.

---

# Planted PII

Each Customer contains two PII properties.

Properties

- email
- national_id

The **national_id** property intentionally alternates between

- Singapore NRIC-style values
- Indian Aadhaar-style values

Expected Result

The GraphCheck PII pack should detect both properties.

---

# Induced Drift

Two versions of the fixture graph are provided.

## Baseline

- fraud-ring.baseline.cypher

Contains

- 1500 base Customers

---

## Current

- fraud-ring.cypher

Contains

- 1320 base Customers

---

Expected Drift

The customer population intentionally decreases

```
1500 → 1320
```

which represents a **12% reduction**.

This change is expected to trigger GraphCheck's drift detection.


# Expected Verification Results

GraphCheck should report

- 3 orphan accounts
- 1 cardinality violation
- PII in the `email` property
- PII in the `national_id` property
- 12% customer-count drift between the baseline and current fixture graphs

---

# Files

This fixture includes

- fraud-ring.baseline.cypher
- fraud-ring.cypher
- load_graph.py
- test_fraud_ring.py
- fraud-ring.md