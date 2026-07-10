import sys
from pathlib import Path
from neo4j import GraphDatabase

# Update these values for your local Neo4j instance
URI = "bolt://localhost:7687"
USERNAME = "neo4j"
PASSWORD = "password"


def load_fixture(cypher_path: Path):
    driver = GraphDatabase.driver(
        URI,
        auth=(USERNAME, PASSWORD)
    )

    cypher = cypher_path.read_text(encoding="utf-8")

    statements = [
        statement.strip()
        for statement in cypher.split(";")
        if statement.strip()
    ]

    with driver.session() as session:
        for statement in statements:
            session.run(statement)

    driver.close()

    print(f"Loaded {cypher_path.name} successfully.")


if __name__ == "__main__":
    path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path("fraud-ring.cypher")
    )

    load_fixture(path)