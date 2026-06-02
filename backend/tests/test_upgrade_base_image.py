"""Base-image-for-major-jump logic (#81).

A PAN-OS *major* upgrade crosses the X.Y feature-train boundary (11.2 -> 12.1).
PAN-OS won't install a maintenance release of the new train until that train's
BASE image is downloaded — and the base is NOT always X.Y.0 (12.1's base is
**12.1.2**). The firewall's `request system software info` marks the base per
train with `release-type == "Base"`, so we read it rather than guessing.

These cover the pure logic (version parsing, base selection from a software
list, and the major-jump gate). The shapes mirror real `list_software()`
output captured from a live PA-VM (12.1.2 = Base, 11.2.0 = Base).
"""

from __future__ import annotations

from app.core.command_proxy.pan_client import base_image_from_list, feature_train
from app.upgrade.services.upgrade import _is_major_jump

# Mirrors real `request system software info` output (trimmed): exactly one
# entry per train carries release_type == "Base", and for 12.1 that's 12.1.2.
SW = [
    {"version": "12.1.7", "release_type": "", "downloaded": False, "current": False},
    {"version": "12.1.4-h6", "release_type": "", "downloaded": False, "current": False},
    {"version": "12.1.4", "release_type": "", "downloaded": False, "current": False},
    {"version": "12.1.3", "release_type": "", "downloaded": False, "current": False},
    {"version": "12.1.2", "release_type": "Base", "downloaded": False, "current": False},
    {"version": "11.2.11", "release_type": "", "downloaded": False, "current": True},
    {"version": "11.2.0", "release_type": "Base", "downloaded": True, "current": False},
]


# ---------- feature_train ----------

def test_feature_train_parses_x_y():
    assert feature_train("11.2.11") == (11, 2)
    assert feature_train("12.1.4-h2") == (12, 1)
    assert feature_train("10.2.0") == (10, 2)


def test_feature_train_unparseable_is_none():
    assert feature_train("") is None
    assert feature_train(None) is None
    assert feature_train("11") is None
    assert feature_train("garbage") is None


# ---------- base_image_from_list ----------

def test_base_is_the_release_type_base_entry_not_dot_zero():
    # The whole point: 12.1's base is 12.1.2, NOT 12.1.0.
    assert base_image_from_list("12.1.4-h6", SW) == "12.1.2"
    assert base_image_from_list("12.1.7", SW) == "12.1.2"


def test_base_for_normal_dot_zero_train():
    assert base_image_from_list("11.2.11", SW) == "11.2.0"


def test_base_when_target_is_itself_the_base():
    # Caller treats base == target as "nothing extra to download".
    assert base_image_from_list("12.1.2", SW) == "12.1.2"


def test_base_none_when_train_absent():
    assert base_image_from_list("9.1.5", SW) is None


def test_base_fallback_to_lowest_non_hotfix_when_no_marker():
    # Older PAN-OS that doesn't populate release-type -> lowest non-hotfix wins.
    sw = [
        {"version": "11.1.3-h1", "release_type": ""},
        {"version": "11.1.2", "release_type": ""},
        {"version": "11.1.0", "release_type": ""},
    ]
    assert base_image_from_list("11.1.3", sw) == "11.1.0"


# ---------- _is_major_jump ----------

def test_major_jump_crosses_feature_train():
    assert _is_major_jump("11.2.11", "12.1.4") is True
    assert _is_major_jump("11.1.6", "11.2.0") is True


def test_same_train_is_not_a_major_jump():
    assert _is_major_jump("11.2.4", "11.2.11") is False
    assert _is_major_jump("12.1.2", "12.1.4-h6") is False


def test_unknown_current_errs_toward_checking():
    assert _is_major_jump(None, "12.1.4") is True
    assert _is_major_jump("", "12.1.4") is True


def test_unparseable_target_is_not_a_jump():
    assert _is_major_jump("11.2.11", "garbage") is False
