"""Stable configuration identity without timestamps or connection credentials."""

import hashlib
import json


def config_hash(configuration: object) -> str:
    payload = json.dumps(configuration, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
