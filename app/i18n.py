# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/i18n.py
=============
Translation for user-facing text.

English is the source language, and the English string is its own key:

    from app.i18n import tr
    button.setText(tr("New chat"))

With no catalog loaded, or no entry for a string, tr() returns what it
was given. So wrapping a string can never make text disappear, and a
half-translated catalog degrades to English rather than to blanks or to
raw identifiers.

Why not Qt's own tr()/.ts files: those need pylupdate6 and lrelease in
the build, and a QObject to call the method on, which most of the code
that needs a string here is not. JSON catalogs and one function need no
build step, and a translator edits a readable file.

Catalogs live in app/locale/<code>.json as a flat {english: translated}
map. A value that is empty or identical to the key is treated as not yet
translated. Language comes from the setting in Settings, falling back to
the OS locale, falling back to English.

Adding a language:
  1. tools/extract_strings.py > app/locale/<code>.json writes a stub
     catalog with every wrapped string as a key and "" as the value.
  2. Fill in the values.
  3. It shows up in Settings on the next launch. Nothing to register.

Plurals and gendered agreement are not handled. Neither is worth the
machinery here, because a string that needs them can be written to avoid
them ("Deleted 1 macro" / "Deleted 3 macros" as two keys, chosen by the
caller), and inventing half a plural system tends to produce text that
is wrong in the languages it does not model.
"""

from __future__ import annotations

import json
import locale
import os

from paths import BASE_PATH

LOCALE_DIR = os.path.join(BASE_PATH, "app", "locale")

# The source language. Its catalog is the identity map, so it is never
# loaded from disk.
SOURCE_LANGUAGE = "en"

# The value the Settings picker stores for "Match my system". Distinct
# from "unset", which now means English: defaulting to the OS locale
# gave people a machine-translated interface they never asked for.
FOLLOW_SYSTEM = "system"

# Languages written right to left. Qt will not work this out on its own:
# the layout direction has to be set on the application before any
# window is built, or an Arabic UI renders with its text translated and
# its layout still mirrored the wrong way, which is worse than English.
RTL_LANGUAGES = frozenset({"ar", "he", "fa", "ur", "yi", "dv", "ps"})

_catalog: dict = {}
_language = SOURCE_LANGUAGE
_missing: set = set()


def tr(text: str, **fields) -> str:
    """
    The translation of `text`, or `text` itself when there is none.

    Keyword arguments are substituted into the result with str.format,
    so a translation can reorder them where the grammar needs it:

        tr("Deleted {name}", name=macro_name)

    A translation with a placeholder the caller did not pass falls back
    to the English, because a visible English string beats a KeyError or
    a stray "{name}" on screen.
    """
    translated = _catalog.get(text) or text
    if not fields:
        if translated is text and _language != SOURCE_LANGUAGE:
            _missing.add(text)
        return translated
    try:
        return translated.format(**fields)
    except (KeyError, IndexError, ValueError):
        try:
            return text.format(**fields)
        except (KeyError, IndexError, ValueError):
            return text


def available_languages() -> "list[tuple[str, str]]":
    """(code, display name) for every catalog on disk, English first."""
    languages = [(SOURCE_LANGUAGE, "English")]
    if not os.path.isdir(LOCALE_DIR):
        return languages
    for filename in sorted(os.listdir(LOCALE_DIR)):
        if not filename.endswith(".json"):
            continue
        code = filename[:-5]
        if code == SOURCE_LANGUAGE:
            continue
        languages.append((code, _display_name(code)))
    return languages


def current_language() -> str:
    return _language


def is_rtl(code: str = None) -> bool:
    """Whether a language reads right to left. See RTL_LANGUAGES."""
    return (code or _language) in RTL_LANGUAGES


def apply_layout_direction(app) -> None:
    """
    Point the whole application the right way round for the current
    language. Call once, before any window exists.

    Qt mirrors layouts, scrollbars, alignment and icon order off this one
    setting, so it does most of the work; what it does not fix is a
    stylesheet with a hardcoded `padding-left`, of which this app has
    several. Those read slightly off in Arabic rather than broken, which
    is the trade worth making until someone actually uses it in Arabic.
    """
    from PyQt6.QtCore import Qt
    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if is_rtl() else Qt.LayoutDirection.LeftToRight
    )


def set_language(code: str) -> bool:
    """
    Load `code`'s catalog. False means it could not be loaded and the
    language was left alone, so a missing or broken catalog shows
    English rather than taking the UI down.

    Already-built widgets keep the text they were given: nothing here
    re-translates a live window. Settings tells the user a restart is
    needed, which is the honest version of a rebuild-everything pass
    that would miss half the app anyway.
    """
    global _catalog, _language, _missing
    code = (code or SOURCE_LANGUAGE).strip().lower()

    if code == SOURCE_LANGUAGE:
        _catalog, _language, _missing = {}, SOURCE_LANGUAGE, set()
        return True

    path = os.path.join(LOCALE_DIR, f"{code}.json")
    if not os.path.isfile(path):
        print(f"[i18n] No catalog for {code!r}, staying in English.")
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[i18n] Could not read {path}: {e}")
        return False
    if not isinstance(raw, dict):
        print(f"[i18n] {path} is not a flat object of strings.")
        return False

    # An entry that is blank, or the same as its key, is untranslated.
    # Dropping it here means tr() falls through to English by the same
    # path as a key that was never in the catalog at all.
    _catalog = {
        k: v for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, str) and v.strip() and v != k
    }
    _language = code
    _missing = set()
    print(f"[i18n] {code}: {len(_catalog)} string(s) of {len(raw)}.")
    return True


def missing_strings() -> "list[str]":
    """Strings tr() was asked for that the current catalog has no entry
    for. Populated as the app runs, so it reflects the screens actually
    opened rather than the whole catalog."""
    return sorted(_missing)


def system_language() -> str:
    """The OS's language code, or English if it cannot be read."""
    try:
        code, _ = locale.getdefaultlocale()
    except (ValueError, TypeError):
        return SOURCE_LANGUAGE
    if not code:
        return SOURCE_LANGUAGE
    return code.split("_")[0].lower()


_DISPLAY_NAMES = {
    "ar": "العربية", "de": "Deutsch", "en": "English", "es": "Español",
    "fr": "Français", "hi": "हिन्दी", "it": "Italiano", "ja": "日本語",
    "ko": "한국어", "nl": "Nederlands", "pl": "Polski", "pt": "Português",
    "ru": "Русский", "tr": "Türkçe", "uk": "Українська", "zh": "中文",
}


def _display_name(code: str) -> str:
    return _DISPLAY_NAMES.get(code, code.upper())
