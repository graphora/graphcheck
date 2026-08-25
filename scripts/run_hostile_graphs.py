from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
TEST_FILE = "tests/integration/test_hostile_graphs.py"
CASES = yaml.safe_load((ROOT / "tests/integration/hostile/cases.yml").read_text(encoding="utf-8"))[
    "cases"
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run GraphCheck's hostile graph certification set."
    )
    parser.add_argument("--case", choices=["all", "fast", *CASES], default="fast")
    parser.add_argument("--dataset", type=Path, help="Use a cached email-EuAll.txt.gz artifact.")
    args = parser.parse_args()
    env = {
        **os.environ,
        "GRAPHCHECK_NEO4J_INTEGRATION": "1",
        "GRAPHCHECK_NEO4J_TARGET": "lts-cypher-5",
    }
    selected = args.case
    for name, case in CASES.items():
        if (enable_env := case.get("enable_env")) and selected in {"all", name}:
            env[enable_env] = "1"
    if args.dataset is not None:
        env["GRAPHCHECK_HOSTILE_DATASET"] = str(args.dataset.resolve())
    if "junitxml" in env.get("PYTEST_ADDOPTS", ""):
        (ROOT / ".hostile-artifacts").mkdir(exist_ok=True)
    command = [sys.executable, "-m", "pytest", TEST_FILE, "-v"]
    if selected == "fast":
        command.extend(
            [
                "-k",
                " or ".join(
                    case["pytest_test"] for case in CASES.values() if case["lane"] == "fast"
                ),
            ]
        )
    elif selected != "all":
        command.extend(["-k", CASES[selected]["pytest_test"]])
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
