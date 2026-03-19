"""Tests for Stripe Billing Meter event recording (BILL-01).

Covers:
- record_gpu_usage calls stripe.billing.MeterEvent.create with correct args
- The 'value' in the payload is a string, not an int (Stripe API requirement)

Implementation target: Plan 03-02.
"""

import pytest


class TestGPUUsageMeter:
    """BILL-01: GPU usage is recorded as a Stripe Billing Meter event."""

    def test_record_gpu_usage_calls_stripe(self):
        """Mock stripe.billing.MeterEvent.create. Verify it is called with:
        - event_name='gpu_seconds'
        - payload={'stripe_customer_id': 'cus_123', 'value': '3600'}
        Note: 'value' must be a string ('3600'), not an integer (3600).

        Stub — implementation in Plan 03-02.
        """
        pytest.skip("STUB -- implementation in Plan 03-02")

    def test_record_gpu_usage_value_is_string(self):
        """Verify the 'value' key in the MeterEvent payload is a str instance,
        not an int. The Stripe Billing Meters API rejects integer values and
        this distinction is easy to get wrong.

        Stub — implementation in Plan 03-02.
        """
        pytest.skip("STUB -- implementation in Plan 03-02")
