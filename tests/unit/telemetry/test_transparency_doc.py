import json
import re
from pathlib import Path

from graphcheck.telemetry import policy

ROOT = Path(__file__).parents[3]
TELEMETRY_DOC = ROOT / "docs" / "reference" / "telemetry.md"
ALLOWLIST_BLOCK = re.compile(
    r"<!-- telemetry-allowlist:start -->\s*"
    r"```json\s*(?P<manifest>\{.*?\})\s*```\s*"
    r"<!-- telemetry-allowlist:end -->",
    re.DOTALL,
)


def _documented_allowlist() -> dict[str, object]:
    documentation = TELEMETRY_DOC.read_text(encoding="utf-8")
    matches = list(ALLOWLIST_BLOCK.finditer(documentation))
    assert len(matches) == 1, "docs/reference/telemetry.md must contain exactly one allowlist block"
    return json.loads(matches[0].group("manifest"))


def _code_allowlist() -> dict[str, object]:
    return {
        "common_properties": sorted(policy._POSTHOG_COMMON_PROPERTY_KEYS),
        "events": {
            event_name: [sorted(schema) for schema in schemas]
            for event_name, schemas in sorted(policy._POSTHOG_EVENT_PROPERTY_SCHEMAS.items())
        },
    }


def test_transparency_document_is_in_lockstep_with_outbound_allowlist():
    assert _documented_allowlist() == _code_allowlist(), (
        "PostHog event names or fields changed. Review the privacy impact and update the "
        "telemetry allowlist block in docs/reference/telemetry.md."
    )
