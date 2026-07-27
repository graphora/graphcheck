from __future__ import annotations

from dataclasses import dataclass

from graphcheck.generation.config import GenerateConfig
from graphcheck.generation.transmission import LoadedDocument

TRANSMITTED_FIELDS = (
    "label_names",
    "property_names",
    "property_types",
    "relationship_type_names",
    "schema_coverage",
    "aggregate_counts",
    "degree_distributions",
    "property_coverage",
    "graphcheck_check_schemas",
    "user_documents",
)
EXCLUDED_FIELDS = (
    "graph_records",
    "property_values",
    "query_results",
    "target_metadata",
    "fingerprints",
    "credentials",
    "api_keys",
    "partial_reason",
    "absolute_paths",
)


@dataclass(frozen=True)
class GenerateDisclosure:
    provider: str
    model: str
    destination: str
    baseline: str
    profile_status: str
    documents: tuple[LoadedDocument, ...]
    non_deterministic: bool = True

    @classmethod
    def build(
        cls,
        *,
        config: GenerateConfig,
        baseline: str,
        profile_status: str,
        documents: list[LoadedDocument],
    ) -> GenerateDisclosure:
        return cls(
            provider=config.provider,
            model=config.model,
            destination=config.destination,
            baseline=baseline,
            profile_status=profile_status,
            documents=tuple(documents),
        )

    def as_json(self) -> dict[str, object]:
        return {
            "event": "generate.disclosure",
            "provider": self.provider,
            "model": self.model,
            "destination": self.destination,
            "baseline": self.baseline,
            "profile_status": self.profile_status,
            "transmitted_fields": list(TRANSMITTED_FIELDS),
            "documents": [
                {
                    "path": document.display_path,
                    "bytes": document.byte_count,
                    "verbatim": True,
                }
                for document in self.documents
            ],
            "documents_may_contain_sensitive_content": True,
            "documents_inspected_or_redacted": False,
            "excluded_fields": list(EXCLUDED_FIELDS),
            "non_deterministic": True,
        }

    def render_human(self) -> str:
        count = len(self.documents)
        noun = "document" if count == 1 else "documents"
        total_bytes = sum(document.byte_count for document in self.documents)
        document_paths = (
            ", ".join(document.display_path for document in self.documents)
            if self.documents
            else "none"
        )
        partial_line = (
            "\nProfile status: partial (incomplete)." if self.profile_status == "partial" else ""
        )
        return (
            "GraphCheck generate disclosure\n"
            f"Provider: {self.provider}\n"
            f"Model: {self.model}\n"
            f"Destination: {self.destination}\n"
            f"Baseline: {self.baseline}"
            f"{partial_line}\n"
            "Transmitting: label names, property names and observed types, relationship type "
            "names, schema constraint/index coverage, aggregate counts, degree distributions, "
            "property coverage, GraphCheck check schemas, and "
            f"{count} user-supplied {noun} ({total_bytes:,} bytes).\n"
            f"Documents sent verbatim: {document_paths}\n"
            "Sensitive document warning: user-supplied documents may contain sensitive content; "
            "GraphCheck does not inspect or redact their contents.\n"
            "Not transmitting: graph records or property values, query results, target/server "
            "metadata, fingerprints, credentials, API keys, profiler failure text, or local "
            "absolute paths.\n"
            "Note: generated checks are non-deterministic authoring suggestions and remain inert "
            "until reviewed."
        )
