# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/profiles.py
=================
Multiple named AI profiles - each its own (name, provider, model)
combination - with wake-word ROUTING between them: saying one profile's
name switches the live chat to that profile's provider/model, same as
saying another's switches to that one instead. Before this existed,
there was exactly one assistant identity at a time (ai.main's
get_assistant_name()/get_active_provider()/get_active_model(), all
single global values persisted to .env) and the wake word
(app/workers/wakeword_worker.py) only ever checked that one name.

Deliberately built as a thin layer ON TOP of that existing single-
assistant machinery rather than a replacement for it - activate_profile()
below just calls ai_main.set_assistant_name() +
ai_main.set_active_provider_and_model() together. Every OTHER piece of
this app that already reads "the current assistant" (the system prompt
render, the chat UI's copy, Settings > Assistant name, the wake word's
OWN "what name am I currently matching" check) keeps working completely
unchanged - a profile switch just changes what those two functions
currently return, exactly as if the user had gone into Settings and
changed them by hand.

Storage: a JSON file (not another .env key=value pair - a genuine list
of records doesn't fit that shape) at
paths.USER_DATA_PATH/profiles.json:
    {
      "active_profile_id": "<uuid>",
      "profiles": [
        {"id": "<uuid>", "name": "Aria", "provider": "gemini",
         "model_id": "models/gemini-3-flash-preview", "created_at": 1234567890.0},
        ...
      ]
    }

First-ever access (no file yet) migrates the pre-existing single-
assistant state into one profile automatically - see
_load_or_migrate() - so upgrading an existing install never resets or
loses the assistant someone already set up; it just becomes profile #1.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass

from paths import USER_DATA_PATH

_PROFILES_PATH = os.path.join(USER_DATA_PATH, "profiles.json")


@dataclass
class Profile:
    id: str
    name: str
    provider: str    # "gemini" | "openai" | "anthropic"
    model_id: str
    created_at: float
    system_prompt: str = ""  # extra instructions appended for this profile only - see ai.main._render_instructions


class ProfileNameTakenError(ValueError):
    """Raised by create_profile/rename_profile when `name` (case-
    insensitive) already belongs to a different profile - two profiles
    with the same wake word would make routing ambiguous, so this is
    checked before ever writing one."""


# ── Load / save ───────────────────────────────────────────────────────

def _default_state() -> dict:
    """
    First-run migration: wraps whatever ai.main already has configured
    (possibly still just its own defaults, if this is a first launch
    entirely) as a single profile, marked active - so profiles.json
    coming into existence never feels like a reset to someone who
    already renamed their assistant and picked a model before this
    feature existed.
    """
    import ai.main as ai_main
    profile = Profile(
        id=str(uuid.uuid4()),
        name=ai_main.get_assistant_name(),
        provider=ai_main.get_active_provider(),
        model_id=ai_main.get_active_model(),
        created_at=time.time(),
    )
    return {"active_profile_id": profile.id, "profiles": [asdict(profile)]}


def _load_state() -> dict:
    if not os.path.exists(_PROFILES_PATH):
        state = _default_state()
        _save_state(state)
        return _self_heal_active_name(state)
    try:
        with open(_PROFILES_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not state.get("profiles"):
            raise ValueError("empty profiles list")
        return _self_heal_active_name(state)
    except Exception as e:
        # Corrupt/unreadable file - re-migrate rather than crash every
        # caller of list_profiles()/get_active_profile() from here on.
        print(f"[profiles] Couldn't read {_PROFILES_PATH} ({e}) - recreating from current settings.")
        state = _default_state()
        _save_state(state)
        return state


def _self_heal_active_name(state: dict) -> dict:
    """
    Bug fix: profiles.json created by an install that hit the
    premature-migration bug (ai.profiles read before the first-run name
    gate ever ran - see app/name_gate_widget.py's own fix for the new-
    install case) has its active profile's name permanently stuck on
    the default "Kestrel", even though the name gate's fix means
    ai_main.get_assistant_name() itself now correctly returns whatever
    the person actually typed. That mismatch just sat there forever on
    any install that was already past first-run before this got fixed -
    the gate's own fix only prevents it for brand-new installs.
    Every time state is loaded, if a name has genuinely been configured
    (not just defaulted) and it doesn't match the active profile's own
    name, correct the active profile to match - ai_main's value is the
    one an explicit user action (the name gate, or a Settings rename)
    actually set; the profile record is what everything else displays.
    """
    import ai.main as ai_main
    if not ai_main.is_assistant_name_configured():
        return state
    configured_name = ai_main.get_assistant_name()
    active_id = state.get("active_profile_id")
    for p in state.get("profiles", []):
        if p.get("id") == active_id and p.get("name") != configured_name:
            print(f"[profiles] Active profile name {p.get('name')!r} didn't match the configured assistant name {configured_name!r} - correcting it.")
            p["name"] = configured_name
            _save_state(state)
            break
    return state


def _save_state(state: dict) -> None:
    os.makedirs(USER_DATA_PATH, exist_ok=True)
    with open(_PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ── Reads ─────────────────────────────────────────────────────────────

def list_profiles() -> "list[Profile]":
    return [Profile(**p) for p in _load_state()["profiles"]]


def get_profile(profile_id: str) -> "Profile | None":
    for p in list_profiles():
        if p.id == profile_id:
            return p
    return None


def get_active_profile_id() -> str:
    return _load_state()["active_profile_id"]


def get_active_profile() -> "Profile | None":
    return get_profile(get_active_profile_id())


def _name_taken(name: str, state: dict, *, ignore_id: "str | None" = None) -> bool:
    lowered = name.strip().lower()
    return any(
        p["name"].strip().lower() == lowered and p["id"] != ignore_id
        for p in state["profiles"]
    )


# ── Writes ────────────────────────────────────────────────────────────

def create_profile(name: "str | None" = None, provider: str = "gemini", model_id: "str | None" = None, system_prompt: str = "") -> Profile:
    """
    Adds a new profile. `name` left blank gets a random suggestion from
    ai.assistant_names (skipping every name already in use by another
    profile) rather than requiring the caller to think one up. Raises
    ProfileNameTakenError if an explicitly-given `name` collides
    (case-insensitively) with an existing profile - two profiles
    sharing a wake word can't be routed between.

    `system_prompt` is extra instructions specific to this profile only
    - appended after the shared base instructions.txt every profile
    already gets (see ai.main._render_instructions) rather than
    replacing it, so a profile only needs to describe what makes IT
    different, not restate every baseline behavior/safety rule from
    scratch. Left blank, a profile behaves exactly like the base
    assistant always has - nothing extra appended.
    """
    from ai.assistant_names import suggest_name
    from ai.providers.base import default_model_for

    state = _load_state()
    existing_names = {p["name"] for p in state["profiles"]}

    if name is None or not name.strip():
        name = suggest_name(taken=existing_names)
    elif _name_taken(name, state):
        raise ProfileNameTakenError(
            f"'{name}' is already used by another profile - pick a different name."
        )

    profile = Profile(
        id=str(uuid.uuid4()),
        name=name.strip(),
        provider=provider,
        model_id=model_id or default_model_for(provider),
        created_at=time.time(),
        system_prompt=system_prompt.strip(),
    )
    state["profiles"].append(asdict(profile))
    _save_state(state)
    return profile


def rename_profile(profile_id: str, new_name: str) -> None:
    state = _load_state()
    if _name_taken(new_name, state, ignore_id=profile_id):
        raise ProfileNameTakenError(
            f"'{new_name}' is already used by another profile - pick a different name."
        )
    for p in state["profiles"]:
        if p["id"] == profile_id:
            p["name"] = new_name.strip()
            break
    else:
        raise KeyError(f"No profile with id {profile_id!r}.")
    _save_state(state)
    if state["active_profile_id"] == profile_id:
        import ai.main as ai_main
        ai_main.set_assistant_name(new_name.strip())


def update_profile_model(profile_id: str, provider: str, model_id: str) -> None:
    state = _load_state()
    for p in state["profiles"]:
        if p["id"] == profile_id:
            p["provider"], p["model_id"] = provider, model_id
            break
    else:
        raise KeyError(f"No profile with id {profile_id!r}.")
    _save_state(state)
    if state["active_profile_id"] == profile_id:
        import ai.main as ai_main
        ai_main.set_active_provider_and_model(provider, model_id)


def update_profile_prompt(profile_id: str, system_prompt: str) -> None:
    """
    Sets this profile's extra instructions (see create_profile's
    docstring for the append-not-replace convention). Nothing needs
    telling ai.main even when this IS the active profile - unlike a
    name or model change, there's no separate "currently active custom
    prompt" value cached anywhere to keep in sync: ai.main.
    _render_instructions reads the active profile's system_prompt fresh
    from profiles.json on every single request, so this just needs to
    be saved.
    """
    state = _load_state()
    for p in state["profiles"]:
        if p["id"] == profile_id:
            p["system_prompt"] = system_prompt.strip()
            break
    else:
        raise KeyError(f"No profile with id {profile_id!r}.")
    _save_state(state)


def delete_profile(profile_id: str) -> None:
    """
    Refuses to delete the last remaining profile (there always has to
    be at least one - that's what the wake word and the main chat
    itself are pointed at) and refuses to delete the currently active
    one (switch to a different profile first via activate_profile -
    otherwise ai.main's own assistant-name/provider state would keep
    pointing at a profile record that no longer exists).
    """
    state = _load_state()
    if len(state["profiles"]) <= 1:
        raise ValueError("Can't delete the only remaining profile.")
    if state["active_profile_id"] == profile_id:
        raise ValueError("Can't delete the active profile - switch to a different one first.")
    state["profiles"] = [p for p in state["profiles"] if p["id"] != profile_id]
    _save_state(state)


def activate_profile(profile_id: str) -> Profile:
    """
    Switches the live assistant identity + active provider/model to
    `profile_id` - the same two calls (ai_main.set_assistant_name,
    ai_main.set_active_provider_and_model) Settings itself already makes
    for a manual rename/model change, so everything downstream of those
    (system prompt render, chat UI copy, the wake word's own name check)
    picks this up exactly as it would a manual change. Can raise
    ai.providers.ProviderClientError (propagated, not swallowed) if that
    profile's provider has no API key configured yet - the caller
    (wakeword routing, or a Settings "switch profile" action) decides
    how to surface that.

    Regression fix: this used to call ai_main.set_assistant_name() and
    THEN _load_state() again to record the new active_profile_id - but
    _load_state() always runs _self_heal_active_name(), which compares
    ai_main.get_assistant_name() (by then already the NEW profile's
    name) against state["active_profile_id"] (still the OLD profile's
    id, since that second _load_state() call happened before this
    function ever got to updating it). That mismatch looked exactly
    like the legacy bug _self_heal_active_name exists to fix, so it
    "corrected" it - by overwriting the OLD profile's name with the
    NEW one. Switching A -> B left both A and B named "B" in
    profiles.json, silently collapsing however many distinct profile
    names down by one every time anybody switched. Loading state
    exactly ONCE, before ai_main's globals are touched at all, and
    reusing that same dict for the active_profile_id update below
    avoids ever calling _self_heal_active_name with a name that's
    already moved on from the id it's being compared against.

    Perf fix: set_assistant_name() used to run with its default
    rebuild=True, which - same as a manual Settings rename - rebuilds
    the ENTIRE chat session via refresh_actions() (reload every macro
    from disk, rebuild the tool list, create a brand-new provider
    client). set_active_provider_and_model() right below it then does
    that same rebuild again from scratch via switch_provider(), since
    that's the call that actually needs to happen (it's the one
    switching provider/model, which refresh_actions() alone doesn't
    touch). The first rebuild's session was never used for a single
    message - it existed only to be immediately thrown away - so every
    profile switch was paying for two full session rebuilds (each
    involving disk I/O and a provider-client round trip) to do the work
    of one. That doubled cost is what made switching profiles visibly
    slow. Passing rebuild=False here skips that wasted first rebuild;
    the name is still persisted (and get_assistant_name() reflects it
    immediately) so the ONE rebuild that follows already renders the
    new name into its system prompt correctly.
    """
    state = _load_state()
    profile = None
    for p in state["profiles"]:
        if p["id"] == profile_id:
            profile = Profile(**p)
            break
    if profile is None:
        raise KeyError(f"No profile with id {profile_id!r}.")

    import ai.main as ai_main
    ai_main.set_assistant_name(profile.name, rebuild=False)
    ai_main.set_active_provider_and_model(profile.provider, profile.model_id)

    state["active_profile_id"] = profile.id
    _save_state(state)
    return profile


# ── Wake-word routing ─────────────────────────────────────────────────

def match_profile_for_phrase(lowered_text: str) -> "Profile | None":
    """
    Returns whichever profile's name appears (case-insensitive
    substring, same matching style ai.main's single-assistant wake word
    always used) in `lowered_text`, or None if none do. When more than
    one profile's name would match (e.g. "Ari" and "Aria" both profiles,
    phrase contains "aria" - "Ari" matches as a substring of it too),
    the LONGEST matching name wins - the more specific match is far
    more likely to be the one actually said, rather than an incidental
    substring of it.

    A name also matches through the spellings speech recognition
    commonly returns for it (ai.assistant_names.phonetic_variants). The
    transcript is whatever Google's API decided the audio said, and it
    writes "Clio" for Cleo and "Nadya" for Nadia often enough that a
    name-only match reads, from the user's side, as a wake word that
    silently does not work. Only spellings that are not themselves
    ordinary words are listed there, so this widens what counts as the
    name being said without widening what counts as an accidental wake.

    Ranking still uses the PROFILE NAME's length, not the length of
    whichever variant matched, so a longer name always beats a shorter
    one even when the shorter one matched through a long alias.
    """
    from ai.assistant_names import phonetic_variants

    candidates = [
        p for p in list_profiles()
        if any(variant in lowered_text for variant in phonetic_variants(p.name))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: len(p.name.strip()))
