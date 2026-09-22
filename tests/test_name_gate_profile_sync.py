# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_name_gate_profile_sync.py
=======================================
Regression test for the "AI name shows as Kestrel in the profile" bug.

Root cause: MainWindow builds ChatWidget (and its topbar profile picker)
as part of its own single-window shell BEFORE the first-run name gate
ever runs (see main.py: `window = MainWindow()` happens first,
`window.run_name_gate()` after). ChatWidget's profile picker reads
ai.profiles.list_profiles(), and the very first access to
ai.profiles auto-migrates whatever ai_main.get_assistant_name() returns
AT THAT MOMENT into profiles.json's first profile - which, before the
name gate has run, is still ai_main.DEFAULT_ASSISTANT_NAME ("Kestrel").
That migration only ever fires once, so a name typed into the gate
afterward only ever updated ai_main's own global value: every OTHER
place that shows "the AI profile" by name (Settings' profile list, the
chat topbar's own picker) stayed stuck on "Kestrel" forever, regardless
of what the user actually chose.

Fix: NameGateWidget._on_continue() now also syncs the active profile's
name after calling ai_main.set_assistant_name(), covering exactly the
premature-migration scenario reproduced below.
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
    """Isolates both ai.main's .env-backed assistant name AND
    ai.profiles' profiles.json from real disk state / real env vars.

    Explicit pop/restore rather than monkeypatch.delenv(raising=False):
    delenv on a var that doesn't already exist registers no cleanup
    action at all, so ai_main.set_assistant_name()'s direct
    `os.environ[key] = value` write (not itself going through
    monkeypatch) would otherwise leak into every later test in the same
    pytest process - which is exactly what happened here the first
    time around, breaking unrelated assertions in test_console_widget.py
    that check for the literal default name "Kestrel"."""
    import ai.main as ai_main
    import ai.profiles as profiles

    original_env_var = os.environ.pop("KESTREL_ASSISTANT_NAME", None)
    env_path = tmp_path / ".env"
    monkeypatch.setattr(ai_main, "ENV_PATH", str(env_path))

    profiles_path = tmp_path / "profiles.json"
    monkeypatch.setattr(profiles, "_PROFILES_PATH", str(profiles_path))

    yield ai_main, profiles

    if original_env_var is None:
        os.environ.pop("KESTREL_ASSISTANT_NAME", None)
    else:
        os.environ["KESTREL_ASSISTANT_NAME"] = original_env_var


def test_premature_profile_migration_gets_corrected_by_name_gate(isolated_state, qapp):
    """
    Reproduces the exact bug sequence: something (standing in for
    ChatWidget's profile picker) touches ai.profiles BEFORE the name
    gate runs, migrating the still-default "Kestrel" name into
    profiles.json. The name gate is then used to pick a real name.
    The active profile must end up with THAT name, not "Kestrel".
    """
    ai_main, profiles = isolated_state

    # Simulate ChatWidget's profile picker being built before the name
    # gate runs (MainWindow's real construction order) - this is what
    # triggers the premature migration.
    assert ai_main.get_assistant_name() == "Kestrel"
    premature = profiles.list_profiles()
    assert len(premature) == 1
    assert premature[0].name == "Kestrel"

    from app.name_gate_widget import NameGateWidget
    gate = NameGateWidget()
    gate._name_input.setText("Nova")
    gate._on_continue()

    assert ai_main.get_assistant_name() == "Nova"
    active = profiles.get_active_profile()
    assert active is not None
    assert active.name == "Nova", "the AI profile still shows the stale premigrated name"


def test_name_gate_is_a_noop_sync_when_no_premature_migration_happened(isolated_state, qapp):
    """If nothing touched ai.profiles before the gate ran, continuing
    should still leave the (freshly-created-on-demand) active profile
    correctly named - not raise, not create a mismatch."""
    ai_main, profiles = isolated_state

    from app.name_gate_widget import NameGateWidget
    gate = NameGateWidget()
    gate._name_input.setText("Juno")
    gate._on_continue()

    assert ai_main.get_assistant_name() == "Juno"
    active = profiles.get_active_profile()
    assert active is not None
    assert active.name == "Juno"


def test_name_gate_blank_field_falls_back_to_default_and_still_syncs(isolated_state, qapp):
    ai_main, profiles = isolated_state
    profiles.list_profiles()  # premature migration, same as MainWindow's real order

    from app.name_gate_widget import NameGateWidget
    gate = NameGateWidget()
    gate._name_input.setText("")
    gate._on_continue()

    assert ai_main.get_assistant_name() == ai_main.DEFAULT_ASSISTANT_NAME
    active = profiles.get_active_profile()
    assert active.name == ai_main.DEFAULT_ASSISTANT_NAME


def test_self_heals_a_profile_json_corrupted_before_the_name_gate_fix_existed(isolated_state, qapp):
    """
    Covers installs that already went through the premature-migration
    bug BEFORE the name_gate fix existed: profiles.json is already on
    disk with the active profile permanently stuck on "Kestrel", but
    ai_main's own configured name is correctly "Jasper" (the name gate
    ran fine at the time - set_assistant_name() worked - it's only the
    profile record that never got the memo). Loading profiles now
    should self-heal the active profile's name rather than requiring
    the gate to run again (it won't - is_assistant_name_configured()
    is already True).
    """
    ai_main, profiles = isolated_state

    ai_main.set_assistant_name("Jasper")
    assert ai_main.is_assistant_name_configured()

    # Manually construct a profiles.json exactly as the old bug would
    # have left it: active profile named "Kestrel", NOT "Jasper".
    import json
    stale_state = {
        "active_profile_id": "profile-1",
        "profiles": [{
            "id": "profile-1", "name": "Kestrel", "provider": "gemini",
            "model_id": "models/gemini-3-flash-preview", "created_at": 0.0,
        }],
    }
    with open(profiles._PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(stale_state, f)

    active = profiles.get_active_profile()
    assert active.name == "Jasper", "profiles.json's stale 'Kestrel' name was never self-healed"

    # And it should actually be persisted, not just patched in memory.
    with open(profiles._PROFILES_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["profiles"][0]["name"] == "Jasper"


def test_does_not_touch_profile_name_when_assistant_name_never_configured(isolated_state, qapp):
    """If the name was never explicitly configured (is_assistant_name_configured()
    is False - a fresh install that hasn't hit the name gate yet), the
    self-heal must not fire - there's nothing authoritative to correct
    the profile against yet."""
    ai_main, profiles = isolated_state
    assert not ai_main.is_assistant_name_configured()

    import json
    stale_state = {
        "active_profile_id": "profile-1",
        "profiles": [{
            "id": "profile-1", "name": "Kestrel", "provider": "gemini",
            "model_id": "models/gemini-3-flash-preview", "created_at": 0.0,
        }],
    }
    with open(profiles._PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(stale_state, f)

    active = profiles.get_active_profile()
    assert active.name == "Kestrel"
