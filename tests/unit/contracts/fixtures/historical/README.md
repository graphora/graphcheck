# Historical results fixtures

These files are frozen copies of `tests/contracts/fixtures/results.complete.json` at the following
Git revisions. Their old paths are provenance, not live dependencies.

| Schema | Revision |
| --- | --- |
| 1.0 | `256c5a6764e9f95d3567e4fa79fbaa362dc86fd7` |
| 1.1 | `faee57d0870e09b3b2040f6b185162d240501e00` |
| 1.2 | `f37fa2ed5715a7c68c119edc6f3e32efc31cb5eb` |
| 2.0 | `38d4af538500e0dedb4092e096cf47445793169b` |

Archived schemas were copied from `docs/specs/results.schema.json` into
`docs/schemas/results-<version>.schema.json` at the last revision before each schema bump:
1.0 at `256c5a6764e9f95d3567e4fa79fbaa362dc86fd7`,
1.1 at `91b998f5a213d5bc242ae2af70cd0184b5e71db2`, and
1.2 at `b7171f04fdf3e726e17a0df86134f3576d803c58`.
The 1.1 schema includes the optional graph counts added after its original fixture was written.

Do not regenerate these fixtures from the current model. Add a directory when a new schema ships.
The compatibility tests load every fixture, validate its historical structure, check normalized
2.0 data and stderr, and execute the transformer copied directly from the public policy page.
Inventories supplied in migration tests are explicit synthetic test inputs, not inferred metadata.
