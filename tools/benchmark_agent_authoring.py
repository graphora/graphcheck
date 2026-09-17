"""Freeze and score first-submission agent YAML against the canonical fraud-ring fixture."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import platform
import runpy
import shutil
import subprocess
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from neo4j import GraphDatabase

from graphcheck import __version__
from graphcheck.connection_profiles import ConnectionProfile
from graphcheck.contracts.check import load_suite, load_suite_yaml
from graphcheck.engine import Engine, EngineConfig
from graphcheck.neo4j_adapter import Neo4jClient
from graphcheck.profiler import profile
from graphcheck.reporting.writer import results_json

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "tools" / "agent-authoring-benchmark"
FIXTURE = ROOT / "tests" / "fixtures" / "external" / "fraud-ring"
MODELS = {name: f"gpt-5.6-{name}" for name in ("luna", "terra", "sol")}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_digest(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def fixture_files() -> list[Path]:
    return [FIXTURE / "fixtures" / "fraud-ring" / name for name in ("seed.cypher", "schema.md")]


def collect() -> None:
    path = BENCHMARK / "submissions.json"
    if path.exists():
        raise ValueError("Submissions are already frozen")
    tasks = read_json(BENCHMARK / "task-set.json")["intents"]
    files = [BENCHMARK / "raw" / model / f"{task['id']}.yml" for model in MODELS for task in tasks]
    write_json(
        path,
        {
            "collected_at": datetime.now(UTC).isoformat(),
            "sha256": {p.relative_to(BENCHMARK).as_posix(): digest(p) for p in files},
        },
    )


def connection(uri: str) -> ConnectionProfile:
    if urlsplit(uri).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("The benchmark accepts only a disposable loopback Neo4j instance")
    return ConnectionProfile(uri=uri, user="neo4j", password="agent-benchmark", database="neo4j")


def graph_digest(client: Neo4jClient) -> str:
    """Hash all fixture labels, endpoints and properties, not just graph counts."""
    nodes = client.run_read(
        "MATCH (n) RETURN n.id AS id, labels(n) AS labels, properties(n) AS properties ORDER BY id"
    )
    rels = client.run_read(
        "MATCH (a)-[r]->(b) RETURN a.id AS source, type(r) AS type, b.id AS target, "
        "properties(r) AS properties ORDER BY source, type, target"
    )
    return hashlib.sha256(
        json.dumps([nodes, rels], sort_keys=True, default=str).encode()
    ).hexdigest()


def verify_oracles(client: Neo4jClient, task_set: dict) -> None:
    for task in task_set["intents"]:
        actual = client.run_read(task["oracle_query"], task.get("oracle_params", {}))
        if actual != task["oracle_rows"]:
            raise ValueError(f"Fixture oracle mismatch for {task['id']}: {actual!r}")


def prepare(uri: str, *, freeze: bool = True) -> None:
    """Seed an empty disposable database and freeze context before any model session starts."""
    context = BENCHMARK / "context"
    if freeze and (BENCHMARK / "manifest.json").exists():
        raise ValueError("Context is already frozen; use a new benchmark directory for a new trial")
    task_set = read_json(BENCHMARK / "task-set.json")
    if git("-C", str(FIXTURE), "rev-parse", "HEAD") != task_set["fixture_commit"]:
        raise ValueError("Fixture checkout does not match the pinned commit")
    if git("-C", str(FIXTURE), "status", "--porcelain"):
        raise ValueError("Fixture checkout must be clean")
    config = connection(uri)
    with (
        GraphDatabase.driver(uri, auth=(config.user, config.password)) as driver,
        driver.session(database=config.database) as session,
    ):
        actual = session.run("CALL dbms.components() YIELD versions RETURN versions[0]").single()
        if actual[0] != task_set["neo4j_version"]:
            raise ValueError(f"Expected Neo4j {task_set['neo4j_version']}, got {actual[0]}")
        if session.run("MATCH (n) RETURN count(n)").single()[0]:
            raise ValueError("Refusing to seed a nonempty database; no existing data is deleted")
        split = runpy.run_path(str(FIXTURE / "tests" / "cypher_utils.py"))["split_statements"]
        for statement in split(fixture_files()[0].read_text(encoding="utf-8")):
            session.run(statement).consume()
    client = Neo4jClient(config)
    try:
        verify_oracles(client, task_set)
        if not freeze:
            if graph_digest(client) != read_json(BENCHMARK / "manifest.json")["graph_digest"]:
                raise ValueError("Seeded fixture differs from the frozen graph")
            return
        context.mkdir(parents=True, exist_ok=True)
        for name, source in {
            "agents.md": ROOT / "docs/guides/agents.md",
            "llms.txt": ROOT / "docs/llms.txt",
            "check.schema.json": ROOT / "docs/schemas/check.schema.json",
            "schema.md": fixture_files()[1],
        }.items():
            shutil.copyfile(source, context / name)
        write_json(
            context / "intents.json",
            [{"id": task["id"], "intent": task["intent"]} for task in task_set["intents"]],
        )
        baseline = profile(client)
        if baseline.status != "complete":
            raise ValueError("Benchmark requires a complete fixture profile")
        write_json(context / "profile.json", baseline.model_dump(mode="json", by_alias=True))
        inputs = [BENCHMARK / "task-set.json", BENCHMARK / "PROMPT.md", *context.iterdir()]
        write_json(
            BENCHMARK / "manifest.json",
            {
                "created_at": datetime.now(UTC).isoformat(),
                "source_commit": git("rev-parse", "HEAD"),
                "fixture_commit": task_set["fixture_commit"],
                "neo4j_version": task_set["neo4j_version"],
                "graph_digest": graph_digest(client),
                "sha256": {p.relative_to(ROOT).as_posix(): digest(p) for p in inputs},
                "fixture_sha256_lf": {
                    p.relative_to(ROOT).as_posix(): fixture_digest(p) for p in fixture_files()
                },
                "models": MODELS,
                "reasoning_effort": "max",
            },
        )
    finally:
        client.close()


def activated(suite):
    """The user authorized fixture evaluation; only in-memory generated flags change."""
    checks = [
        check.model_copy(
            update={
                "generated": False,
                "spec": check.spec.model_copy(update={"generated": False}),
            }
        )
        for check in suite.checks
    ]
    return suite.model_copy(update={"checks": checks})


def score_one(path: Path, task: dict, validator, engine: Engine) -> tuple[dict, object]:
    row = {
        "intent": task["id"],
        "valid": False,
        "loads": False,
        "runs": False,
        "correct": False,
        "expected_verdict": task["expected_verdict"],
        "actual_verdict": None,
        "sha256": None,
        "generated": None,
        "errors": [],
    }
    result = None
    try:
        raw = path.read_bytes()
        row["sha256"] = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        row["errors"].append({"stage": "submission", "message": str(exc)})
        return row, result
    try:
        validator.validate(load_suite_yaml(text))
        row["valid"] = True
    except ValueError as exc:
        row["errors"].append({"stage": "valid", "message": str(exc)})
    except ValidationError as exc:
        row["errors"].append({"stage": "valid", "message": exc.message})
    try:
        suite = load_suite(text, source=str(path))
        row["loads"] = True
        if len(suite.checks) != 1 or suite.checks[0].id != task["id"] or suite.suite != task["id"]:
            raise ValueError("Submission must contain exactly the requested suite and check ID")
        row["generated"] = suite.checks[0].generated
        result = engine.run_suite(activated(suite), source_text=text)
        check = result.checks[0]
        row["actual_verdict"] = check.verdict.value
        row["runs"] = result.run.run_status.value == "complete" and check.verdict.value in {
            "pass",
            "fail",
            "warn",
        }
        row["correct"] = row["runs"] and check.verdict.value == task["expected_verdict"]
        if check.error:
            row["errors"].append({"stage": "runs", **check.error.model_dump(mode="json")})
    except (ValueError, IndexError) as exc:
        row["errors"].append({"stage": "runs" if row["loads"] else "loads", "message": str(exc)})
    if path.read_bytes() != raw:
        raise RuntimeError(f"Raw submission changed while scoring: {path}")
    return row, result


def score(uri: str, output: Path) -> None:
    if (output / "results.json").exists():
        raise ValueError("Results already exist; pass --output with a new directory for a replay")
    manifest = read_json(BENCHMARK / "manifest.json")
    for name, expected in manifest["sha256"].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f"Frozen benchmark input changed: {name}")
    for name, expected in manifest["fixture_sha256_lf"].items():
        if fixture_digest(ROOT / name) != expected:
            raise ValueError(f"Pinned fixture source changed: {name}")
    task_set = read_json(BENCHMARK / "task-set.json")
    submissions = read_json(BENCHMARK / "submissions.json")
    expected_paths = {
        f"raw/{model}/{task['id']}.yml" for model in MODELS for task in task_set["intents"]
    }
    if set(submissions["sha256"]) != expected_paths:
        raise ValueError("The benchmark requires exactly three complete ten-intent submissions")
    for name, expected in submissions["sha256"].items():
        if digest(BENCHMARK / name) != expected:
            raise ValueError(f"Raw submission changed since collection: {name}")
    validator = Draft202012Validator(
        read_json(BENCHMARK / "context" / "check.schema.json"),
        format_checker=FormatChecker(),
    )
    client = Neo4jClient(connection(uri))
    rows = []
    output.mkdir(parents=True, exist_ok=True)
    try:
        target, _, _ = client.probe()
        client.verify_read_only_credential()
        if target.server_version != manifest["neo4j_version"]:
            raise ValueError("Neo4j version differs from the frozen benchmark")
        verify_oracles(client, task_set)
        before = graph_digest(client)
        if before != manifest["graph_digest"]:
            raise ValueError("Current fixture differs from the frozen graph")
        engine = Engine(
            client,
            baselines={"latest": read_json(BENCHMARK / "context/profile.json")},
            config=EngineConfig(time_budget_s=30, max_concurrency=1),
        )
        for model, model_id in MODELS.items():
            for task in task_set["intents"]:
                path = BENCHMARK / "raw" / model / f"{task['id']}.yml"
                row, result = score_one(path, task, validator, engine)
                rows.append({"model": model_id, "effort": "max", **row})
                if result is not None:
                    write_json(
                        output / model / f"{task['id']}.json", json.loads(results_json(result))
                    )
        if graph_digest(client) != before:
            raise RuntimeError("Graph changed during scoring; results are not publishable")
        write_json(
            output / "results.json",
            {
                "scored_at": datetime.now(UTC).isoformat(),
                "source_commit": git("rev-parse", "HEAD"),
                "python": platform.python_version(),
                "graphcheck": __version__,
                "installed_distribution": version("graphcheck"),
                "neo4j_driver": version("neo4j"),
                "implementation_sha256_lf": {
                    name: fixture_digest(ROOT / name)
                    for name in (
                        "src/graphcheck/contracts/check.py",
                        "src/graphcheck/engine/evaluator.py",
                        "src/graphcheck/engine/compiler.py",
                        "src/graphcheck/engine/runner.py",
                        "src/graphcheck/neo4j_adapter.py",
                    )
                },
                "target": target.model_dump(mode="json"),
                "graph_unchanged": True,
                "graph_digest": before,
                "read_guard": str(client.read_guard_cache_info),
                "rows": rows,
            },
        )
    finally:
        client.close()
    columns = [
        "model",
        "effort",
        "intent",
        "valid",
        "loads",
        "runs",
        "correct",
        "expected_verdict",
        "actual_verdict",
        "generated",
        "sha256",
    ]
    with (output / "results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            {
                model: {
                    key: sum(r[key] for r in rows if r["model"] == model)
                    for key in ("valid", "loads", "runs", "correct")
                }
                for model in MODELS.values()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "seed", "collect", "score"])
    parser.add_argument("--uri", default="bolt://127.0.0.1:17689")
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    BENCHMARK = args.benchmark_dir.resolve()
    if args.action == "score":
        score(args.uri, args.output or BENCHMARK / "results")
    elif args.action == "collect":
        collect()
    else:
        prepare(args.uri, freeze=args.action == "prepare")
