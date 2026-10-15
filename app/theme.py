# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/theme.py
=============
Single source of truth for Kestrel's color palette, dark and light. Every
widget's stylesheet is built from these semantic tokens instead of
hardcoded hex - see chat_widget.py / settings_widget.py / sidebar_widget.py
/ recorder_widget.py's `_build_style(c)` functions, each called once at
construction and again whenever set_theme() is used to switch live.

The "dark" palette here is exactly the colors the app already used before
theming existed - consolidated from ~20 near-identical near-black shades
scattered across files down to a handful of consistent tokens, but visibly
unchanged. "light" is a new palette, not just an inverted/auto-generated
one, so it actually reads well - off-white backgrounds (not stark white),
and the purple accent darkened a step so accent-colored text stays
readable on a light background (the original #7c6aff is a hover/lighter
shade here instead).
"""

THEMES = {
    "dark": {
        "bg_base":          "#0a0a0a",
        "bg_panel":         "#121212",
        "bg_elevated":      "#161616",
        "bg_input":         "#1a1a1a",
        "bg_hover":         "#1e1e1e",
        "bg_hover_strong":  "#232323",
        "border":           "#2a2a2a",
        "border_strong":    "#3a3a3a",
        "text_primary":     "#f2f2f2",
        "text_secondary":   "#c9c9c9",
        "text_muted":       "#9a9a9a",
        "text_faint":       "#7a7a7a",
        "text_disabled":    "#5c5c5c",
        "accent":           "#7c6aff",
        "accent_rgb":       "124,106,255",
        "accent_hover":     "#8f7dff",
        "accent_pressed":   "#6a58e0",
        "accent_soft":      "#b7acff",
        "danger":           "#e57373",
        "danger_rgb":       "214,69,69",
        "danger_soft":      "#e08a8a",
        "success":          "#7ddb8f",
        "warning":          "#e0b35c",
        "white":            "#ffffff",
        "hover_overlay":    "rgba(255,255,255,0.015)",
        # Code blocks (chat bubbles) stay dark regardless of app theme
        # same as chatgpt.com's own code blocks, which don't switch to a
        # light background in light mode either; syntax-highlighting
        # colors read best against a dark background specifically.
        "code_bg":          "#1e1e1e",
        "code_header_bg":   "#2a2a2a",
    },
    "light": {
        "bg_base":          "#f5f5f7",
        "bg_panel":         "#ffffff",
        "bg_elevated":      "#ffffff",
        "bg_input":         "#f0f0f3",
        "bg_hover":         "#ececef",
        "bg_hover_strong":  "#e2e2e7",
        "border":           "#dcdce1",
        "border_strong":    "#c7c7ce",
        "text_primary":     "#1a1a1e",
        "text_secondary":   "#3a3a40",
        "text_muted":       "#6b6b72",
        "text_faint":       "#84848c",
        "text_disabled":    "#b3b3ba",
        "accent":           "#6a58e0",
        "accent_rgb":       "106,88,224",
        "accent_hover":     "#7c6aff",
        "accent_pressed":   "#5646b8",
        "accent_soft":      "#4a3ecb",
        "danger":           "#c9433f",
        "danger_rgb":       "201,67,63",
        "danger_soft":      "#b23a37",
        "success":          "#1f7a3d",
        "warning":          "#8a5f15",
        "white":            "#ffffff",
        "hover_overlay":    "rgba(0,0,0,0.035)",
        # See the dark palette's own comment - deliberately the SAME
        # dark values here too, code blocks don't follow the light theme.
        "code_bg":          "#1e1e1e",
        "code_header_bg":   "#2a2a2a",
    },
}

DEFAULT_THEME = "dark"


def colors(theme: str = DEFAULT_THEME) -> dict:
    """Returns the token dict for `theme`, falling back to dark for anything unrecognized."""
    return THEMES.get(theme, THEMES[DEFAULT_THEME])


def menu_style(c: dict) -> str:
    """
    A popup menu (the tray menu, a chat's right-click menu) in the app's
    own palette rather than the OS's. The menu's icons are tinted from the
    theme, and a theme-coloured icon on an OS-coloured menu is how light
    grey icons ended up on a light grey menu.
    """
    return f"""
QMenu {{
    background-color: {c['bg_elevated']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 4px;
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
}}
QMenu::item {{ padding: 7px 24px 7px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background-color: {c['bg_hover_strong']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QMenu::icon {{ padding-left: 8px; }}
"""
