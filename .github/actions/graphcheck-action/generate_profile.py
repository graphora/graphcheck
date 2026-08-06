#!/usr/bin/env python3
"""Safely generates a minimal profiles.yml for CI use, via yaml.safe_dump
rather than hand-built shell text. Only writes if profiles.yml does not
already exist; the real password is never written, only the name of the
environment variable to read it from.
"""

import os
import sys

try:
    import yaml
except ImportError:
    print("PyYAML is required to generate profiles.yml", file=sys.stderr)
    sys.exit(1)


def main():
    if os.path.exists("profiles.yml"):
        print("profiles.yml already exists, using it as-is.")
        return

    profile = os.environ["GC_PROFILE"]
    uri = os.environ["GC_URI"]
    user = os.environ["GC_USER"]
    database = os.environ["GC_DATABASE"]

    data = {
        "default": profile,
        "profiles": {
            profile: {
                "uri": uri,
                "user": user,
                "password": None,
                "password_env": "NEO4J_PASSWORD",
                "database": database,
            }
        },
    }

    with open("profiles.yml", "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as f:
            f.write("generated_profiles=true\n")

    print("Generated profiles.yml")


if __name__ == "__main__":
    main()
