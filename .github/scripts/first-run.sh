#!/usr/bin/env bash
set -euo pipefail

: "${GRAPHCHECK_BOOTSTRAP_PYTHON:?Set GRAPHCHECK_BOOTSTRAP_PYTHON to the clean runner Python}"
: "${GRAPHCHECK_WHEEL_DIR:?Set GRAPHCHECK_WHEEL_DIR to the directory containing one wheel}"
: "${FIRST_RUN_PLATFORM:?Set FIRST_RUN_PLATFORM to the evidence platform name}"
: "${FIRST_RUN_TRIAL:?Set FIRST_RUN_TRIAL to the one-based trial number}"

FIRST_RUN_DIR="${FIRST_RUN_DIR:-${RUNNER_TEMP:-/tmp}/graphcheck-first-run}"
FIRST_RUN_RUNNER_OS="${FIRST_RUN_RUNNER_OS:-unknown}"
FIRST_RUN_RUNNER_IMAGE="${FIRST_RUN_RUNNER_IMAGE:-unknown}"
FIRST_RUN_VENV="${FIRST_RUN_VENV:-${RUNNER_TEMP:-/tmp}/graphcheck-first-run-venv-${FIRST_RUN_PLATFORM}-${FIRST_RUN_TRIAL}}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
evidence_script="$script_dir/first_run_evidence.py"
mkdir -p "$FIRST_RUN_DIR"

fail_stage() {
  local stage="$1" fix="$2"
  printf 'First-run failed during %s.\nFix: %s\n' "$stage" "$fix" | tee "$FIRST_RUN_DIR/failure.txt" >&2
  exit 1
}

run_stage() {
  local stage="$1" log="$2" fix="$3"
  shift 3
  set +e
  "$@" 2>&1 | tee -a "$log"
  local status=${PIPESTATUS[0]}
  set -e
  test "$status" -eq 0 || fail_stage "$stage" "$fix See $log for details."
}

