from __future__ import annotations

from v17.posthog_observability import engineering_failure_fingerprint


def test_engineering_fingerprint_is_stable() -> None:
    left = {"sport": "NFL", "market_family": "ML", "scorer_stage": "hydrate"}
    right = {"scorer_stage": "hydrate", "market_family": "ML", "sport": "NFL"}
    assert engineering_failure_fingerprint(left) == engineering_failure_fingerprint(right)


def test_engineering_fingerprint_separates_stages() -> None:
    hydrate = {"sport": "NFL", "market_family": "ML", "scorer_stage": "hydrate"}
    score = {"sport": "NFL", "market_family": "ML", "scorer_stage": "score"}
    assert engineering_failure_fingerprint(hydrate) != engineering_failure_fingerprint(score)
