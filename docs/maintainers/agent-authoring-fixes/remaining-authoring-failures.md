# Remaining model mistakes and evaluation limits

Luna's intent 09 is the only non-recurring observed verdict mistake. It used
`from_label: Account`, `to_label: Transaction`, `direction: in`, so it checked incoming SENT edges
on 2,504 Accounts and failed all of them. The intent asks for one incoming Account sender per
Transaction. The correct parameters are `from_label: Transaction`, `to_label: Account`,
`direction: in`, `rel_type: SENT`, `exactly: 1`; the fixture has zero violations.

The `incoming-sender-scope` example in
[remediations.yml](../../../tools/agent-authoring-benchmark/remediations.yml) records the repair and
must pass. The original submission stays untouched. The guide and `CardinalityWith.from_label`
schema description clarify that `from_label` names the evaluated population for every direction.
The remaining model-side task is translating that rule correctly during authoring; the measured
score is still Luna 6/10.

Passing this fixture does not prove a check behaves correctly on another graph. For example,
Sol's intent 09 returns only business IDs for potential violating Transactions, and Terra's
passing membership check returns only account IDs. A future failing state could expose another
missing-evidence error. These are review observations, not additional measured failures in this
trial. A future benchmark should add disjoint passing/failing variants, empty-result cases,
multiple independent sessions per model, and semantic checks on returned findings.

There was one completed session per model, with ten related intents in each session. This gives
30 submissions, not 30 independent model sessions. The initial session dispatches hit an account
usage limit before writing any submission and were resumed with the same frozen context after
the limit reset. No evaluated candidate was discarded, repaired, or selected from retries.

An empty graph may offer no honest witness for an aggregate mismatch. The current engine reports
that as inconclusive. An aggregate competency evidence contract, if desired, needs explicit design
and separate tests; it is outside the guide/schema/diagnostic fixes measured here.
