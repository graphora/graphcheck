"""Load the fraud-ring performance shape into an empty, dedicated benchmark database."""

from __future__ import annotations

import argparse
import os
import time

from neo4j import GraphDatabase, Query

SIZES = (100_000, 1_000_000, 10_000_000)


def generate(driver, database: str, nodes: int, batch_size: int = 5000) -> None:
    if nodes not in SIZES or not database.startswith("graphcheck-benchmark-"):
        raise ValueError("Use a supported size and a graphcheck-benchmark-* database.")
    with driver.session(database=database) as session:
        if session.run("MATCH (n) RETURN count(n) AS count").single()["count"]:
            raise ValueError("Benchmark database must be empty; existing data is never deleted.")
        for label in ("Customer", "Account", "Transaction"):
            session.run(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            ).consume()
        customers, accounts, transactions = nodes * 3 // 10, nodes // 2, nodes // 5
        jobs = [
            (
                "customers",
                customers - 5,
                "CREATE (:Customer {id: 'CUST-' + toString(i), "
                "name: 'Customer ' + toString(i), tax_id: toString(100000000 + i), "
                "email: 'customer' + toString(i) + '@example.com', "
                "national_id: CASE WHEN i % 2 = 0 THEN 'S' + toString(1000000 + i) + 'D' "
                "ELSE toString(200000000000 + i) END})",
            ),
            (
                "accounts",
                accounts,
                "CREATE (:Account {id: 'ACC-' + toString(i), "
                "type: CASE WHEN i % 10 = 0 THEN 'shell' WHEN i % 2 = 0 THEN 'savings' "
                "ELSE 'checking' END, balance: (i * 137) % 50000})",
            ),
            (
                "transactions",
                transactions,
                "CREATE (:Transaction {id: 'TXN-' + toString(i), "
                "amount: (i * 91) % 10000, ts: datetime({epochSeconds: 1750000000 + i * 3600}), "
                "settled_at: datetime({epochSeconds: 1750003600 + i * 3600})})",
            ),
            (
                "ownership",
                accounts,
                "MATCH (c:Customer {id: 'CUST-' + "
                "toString(((i - 1) % $customers) + 1)}), "
                "(a:Account {id: 'ACC-' + toString(i)}) CREATE (c)-[:OWNS]->(a)",
            ),
            (
                "payments",
                transactions,
                "WITH i, (i * 3) % $accounts + 1 AS sender, "
                "(i * 7) % $accounts + 1 AS raw_receiver "
                "WITH i, sender, CASE WHEN sender = raw_receiver THEN raw_receiver % $accounts + 1 "
                "ELSE raw_receiver END AS receiver "
                "MATCH (t:Transaction {id: 'TXN-' + toString(i)}), "
                "(a:Account {id: 'ACC-' + toString(sender)}), "
                "(b:Account {id: 'ACC-' + toString(receiver)}) "
                "CREATE (a)-[:SENT]->(t)-[:RECEIVED_BY]->(b)",
            ),
        ]
        started = time.monotonic()
        for name, count, query in jobs:
            for start in range(1, count + 1, batch_size):
                session.run(
                    Query("UNWIND range($start, $end) AS i " + query, timeout=120),
                    start=start,
                    end=min(start + batch_size - 1, count),
                    customers=customers - 5,
                    accounts=accounts,
                ).consume()
            print(f"Loaded {name}: {count:,} ({time.monotonic() - started:.1f}s)", flush=True)
        per_hub = accounts // 50
        for hub in range(5):
            session.run(
                "CREATE (:Customer {id: $id, name: $name, tax_id: $tax, "
                "email: $email, national_id: $national})",
                id=f"CUST-HUB-{hub}",
                name=f"Hub Customer {hub}",
                tax=f"999{hub}",
                email=f"hub{hub}@example.com",
                national=f"999{hub}0000000",
            ).consume()
            for start in range(hub * per_hub + 1, (hub + 1) * per_hub + 1, batch_size):
                session.run(
                    Query(
                        "UNWIND range($start, $end) AS i "
                        "MATCH (c:Customer {id: $id}), (a:Account {id: 'ACC-' + toString(i)}) "
                        "CREATE (c)-[:CONTROLS]->(a)",
                        timeout=120,
                    ),
                    start=start,
                    end=min(start + batch_size - 1, (hub + 1) * per_hub),
                    id=f"CUST-HUB-{hub}",
                ).consume()
        print(f"Loaded {nodes:,} nodes in {time.monotonic() - started:.1f}s", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, choices=SIZES, required=True)
    args = parser.parse_args()
    database = f"graphcheck-benchmark-{args.nodes}"
    with GraphDatabase.driver(
        os.environ["GRAPHCHECK_PERFORMANCE_URI"],
        auth=(
            os.environ.get("GRAPHCHECK_PERFORMANCE_ADMIN_USER", "neo4j"),
            os.environ["GRAPHCHECK_PERFORMANCE_PASSWORD"],
        ),
    ) as driver:
        with driver.session(database="system") as session:
            session.run(f"CREATE DATABASE `{database}` IF NOT EXISTS WAIT 30 SECONDS").consume()
        generate(driver, database, args.nodes)


if __name__ == "__main__":
    main()
