from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from graphcheck.errors import (
    profile_invalid,
    profile_missing,
    profile_not_found,
    profile_password_missing,
    profile_uri_invalid,
)
from graphcheck.project import PROFILES_FILE


class ConnectionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    user: str
    password: str | None = None
    password_env: str | None = None
    database: str

    def resolved(self, name: str) -> ConnectionProfile:
        if self.password_env:
            env_password = os.environ.get(self.password_env)
            if env_password:
                return self.model_copy(update={"password": env_password, "password_env": None})
            if not self.password:
                raise profile_password_missing(name, self.password_env)
        if not self.password:
            raise profile_password_missing(name)
        return self.model_copy(update={"password_env": None})


class ProfilesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str
    profiles: dict[str, ConnectionProfile]


def default_profiles() -> ProfilesFile:
    return ProfilesFile(
        default="local",
        profiles={
            "local": ConnectionProfile(
                uri="bolt://localhost:7687",
                user="neo4j",
                password="graphora",
                database="neo4j",
            )
        },
    )


def write_default_profiles(root: Path) -> None:
    (root / PROFILES_FILE).write_text(
        """# Edit the inline password below for the fastest local setup.
# For CI/production, remove `password` and use `password_env: NEO4J_PASSWORD` instead.
# Enterprise: use a dedicated read-only user. Community: EXPLAIN guards every submitted query.
# Use bolt:// for direct local Bolt, or neo4j+s:// for a CA-signed TLS/routing endpoint.
default: local
profiles:
  local:
    uri: bolt://localhost:7687
    user: neo4j
    password: graphora
    database: neo4j
""",
        encoding="utf-8",
    )


def load_profiles(root: Path) -> ProfilesFile:
    path = root / PROFILES_FILE
    if not path.exists():
        raise profile_missing()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return ProfilesFile.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise profile_invalid(f"Invalid profiles.yml: {exc}") from exc


def select_profile(
    profiles: ProfilesFile, name: str | None = None
) -> tuple[str, ConnectionProfile]:
    selected = name or profiles.default
    try:
        profile = profiles.profiles[selected]
    except KeyError as exc:
        raise profile_not_found(selected) from exc
    validate_profile_uri(profile.uri)
    return selected, profile.resolved(selected)


def validate_profile_uri(uri: str) -> None:
    try:
        parsed = urlsplit(uri)
        valid = parsed.scheme in {
            "bolt",
            "bolt+s",
            "bolt+ssc",
            "neo4j",
            "neo4j+s",
            "neo4j+ssc",
        } and bool(parsed.hostname)
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            valid = False
    except ValueError:
        valid = False
    if not valid:
        raise profile_uri_invalid(uri)
