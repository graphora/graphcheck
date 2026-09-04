import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from graphcheck.contracts.results import Estimate
from graphcheck.engine.sampling import (
    CONFIDENCE_95,
    CYPHER_SAMPLE_MODULUS,
    SamplingDecision,
    SamplingPolicy,
    cypher_hash_expression,
    cypher_hash_parameters,
    cypher_hash_value,
    derive_check_seed,
    deterministic_sample_indices,
    wilson_estimate,
)


def _decision(policy: SamplingPolicy, population: int, check_id: str = "hub-outlier"):
    return policy.decide(
        population,
        graph_fingerprint="sha256:graph",
        suite_sha="sha256:suite",
        check_id=check_id,
    )


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"exhaustive_limit": -1, "sample_size": 10}, ValueError),
        ({"exhaustive_limit": True, "sample_size": 10}, TypeError),
        ({"exhaustive_limit": 100, "sample_size": 0}, ValueError),
        ({"exhaustive_limit": 100, "sample_size": 1.5}, TypeError),
        ({"exhaustive_limit": 100, "sample_size": 10, "seed": -1}, ValueError),
        ({"exhaustive_limit": 100, "sample_size": 10, "seed": ""}, ValueError),
        ({"exhaustive_limit": 100, "sample_size": 10, "seed": True}, TypeError),
    ],
)
def test_policy_rejects_invalid_configuration(kwargs, error):
    with pytest.raises(error):
        SamplingPolicy(**kwargs)


@pytest.mark.parametrize("population", [-1, True, 1.5])
def test_policy_rejects_invalid_population(population):
    with pytest.raises((TypeError, ValueError)):
        _decision(SamplingPolicy(exhaustive_limit=10, sample_size=3), population)


@pytest.mark.parametrize("field", ["graph_fingerprint", "suite_sha", "check_id"])
def test_seed_identity_fields_are_required(field):
    values = {
        "graph_fingerprint": "graph",
        "suite_sha": "suite",
        "check_id": "check",
    }
    values[field] = ""
    with pytest.raises(ValueError):
        derive_check_seed(7, **values)


def test_seed_components_are_length_prefixed_and_type_tagged():
    one = derive_check_seed(7, graph_fingerprint="a", suite_sha="bc", check_id="d")
    two = derive_check_seed(7, graph_fingerprint="ab", suite_sha="c", check_id="d")
    text_seed = derive_check_seed("7", graph_fingerprint="a", suite_sha="bc", check_id="d")

    assert one != two
    assert one != text_seed


def test_known_seed_and_selection_are_pinned_across_python_versions():
    seed = derive_check_seed(
        "release-seed",
        graph_fingerprint="sha256:graph",
        suite_sha="sha256:suite",
        check_id="hub-outlier",
    )

    assert seed == (104486794838259924160579718683842748565530945358400966439722666519434178147757)
    assert deterministic_sample_indices(100, 8, seed=seed) == (10, 14, 45, 64, 77, 86, 89, 94)


def test_exact_at_limit_and_when_configured_sample_covers_population():
    policy = SamplingPolicy(exhaustive_limit=10, sample_size=20, seed=3)

    at_limit = _decision(policy, 10)
    sample_covers_all = _decision(policy, 15)

    for decision in (at_limit, sample_covers_all):
        assert decision.sampled is False
        assert decision.exact is True
        assert decision.sample_size == decision.population
        assert decision.estimate(0) is False


def test_empty_population_is_exact():
    decision = _decision(SamplingPolicy(exhaustive_limit=0, sample_size=10), 0)

    assert decision == SamplingDecision(
        population=0,
        sample_size=0,
        seed=decision.seed,
        exact=True,
    )
    assert decision.estimate(0) is False


def test_large_population_is_sampled_deterministically_per_check():
    policy = SamplingPolicy(exhaustive_limit=20, sample_size=8, seed="stable")

    first = _decision(policy, 1_000)
    repeat = _decision(policy, 1_000)
    other_check = _decision(policy, 1_000, check_id="other-check")

    assert first == repeat
    assert first.sampled is True
    assert first.exact is False
    assert first.sample_size == 8
    assert first.seed != other_check.seed


def test_cypher_hash_parameters_are_deterministic_and_seed_sensitive():
    first = cypher_hash_parameters(123)

    assert first == cypher_hash_parameters(123)
    assert first != cypher_hash_parameters(124)
    assert set(first) == {"sample_hash_a", "sample_hash_b", "sample_hash_c", "sample_hash_d"}
    assert all(0 <= value < CYPHER_SAMPLE_MODULUS for value in first.values())


