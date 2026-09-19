"""Tier 2: Boundary & Corner Cases — B15: Identity Engine Boundaries.

Forwarder/alias to test_b15_identity_fallback_boundaries.py.
"""

from tests.e2e.tier2_boundaries.test_b15_identity_fallback_boundaries import (
    test_b15_empty_url_handling,
    test_b15_url_with_fragment_stripped,
    test_b15_url_with_hundred_query_parameters,
    test_b15_url_with_lowercase_scheme_and_host,
    test_b15_url_with_trailing_slash_normalization,
)

__all__ = [
    "test_b15_empty_url_handling",
    "test_b15_url_with_fragment_stripped",
    "test_b15_url_with_hundred_query_parameters",
    "test_b15_url_with_lowercase_scheme_and_host",
    "test_b15_url_with_trailing_slash_normalization",
]
