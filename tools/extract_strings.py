#!/usr/bin/env python3
# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tools/extract_strings.py
==========================
Collects every string wrapped in tr() and writes a catalog stub, and
reports user-facing strings that are not wrapped yet.

    python tools/extract_strings.py                 what is wrapped, and what is not
    python tools/extract_strings.py --stub fr       write app/locale/fr.json
    python tools/extract_strings.py --unwrapped     only the ones still to do

Parses with ast rather than grepping, so a tr() inside a longer
expression is found and the word "tr" in a comment is not. A stub keeps
the translations already in the file and adds "" for anything new, so
re-running it after adding strings never discards work.

The unwrapped report is a lead, not a verdict: it flags string literals
passed to the Qt calls that put text on screen, and plenty of those are
object names, style sheets or format strings that should stay as they
are. Read it, do not apply it.
"""

import argparse
import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Qt calls whose first string argument lands on screen.
TEXT_SETTERS = {
    "setText", "setPlaceholderText", "setToolTip", "setWindowTitle",
    "setStatusTip", "setWhatsThis", "setInformativeText", "setTitle",
    "setLabelText", "setSuffix", "setPrefix", "setItemText", "setTabText",
    "addItem", "addAction", "addButton", "setButtonText",
}
TEXT_CONSTRUCTORS = {"QLabel", "QPushButton", "QAction", "QCheckBox",
                     "QRadioButton", "QGroupBox", "QToolButton"}

SKIP_DIRS = {"__pycache__", ".git", "build", "dist", ".venv", "tests", "tools", "locale"}


def _is_str(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _looks_translatable(text: str) -> bool:
    """A literal worth a translator's time: has a letter, has a space or
    is a real word, and is not markup, a path or a style sheet."""
    if not text or len(text) < 2 or len(text) > 400:
        return False
    if not any(c.isalpha() for c in text):
        return False
    if any(marker in text for marker in ("{c[", "QWidget", "QPushButton", "background-color",
                                          "font-family", "px;", "<html", "http://", "https://",
                                          "\\", "::")):
        return False
    if text.startswith(("#", ".", "/")) or text.endswith((".png", ".svg", ".ico", ".ui", ".py")):
        return False
    # An identifier-shaped token is almost always an objectName.
    if " " not in text and (text.isidentifier() or text.replace("_", "").isalnum()) and text.islower():
        return False
    return True


def scan():
    wrapped, unwrapped = {}, {}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for filename in sorted(files):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(base, filename)
            rel = os.path.relpath(path, ROOT)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read(), path)
            except (SyntaxError, UnicodeDecodeError):
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (node.func.id if isinstance(node.func, ast.Name)
                        else node.func.attr if isinstance(node.func, ast.Attribute) else "")

                if name == "tr" and node.args and _is_str(node.args[0]):
                    wrapped.setdefault(node.args[0].value, []).append(f"{rel}:{node.lineno}")
                    continue

                if name in TEXT_SETTERS or name in TEXT_CONSTRUCTORS:
                    for arg in node.args[:1]:
                        if _is_str(arg) and _looks_translatable(arg.value):
                            unwrapped.setdefault(arg.value, []).append(f"{rel}:{node.lineno}")
    return wrapped, unwrapped


def write_stub(code: str, wrapped: dict) -> str:
    locale_dir = os.path.join(ROOT, "app", "locale")
    os.makedirs(locale_dir, exist_ok=True)
    path = os.path.join(locale_dir, f"{code}.json")

    existing = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"Could not read the existing {path} ({e}); it will be replaced.")

    catalog = {key: existing.get(key, "") for key in sorted(wrapped)}
    # Anything already translated but no longer in the source stays, so a
    # string temporarily removed does not cost its translation.
    for key, value in existing.items():
        if key not in catalog and value:
            catalog[key] = value

    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stub", metavar="CODE", help="write app/locale/CODE.json")
    parser.add_argument("--unwrapped", action="store_true", help="only the unwrapped report")
    args = parser.parse_args()

    wrapped, unwrapped = scan()

    if args.stub:
        path = write_stub(args.stub, wrapped)
        done = sum(1 for v in json.load(open(path, encoding="utf-8")).values() if v)
        print(f"{path}: {len(wrapped)} string(s), {done} translated.")
        return 0

    if not args.unwrapped:
        print(f"{len(wrapped)} string(s) wrapped in tr().\n")

    print(f"{len(unwrapped)} user-facing literal(s) not wrapped yet:")
    for text, places in sorted(unwrapped.items()):
        where = places[0] + (f" (+{len(places) - 1} more)" if len(places) > 1 else "")
        print(f"  {text!r:60}  {where}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
