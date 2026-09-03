# Clean-machine test protocol

A repeatable script for running 5 non-builders through GraphCheck from zero to a useful outcome
in under 10 minutes, on a machine that never had GraphCheck on it. This ticket covers the
protocol and triage; recruiting the 5 testers is owner-driven.

## Before a session

- A fresh OS or container the tester has not used GraphCheck on before.
- Docker installed and running (for a local Neo4j instance).
- Python 3.12, 3.13, or 3.14 and `uv` available.
- Nothing pre-configured: no existing `profiles.yml`, no GraphCheck already installed.
- Pick a unique container name for this session (for example `graphcheck-clean-test-1`,
  `-2`, ...) so back-to-back sessions never collide on a name still in use from a prior run.

## The script

Read each step aloud to the tester rather than doing it for them. Note the wall-clock time at the
start and when they reach a working report.

1. **Install GraphCheck.**
```console
   git clone https://github.com/graphora/graphcheck.git
   cd graphcheck
   uv tool install .
   graphcheck --version
```

2. **Start a local Neo4j instance.**
```console
   docker run -d --name <session-container-name> -p 7687:7687 -p 7474:7474 -e NEO4J_AUTH=neo4j/testpassword neo4j:5
```
   Wait about 20-30 seconds before the next step. Neo4j is not ready the moment the container
   starts; running `graphcheck debug` too early produces a `neo4j.unreachable` error that is not
   a real product issue - don't log it as a friction point unless it still fails after the wait.

3. **Create a project and connect.**
```console
   mkdir graph-health
   cd graph-health
   graphcheck init
```
   Open the generated `profiles.yml`. Set `uri: bolt://localhost:7687`, and replace the inline
   `password:` line with the same password used in step 2 (`testpassword`) - this is the fastest
   path for a local test session; leave `password_env` alone.

4. **Confirm the connection.**
```console
   graphcheck debug
```
   This should report Neo4j as reachable. If it doesn't after the wait in step 2, that's a real
   friction point - log it, don't fix it for them.

5. **Run the example checks.**
```console
   graphcheck run
```

6. **Open the report.**
   - Windows: `start .graphcheck\runs\latest\report.html`
   - macOS: `open .graphcheck/runs/latest/report.html`
   - Linux: `xdg-open .graphcheck/runs/latest/report.html`

## Part D interview

Ask these in order, right after the tester has the report open. Don't lead the answers.

1. "What did you learn about your graph?"
2. "Would you run this again?"

Record a yes/no useful-outcome verdict for the session, using the tester's answer to question 1
as the evidence quote.

## Friction log

For each session, record:

- Tester (name or anonymized id)
- Start time, time report opened, total elapsed
- Useful-outcome verdict (yes/no) and the Q1 quote backing it
- Every point where the tester paused, got confused, hit an error, or asked for help - quote
  what they said or what error they saw, don't paraphrase away the confusion
- Whether an existing doc (README, quickstart, troubleshooting) would have prevented the friction,
  and which one

File a follow-up ticket for every friction point that isn't already covered by an existing issue.
Link the ticket back to the specific session in this log.

## After a session

Remove the session's container and project directory so the next session starts genuinely clean:
```console
docker rm -f <session-container-name>
```
Delete the `graph-health` project directory (or run the next session in a fresh location).

## Session log

| # | Tester | Elapsed | Verdict | Friction points | Follow-up tickets |
| --- | --- | --- | --- | --- | --- |
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |