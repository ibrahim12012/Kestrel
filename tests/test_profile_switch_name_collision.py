# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_profile_switch_name_collision.py
==============================================
Regression test for: "switching between 3 profiles with 3 different
names, 2 of them end up sharing one name - only 2 distinct names left
out of 3."

Root cause: ai.profiles.activate_profile() used to call
ai_main.set_assistant_name(profile.name) - updating the GLOBAL
assistant name to the profile being switched TO - and only THEN call
_load_state() a second time to record the new active_profile_id.
_load_state() always runs _self_heal_active_name(), which exists to
fix a completely different, one-time legacy-migration bug (see
test_name_gate_profile_sync.py) - it compares
ai_main.get_assistant_name() against whichever profile
state["active_profile_id"] currently points at, and overwrites that
profile's OWN name if they don't match.

Called in that order, at the moment of the second _load_state() call,
ai_main.get_assistant_name() already returns the NEW profile's name,
but state["active_profile_id"] STILL points at the OLD (about-to-be-
replaced) active profile - because activate_profile() hadn't updated
it yet. _self_heal_active_name() sees that mismatch, assumes it's the
legacy bug, and "fixes" it by overwriting the OLD profile's name with
the NEW one. Switching Alpha -> Bravo left BOTH profiles named "Bravo"
in profiles.json, permanently, until something else happened to fix
that profile's name again.

Fix: activate_profile() now loads state exactly ONCE, before touching
any of ai_main's globals, and reuses that same dict to write the new
active_profile_id - so _self_heal_active_name() is never invoked with
a name that's already moved on from the id it's being compared against.
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

for _name in ("pyautogui", "keyboard", "mouse", "AppOpener"):
    if _name not in sys.modules or not isinstance(sys.modules[_name], MagicMock):
        sys.modules[_name] = MagicMock(name=_name)
if not isinstance(sys.modules["pyautogui"].FailSafeException, type):
    sys.modules["pyautogui"].FailSafeException = type("FailSafeException", (Exception,), {})
if "google.genai" not in sys.modules:
    _google_mock = MagicMock(name="google")
    _genai_mock = MagicMock(name="google.genai")
    _types_mock = MagicMock(name="google.genai.types")
    _genai_mock.types = _types_mock
    _google_mock.genai = _genai_mock
    sys.modules["google"] = _google_mock
    sys.modules["google.genai"] = _genai_mock
    sys.modules["google.genai.types"] = _types_mock


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Same isolation as test_name_gate_profile_sync.py's own fixture - see that file's docstring for why the explicit pop/restore matters
    rather than monkeypatch.delenv(raising=False)."""
    import ai.main as ai_main
    import ai.profiles as profiles

    original_env_var = os.environ.pop("KESTREL_ASSISTANT_NAME", None)
    monkeypatch.setattr(ai_main, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(profiles, "_PROFILES_PATH", str(tmp_path / "profiles.json"))

    yield ai_main, profiles

    if original_env_var is None:
        os.environ.pop("KESTREL_ASSISTANT_NAME", None)
    else:
        os.environ["KESTREL_ASSISTANT_NAME"] = original_env_var


def _make_three_distinct_profiles(profiles):
    """Sets up exactly the scenario in the bug report: three profiles,
    three different names, none of them the default."""
    migrated = profiles.list_profiles()[0]  # first-run migration's own auto-created profile
    profiles.rename_profile(migrated.id, "Alpha")
    bravo = profiles.create_profile("Bravo", provider="gemini")
    charlie = profiles.create_profile("Charlie", provider="gemini")
    return migrated, bravo, charlie


def test_switching_between_three_profiles_keeps_three_distinct_names(isolated_state):
    ai_main, profiles = isolated_state
    alpha, bravo, charlie = _make_three_distinct_profiles(profiles)

    assert sorted(p.name for p in profiles.list_profiles()) == ["Alpha", "Bravo", "Charlie"]

    # The exact repro from the bug report: hop between all three,
    # checking after EVERY switch - the old bug corrupted whichever
    # profile had JUST been switched away FROM, so it only shows up
    # after at least one switch has already happened.
    for target in (bravo, charlie, alpha, bravo):
        profiles.activate_profile(target.id)
        names = [p.name for p in profiles.list_profiles()]
        assert len(set(names)) == 3, (
            f"profile names collapsed after switching to {target.name!r}: {names}"
        )
        assert sorted(names) == ["Alpha", "Bravo", "Charlie"]


def test_activate_profile_does_not_rename_the_previously_active_profile(isolated_state):
    """Narrower version of the above, isolating the exact before/after
    this bug corrupted: the profile you just switched AWAY from must
    keep its own name, not inherit the one you switched TO."""
    ai_main, profiles = isolated_state
    alpha, bravo, _charlie = _make_three_distinct_profiles(profiles)[:3]

    profiles.activate_profile(alpha.id)
    assert profiles.get_profile(bravo.id).name == "Bravo"

    profiles.activate_profile(bravo.id)
    assert profiles.get_profile(alpha.id).name == "Alpha", (
        "switching TO Bravo renamed Alpha (the profile switched away from) to Bravo too"
    )
    assert profiles.get_profile(bravo.id).name == "Bravo"


def test_activate_profile_correctly_updates_ai_main_and_active_id(isolated_state):
    """Not just "names don't collide" - activate_profile should still
    do its actual job: ai_main's globals and active_profile_id both
    end up pointing at the profile that was switched to."""
    ai_main, profiles = isolated_state
    alpha, bravo, _charlie = _make_three_distinct_profiles(profiles)[:3]

    profiles.activate_profile(bravo.id)

    assert ai_main.get_assistant_name() == "Bravo"
    assert profiles.get_active_profile_id() == bravo.id
    assert profiles.get_active_profile().name == "Bravo"