shopt -s nullglob
wheels=("$GRAPHCHECK_WHEEL_DIR"/*.whl)
test "${#wheels[@]}" -eq 1 || fail_stage "wheel discovery" "Place exactly one built GraphCheck wheel in $GRAPHCHECK_WHEEL_DIR."
wheel="${wheels[0]}"

read -r started_at_ns started_monotonic_ns < <("$GRAPHCHECK_BOOTSTRAP_PYTHON" -c "import time; print(time.time_ns(), time.monotonic_ns())")
install_log="$FIRST_RUN_DIR/install.log"
run_stage "virtual environment creation" "$install_log" "Verify that Python includes venv support and the runner temp directory is writable." "$GRAPHCHECK_BOOTSTRAP_PYTHON" -m venv "$FIRST_RUN_VENV"
graphcheck_python="$FIRST_RUN_VENV/bin/python"
graphcheck_bin="$FIRST_RUN_VENV/bin/graphcheck"
run_stage "GraphCheck installation" "$install_log" "Verify package-index connectivity and that the wheel supports this Python version." "$graphcheck_python" -m pip install --disable-pip-version-check --no-cache-dir "$wheel"
install_finished_ns=$("$GRAPHCHECK_BOOTSTRAP_PYTHON" -c "import time; print(time.monotonic_ns())")

fixture_log="$FIRST_RUN_DIR/fixture.log"
run_stage "Neo4j fixture preparation" "$fixture_log" "Start the pinned Neo4j service and verify Bolt credentials." "$graphcheck_python" "$evidence_script" prepare-fixture
fixture_finished_ns=$("$GRAPHCHECK_BOOTSTRAP_PYTHON" -c "import time; print(time.monotonic_ns())")

project="$FIRST_RUN_DIR/project"
mkdir -p "$project"
cd "$project"
init_started_ns=$("$GRAPHCHECK_BOOTSTRAP_PYTHON" -c "import time; print(time.monotonic_ns())")
run_stage "graphcheck init" "$FIRST_RUN_DIR/init.log" "Run graphcheck debug --json and correct the reported connection or project configuration problem." "$graphcheck_bin" init
init_finished_ns=$("$GRAPHCHECK_BOOTSTRAP_PYTHON" -c "import time; print(time.monotonic_ns())")
grep -q "Detected Neo4j" "$FIRST_RUN_DIR/init.log" || fail_stage "graphcheck init connectivity" "Run graphcheck debug --json and correct the reported Neo4j connection problem."
grep -q "Traceback (most recent call last)" "$FIRST_RUN_DIR/init.log" && fail_stage "graphcheck init diagnostics" "Use the stable diagnostic instead of exposing a Python traceback."
for required in graphcheck.yml profiles.yml checks/example.yml; do
  test -f "$required" || fail_stage "graphcheck init artifact validation" "Confirm the project directory is writable and retry graphcheck init."
done

run_started_ns=$("$GRAPHCHECK_BOOTSTRAP_PYTHON" -c "import time; print(time.monotonic_ns())")
set +e
"$graphcheck_bin" run 2>&1 | tee "$FIRST_RUN_DIR/run.log"
run_exit_code=${PIPESTATUS[0]}
set -e
run_finished_ns=$("$GRAPHCHECK_BOOTSTRAP_PYTHON" -c "import time; print(time.monotonic_ns())")
finished_at_ns=$("$GRAPHCHECK_BOOTSTRAP_PYTHON" -c "import time; print(time.time_ns())")
grep -q "Traceback (most recent call last)" "$FIRST_RUN_DIR/run.log" && fail_stage "graphcheck run diagnostics" "Use the stable diagnostic instead of exposing a Python traceback."

profile_log="$FIRST_RUN_DIR/profile.log"
run_stage "graphcheck profile smoke check" "$profile_log" "Run graphcheck debug --json and correct the reported profiling problem." "$graphcheck_bin" profile
grep -q "Traceback (most recent call last)" "$profile_log" && fail_stage "graphcheck profile diagnostics" "Use the stable diagnostic instead of exposing a Python traceback."
baseline_found=0
for baseline in .graphcheck/baselines/*.json; do
  test -f "$baseline" && baseline_found=1 && break
done
test "$baseline_found" -eq 1 || fail_stage "graphcheck profile artifact validation" "Confirm the artifact directory is writable and retry graphcheck profile."

python_version=$("$graphcheck_python" -c "import platform; print(platform.python_version())")
run_stage "first-result validation" "$FIRST_RUN_DIR/validation.log" "Inspect results.json, report.html, and run.log; a valid result must be complete, scored, and execute at least one check." \
  "$GRAPHCHECK_BOOTSTRAP_PYTHON" "$evidence_script" record \
  --results .graphcheck/runs/latest/results.json \
  --report .graphcheck/runs/latest/report.html \
  --output "$FIRST_RUN_DIR/timing.json" \
  --platform "$FIRST_RUN_PLATFORM" \
  --trial "$FIRST_RUN_TRIAL" \
  --run-exit-code "$run_exit_code" \
  --started-at-ns "$started_at_ns" \
  --finished-at-ns "$finished_at_ns" \
  --install-ns "$((install_finished_ns - started_monotonic_ns))" \
  --fixture-ns "$((fixture_finished_ns - install_finished_ns))" \
  --init-ns "$((init_finished_ns - init_started_ns))" \
  --run-ns "$((run_finished_ns - run_started_ns))" \
  --total-ns "$((run_finished_ns - started_monotonic_ns))" \
  --python-version "$python_version" \
  --runner-os "$FIRST_RUN_RUNNER_OS" \
  --runner-image "$FIRST_RUN_RUNNER_IMAGE"

total_seconds=$("$GRAPHCHECK_BOOTSTRAP_PYTHON" -c "print(round(($run_finished_ns - $started_monotonic_ns) / 1_000_000_000, 3))")
printf 'Install -> init -> first valid run completed in %.3f seconds; median budget: <900 seconds.\n' "$total_seconds" | tee "$FIRST_RUN_DIR/timing.txt" | tee -a "$FIRST_RUN_DIR/run.log"
