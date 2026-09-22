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

    def __post_init__(self):
        if not self.element_key:
            self.element_key = self.accessibility_path

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
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
        )


@dataclass
class AppStateNode:
    """One state in the Application Action Tree - spec section 3's full node object."""
    app_name: str
    current_node_id: str
    screen_status: ScreenStatus
    backtrack_action: BacktrackAction
    discovered_actions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "app_name": self.app_name,
            "current_node_id": self.current_node_id,
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

    def add_node(self, node: AppStateNode) -> None:
        self.nodes[node.current_node_id] = node

    def to_dict(self) -> dict:
        return {
            "app_name": self.app_name,
            "root_node_id": self.root_node_id,
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
        )

    # -- persistence ------------------------------------------------

    def _default_path(self) -> str:
        from ai.actions import _sanitize_macro_name  # reuse the one name-sanitizer the app already has
        return os.path.join(TREES_DIR, f"{_sanitize_macro_name(self.app_name)}_tree.json")

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

    # -- convenience --------------------------------------------------

    def node_count(self) -> int:
        return len(self.nodes)

    def action_count(self) -> int:
        return sum(len(n.discovered_actions) for n in self.nodes.values())
