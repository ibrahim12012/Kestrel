# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/discovery
============
Phase 2: Autonomous UI Mapping Engine.

Implements the Structural Action-Tree Mapping & Atomic Macro Compilation
Pipeline described in the Phase 2 spec: structural accessibility parsing
replaces real-time computer vision for *navigation/mapping* of an
application, and the resulting map is compiled into atomic, parameterized
Python functions the AI can call directly - no per-step vision inference
needed at runtime once an app has been mapped.

This does NOT replace ai/main.py's existing vision-based
get_position_object_and_click - that remains the fallback (and the only
option) for anything the accessibility tree can't see (custom canvas
elements, complex web containers with no exposed roles). See
ai/discovery/accessibility.py's module docstring for exactly how the two
fit together.

Public surface:
    map_application_ui(app_name, ...) -> ai.discovery.tree.ActionTree
    compile_action_tree(tree, ...)     -> path to the generated
                                           ai/generated/<App>_actions.py
    DiscoveryError                     -> raised when mapping can't run
                                           at all (e.g. no accessibility
                                           backend available on this OS)

Typical use (see ai/actions.py: run_mapping, and tool_use_mapped_control
for pressing a mapped control by name):

    from ai.discovery import map_application_ui

    tree = map_application_ui("Notepad")
    tree.save()                       # ai/discovery/trees/notepad_tree.json
"""

from ai.discovery.errors import DiscoveryError
from ai.discovery.tree import ActionTree, AppStateNode, DiscoveredAction, ScreenStatus

# The mapper and the compiler are loaded on first use. Importing them
# imports uiautomation, which is slow to load and sets the process's DPI
# mode as a side effect, and the Mapping page imports this package (for
# ai.discovery.events) while the app is starting.
_LOADED_ON_USE = {
    "map_application_ui": "ai.discovery.mapper",
    "compile_action_tree": "ai.discovery.compiler",
}


def __getattr__(name):
    module = _LOADED_ON_USE.get(name)
    if module is None:
        raise AttributeError(f"module 'ai.discovery' has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value

__all__ = [
    "DiscoveryError",
    "ActionTree",
    "AppStateNode",
    "DiscoveredAction",
    "ScreenStatus",
    "map_application_ui",
    "compile_action_tree",
]
