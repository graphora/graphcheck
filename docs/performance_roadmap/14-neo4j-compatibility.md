# PR 14 — Define the Neo4j driver/server/Cypher compatibility matrix

- Category: compatibility and release engineering
- Roadmap source: Step 9
- Prerequisites: PR 03 is preferred so native-token plans are exercised
- Suggested PR title: `ci: test driver 5/6, Server 5.26/CalVer, and Cypher 5/25`

## Goal

Declare and enforce compatibility across three separate dimensions: Neo4j Python driver, Neo4j
Server, and Cypher language version.

## Version terminology

- Neo4j Server uses calendar versions from `2025.01.0` onward.
- Neo4j Server `5.26` is an LTS checkpoint.
- The Python driver is independently versioned and currently has a `6.x` line.
- Current CalVer servers can execute Cypher 5 or Cypher 25.

“Neo4j 6” is not an acceptable shorthand.

Official references:

- [Server versioning](https://neo4j.com/docs/upgrade-migration-guide/current/)
- [Current Operations Manual](https://neo4j.com/docs/operations-manual/current/)
- [Python driver/server compatibility](https://neo4j.com/docs/python-manual/current/install/)
- [Cypher version configuration](https://neo4j.com/docs/operations-manual/current/configuration/cypher-version-configuration/)
- [Cypher compatibility and deprecations](https://neo4j.com/docs/cypher-manual/current/deprecations-additions-removals-compatibility/)

## Scope

- Bound the Python driver to tested major versions.
- Pin deliberate server images in CI.
- Test Cypher 5 and Cypher 25 on a current CalVer server.
- Decide/document Neo4j 4.4 legacy status.
- Audit `id()` and other deprecated/removed Cypher.
- Add installed-wheel smoke tests.

## Non-goals

- Upgrading customer databases.
- Supporting an untested future driver major.
- Treating a floating Docker tag as a compatibility guarantee.
- Changing GraphCheck's graph semantics merely to silence a deprecation.

## Proposed policy

| Dimension | Required target | Policy |
| --- | --- | --- |
| Python driver | lowest declared supported release | compatibility floor |
| Python driver | latest 6.x | primary current driver |
| Python driver | future 7.x | blocked until tested |
| Neo4j Server | 5.26 LTS | required LTS target |
| Neo4j Server | selected current CalVer | required current target |
| Neo4j Server | 4.4 | explicit legacy/EOL decision |
| Cypher | 5 | required while legacy compatibility remains |
| Cypher | 25 | required on current CalVer |

Use a tested driver range such as:

```toml
"neo4j>=5.20,<7"
```

Raise the lower bound if the implementation uses a newer API.

## CI matrix

At minimum:

```text
driver-min      + Server 5.26    + Cypher 5
driver-latest-6 + Server 5.26    + Cypher 5
driver-latest-6 + current CalVer + Cypher 5
driver-latest-6 + current CalVer + Cypher 25
driver-latest-6 + Server 4.4     + legacy policy
```

Pin exact server tags or centrally declared versions. Exercise Enterprise separately where
privileges, HOME graph behavior, schema metadata, or APOC differ.

## Cypher 25 and element identity

Current generated queries use deprecated `id()` for evidence identity, ordering, and numeric
sampling. Migrate toward `elementId()` without parsing its string format, which Neo4j does not
guarantee.

Sampling requires a separate solution because the current hash assumes a numeric ID. Acceptable
transition options:

- a deterministic string hash expressible in both supported Cypher modes;
- a capability-selected query variant;
- a deliberately retained, explicitly tested Cypher 5 path until a sound Cypher 25 algorithm lands.

Do not silently switch to lexicographically first element IDs and call that an unbiased sample.

## Implementation

1. Record the actual driver APIs used and determine the true minimum version.
2. Add an upper bound excluding untested driver 7.
3. Replace broad `neo4j:5` test tags with deliberate versions.
4. Add a selected CalVer server target.
5. Configure and test Cypher 5 and Cypher 25 independently.
6. Search generated Cypher for deprecated/removed constructs.
7. Add `elementId()` migration tests and an explicit sampling transition decision.
8. Run one driver lane with `PYTHONNEO4JDEBUG` or Python development mode.
9. Build/install the wheel in a clean environment and smoke-test `--version`/`--help`.
10. Document GraphCheck, driver, server, and Cypher versions separately in support output.

## Tests

Run the normal quality gate under minimum and latest supported drivers. Run connector, compiler-plan,
engine, and relevant privilege tests across the server/Cypher matrix.

Required checks:

- no resource/concurrency warnings in the development-mode lane;
- generated queries parse and execute in both Cypher modes;
- query-type classification remains fail-closed;
- package resources exist in the installed wheel;
- version parsing supports CalVer;
- Neo4j 4.4 policy is explicit.

## Acceptance criteria

- Dependency resolution cannot select an untested future driver major.
- Minimum driver and latest 6.x both pass required tests.
- Server 5.26 and the selected CalVer release pass integration tests.
- Built-ins execute under Cypher 5 and 25, or an explicit temporary compatibility limitation fails
  clearly.
- Public docs distinguish driver, server, and Cypher versions.
- `id()` has no new uses and existing uses have a tracked migration.

## Rollback

If CalVer/Cypher 25 support is not ready, pin and document the narrower supported combination with a
clear compatibility error. Do not mislabel driver 6.x as Server 6.

## PR checklist

- [ ] Exact server images are recorded.
- [ ] Driver upper bound matches tested majors.
- [ ] Cypher 5 and 25 are distinct CI lanes.
- [ ] Installed-wheel smoke test passes.
- [ ] Neo4j 4.4 status is documented.
