"""Release-owned telemetry configuration.

The PostHog project API key is a public ingestion identifier, not a credential. Keeping it in
this isolated module makes the eventual release change explicit and keeps transport wiring out of
the engine and consent layers.
"""

POSTHOG_PROJECT_API_KEY: str | None = None
"""TODO(release): set this to GraphCheck's public ``phc_...`` PostHog project API key."""

POSTHOG_HOST = "https://us.i.posthog.com"
