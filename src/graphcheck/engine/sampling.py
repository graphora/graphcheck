"""Deterministic sampling primitives for the C1 engine.

In-memory callers can select indices from a canonically ordered population with Floyd's
algorithm. Neo4j-backed plans use the compatible seeded cubic hash parameters below; their
candidate identifier and tie-break order must also be stable for a sample to be reproducible.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from graphcheck.contracts.results import Estimate

CONFIDENCE_95 = 0.95
_Z_95 = 1.959963984540054
_SEED_DOMAIN = b"graphcheck:c1:sampling-seed:v1"
_RNG_DOMAIN = b"graphcheck:c1:sampling-rng:v1"
_CYPHER_HASH_DOMAIN = b"graphcheck:c1:cypher-hash:v1"
CYPHER_SAMPLE_MODULUS = 2_147_483_647
CYPHER_HASH_PARAMETER_NAMES = (
    "sample_hash_a",
    "sample_hash_b",
    "sample_hash_c",
    "sample_hash_d",
)


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or value.isspace():
        raise ValueError(f"{name} must not be empty")
    return value


def _seed_material(seed: int | str) -> bytes:
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        raise TypeError("seed must be a non-negative integer or non-empty string")
    if isinstance(seed, int):
        if seed < 0:
            raise ValueError("seed must be non-negative")
        return b"integer:" + str(seed).encode("ascii")
    if not seed or seed.isspace():
        raise ValueError("seed must not be empty")
    return b"string:" + seed.encode("utf-8")


def _length_prefix(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def derive_check_seed(
    seed: int | str,
    *,
    graph_fingerprint: str,
    suite_sha: str,
    check_id: str,
) -> int:
    """Derive a stable, domain-separated seed for one check.

    Length-prefixing keeps component boundaries unambiguous.  Including both graph
    and suite identities means the same user seed does not accidentally reuse a
    sample after either input changes.
    """

    components = (
        _seed_material(seed),
        _identifier(graph_fingerprint, "graph_fingerprint").encode("utf-8"),
        _identifier(suite_sha, "suite_sha").encode("utf-8"),
        _identifier(check_id, "check_id").encode("utf-8"),
    )
    digest = hashlib.sha256(_SEED_DOMAIN + b"".join(map(_length_prefix, components))).digest()
    return int.from_bytes(digest, "big")


class _HashRandom:
    """Small counter-mode hash generator with an unbiased ``randbelow``."""

    def __init__(self, seed: int) -> None:
        seed = _integer(seed, "seed")
        self._key = hashlib.sha256(_RNG_DOMAIN + _length_prefix(str(seed).encode("ascii"))).digest()
        self._counter = 0

    def _read(self, size: int) -> bytes:
        output = bytearray()
        while len(output) < size:
            if self._counter >= 1 << 128:
                raise OverflowError("sampling random stream exhausted")
            output.extend(hashlib.sha256(self._key + self._counter.to_bytes(16, "big")).digest())
            self._counter += 1
        return bytes(output[:size])

    def randbelow(self, upper: int) -> int:
        upper = _integer(upper, "upper", minimum=1)
        if upper == 1:
            return 0
        bits = upper.bit_length()
        byte_count = (bits + 7) // 8
        mask = (1 << bits) - 1
        while True:
            candidate = int.from_bytes(self._read(byte_count), "big") & mask
            if candidate < upper:
                return candidate


def deterministic_sample_indices(
    population: int,
    sample_size: int,
    *,
    seed: int,
) -> tuple[int, ...]:
    """Return a stable, uniform subset of ``range(population)``.

    Floyd's algorithm uses O(sample_size) memory and random draws, so choosing a
    small sample does not require walking a multi-million-element population.
    Returned indices are sorted to give callers a deterministic execution order.
    """

    population = _integer(population, "population")
    sample_size = _integer(sample_size, "sample_size")
    seed = _integer(seed, "seed")
    if sample_size > population:
        raise ValueError("sample_size must not exceed population")
    if sample_size == 0:
        return ()

    random = _HashRandom(seed)
    selected: set[int] = set()
    for candidate_max in range(population - sample_size, population):
        candidate = random.randbelow(candidate_max + 1)
        selected.add(candidate_max if candidate in selected else candidate)
    return tuple(sorted(selected))


def cypher_hash_parameters(seed: int) -> dict[str, int]:
    """Return coefficients for a seeded cubic hash that is portable to Neo4j 4.4.

    A seed-derived affine ordering is deterministic but not a statistically fair bottom-k sample:
    for dense IDs it over-selects values at the ends of the range. A random cubic polynomial over
    a prime field provides a four-wise-independent ranking family and removes that positional bias.
    Horner evaluation keeps every intermediate below ``p**2``, inside Neo4j's signed 64-bit range.
    """

    seed = _integer(seed, "seed")
    material = _length_prefix(str(seed).encode("ascii"))
    digest = hashlib.sha256(_CYPHER_HASH_DOMAIN + material).digest()
    return {
        name: int.from_bytes(digest[offset : offset + 8], "big") % CYPHER_SAMPLE_MODULUS
        for name, offset in zip(CYPHER_HASH_PARAMETER_NAMES, range(0, 32, 8), strict=True)
    }


def cypher_hash_expression(value: str) -> str:
    """Render the fixed parameterized Horner expression for an integer Cypher value."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Cypher hash value expression must be a non-blank string")
    modulus = CYPHER_SAMPLE_MODULUS
    return (
        f"((((($sample_hash_a * ({value}) + $sample_hash_b) % {modulus}) "
        f"* ({value}) + $sample_hash_c) % {modulus}) * ({value}) "
        f"+ $sample_hash_d) % {modulus}"
    )


