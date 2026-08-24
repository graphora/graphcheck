from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
TEST_FILE = "tests/integration/test_hostile_graphs.py"
TESTS = {
    "llm-kg-builder": "llm_kg_builder",
    "public-scale": "public_scale",
    "neo4j-4.4-cluster": "neo4j_44_cluster",
    "apoc-less": "apoc_less",
    "empty": "empty_graph",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run GraphCheck's hostile graph certification set."
    )
    parser.add_argument("--case", choices=["all", "fast", *TESTS], default="fast")
    parser.add_argument("--dataset", type=Path, help="Use a cached email-EuAll.txt.gz artifact.")
    args = parser.parse_args()
    env = {
        **os.environ,
        "GRAPHCHECK_NEO4J_INTEGRATION": "1",
        "GRAPHCHECK_NEO4J_TARGET": "lts-cypher-5",
    }
    selected = args.case
    if selected in {"all", "public-scale"}:
        env["GRAPHCHECK_HOSTILE_SCALE"] = "1"
    if selected in {"all", "neo4j-4.4-cluster"}:
        env["GRAPHCHECK_HOSTILE_NEO4J44"] = "1"
    if args.dataset is not None:
        env["GRAPHCHECK_HOSTILE_DATASET"] = str(args.dataset.resolve())
    if "junitxml" in env.get("PYTEST_ADDOPTS", ""):
        (ROOT / ".hostile-artifacts").mkdir(exist_ok=True)
    command = [sys.executable, "-m", "pytest", TEST_FILE, "-v"]
    if selected == "fast":
        command.extend(["-k", "empty_graph or llm_kg_builder or apoc_less"])
    elif selected != "all":
        command.extend(["-k", TESTS[selected]])
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
