from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from graphcheck.errors import GraphCheckError
from graphcheck.generation.client import (
    StructuredOutputClient,
    create_structured_output_client,
)
from graphcheck.generation.config import GenerateConfig, resolve_api_key
from graphcheck.generation.disclosure import GenerateDisclosure
from graphcheck.generation.prompts import build_correction_request, build_initial_request
from graphcheck.generation.proposals import (
    ProposalRequest,
    RawProposalBatch,
    ValidatedCandidate,
    serialize_validated_suite,
    validate_candidate,
)
from graphcheck.generation.transmission import (
    GenerateRequest,
    build_profile_context,
    display_path,
    load_generation_baseline,
    read_documents,
)
from graphcheck.generation.writer import GeneratedSuiteWriter
from graphcheck.project import ProjectConfig, load_project_config


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GeneratedCheck(_Strict):
    id: str
    kind: Literal["conformance", "competency", "drift"]


class DroppedCandidate(_Strict):
    attempt: Literal[1, 2]
    candidate: str
    code: Literal[
        "generate.candidate_invalid",
        "generate.candidate_duplicate",
        "generate.candidate_excess",
    ]
    reason: str


class GenerateResult(_Strict):
    command: Literal["generate"] = "generate"
    status: Literal["generated"] = "generated"
    path: str
    requested: int
    written: int
    dropped: int
    provider: str
    model: str
    baseline: str
    non_deterministic: Literal[True] = True
    checks: list[GeneratedCheck]
    dropped_candidates: list[DroppedCandidate] = Field(default_factory=list)


