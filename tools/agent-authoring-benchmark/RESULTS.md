# Fraud-ring agent authoring results

Recorded 2026-09-16 against the frozen context and Neo4j Community 5.26.28 fixture. One
completed session per model, ten first submissions per session, all at max effort. Every file
validated and loaded, but only **21/30** returned the intended verdict. No YAML was repaired.

| Model | Effort | Valid | Loads | Runs read-only | Correct |
| --- | --- | --- | --- | --- | --- |
| gpt-5.6-luna | max | 10/10 | 10/10 | 7/10 | 6/10 |
| gpt-5.6-terra | max | 10/10 | 10/10 | 8/10 | 8/10 |
| gpt-5.6-sol | max | 10/10 | 10/10 | 7/10 | 7/10 |

A `fail` or `warn` is correct when the answer key expects it. An `errored` outcome is not
successful execution. Stages are independent: schema validation and loading use the original
YAML; execution uses only an in-memory activation of its generated flags. See
[method and replay commands](README.md), [fixed answer key](task-set.json),
[session settings](sessions.json), [input hashes](manifest.json), and
[raw submission hashes](submissions.json). The complete graph digest was unchanged.

| Model | Intent / raw YAML | Valid | Loads | Runs | Correct | Expected | Actual |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-5.6-luna | [01-account-no-orphans](raw/luna/01-account-no-orphans.yml) | yes | yes | yes | yes | fail | fail |
| gpt-5.6-luna | [02-account-at-most-one-owner](raw/luna/02-account-at-most-one-owner.yml) | yes | yes | no | no | fail | errored |
| gpt-5.6-luna | [03-customer-email-complete](raw/luna/03-customer-email-complete.yml) | yes | yes | yes | yes | warn | warn |
| gpt-5.6-luna | [04-account-balance-integer](raw/luna/04-account-balance-integer.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-luna | [05-account-type-allowed](raw/luna/05-account-type-allowed.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-luna | [06-account-id-unique](raw/luna/06-account-id-unique.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-luna | [07-customer-count-regression](raw/luna/07-customer-count-regression.yml) | yes | yes | no | no | fail | errored |
| gpt-5.6-luna | [08-control-path-membership](raw/luna/08-control-path-membership.yml) | yes | yes | no | no | pass | errored |
| gpt-5.6-luna | [09-transaction-single-sender](raw/luna/09-transaction-single-sender.yml) | yes | yes | yes | no | pass | fail |
| gpt-5.6-luna | [10-owns-count-drift](raw/luna/10-owns-count-drift.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-terra | [01-account-no-orphans](raw/terra/01-account-no-orphans.yml) | yes | yes | yes | yes | fail | fail |
| gpt-5.6-terra | [02-account-at-most-one-owner](raw/terra/02-account-at-most-one-owner.yml) | yes | yes | no | no | fail | errored |
| gpt-5.6-terra | [03-customer-email-complete](raw/terra/03-customer-email-complete.yml) | yes | yes | yes | yes | warn | warn |
| gpt-5.6-terra | [04-account-balance-integer](raw/terra/04-account-balance-integer.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-terra | [05-account-type-allowed](raw/terra/05-account-type-allowed.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-terra | [06-account-id-unique](raw/terra/06-account-id-unique.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-terra | [07-customer-count-regression](raw/terra/07-customer-count-regression.yml) | yes | yes | no | no | fail | errored |
| gpt-5.6-terra | [08-control-path-membership](raw/terra/08-control-path-membership.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-terra | [09-transaction-single-sender](raw/terra/09-transaction-single-sender.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-terra | [10-owns-count-drift](raw/terra/10-owns-count-drift.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-sol | [01-account-no-orphans](raw/sol/01-account-no-orphans.yml) | yes | yes | yes | yes | fail | fail |
| gpt-5.6-sol | [02-account-at-most-one-owner](raw/sol/02-account-at-most-one-owner.yml) | yes | yes | no | no | fail | errored |
| gpt-5.6-sol | [03-customer-email-complete](raw/sol/03-customer-email-complete.yml) | yes | yes | yes | yes | warn | warn |
| gpt-5.6-sol | [04-account-balance-integer](raw/sol/04-account-balance-integer.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-sol | [05-account-type-allowed](raw/sol/05-account-type-allowed.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-sol | [06-account-id-unique](raw/sol/06-account-id-unique.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-sol | [07-customer-count-regression](raw/sol/07-customer-count-regression.yml) | yes | yes | no | no | fail | errored |
| gpt-5.6-sol | [08-control-path-membership](raw/sol/08-control-path-membership.yml) | yes | yes | no | no | pass | errored |
| gpt-5.6-sol | [09-transaction-single-sender](raw/sol/09-transaction-single-sender.yml) | yes | yes | yes | yes | pass | pass |
| gpt-5.6-sol | [10-owns-count-drift](raw/sol/10-owns-count-drift.yml) | yes | yes | yes | yes | pass | pass |

The [CSV table](results/results.csv) and [structured diagnostics](results/results.json) are
committed alongside the full per-check GraphCheck results in `results/<model>/`.

The three recurring root causes are:

1. Wrong single-column regression projection: four submissions (Luna/Sol 07 and 08).
   [Fix](../../docs/maintainers/agent-authoring-fixes/regression-values.md).
2. Business IDs used as finding identity: three submissions (all models, 02).
   [Fix](../../docs/maintainers/agent-authoring-fixes/finding-identities.md).
3. Count aggregates without failure evidence: three submissions (all models, 07).
   [Fix](../../docs/maintainers/agent-authoring-fixes/aggregate-evidence.md).

These categories overlap for Luna/Sol 07. Luna 09 separately mis-scoped the relationship count;
it is recorded with passing-check limitations in
[remaining authoring failures](../../docs/maintainers/agent-authoring-fixes/remaining-authoring-failures.md).
Eight submissions errored for missing evidence; one executed with the wrong verdict.

Guide/schema/diagnostic fixes and separate remediation examples do not change this first-draft
matrix. A new authoring trial is required to establish improvement. This small, single-fixture
sample is not a general model ranking or proof that passing checks work on other graph states.
