"""Tests for forget_guard: anti-cross-contamination / domain binding."""

from nirvana import forget_guard


def test_domain_of_parses_host():
    """Host-level binding: tam host kimlik olarak kullanılır (anti-karışıklık)."""
    d = forget_guard.domain_of("https://shop.example.com/cart")
    assert d == "shop.example.com"


def test_domain_of_strips_www():
    d = forget_guard.domain_of("https://www.example.com")
    assert d == "example.com"


def test_proof_matches_form_true():
    """Proof card for example.com matches form from example.com."""
    result = forget_guard.proof_matches_form("example.com", "example.com")
    assert result is True


def test_proof_matches_form_false_on_cross_domain():
    """Proof card for evil.com does NOT match form from example.com."""
    result = forget_guard.proof_matches_form("evil.com", "example.com")
    assert result is False
