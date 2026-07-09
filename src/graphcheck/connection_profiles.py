from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from graphcheck.errors import (
    profile_invalid,
    profile_missing,
    profile_not_found,
    profile_password_missing,
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
            if self.password is None:
                raise profile_password_missing(name, self.password_env)
        if self.password is None:
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
    profiles = default_profiles()
    (root / PROFILES_FILE).write_text(
        yaml.safe_dump(profiles.model_dump(), sort_keys=False),
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
        return selected, profiles.profiles[selected].resolved(selected)
    except KeyError as exc:
        raise profile_not_found(selected) from exc
