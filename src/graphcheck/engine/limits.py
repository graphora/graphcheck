"""Published limits for the measured core/PII workload; see compatibility.md."""

from graphcheck.contracts.results import ResultsTarget
from graphcheck.errors import GraphCheckError

MAX_SUPPORTED_NODES = 10_000_000


def require_supported_size(target: ResultsTarget) -> None:
    if target.nodes is not None and target.nodes > MAX_SUPPORTED_NODES:
        raise GraphCheckError(
            "engine.graph_size_exceeded",
            f"Graph size ({target.nodes} nodes, {target.relationships} relationships) exceeds "
            f"the supported ceiling of {MAX_SUPPORTED_NODES:,} nodes.",
            "Audit a smaller database or partition within the node limit. Selecting fewer checks "
            "or enabling sampling does not bypass the graph-size ceiling. "
            "See docs/reference/compatibility.md#supported-graph-size.",
        )
