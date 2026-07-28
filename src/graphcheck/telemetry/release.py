"""Release-owned telemetry configuration.

The PostHog project API key is a public ingestion identifier, not a credential. Keeping it in
this isolated module makes the eventual release change explicit and keeps transport wiring out of
the engine and consent layers.
"""

POSTHOG_PROJECT_API_KEY: str | None = "phc_knZzPbRwHobaDdLafjoWn48oAbrp97LwJ9drKStzExiz"

POSTHOG_HOST = "https://eu.i.posthog.com"
