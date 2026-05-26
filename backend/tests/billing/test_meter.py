"""Tests for Stripe Billing Meter event recording (BILL-01).

Covers:
- record_gpu_usage calls stripe.billing.MeterEvent.create with correct args
- The 'value' in the payload is a string, not an int (Stripe API requirement)

Implementation target: Plan 03-02.
"""

from unittest.mock import patch

from billing.stripe_client import record_gpu_usage


class TestGPUUsageMeter:
    """BILL-01: GPU usage is recorded as a Stripe Billing Meter event."""

    def test_record_gpu_usage_calls_stripe(self):
        """Mock stripe.billing.MeterEvent.create. Verify it is called with:
        - event_name='gpu_seconds'
        - payload={'stripe_customer_id': 'cus_123', 'value': '3600'}
        - idempotency_key='gpu_usage_<job_id>'
        Note: 'value' must be a string ('3600'), not an integer (3600).
        """
        with patch("billing.stripe_client.stripe.billing.MeterEvent.create") as mock_create:
            record_gpu_usage("cus_123", "job-abc", 3600)
            mock_create.assert_called_once_with(
                event_name="gpu_seconds",
                payload={"stripe_customer_id": "cus_123", "value": "3600"},
                idempotency_key="gpu_usage_job-abc",
            )

    def test_record_gpu_usage_value_is_string(self):
        """Verify the 'value' key in the MeterEvent payload is a str instance,
        not an int. The Stripe Billing Meters API rejects integer values and
        this distinction is easy to get wrong.
        """
        with patch("billing.stripe_client.stripe.billing.MeterEvent.create") as mock_create:
            record_gpu_usage("cus_456", "job-xyz", 1200)
            call_payload = mock_create.call_args[1]["payload"]
            assert isinstance(call_payload["value"], str), (
                f"Payload 'value' must be str, got {type(call_payload['value'])}"
            )
