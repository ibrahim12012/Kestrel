# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_assistant_names.py
=============================
The suggested-name pool is also the wake-word pool, and the wake word is
matched as a plain substring of a speech-to-text transcript. That makes
the pool's contents a correctness concern rather than a taste one, so
the rules ai/assistant_names.py's docstring lays out are asserted here
rather than left as prose nobody re-reads when adding a name.

The rule that matters most is the last one: an alias must not be an
ordinary word. An alias is matched against everything said near the
machine all day, so a bad one is an assistant that wakes up on its own.
"""

import random

from ai.assistant_names import (
    ASR_ALIASES,
    NAME_POOL,
    phonetic_variants,
    suggest_name,
)

# Words common enough in ordinary speech that having one as a wake word
# means waking on conversation rather than on being addressed. Not
# exhaustive - it is the set the old pool actually tripped over.
COMMON_WORDS = {
    "echo", "river", "harbor", "haven", "meadow", "story", "sage", "reed",
    "coral", "comet", "ember", "fable", "fern", "flint", "lark", "opal",
    "quill", "sparrow", "thistle", "vale", "breeze", "cedar", "clove",
    "dash", "delta", "iris", "indigo", "area", "sterling", "cascade",
    "bramble", "haze", "answer", "ivy", "sonnet", "solstice",
}

# Names whose likeliest mistranscription IS a common word, so no alias
# can rescue them and they must not be in the pool at all.
POISONED = {"aria", "juno", "ivy", "ansel", "rhea", "calla", "willa", "sable"}


# Vowel pairs that are two syllables rather than one sound. A plain
# vowel-group count reads "Cleo" and "Freya" as one syllable each,
# because it sees "eo" and "eya" as single runs - both are two.
HIATUS_PAIRS = ("eo", "ia", "io", "ya", "ua", "ea", "eu")


def _syllables(name: str) -> int:
    """
    Rough vowel-group count, corrected for hiatus. Good enough to catch
    a one-syllable name, which is all this is for - it does not need to
    be right about "Peregrine" to be right about "Wren".
    """
    name = name.lower()
    groups, prev_vowel = 0, False
    for ch in name:
        is_vowel = ch in "aeiouy"
        if is_vowel and not prev_vowel:
            groups += 1
        prev_vowel = is_vowel
    groups += sum(name.count(pair) for pair in HIATUS_PAIRS)
    if name.endswith("e") and groups > 1:
        groups -= 1          # silent terminal e
    return max(1, groups)


# ── pool composition ─────────────────────────────────────────────────

def test_every_name_is_at_least_two_syllables():
    """One syllable gives the recogniser too little to go on: missed
    more often and falsely triggered more often."""
    short = [n for n in NAME_POOL if _syllables(n) < 2]
    assert not short, f"one-syllable names in the pool: {short}"


def test_no_name_is_an_ordinary_english_word():
    bad = [n for n in NAME_POOL if n.lower() in COMMON_WORDS]
    assert not bad, f"names that are ordinary words: {bad}"


def test_names_whose_mistranscription_is_a_common_word_are_excluded():
    """"Aria" comes back as "area", "Juno" as "you know". Aliasing those
    would trade a missed-wake problem for a worse false-wake one."""
    bad = [n for n in NAME_POOL if n.lower() in POISONED]
    assert not bad, f"names with a common-word mistranscription: {bad}"


def test_the_product_name_is_not_suggested_as_an_assistant_name():
    """The product is not the assistant - see name_gate_widget.py."""
    import branding
    assert branding.APP_NAME.lower() not in {n.lower() for n in NAME_POOL}


def test_no_shipping_assistant_wake_words():
    reserved = {"siri", "alexa", "cortana", "google", "bixby", "gemini", "claude"}
    assert not {n.lower() for n in NAME_POOL} & reserved


def test_pool_has_no_duplicates_case_insensitively():
    lowered = [n.lower() for n in NAME_POOL]
    assert len(lowered) == len(set(lowered))


def test_no_pool_name_is_a_substring_of_another():
    """The matcher breaks ties by name length, but two names where one
    contains the other still means the shorter one fires on the longer
    one's transcript. Keeping the pool free of those avoids relying on
    the tie-break at all."""
    lowered = sorted((n.lower() for n in NAME_POOL), key=len)
    for i, short in enumerate(lowered):
        for long in lowered[i + 1:]:
            assert short not in long, f"{short!r} is a substring of {long!r}"


# ── aliases ──────────────────────────────────────────────────────────

def test_every_alias_key_is_a_pool_name():
    lowered = {n.lower() for n in NAME_POOL}
    orphans = set(ASR_ALIASES) - lowered
    assert not orphans, f"aliases for names not in the pool: {orphans}"


def test_no_alias_is_an_ordinary_word():
    """The rule the whole alias mechanism rests on. An alias is matched
    against all day's conversation."""
    offenders = {
        alias for aliases in ASR_ALIASES.values()
        for alias in aliases if alias in COMMON_WORDS
    }
    assert not offenders, f"aliases that are ordinary words: {offenders}"


