from dataclasses import dataclass
from pathlib import Path

from neo4j import GraphDatabase

from graphcheck.connection_profiles import ConnectionProfile


@dataclass
class LoadStats:
    nodes: int
    relationships: int
    statements: int


def split_cypher_statements(cypher: str) -> list[str]:
    statements = []
    current = []

    in_single_quote = False
    in_double_quote = False
    in_comment = False

    i = 0

    while i < len(cypher):
        char = cypher[i]
        next_char = cypher[i + 1] if i + 1 < len(cypher) else ""

        # Start of a // comment
        if (
            not in_single_quote
            and not in_double_quote
            and not in_comment
            and char == "/"
            and next_char == "/"
        ):
            in_comment = True
            i += 2
            continue

        # End of comment
        if in_comment:
            if char == "\n":
                in_comment = False
            i += 1
            continue

        # Toggle single-quoted string
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote

        # Toggle double-quoted string
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote

        # Split only if not inside a string
        if char == ";" and not in_single_quote and not in_double_quote:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(char)

        i += 1

    statement = "".join(current).strip()
    if statement:
        statements.append(statement)

    return statements


def load_graph(profile: ConnectionProfile, cypher_path: Path):
    driver = GraphDatabase.driver(
        profile.uri,
        auth=(profile.user, profile.password),
    )

    try:
        cypher = cypher_path.read_text(encoding="utf-8")

        statements = split_cypher_statements(cypher)

        executed = 0

        with driver.session(database=profile.database) as session:
            for statement in statements:
                session.run(statement)
                executed += 1

            nodes = session.run("MATCH (n) RETURN count(n) AS total").single()["total"]

            relationships = session.run("MATCH ()-[r]->() RETURN count(r) AS total").single()[
                "total"
            ]

        return LoadStats(
            nodes=nodes,
            relationships=relationships,
            statements=executed,
        )

    finally:
        driver.close()
