# Minimal project

Copy `profiles.example.yml` to the ignored `profiles.yml` filename, set `NEO4J_PASSWORD`, and run
GraphCheck from this directory:

```console
cp profiles.example.yml profiles.yml
graphcheck run
```

PowerShell users can replace the `cp` command with
`Copy-Item profiles.example.yml profiles.yml`.
