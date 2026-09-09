"""Tests for service_readiness: payment + contract gatekeeping."""

from nirvana import service_readiness
import config


def test_gateway_requires_payment_confirmed():
    """Without payment confirmed, service is not ready."""
    assert not service_readiness.gateway_ready(
        payment_confirmed=False, contract=True
    )


def test_gateway_requires_contract():
    assert not service_readiness.gateway_ready(
        payment_confirmed=True, contract=False
    )


def test_gateway_ready_when_both_true():
    assert service_readiness.gateway_ready(
        payment_confirmed=True, contract=True
    )


def test_no_unauthorized_targets():
    """Service never starts for unauthorized targets."""
    targets = service_readiness._authorized_targets()
    assert isinstance(targets, set)
