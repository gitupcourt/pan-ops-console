"""Password policy.

Verifies the strength check rejects the common attacker-friendly
patterns and accepts strong ones. zxcvbn handles the smart bit; we just
confirm the wiring + the minimum-length floor.
"""

import pytest

from app.core.auth.services.passwords import (
    PasswordPolicyError,
    check_password_strength,
    hash_password,
    verify_password,
)


@pytest.mark.parametrize(
    "weak",
    [
        "short",                  # too short, period
        "Password123!",           # the canonical "looks strong, isn't"
        "qwertyuiopas",           # keyboard walk, exactly 12 chars
        "aaaaaaaaaaaa",           # repetition, 12 chars
        "abcdefghijkl",           # alphabet walk
        "00000000000000000000",   # repetition, long
    ],
)
def test_rejects_weak(weak):
    with pytest.raises(PasswordPolicyError):
        check_password_strength(weak)


@pytest.mark.parametrize(
    "strong",
    [
        "correct horse battery staple",       # the diceware classic
        "raccoon-orchestra-pickle-7",          # 4 uncommon words + digit
        "the rain in spain stays mainly",      # long phrase
    ],
)
def test_accepts_strong(strong):
    # Should not raise
    check_password_strength(strong)


def test_user_inputs_penalize():
    """A password that contains the username/email should fail even if
    it would otherwise be strong. Protects against "name + 12345"."""
    with pytest.raises(PasswordPolicyError):
        check_password_strength(
            "courtland-courtland-1234",
            user_inputs=["courtland", "court@example.com"],
        )


def test_hash_verify_roundtrip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True
    assert verify_password("wrong password", h) is False


def test_verify_handles_no_hash():
    """Users with no local password (future OIDC-only accounts) must
    not be able to authenticate via the password path."""
    assert verify_password("anything", None) is False
    assert verify_password("anything", "") is False


def test_verify_no_hash_still_runs_argon2_for_timing(monkeypatch):
    """F-6: the no-hash path must still exercise Argon2 (against the dummy
    hash) so its response time matches the wrong-password path — otherwise
    an instant False leaks which usernames exist. We assert the hasher's
    verify is invoked even when there's no stored hash."""
    from app.core.auth.services import passwords

    calls = {"n": 0}
    real_verify = passwords._hasher.verify

    def _spy(h, p):
        calls["n"] += 1
        return real_verify(h, p)

    monkeypatch.setattr(passwords._hasher, "verify", _spy)
    assert verify_password("anything", None) is False
    # The dummy verify ran (and mismatched) — work was done, not skipped.
    assert calls["n"] == 1
