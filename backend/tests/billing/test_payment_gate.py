"""Tests for payment method gate (BILL-03).

Covers:
- check_payment_method returns True when customer has a default payment method
- check_payment_method returns False when customer has no default payment method

Implementation target: Plan 03-02.
"""

import pytest


class TestPaymentGate:
    """BILL-03: Jobs are gated on payment method being on file."""

    def test_check_payment_method_true(self):
        """Mock stripe.Customer.retrieve to return a customer object with
        invoice_settings.default_payment_method set to a non-None value.
        Verify check_payment_method returns True.

        Stub — implementation in Plan 03-02.
        """
        pytest.skip("STUB -- implementation in Plan 03-02")

    def test_check_payment_method_false(self):
        """Mock stripe.Customer.retrieve to return a customer object with
        invoice_settings.default_payment_method set to None or empty.
        Verify check_payment_method returns False.

        Stub — implementation in Plan 03-02.
        """
        pytest.skip("STUB -- implementation in Plan 03-02")