def test_cypher_cubic_hash_does_not_bias_positions_in_a_dense_id_range():
    selected = dict.fromkeys(range(10), 0)
    for seed in range(8192):
        params = cypher_hash_parameters(seed)
        winner = min(selected, key=lambda node_id: cypher_hash_value(node_id, params))
        selected[winner] += 1

    for count in selected.values():
        assert count / sum(selected.values()) == pytest.approx(0.1, abs=0.02)


def test_seeded_hash_gate_is_near_uniform_across_many_seeds():
    threshold = CYPHER_SAMPLE_MODULUS // 10
    included = sum(
        cypher_hash_value(42, cypher_hash_parameters(seed)) < threshold for seed in range(1000)
    )

    assert 75 <= included <= 125


def test_cypher_hash_expression_uses_only_parameterized_safe_horner_products():
    expression = cypher_hash_expression("candidate")

    assert expression.count("candidate") == 3
    assert all(f"${name}" in expression for name in cypher_hash_parameters(1))
    assert str(CYPHER_SAMPLE_MODULUS) in expression


@pytest.mark.parametrize(
    "params",
    [
        {},
        {
            "sample_hash_a": CYPHER_SAMPLE_MODULUS,
            "sample_hash_b": 0,
            "sample_hash_c": 0,
            "sample_hash_d": 0,
        },
    ],
)
def test_cypher_hash_value_rejects_missing_or_out_of_field_coefficients(params):
    with pytest.raises((TypeError, ValueError)):
        cypher_hash_value(1, params)


@given(
    population=st.integers(min_value=1, max_value=10_000),
    sample_size=st.integers(min_value=0, max_value=200),
    seed=st.integers(min_value=0, max_value=2**128),
)
def test_sample_indices_are_a_stable_bounded_subset(population, sample_size, seed):
    sample_size = min(sample_size, population)

    first = deterministic_sample_indices(population, sample_size, seed=seed)
    second = deterministic_sample_indices(population, sample_size, seed=seed)

    assert first == second
    assert first == tuple(sorted(set(first)))
    assert len(first) == sample_size
    assert all(0 <= index < population for index in first)


def test_wilson_estimate_matches_known_50_percent_interval():
    estimate = wilson_estimate(50, sample_size=100, population=1_000)

    assert isinstance(estimate, Estimate)
    assert estimate.sample_size == 100
    assert estimate.population == 1_000
    assert estimate.confidence == CONFIDENCE_95
    assert estimate.ci == pytest.approx((0.4038315303659956, 0.5961684696340044))


@pytest.mark.parametrize(
    ("positive_count", "expected"),
    [
        (0, (0.0, 0.03699349820698568)),
        (100, (0.9630065017930143, 1.0)),
    ],
)
def test_wilson_interval_handles_extreme_observations(positive_count, expected):
    estimate = wilson_estimate(positive_count, sample_size=100, population=1_000)

    assert estimate.ci == pytest.approx(expected)


@pytest.mark.parametrize(
    "args",
    [
        (-1, 10, 100),
        (11, 10, 100),
        (0, 0, 100),
        (1, 10, 9),
        (1, 10, 10),
        (True, 10, 100),
    ],
)
def test_wilson_estimate_rejects_invalid_or_exhaustive_counts(args):
    with pytest.raises((TypeError, ValueError)):
        wilson_estimate(*args)


@given(
    sample_size=st.integers(min_value=1, max_value=2_000),
    fraction=st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False),
)
def test_wilson_interval_is_ordered_bounded_and_contains_observed_rate(sample_size, fraction):
    positive_count = min(sample_size, round(fraction * sample_size))
    estimate = wilson_estimate(positive_count, sample_size, population=sample_size + 1)
    lower, upper = estimate.ci
    observed = positive_count / sample_size

    assert math.isfinite(lower) and math.isfinite(upper)
    assert 0 <= lower <= observed <= upper <= 1


def test_decision_emits_estimate_only_for_sampled_execution():
    policy = SamplingPolicy(exhaustive_limit=10, sample_size=5)
    exact = _decision(policy, 10)
    sampled = _decision(policy, 100)

    assert exact.estimate(3) is False
    assert sampled.estimate(3) == wilson_estimate(3, 5, 100)


def test_decision_rejects_positive_count_beyond_observed_rows_even_when_exact():
    exact = _decision(SamplingPolicy(exhaustive_limit=10, sample_size=5), 3)

    with pytest.raises(ValueError):
        exact.estimate(4)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"population": 10, "sample_size": 9, "seed": 1, "exact": True},
        {"population": 10, "sample_size": 0, "seed": 1, "exact": False},
        {"population": 10, "sample_size": 2, "seed": 1, "exact": "no"},
    ],
)
def test_sampling_decision_enforces_internal_invariants(kwargs):
    with pytest.raises((TypeError, ValueError)):
        SamplingDecision(**kwargs)
