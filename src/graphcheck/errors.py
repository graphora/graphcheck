from __future__ import annotations

from graphcheck.contracts.results import CheckError


class GraphCheckError(Exception):
    def __init__(self, code: str, message: str, fix: str) -> None:
        super().__init__(message)
        self.error = CheckError(code=code, message=message, fix=fix)


def profile_missing() -> GraphCheckError:
    return GraphCheckError(
        "profile.missing",
        "profiles.yml was not found in the GraphCheck project root.",
        "Run `graphcheck init`, or create profiles.yml next to graphcheck.yml.",
    )


def profile_invalid(message: str) -> GraphCheckError:
    return GraphCheckError(
        "profile.invalid",
        message,
        "Fix profiles.yml, then run `graphcheck debug` again.",
    )


def profile_not_found(name: str) -> GraphCheckError:
    return GraphCheckError(
        "profile.not_found",
        f"Profile {name!r} was not found in profiles.yml.",
        "Use `graphcheck debug --profile <name>`, or update the `default` profile.",
    )


def profile_password_missing(profile: str, env_var: str | None = None) -> GraphCheckError:
    if env_var is None:
        message = f"Profile {profile!r} has no resolved password."
        fix = "Add password or password_env to profiles.yml."
    else:
        message = (
            f"Profile {profile!r} references ${env_var}, but that environment variable is not set."
        )
        fix = f"Set {env_var}, or add a password value to profiles.yml."
    return GraphCheckError(
        "profile.password_missing",
        message,
        fix,
    )
