"""Tests for payment method gate (BILL-03).

Covers:
- check_payment_method returns True when customer has a default payment method
- check_payment_method returns False when customer has no default payment method

Implementation target: Plan 03-02.
"""

from unittest.mock import MagicMock, patch

from billing.stripe_client import check_payment_method


class TestPaymentGate:
    """BILL-03: Jobs are gated on payment method being on file."""

    def test_check_payment_method_true(self):
        """Mock stripe.Customer.retrieve to return a customer object with
        invoice_settings.default_payment_method set to a non-None value.
        Verify check_payment_method returns True.
        """
        mock_customer = MagicMock()
        mock_customer.invoice_settings.default_payment_method = "pm_xxx"
        with patch("billing.stripe_client.stripe.Customer.retrieve", return_value=mock_customer):
            assert check_payment_method("cus_123") is True

    def test_check_payment_method_false(self):
        """Mock stripe.Customer.retrieve to return a customer object with
        invoice_settings.default_payment_method set to None or empty.
        Verify check_payment_method returns False.
        """
        mock_customer = MagicMock()
        mock_customer.invoice_settings.default_payment_method = None
        with patch("billing.stripe_client.stripe.Customer.retrieve", return_value=mock_customer):
            assert check_payment_method("cus_123") is False
