"""Tests for GPU cost estimation (BILL-02).

These are GREEN tests — they run immediately against the estimate module.
No stubs, no external services required.

Covers:
- estimate_cost_range returns a valid (low, high) tuple of floats
- Cost scales with design count
"""

from billing.estimate import estimate_cost_range


class TestCostEstimation:
    """BILL-02: Cost estimates are plausible and scale with design parameters."""

    def test_estimate_returns_tuple(self):
        """Call estimate_cost_range("rfdiffusion") and verify it returns a
        2-tuple of floats where low <= high and both are positive.
        """
        result = estimate_cost_range("rfdiffusion")
        assert isinstance(result, tuple), "Should return a tuple"
        assert len(result) == 2, "Tuple should have exactly 2 elements"
        low, high = result
        assert isinstance(low, float), f"Low should be float, got {type(low)}"
        assert isinstance(high, float), f"High should be float, got {type(high)}"
        assert low > 0, "Low estimate should be positive"
        assert low <= high, f"Low ({low}) should be <= high ({high})"

    def test_estimate_scales_with_designs(self):
        """Call estimate_cost_range("bindcraft", num_designs=100) and verify
        the high estimate exceeds the single-design high estimate.
        This validates the design count multiplier is working correctly.
        """
        single_low, single_high = estimate_cost_range("bindcraft", num_designs=1)
        batch_low, batch_high = estimate_cost_range("bindcraft", num_designs=100)
        assert batch_high > single_high, (
            f"100-design estimate ({batch_high}) should exceed "
            f"1-design estimate ({single_high})"
        )
