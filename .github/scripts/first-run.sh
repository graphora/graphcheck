#!/usr/bin/env bash
set -euo pipefail

: "${GRAPHCHECK_BIN:?Set GRAPHCHECK_BIN to the isolated graphcheck executable}"
: "${GRAPHCHECK_PYTHON:?Set GRAPHCHECK_PYTHON to its Python executable}"

FIRST_RUN_DIR="${FIRST_RUN_DIR:-${RUNNER_TEMP:-/tmp}/graphcheck-first-run}"
mkdir -p "$FIRST_RUN_DIR"

ready=0
for ((attempt = 0; attempt < 60; attempt++)); do
  if "$GRAPHCHECK_PYTHON" -c "from neo4j import GraphDatabase; d=GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j', 'graphora'), connection_timeout=1); d.verify_connectivity(); d.close()" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
test "$ready" -eq 1

"$GRAPHCHECK_PYTHON" -c "from neo4j import GraphDatabase; d=GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j', 'graphora')); d.execute_query(\"MERGE (:Customer {name: 'Ada'})\", database_='neo4j'); d.close()"

project="$FIRST_RUN_DIR/project"
mkdir -p "$project"
cd "$project"
started=$("$GRAPHCHECK_PYTHON" -c "import time; print(time.monotonic())")
"$GRAPHCHECK_BIN" init | tee init.log
"$GRAPHCHECK_BIN" profile | tee profile.log
"$GRAPHCHECK_BIN" run | tee run.log
elapsed=$("$GRAPHCHECK_PYTHON" -c "import time; print(time.monotonic() - $started)")

grep -q "Detected Neo4j" init.log
test -f graphcheck.yml
test -f profiles.yml
test -f checks/example.yml
baseline_found=0
for baseline in .graphcheck/baselines/*.json; do
  test -f "$baseline" && baseline_found=1 && break
done
test "$baseline_found" -eq 1
test -f .graphcheck/runs/latest/results.json
test -f .graphcheck/runs/latest/report.html
"$GRAPHCHECK_PYTHON" -c "import json, pathlib; p=json.loads(pathlib.Path('.graphcheck/runs/latest/results.json').read_text()); assert p['run']['status']=='complete', p['run']; assert p['run']['exit_code']==0, p['run']; assert 0 <= float('$elapsed') < 600, 'first-run flow took $elapsed seconds'"
printf 'init -> profile -> run completed in %.3f seconds (budget: <600 seconds)\n' "$elapsed" | tee timing.txt
