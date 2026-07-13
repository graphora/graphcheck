from pathlib import Path

from neo4j import GraphDatabase

from graphcheck.connection_profiles import ConnectionProfile


def load_graph(profile: ConnectionProfile, cypher_path: Path):
    driver = GraphDatabase.driver(
        profile.uri,
        auth=(profile.user, profile.password),
    )

    cypher = cypher_path.read_text(encoding="utf-8")

    statements = [statement.strip() for statement in cypher.split(";") if statement.strip()]

    with driver.session(database=profile.database) as session:
        for statement in statements:
            session.run(statement)

    driver.close()

    print(f"Loaded {cypher_path.name} successfully.")
