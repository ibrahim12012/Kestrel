# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/assistant_names.py
=======================
A curated pool of names to *suggest* for an assistant - used today by
app/name_gate_widget.py's first-run "what should I call myself?" screen,
and written so it's already ready for the planned multi-profile feature:
once more than one assistant/wake-word profile can exist at once, each
new profile needs a suggested name that isn't already taken by an
existing one, which is exactly what suggest_name(taken=...) is for.

── Why this pool is short ───────────────────────────────────────────
The name is the wake word, and the wake word is matched by looking for
it as a plain substring of whatever Google's speech API transcribed
(see ai.profiles.match_profile_for_phrase). That is a harsh test, and it
rules out three whole categories of otherwise pleasant names:

  1. **Ordinary English words.** "Echo", "River", "Harbor", "Sage",
     "Story", "Ember", "Meadow", "Comet", "Reed", "Coral". These are the
     worst possible wake words, because the listener is running against
     ordinary conversation all day. Say "did you see the river" near the
     machine and the assistant wakes up. Precision matters far more than
     charm here.

  2. **One-syllable names.** "Wren", "Pax", "Sol", "Dash", "Flint",
     "Fern", "Lark". A single syllable gives the recogniser almost no
     acoustic evidence to work with, so it is both missed more often and
     falsely triggered more often. Everything below is two or three.

  3. **Names the recogniser spells differently than we do.** This one
     is fatal rather than merely unreliable. "Cael" comes back as
     "Kale" or "Kyle", "Orin" as "Oren" or "Aaron", "Remy" as "Remi",
     "Marlow" as "Marlowe". The substring match then never fires, and
     from the user's side the wake word is simply broken with no
     explanation. ASR_ALIASES below handles the residual cases.

And one category that looks fine and is not: names whose most likely
mistranscription is itself a common word. "Aria" comes back as "area".
"Juno" comes back as "you know". "Ivy" comes back as "I've". "Ansel"
comes back as "answer". Aliasing those would make the false-wake problem
worse than the missed-wake problem it fixed, so they are simply out. A
name that needs a dangerous alias to work is the wrong name.

Names also avoid colliding with a shipping assistant's own wake word
(no "Siri", "Alexa", "Cortana", "Google", "Bixby"), and the pool never
suggests "Kestrel" itself - the product is not the assistant (see
app/name_gate_widget.py).

Usage:
    from ai.assistant_names import suggest_name, phonetic_variants
    name = suggest_name()                         # any unused-by-caller name
    name = suggest_name(taken={"Nova", "Vera"})   # skips those two
    phonetic_variants("Cleo")                     # ('cleo', 'clio')
"""

import random

# Alphabetical, not ranked - order here doesn't matter since suggest_name
# always shuffles before picking. Every entry is two or three syllables,
# is not an ordinary English word, and survives the spelling test above.
NAME_POOL = [
    "Amara", "Astra", "Barnaby", "Calypso", "Caspian", "Cleo", "Cyrus",
    "Dorian", "Elara", "Freya", "Ingrid", "Juniper", "Luna", "Lyra",
    "Marisol", "Nadia", "Nova", "Odessa", "Orion", "Peregrine",
    "Ramona", "Saffron", "Sienna", "Tobias", "Vera", "Verona", "Vesper",
]

# Spellings the speech API commonly returns for a pool name, which the
# wake-word match should accept as that name.
#
# Every alias here is deliberately NOT an ordinary English word, for the
# reason the module docstring gives: an alias is a substring match
# running against all day's conversation, so a wrong one is a machine
# that wakes up on its own. Nobody says "clio" or "nadya" by accident.
#
# Keys are lowercase. A name absent from this map has no alias, which is
# the common case and the goal.
ASR_ALIASES = {
    "amara": ("amarah", "amaara"),
    "astra": ("aastra",),
    "calypso": ("kalypso",),
    "caspian": ("kaspian",),
    "cleo": ("clio",),
    "cyrus": ("sirus", "syrus"),
    "elara": ("alara", "ellara"),
    "freya": ("fraya", "freyja"),
    "lyra": ("lira", "leira"),
    "marisol": ("marysol",),
    "nadia": ("nadya", "nadiya"),
    "odessa": ("adessa",),
    "peregrine": ("peregrin", "perigrine"),
    "sienna": ("siena",),
    "vesper": ("vespar",),
}


def phonetic_variants(name: str) -> tuple:
    """
    Every spelling that should count as `name` being said: the name
    itself first, then any known mistranscriptions of it.

    All lowercase, because the wake-word match works on a lowercased
    transcript. A name with no entry in ASR_ALIASES - including any name
    the user typed themselves, which is most of them - comes back as a
    one-item tuple, so callers never need to special-case it.
    """
    key = (name or "").strip().lower()
    if not key:
        return ()
    return (key,) + tuple(ASR_ALIASES.get(key, ()))


def suggest_name(taken: "set | None" = None, rng: "random.Random | None" = None) -> str:
    """
    Returns a random name from NAME_POOL that isn't in `taken` (case-
    insensitive - "nova" and "Nova" count as the same name so two
    profiles can't end up with names that only differ by case).

    If every name in the pool is taken, falls back to appending a number
    suffix ("Nova 2", "Nova 3", ...) to a randomly chosen pool name,
    incrementing until an unused combination is found, rather than
    raising or ever returning a name already in use. The pool is
    deliberately short now (see the module docstring), so this path is
    less far-fetched than it was, and it stays correct either way: a
    numbered name is still two or three syllables plus a digit, which
    the recogniser handles fine.

    `rng` is only there for deterministic tests - real callers should
    leave it as None and get `random`'s own module-level generator.
    """
    taken_lower = {name.strip().lower() for name in (taken or ())}
    pick = rng.choice if rng is not None else random.choice
    shuffled = list(NAME_POOL)
    (rng or random).shuffle(shuffled)

    for name in shuffled:
        if name.lower() not in taken_lower:
            return name

    # Every base name is taken - suffix a random one with an incrementing
    # number until we land on something free.
    base = pick(NAME_POOL)
    n = 2
    while f"{base} {n}".lower() in taken_lower:
        n += 1
    return f"{base} {n}"