class GenerationService:
    """Own the disclosed, bounded, exactly-two-attempt generation workflow."""

    def __init__(
        self,
        *,
        client_factory: Callable[
            [GenerateConfig, str | None], StructuredOutputClient
        ] = create_structured_output_client,
        writer_factory: Callable[[Path], GeneratedSuiteWriter] = GeneratedSuiteWriter,
    ) -> None:
        self._client_factory = client_factory
        self._writer_factory = writer_factory

    def generate(
        self,
        *,
        project_root: Path,
        baseline_from: Path | None,
        document_paths: list[Path] | None,
        requested_count: int,
        disclosure_sink: Callable[[GenerateDisclosure], None],
        warning_sink: Callable[[DroppedCandidate], None] | None = None,
        invocation_dir: Path | None = None,
    ) -> GenerateResult:
        config = _load_generation_project_config(project_root)
        generate_config = config.generate
        if generate_config is None:
            raise GraphCheckError(
                "generate.config_missing",
                "graphcheck.yml does not contain a `generate` block.",
                "Add a `generate:` block to `graphcheck.yml` with provider, model, and "
                "credential environment-variable name.",
            )

        # Credential resolution intentionally happens before reading either baseline or docs.
        api_key = resolve_api_key(generate_config)
        baseline_path, baseline = load_generation_baseline(
            project_root=project_root,
            artifacts=config.artifacts,
            requested=baseline_from,
        )
        documents = read_documents(
            document_paths,
            project_root=project_root,
            invocation_dir=invocation_dir,
        )
        request_context = GenerateRequest(
            profile=build_profile_context(baseline),
            documents=[document.document for document in documents],
            requested_count=requested_count,
        )
        baseline_display = display_path(baseline_path, project_root)
        disclosure = GenerateDisclosure.build(
            config=generate_config,
            baseline=baseline_display,
            profile_status=baseline.status.value,
            documents=documents,
        )
        first_request = build_initial_request(request_context)
        client = self._client_factory(generate_config, api_key)

        # This is deliberately adjacent to the first billable/network operation.
        disclosure_sink(disclosure)
        retained: list[ValidatedCandidate] = []
        dropped: list[DroppedCandidate] = []
        summaries: list[str] = []
        initial_envelope_invalid = False
        try:
            first_batch = _propose(client, first_request)
        except GraphCheckError as exc:
            if exc.error.code != "generate.output_invalid":
                raise
            first_batch = None
            initial_envelope_invalid = True
            summaries.append("response envelope: invalid structured candidate batch")

        if first_batch is not None:
            self._process_batch(
                first_batch,
                attempt=1,
                requested_count=requested_count,
                retained=retained,
                dropped=dropped,
                summaries=summaries,
                config=generate_config,
                warning_sink=warning_sink,
            )

        needed = requested_count - len(retained)
        if needed > 0:
            correction = build_correction_request(
                request_context,
                needed=requested_count if initial_envelope_invalid else needed,
                validation_summaries=summaries,
                retained_ids=[candidate.id for candidate in retained],
                replace_full_batch=initial_envelope_invalid,
            )
            try:
                second_batch = _propose(client, correction)
            except GraphCheckError as exc:
                if exc.error.code == "generate.output_invalid":
                    if retained:
                        rejection = DroppedCandidate(
                            attempt=2,
                            candidate="response envelope",
                            code="generate.candidate_invalid",
                            reason="invalid structured candidate batch",
                        )
                        dropped.append(rejection)
                        if warning_sink is not None:
                            warning_sink(rejection)
                        second_batch = None
                    else:
                        message = (
                            "The provider returned invalid structured output on both attempts."
                            if initial_envelope_invalid
                            else "The provider returned invalid structured output for the final "
                            "correction request."
                        )
                        raise GraphCheckError(
                            "generate.output_invalid",
                            message,
                            "Choose a model with structured-output support or reduce docs/count.",
                        ) from None
                else:
                    raise
            if second_batch is not None:
                self._process_batch(
                    second_batch,
                    attempt=2,
                    requested_count=requested_count,
                    retained=retained,
                    dropped=dropped,
                    summaries=[],
                    config=generate_config,
                    warning_sink=warning_sink,
                )

        if not retained:
            raise GraphCheckError(
                "generate.no_valid_candidates",
                "No generated candidate passed GraphCheck validation.",
                "Review the logged reasons and retry with clearer domain docs or another model.",
            )

        # Prove the complete bytes load before asking the writer to touch the filesystem.
        serialize_validated_suite("generated-validation", retained)
        checks_path = Path(config.checks)
        checks_dir = checks_path if checks_path.is_absolute() else project_root / checks_path
        written = self._writer_factory(checks_dir).write(retained)
        result_path = display_path(written.path, project_root)
        dropped_count = max(requested_count - len(retained), len(dropped))
        return GenerateResult(
            path=result_path,
            requested=requested_count,
            written=len(retained),
            dropped=dropped_count,
            provider=generate_config.provider,
            model=generate_config.model,
            baseline=baseline_display,
            checks=[GeneratedCheck(id=candidate.id, kind=candidate.kind) for candidate in retained],
            dropped_candidates=dropped,
        )

    @staticmethod
    def _process_batch(
        batch: RawProposalBatch,
        *,
        attempt: Literal[1, 2],
        requested_count: int,
        retained: list[ValidatedCandidate],
        dropped: list[DroppedCandidate],
        summaries: list[str],
        config: GenerateConfig,
        warning_sink: Callable[[DroppedCandidate], None] | None,
    ) -> None:
        retained_ids = {candidate.id for candidate in retained}
        for index, raw in enumerate(batch.candidates):
            candidate_name = f"proposal[{index}]"
            try:
                candidate = validate_candidate(
                    raw,
                    provider=config.provider,
                    model=config.model,
                    candidate_name=candidate_name,
                )
            except ValueError as exc:
                rejection = DroppedCandidate(
                    attempt=attempt,
                    candidate=candidate_name,
                    code="generate.candidate_invalid",
                    reason=str(exc),
                )
            else:
                if candidate.id in retained_ids:
                    rejection = DroppedCandidate(
                        attempt=attempt,
                        candidate=candidate_name,
                        code="generate.candidate_duplicate",
                        reason=f"duplicate check id {candidate.id!r}",
                    )
                elif len(retained) >= requested_count:
                    rejection = DroppedCandidate(
                        attempt=attempt,
                        candidate=candidate_name,
                        code="generate.candidate_excess",
                        reason="candidate exceeds the requested count",
                    )
                else:
                    retained.append(candidate)
                    retained_ids.add(candidate.id)
                    continue
            summaries.append(f"{rejection.candidate} [{rejection.code}]: {rejection.reason}")
            # Attempt-one invalid/duplicate items are correction inputs, not final drops.
            # Valid excess items are final immediately; every attempt-two rejection is final.
            if attempt == 2 or rejection.code == "generate.candidate_excess":
                dropped.append(rejection)
                if warning_sink is not None:
                    warning_sink(rejection)


def _propose(
    client: StructuredOutputClient,
    request: ProposalRequest,
) -> RawProposalBatch:
    try:
        return RawProposalBatch.model_validate(client.propose(request))
    except GraphCheckError:
        raise
    except ValidationError:
        raise GraphCheckError(
            "generate.output_invalid",
            "The provider did not return a valid structured candidate batch.",
            "Choose a model with structured-output support or reduce docs/count.",
        ) from None


def _load_generation_project_config(project_root: Path) -> ProjectConfig:
    try:
        return load_project_config(project_root)
    except GraphCheckError as exc:
        cause = exc.__cause__
        field = None
        if isinstance(cause, ValidationError):
            errors = cause.errors(include_url=False, include_context=False, include_input=False)
            if errors:
                field = ".".join(str(part) for part in errors[0]["loc"])
        message = (
            f"graphcheck.yml field {field} is invalid." if field else "graphcheck.yml is invalid."
        )
        raise GraphCheckError(
            "generate.config_invalid",
            message,
            "Correct the named `graphcheck.yml` field.",
        ) from None
