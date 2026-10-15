# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/discovery/tree.py
=====================
The data model for a mapped Application Action Tree - mirrors the
"State & Action Node Telemetry Pattern" JSON schema from the Phase 2 spec
exactly (field names included), plus the ScreenStatus flags the mapper
(ai/discovery/mapper.py) assigns to each node as it explores.

ActionTree.save() is the "State Graph Matrix ([app_name]_tree.json)"
architectural output artifact described in spec section 5 - every node
keyed by id, with parent/child navigation implied by each
DiscoveredAction's target_node_id.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from enum import Enum

from paths import USER_DATA_PATH

TREES_DIR = os.path.join(USER_DATA_PATH, "ai", "discovery", "trees")


class ScreenStatus(str, Enum):
    """
    The three functional state flags from spec section 2. Stored as a str
    Enum (not a plain Enum) so json.dump can serialize a node's
    screen_status without a custom encoder, and so equality against a
    plain string (as seen when a tree is reloaded from JSON) just works.
    """
    EXPLORING = "EXPLORING"
    SINGLE_CHOICE_LEFT = "SINGLE_CHOICE_LEFT"
    STOP_BRANCH_MAX = "STOP_BRANCH_MAX"


@dataclass
class BacktrackAction:
    """Structural back-path pointer for a node - spec section 3's "backtrack_action"."""
    type: str = "NATIVE_ACCESSIBILITY_BACK"   # or "KEY_FALLBACK" when no back control was found
    accessibility_id: "str | None" = None
    fallback_key: str = "ESCAPE"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "accessibility_id": self.accessibility_id,
            "fallback_key": self.fallback_key,
        }