def cypher_hash_value(value: int, params: Mapping[str, object]) -> int:
    """Evaluate the same cubic hash in Python for parity and distribution verification."""

    hashed = _integer(value, "value") % CYPHER_SAMPLE_MODULUS
    coefficients = [_integer(params.get(name), name) for name in CYPHER_HASH_PARAMETER_NAMES]
    if any(coefficient >= CYPHER_SAMPLE_MODULUS for coefficient in coefficients):
        raise ValueError("Cypher hash coefficients must be below the sampling modulus")
    result = coefficients[0]
    for coefficient in coefficients[1:]:
        result = (result * hashed + coefficient) % CYPHER_SAMPLE_MODULUS
    return result


def wilson_estimate(
    positive_count: int,
    sample_size: int,
    population: int,
) -> Estimate:
    """Build a SPEC-01 estimate with a two-sided 95% Wilson proportion interval."""

    positive_count = _integer(positive_count, "positive_count")
    sample_size = _integer(sample_size, "sample_size", minimum=1)
    population = _integer(population, "population", minimum=1)
    if positive_count > sample_size:
        raise ValueError("positive_count must not exceed sample_size")
    if sample_size >= population:
        raise ValueError("an exhaustive population must use estimate=false")

    proportion = positive_count / sample_size
    z_squared = _Z_95**2
    denominator = 1 + z_squared / sample_size
    center = (proportion + z_squared / (2 * sample_size)) / denominator
    margin = (
        _Z_95
        * math.sqrt((proportion * (1 - proportion) + z_squared / (4 * sample_size)) / sample_size)
        / denominator
    )
    lower = 0.0 if positive_count == 0 else max(0.0, center - margin)
    upper = 1.0 if positive_count == sample_size else min(1.0, center + margin)
    interval = (lower, upper)
    return Estimate(
        sample_size=sample_size,
        population=population,
        confidence=CONFIDENCE_95,
        ci=interval,
    )


@dataclass(frozen=True, slots=True)
class SamplingDecision:
    """The exact or sampled execution decision for one check."""

    population: int
    sample_size: int
    seed: int
    exact: bool

    def __post_init__(self) -> None:
        population = _integer(self.population, "population")
        sample_size = _integer(self.sample_size, "sample_size")
        _integer(self.seed, "seed")
        if sample_size > population:
            raise ValueError("sample_size must not exceed population")
        if not isinstance(self.exact, bool):
            raise TypeError("exact must be boolean")
        if self.exact and sample_size != population:
            raise ValueError("an exact decision must cover the full population")
        if not self.exact and not 0 < sample_size < population:
            raise ValueError("a sampled decision must be a non-empty strict subset")

    @property
    def sampled(self) -> bool:
        return not self.exact

    def estimate(self, positive_count: int) -> Estimate | Literal[False]:
        """Return estimate metadata, or ``False`` for an exhaustive decision."""

        positive_count = _integer(positive_count, "positive_count")
        if positive_count > self.sample_size:
            raise ValueError("positive_count must not exceed sample_size")
        if not self.sampled:
            return False
        return wilson_estimate(positive_count, self.sample_size, self.population)


@dataclass(frozen=True, slots=True)
class SamplingPolicy:
    """Seeded policy deciding when and how a check samples its population."""

    exhaustive_limit: int
    sample_size: int
    seed: int | str = 0

    def __post_init__(self) -> None:
        _integer(self.exhaustive_limit, "exhaustive_limit")
        _integer(self.sample_size, "sample_size", minimum=1)
        _seed_material(self.seed)

    def check_seed(
        self,
        *,
        graph_fingerprint: str,
        suite_sha: str,
        check_id: str,
    ) -> int:
        return derive_check_seed(
            self.seed,
            graph_fingerprint=graph_fingerprint,
            suite_sha=suite_sha,
            check_id=check_id,
        )

    def decide(
        self,
        population: int,
        *,
        graph_fingerprint: str,
        suite_sha: str,
        check_id: str,
    ) -> SamplingDecision:
        population = _integer(population, "population")
        seed = self.check_seed(
            graph_fingerprint=graph_fingerprint,
            suite_sha=suite_sha,
            check_id=check_id,
        )
        if population <= self.exhaustive_limit or population <= self.sample_size:
            return SamplingDecision(
                population=population,
                sample_size=population,
                seed=seed,
                exact=True,
            )
        return SamplingDecision(
            population=population,
            sample_size=self.sample_size,
            seed=seed,
            exact=False,
        )