def test_aliases_are_lowercase_and_distinct_from_their_name():
    for name, aliases in ASR_ALIASES.items():
        assert name == name.lower(), f"alias key {name!r} is not lowercase"
        for alias in aliases:
            assert alias == alias.lower(), f"alias {alias!r} is not lowercase"
            assert alias != name, f"{name!r} lists itself as an alias"


def test_no_alias_collides_with_a_different_pool_name():
    """An alias that is another name in the pool would route the wrong
    profile on a perfectly clean transcript."""
    lowered = {n.lower() for n in NAME_POOL}
    for name, aliases in ASR_ALIASES.items():
        for alias in aliases:
            assert alias not in lowered - {name}, f"{alias!r} (alias of {name!r}) is a pool name"


def test_no_alias_is_shared_between_two_names():
    seen = {}
    for name, aliases in ASR_ALIASES.items():
        for alias in aliases:
            assert alias not in seen, f"{alias!r} is an alias of both {seen.get(alias)!r} and {name!r}"
            seen[alias] = name


# ── phonetic_variants ────────────────────────────────────────────────

def test_variants_lead_with_the_name_itself():
    assert phonetic_variants("Cleo")[0] == "cleo"


def test_variants_include_known_mistranscriptions():
    assert "clio" in phonetic_variants("Cleo")
    assert "nadya" in phonetic_variants("Nadia")


def test_a_name_with_no_aliases_returns_just_itself():
    """Most names, including every name a user types in themselves."""
    assert phonetic_variants("Luna") == ("luna",)


def test_variants_of_a_user_supplied_name_still_work():
    """The pool is only a source of suggestions - people type their own,
    and those must go through the same path without special-casing."""
    assert phonetic_variants("Bartholomew") == ("bartholomew",)


def test_variants_normalise_case_and_surrounding_space():
    assert phonetic_variants("  CLEO  ") == ("cleo", "clio")


def test_empty_name_yields_no_variants():
    """An empty tuple means `any(v in text for v in ...)` is False. An
    empty STRING would be in every transcript and wake on silence."""
    assert phonetic_variants("") == ()
    assert phonetic_variants("   ") == ()
    assert phonetic_variants(None) == ()


# ── suggest_name ─────────────────────────────────────────────────────

def test_suggests_a_name_from_the_pool():
    assert suggest_name() in NAME_POOL


def test_skips_taken_names_case_insensitively():
    taken = {n.lower() for n in NAME_POOL[:-1]}
    assert suggest_name(taken=taken) == NAME_POOL[-1]


def test_falls_back_to_a_numbered_name_when_everything_is_taken():
    taken = {n.lower() for n in NAME_POOL}
    picked = suggest_name(taken=taken, rng=random.Random(0))
    assert picked.rsplit(" ", 1)[0] in NAME_POOL
    assert picked.rsplit(" ", 1)[1] == "2"


def test_numbered_fallback_increments_past_taken_suffixes():
    taken = {n.lower() for n in NAME_POOL}
    taken |= {f"{n.lower()} 2" for n in NAME_POOL}
    picked = suggest_name(taken=taken, rng=random.Random(0))
    assert picked.endswith(" 3")


def test_is_deterministic_under_a_seeded_rng():
    a = suggest_name(rng=random.Random(7))
    b = suggest_name(rng=random.Random(7))
    assert a == b
