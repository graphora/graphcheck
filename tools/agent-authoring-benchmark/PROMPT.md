# Check authoring task

Write one GraphCheck YAML suite per intent in `intents.json`, with exactly one check in each
suite. Use the intent ID as both the suite ID and check ID. Every suite must set file-level
`generated: true`. Derive the check from the intent and the supplied fraud-ring context.

Read only these benchmark inputs: `agents.md`, `llms.txt`, `check.schema.json`, `profile.json`,
`schema.md`, and `intents.json`. They are the same frozen context for all three sessions. The
profile is the `latest` baseline and the current graph is the same seeded fraud-ring fixture.
Expected verdicts, reference queries, and other agents' outputs are withheld.

Write the ten files directly into your assigned output directory as `<intent-id>.yml`. Use UTF-8.
These files are the raw submission: do not run GraphCheck, schema validation, the database, or
tests; do not repair files after evaluation. Do not inspect repository source, other documents,
the benchmark harness, the answer key, or other sessions. You may read the listed context and
use file-writing tools. Produce your best first submission; do not leave placeholders.

The harness will preserve the submitted bytes. The user's benchmark request authorizes the
harness to activate a temporary in-memory copy solely for read-only fixture evaluation.
Production proposals retain `generated: true` and still require human review.