@dataclass
class DiscoveredAction:
    """One actionable element discovered on a node's screen - spec section 3's "discovered_actions" entries."""
    action_id: str
    element_type: str
    accessibility_path: str
    bounding_box: dict
    # What the mapper's tried set is keyed by, which is not
    # accessibility_path: a path can be positional and shift when the
    # screen relayouts, and this has to stay put across revisits or the
    # same control gets clicked again on every arrival. Defaults to the
    # path so trees saved before this existed still load.
    element_key: str = ""
    requires_input: bool = False
    sample_value: "str | None" = None
    target_node_id: "str | None" = None
    # Not in the original spec schema, but load-bearing for the Safety
    # Interception Hooks (spec section 4): an action the mapper
    # recognized as destructive (see ai.safety.is_destructive_label) is
    # recorded here but deliberately never triggered during the mapping run
    # itself - see ai/discovery/mapper.py's EXPLORING branch. The
    # compiler (ai/discovery/compiler.py) reads this flag to decide
    # whether the compiled atomic function needs a confirmation gate.
    is_destructive: bool = False
    # The control's accessible name as a person reads it ("Save", "File
    # name"). The action_id is a slug of it, which loses capitals,
    # spaces and anything non-ASCII; this keeps the real thing for the
    # mapping overlay and for telling the model what is where.
    name: str = ""

    def __post_init__(self):
        if not self.element_key:
            self.element_key = self.accessibility_path

    def center(self) -> "tuple[int, int]":
        box = self.bounding_box or {}
        return int(box.get("x", 0) + box.get("w", 0) // 2), int(box.get("y", 0) + box.get("h", 0) // 2)

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "name": self.name,
            "element_type": self.element_type,
            "accessibility_path": self.accessibility_path,
            "element_key": self.element_key,
            "bounding_box": self.bounding_box,
            "requires_input": self.requires_input,
            "sample_value": self.sample_value,
            "target_node_id": self.target_node_id,
            "is_destructive": self.is_destructive,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DiscoveredAction":
        return cls(
            action_id=data["action_id"],
            element_type=data["element_type"],
            accessibility_path=data["accessibility_path"],
            element_key=data.get("element_key") or data["accessibility_path"],
            bounding_box=data.get("bounding_box") or {"x": 0, "y": 0, "w": 0, "h": 0},
            requires_input=bool(data.get("requires_input", False)),
            sample_value=data.get("sample_value"),
            target_node_id=data.get("target_node_id"),
            is_destructive=bool(data.get("is_destructive", False)),
            name=data.get("name") or "",
        )


@dataclass
class AppStateNode:
    """One state in the Application Action Tree - spec section 3's full node object."""
    app_name: str
    current_node_id: str
    screen_status: ScreenStatus
    backtrack_action: BacktrackAction
    discovered_actions: list = field(default_factory=list)
    # How the screen was reached, for drawing the map as a tree: the
    # screen and the action on it that led here. None for the first
    # screen, and for one reached by backing out rather than by an action.
    title: str = ""
    parent_node_id: "str | None" = None
    via_action_id: "str | None" = None

    def to_dict(self) -> dict:
        return {
            "app_name": self.app_name,
            "current_node_id": self.current_node_id,
            "title": self.title,
            "parent_node_id": self.parent_node_id,
            "via_action_id": self.via_action_id,
            "screen_status": self.screen_status.value if isinstance(self.screen_status, ScreenStatus) else self.screen_status,
            "backtrack_action": self.backtrack_action.to_dict(),
            "discovered_actions": [a.to_dict() for a in self.discovered_actions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppStateNode":
        ba = data.get("backtrack_action") or {}
        return cls(
            app_name=data["app_name"],
            current_node_id=data["current_node_id"],
            screen_status=ScreenStatus(data["screen_status"]),
            backtrack_action=BacktrackAction(
                type=ba.get("type", "NATIVE_ACCESSIBILITY_BACK"),
                accessibility_id=ba.get("accessibility_id"),
                fallback_key=ba.get("fallback_key", "ESCAPE"),
            ),
            discovered_actions=[DiscoveredAction.from_dict(a) for a in data.get("discovered_actions", [])],
            title=data.get("title") or "",
            parent_node_id=data.get("parent_node_id"),
            via_action_id=data.get("via_action_id"),
        )


@dataclass
class ActionTree:
    """
    The full mapped graph for one application. `nodes` is keyed by
    current_node_id; parent/child edges aren't stored separately - they're
    implied by each node's discovered_actions[i].target_node_id, exactly
    as spec section 5 describes ("connected atomic action functions ...
    and parent/child navigation paths").
    """
    app_name: str
    root_node_id: "str | None" = None
    nodes: dict = field(default_factory=dict)   # node_id -> AppStateNode
    # How the run that made it ended: "complete", or why it stopped short
    # ("stopped", "screen_limit", "action_limit"). A map that isn't
    # complete is never reused in place of mapping the app again.
    ending: str = "complete"
    # The window that was mapped, as the user would recognise it.
    window_title: str = ""
    program: str = ""

    @property
    def complete(self) -> bool:
        return self.ending == "complete"

    def add_node(self, node: AppStateNode) -> None:
        self.nodes[node.current_node_id] = node

    def to_dict(self) -> dict:
        return {
            "app_name": self.app_name,
            "root_node_id": self.root_node_id,
            "ending": self.ending,
            "window_title": self.window_title,
            "program": self.program,
            "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ActionTree":
        return cls(
            app_name=data["app_name"],
            root_node_id=data.get("root_node_id"),
            nodes={
                node_id: AppStateNode.from_dict(node_data)
                for node_id, node_data in data.get("nodes", {}).items()
            },
            # A map saved before runs recorded how they ended is taken as
            # unfinished: it may be from a run that was stopped, or of
            # whichever window the old matching happened to pick.
            ending=data.get("ending") or "",
            window_title=data.get("window_title") or "",
            program=data.get("program") or "",
        )

    # -- persistence ------------------------------------------------

    def _default_path(self) -> str:
        return map_path(self.app_name)

    def save(self, path: "str | None" = None) -> str:
        """Writes the State Graph Matrix JSON artifact. Returns the path written."""
        path = path or self._default_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return path

    @classmethod
    def load(cls, path: str) -> "ActionTree":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def events(self) -> "tuple[list, list]":
        """The map as the Mapping page and the overlay draw it: screens
        (node_event dicts) in the order they were found, and links to
        screens found earlier."""
        nodes = [node_event(node) for node in self.nodes.values()]
        edges = []
        for node_id, node in self.nodes.items():
            for action in node.discovered_actions:
                target = action.target_node_id
                if target and target != node_id and self.nodes.get(target) is not None \
                        and self.nodes[target].parent_node_id != node_id:
                    edges.append((node_id, action.action_id, target))
        return nodes, edges

    # -- convenience --------------------------------------------------

    def node_count(self) -> int:
        return len(self.nodes)

    def action_count(self) -> int:
        return sum(len(n.discovered_actions) for n in self.nodes.values())

    def outline(self, max_lines: int = 160) -> str:
        """
        The map as an indented tree of screens and their controls, with
        each control's name and on-screen centre, for the model. Screens
        are nested under the control that opens them.
        """
        lines: list = []
        seen: set = set()

        def screen_label(node: "AppStateNode") -> str:
            title = f' "{node.title}"' if node.title else ""
            return f"{node.current_node_id}{title}"

        def walk(node_id: str, depth: int) -> None:
            node = self.nodes.get(node_id)
            if node is None or node_id in seen or len(lines) >= max_lines:
                return
            seen.add(node_id)
            indent = "  " * depth
            lines.append(f"{indent}Screen {screen_label(node)}")
            for action in node.discovered_actions:
                if len(lines) >= max_lines:
                    return
                cx, cy = action.center()
                name = action.name or "(no name)"
                flags = " [destructive, never clicked while mapping]" if action.is_destructive else ""
                target = ""
                if action.target_node_id and action.target_node_id != node_id:
                    target = f" -> {action.target_node_id}" if action.target_node_id in seen else ""
                lines.append(f"{indent}  - {action.element_type} '{name}' at ({cx}, {cy}){flags}{target}")
                if action.target_node_id and action.target_node_id not in seen and action.target_node_id != node_id:
                    walk(action.target_node_id, depth + 2)

        if self.root_node_id:
            walk(self.root_node_id, 0)
        for node_id in self.nodes:
            walk(node_id, 0)
        if len(lines) >= max_lines:
            lines.append("  ... (map continues; the full map is in the saved JSON)")
        return "\n".join(lines)


def node_event(node: AppStateNode) -> dict:
    """A mapped screen, as the mapping overlay and the Mapping page draw it
    (app/map_tree.py): plain data, no discovery types."""
    return {
        "id": node.current_node_id,
        "title": node.title,
        "parent": node.parent_node_id,
        "via": node.via_action_id,
        "actions": [
            {
                "id": a.action_id,
                "name": a.name,
                "type": a.element_type,
                "box": dict(a.bounding_box or {}),
                "destructive": a.is_destructive,
                "input": a.requires_input,
            }
            for a in node.discovered_actions
        ],
    }


# ── Saved maps ──────────────────────────────────────────────────────────

def file_stem(app_name: str) -> str:
    """The file name a map is saved under. The app name made safe for a
    file name; a name with no Latin letters (Chinese, Arabic, Hindi) would
    come out empty, and every such app would share one file, so its own
    short fingerprint is added."""
    from ai.actions import _sanitize_macro_name  # the one name-sanitizer the app already has
    stem = _sanitize_macro_name(app_name)
    plain = "".join(ch for ch in (app_name or "") if ch.isascii() and ch.isalnum())
    if not plain:
        digest = hashlib.sha1((app_name or "").strip().lower().encode("utf-8")).hexdigest()[:10]
        stem = f"app_{digest}"
    return stem


def map_path(app_name: str) -> str:
    return os.path.join(TREES_DIR, f"{file_stem(app_name)}_tree.json")


def saved_maps() -> "list[dict]":
    """Every saved map, newest first: app, path, screens, controls,
    complete, ending, window, program, saved (a timestamp)."""
    found = []
    try:
        names = os.listdir(TREES_DIR)
    except OSError:
        return found
    for name in names:
        if not name.endswith("_tree.json"):
            continue
        path = os.path.join(TREES_DIR, name)
        try:
            tree = ActionTree.load(path)
        except Exception:
            continue
        found.append({
            "app": tree.app_name, "path": path, "screens": tree.node_count(),
            "controls": tree.action_count(), "complete": tree.complete, "ending": tree.ending,
            "window": tree.window_title, "program": tree.program,
            "saved": os.path.getmtime(path),
        })
    found.sort(key=lambda entry: entry["saved"], reverse=True)
    return found


def delete_map(app_name: str, path: "str | None" = None) -> bool:
    """Remove an app's saved map, and the code compiled from it by older
    versions. `path` is the map's file when it was listed by saved_maps()
    (a map saved under an older naming isn't at map_path). True if there
    was one."""
    from ai.discovery.compiler import compiled_path
    removed = False
    for candidate in dict.fromkeys(p for p in (path, map_path(app_name), compiled_path(app_name)) if p):
        try:
            os.remove(candidate)
            removed = True
        except FileNotFoundError:
            pass
    return removed
