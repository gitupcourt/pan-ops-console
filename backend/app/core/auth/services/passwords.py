"""Password hashing + strength checking.

Argon2id via argon2-cffi (defaults are sane for 2026 — m=64MiB, t=3, p=4).
Strength check via zxcvbn — actually-smart about common patterns, leetspeak,
keyboard walks, breached-password lists. The arbitrary "1 upper / 1 number /
1 symbol" rules of yore are worse than worthless; they push users to
predictable substitutions.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from zxcvbn import zxcvbn

# Module-level hasher: ~80 ms per hash on a modern x86 CPU, which is the
# right neighborhood for an interactive login. If logins feel slow, lower
# `time_cost`; do not lower `memory_cost`.
_hasher = PasswordHasher()

# Minimums. zxcvbn score is 0..4 (4 = "very strong"). We require >=3
# ("safely unguessable; moderate protection from offline slow-hash scenario").
MIN_LENGTH = 12
MIN_ZXCVBN_SCORE = 3


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    """Constant-time compare. Returns False (not raises) on mismatch so the
    caller can decide how to respond. Returns False for users with no
    password set (e.g. OIDC-only accounts).
    """
    if not hashed:
        return False
    try:
        _hasher.verify(hashed, plain)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def needs_rehash(hashed: str) -> bool:
    """True if the stored hash uses older Argon2 parameters than current
    defaults. Call after a successful verify and re-hash if True.
    """
    return _hasher.check_needs_rehash(hashed)


class PasswordPolicyError(ValueError):
    """Raised when a password fails the strength policy. The message is
    safe to surface to end users.
    """


def check_password_strength(password: str, *, user_inputs: list[str] | None = None) -> None:
    """Raise PasswordPolicyError if the password is too weak.

    user_inputs lets zxcvbn penalize passwords that contain the user's own
    username, email, etc. Pass [username, email] when available.
    """
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {MIN_LENGTH} characters long."
        )
    result = zxcvbn(password, user_inputs=user_inputs or [])
    score = result.get("score", 0)
    if score < MIN_ZXCVBN_SCORE:
        # zxcvbn's feedback is genuinely helpful — surface it verbatim.
        feedback = result.get("feedback", {}) or {}
        warning = feedback.get("warning") or "This password is too easy to guess."
        suggestions = feedback.get("suggestions") or [
            "Add another word or two. Uncommon words are best."
        ]
        # Avoid "must contain N specials" guidance; zxcvbn already knows better.
        msg = warning
        if suggestions:
            msg = f"{warning} {' '.join(suggestions)}"
        raise PasswordPolicyError(msg.strip())
