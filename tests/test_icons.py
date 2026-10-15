# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_icons.py
=====================
The app's icon set (assets/icons/*.svg, drawn for Kestrel) and the code
that asks for icons by name.

Every name the code asks for has to exist: a missing one is a blank
button, which nothing else would catch. Every icon has to be tintable:
the app recolours them per theme through currentColor, so a hard-coded
colour would stay that colour in every theme.
"""

import ast
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
ICON_DIR = os.path.join(ROOT, "assets", "icons")
SVG_NS = "{http://www.w3.org/2000/svg}"


def _icon_files():
    return sorted(glob.glob(os.path.join(ICON_DIR, "*.svg")))


def _branch_values(expr) -> "list[str]":
    """The strings an expression can evaluate to: a constant, or either
    side of a conditional expression (not the condition)."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return [expr.value]
    if isinstance(expr, ast.IfExp):
        return _branch_values(expr.body) + _branch_values(expr.orelse)
    return []


def _names_asked_for() -> "dict[str, str]":
    """Icon name -> where the code asks for it, from the source itself."""
    wanted = {}
    calls = {"icon", "pixmap", "has", "_button", "_small_btn", "_make_system_row"}
    system_marker = re.compile(r"\[\[SYSTEM:([a-z0-9_-]+)\|")
    for path in glob.glob(os.path.join(ROOT, "app", "**", "*.py"), recursive=True) + \
            glob.glob(os.path.join(ROOT, "ai", "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        where = os.path.relpath(path, ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and node.args:
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                is_icons_call = (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                                 and func.value.id == "icons")
                first = node.args[0]
                if name in calls and (is_icons_call or name in ("_button", "_small_btn", "_make_system_row")):
                    for value in _branch_values(first):
                        wanted[value] = where
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    # self._tray_actions["window"] = ...
                    if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute)
                            and target.value.attr == "_tray_actions"
                            and isinstance(target.slice, ast.Constant)):
                        wanted[target.slice.value] = where
                    # _NAV_ICONS = (("btnNewChat", "new-chat"), ...)
                    if isinstance(target, ast.Name) and target.id == "_NAV_ICONS":
                        for pair in ast.walk(node.value):
                            if isinstance(pair, ast.Tuple) and len(pair.elts) == 2 \
                                    and isinstance(pair.elts[1], ast.Constant):
                                wanted[pair.elts[1].value] = where
                    # icon_name = "warning" if ... else ("edit" if ... else "target")
                    if isinstance(target, ast.Name) and target.id == "icon_name":
                        for value in _branch_values(node.value):
                            wanted[value] = where
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for match in system_marker.finditer(node.value):
                    wanted[match.group(1)] = where
    return wanted


def test_there_is_an_icon_set():
    assert len(_icon_files()) >= 40


def test_every_icon_the_code_asks_for_exists():
    missing = {name: where for name, where in _names_asked_for().items()
               if not os.path.isfile(os.path.join(ICON_DIR, f"{name}.svg"))}
    assert missing == {}


@pytest.mark.parametrize("path", _icon_files(), ids=os.path.basename)
def test_each_icon_is_a_plain_tintable_24px_svg(path):
    root = ET.parse(path).getroot()
    assert root.tag == f"{SVG_NS}svg"
    assert root.get("viewBox") == "0 0 24 24"
    text = open(path, encoding="utf-8").read()
    assert "currentColor" in text
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(", text), "a fixed colour would not follow the theme"
    for bad in ("<script", "<image", "href=\"http", "<foreignObject", "<style"):
        assert bad not in text
    drawn = [el for el in root.iter() if el.tag.replace(SVG_NS, "") in
             ("path", "circle", "rect", "line", "polyline", "polygon", "ellipse")]
    assert drawn, "an icon with nothing in it"


def test_the_icon_module_serves_the_set():
    pytest.importorskip("PyQt6.QtSvg")
    from app import icons
    names = icons.available()
    assert "settings" in names and "trash" in names
    assert icons.has("camera") and not icons.has("no-such-icon")
