# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_i18n.py
====================
app/i18n.py's contract, which is mostly about what happens when a
translation is missing or wrong. A translation layer that can blank the
UI or raise on a bad catalog is worse than no translation layer, so the
failure paths get more attention here than the happy one.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import i18n


def _with_catalog(monkeypatch, tmp_path, code, catalog):
    path = tmp_path / f"{code}.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(i18n, "LOCALE_DIR", str(tmp_path))
    return path


def _reset():
    i18n.set_language(i18n.SOURCE_LANGUAGE)


def test_returns_the_source_string_with_no_catalog():
    _reset()
    assert i18n.tr("Settings") == "Settings"


def test_translates_from_a_loaded_catalog(monkeypatch, tmp_path):
    _with_catalog(monkeypatch, tmp_path, "fr", {"Settings": "Paramètres"})
    assert i18n.set_language("fr") is True
    assert i18n.tr("Settings") == "Paramètres"
    _reset()


def test_a_key_with_no_entry_falls_through_to_english(monkeypatch, tmp_path):
    _with_catalog(monkeypatch, tmp_path, "fr", {"Settings": "Paramètres"})
    i18n.set_language("fr")
    assert i18n.tr("Delete") == "Delete"
    assert "Delete" in i18n.missing_strings()
    _reset()


def test_a_blank_or_identical_entry_counts_as_untranslated(monkeypatch, tmp_path):
    _with_catalog(monkeypatch, tmp_path, "fr", {"Save": "", "Chat": "Chat", "Open": "Ouvrir"})
    i18n.set_language("fr")
    assert i18n.tr("Save") == "Save"
    assert i18n.tr("Chat") == "Chat"
    assert i18n.tr("Open") == "Ouvrir"
    _reset()


def test_a_missing_catalog_leaves_the_language_alone(monkeypatch, tmp_path):
    _with_catalog(monkeypatch, tmp_path, "fr", {"Settings": "Paramètres"})
    i18n.set_language("fr")
    assert i18n.set_language("zz") is False
    assert i18n.current_language() == "fr"
    assert i18n.tr("Settings") == "Paramètres"
    _reset()


def test_a_malformed_catalog_is_refused_not_raised(monkeypatch, tmp_path):
    (tmp_path / "bad.json").write_text("{not json at all", encoding="utf-8")
    monkeypatch.setattr(i18n, "LOCALE_DIR", str(tmp_path))
    assert i18n.set_language("bad") is False
    assert i18n.tr("Settings") == "Settings"


def test_a_catalog_that_is_not_an_object_is_refused(monkeypatch, tmp_path):
    (tmp_path / "list.json").write_text('["Settings"]', encoding="utf-8")
    monkeypatch.setattr(i18n, "LOCALE_DIR", str(tmp_path))
    assert i18n.set_language("list") is False


def test_placeholders_are_substituted(monkeypatch, tmp_path):
    _with_catalog(monkeypatch, tmp_path, "fr", {"Deleted {name}": "{name} supprimé"})
    i18n.set_language("fr")
    assert i18n.tr("Deleted {name}", name="Macro A") == "Macro A supprimé"
    _reset()


def test_a_translation_may_reorder_placeholders(monkeypatch, tmp_path):
    _with_catalog(monkeypatch, tmp_path, "x", {"{a} then {b}": "{b} puis {a}"})
    i18n.set_language("x")
    assert i18n.tr("{a} then {b}", a="one", b="two") == "two puis one"
    _reset()


def test_a_translation_with_a_bad_placeholder_falls_back_to_english(monkeypatch, tmp_path):
    # A translator typo must not put a KeyError on screen.
    _with_catalog(monkeypatch, tmp_path, "x", {"Deleted {name}": "{nom} supprimé"})
    i18n.set_language("x")
    assert i18n.tr("Deleted {name}", name="Macro A") == "Deleted Macro A"
    _reset()


def test_english_is_always_offered_and_comes_first(monkeypatch, tmp_path):
    _with_catalog(monkeypatch, tmp_path, "fr", {"Settings": "Paramètres"})
    languages = i18n.available_languages()
    assert languages[0] == ("en", "English")
    assert ("fr", "Français") in languages
    _reset()


SHIPPED = ("ar", "es", "fr", "hi", "pt", "zh")


def _shipped_catalogs():
    for code in SHIPPED:
        path = os.path.join(i18n.LOCALE_DIR, f"{code}.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                yield code, json.load(f)


def test_every_shipped_catalog_is_loadable_and_complete():
    """A blank value is fine on a language someone has just started.
    These are meant to be finished."""
    seen = 0
    for code, catalog in _shipped_catalogs():
        seen += 1
        assert isinstance(catalog, dict) and catalog, f"{code} is empty"
        untranslated = [k for k, v in catalog.items() if not str(v).strip()]
        assert not untranslated, f"{code} is missing: {untranslated[:5]}"
    assert seen == len(SHIPPED), "a shipped catalog is missing from app/locale/"


def test_no_translation_translates_its_own_placeholders():
    """The failure this catches is specific and nasty: a translator, or
    a machine, renders {count} into their own language, tr() then cannot
    substitute it, and the user sees a stray brace or nothing at all."""
    import re
    placeholders = re.compile(r"\{(\w+)\}")
    for code, catalog in _shipped_catalogs():
        for key, value in catalog.items():
            if not value.strip():
                continue
            assert set(placeholders.findall(key)) == set(placeholders.findall(value)), (
                f"{code}: placeholders changed in {key!r} -> {value!r}"
            )


def test_every_translation_can_actually_be_formatted():
    """A stray { or } anywhere in a translation makes str.format raise,
    which tr() catches, but a catalog needing that fallback is broken."""
    import string
    for code, catalog in _shipped_catalogs():
        for key, value in catalog.items():
            try:
                list(string.Formatter().parse(value))
            except ValueError as e:
                raise AssertionError(f"{code}: {key!r} -> {value!r} is unformattable: {e}")


def test_rtl_languages_are_recognised():
    assert i18n.is_rtl("ar") is True
    assert i18n.is_rtl("he") is True
    for code in ("en", "fr", "es", "pt", "zh", "hi"):
        assert i18n.is_rtl(code) is False, f"{code} is not right-to-left"


def test_every_shipped_language_is_offered_in_settings():
    offered = dict(i18n.available_languages())
    for code in SHIPPED:
        if os.path.isfile(os.path.join(i18n.LOCALE_DIR, f"{code}.json")):
            assert code in offered, f"{code} has a catalog but is not offered"
            assert offered[code] != code.upper(), f"{code} has no display name"


def test_every_wrapped_string_is_in_the_french_catalog():
    """tools/extract_strings.py is what keeps the catalog in step with the
    source. If this fails, re-run it with --stub fr and translate what it
    adds."""
    import importlib.util
    tools = os.path.join(os.path.dirname(__file__), "..", "tools", "extract_strings.py")
    if not os.path.isfile(tools):
        return
    spec = importlib.util.spec_from_file_location("extract_strings", tools)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    path = os.path.join(i18n.LOCALE_DIR, "fr.json")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        catalog = json.load(f)
    wrapped, _ = module.scan()
    missing = sorted(set(wrapped) - set(catalog))
    assert not missing, f"wrapped but not in fr.json: {missing[:10]}"
