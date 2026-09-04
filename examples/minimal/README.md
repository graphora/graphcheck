# Minimal project

Copy `profiles.example.yml` to the ignored `profiles.yml` filename, set `NEO4J_PASSWORD`, and run
GraphCheck from this directory:

```console
cp profiles.example.yml profiles.yml
graphcheck run
```

PowerShell users can replace the `cp` command with
`Copy-Item profiles.example.yml profiles.yml`.

The default `checks/` directory is baseline-free, so the first run does not require a prior
`graphcheck profile`. An optional drift example lives in
`optional-checks/customer-count-drift.yml`. To try it, create a baseline with `graphcheck profile`,
copy that file into `checks/`, and run `graphcheck run` again.
