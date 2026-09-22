# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/discovery/mapper.py
========================
Structural UI mapping: a bounded depth-first walk over an application's
accessibility tree, written against the AccessibilityBackend interface
(ai/discovery/accessibility.py) so it stays unit-testable with the
in-memory fake in tests/test_discovery_mapper.py.

Screen states:

  EXPLORING            More untried actions remain here.
  SINGLE_CHOICE_LEFT    Down to the last untried action, or started with one.
  STOP_BRANCH_MAX       Terminal screen, or the depth/node/action cap.

The mapper never activates anything ai.safety flags as destructive. It
records the action so the compiled function can gate it behind a
confirmation, then hovers it and moves on.

Why the traversal is shaped the way it is
-----------------------------------------
An earlier version kept its "already clicked this" memory in a per-visit
worklist, so a screen was only safe from being re-clicked as long as it
kept hashing to the same value. It did not. The hash folded in the window
title and every live control name, both of which a real app rewrites
constantly, and which the mapper itself rewrote by typing into search
boxes. A changed hash meant a new node, a new worklist and another full
pass over controls it had already clicked.

Three things fix that, and all three matter:

  1. _screen_shape drops the window title, keys on element ids as well
     as names, and blanks the names of controls whose name is content
     rather than structure, so a screen keeps one identity while the app
     is used.
  2. _match_screen falls back to shape similarity, so a toast appearing
     or a button greying out does not split a screen into two nodes.
  3. The tried set lives on the node, keyed by element, not on the visit.
     Arriving at a known screen resumes it instead of restarting it.

On top of that the traversal tracks where it actually is rather than
where it assumes it is: landing on a screen already on the stack unwinds
to it, and every backtrack is followed by a re-read of the screen.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import ai.safety as safety
from ai.discovery.accessibility import AccessibilityBackend, AccessibilityElement, get_backend
from ai.discovery.errors import DiscoveryError
from ai.discovery.tree import ActionTree, AppStateNode, BacktrackAction, DiscoveredAction, ScreenStatus
from ai.log import log as print

DEFAULT_MAX_NODES = 60
DEFAULT_MAX_DEPTH = 8
DEFAULT_MOCK_VALUE = "test_query"

# Clicks and keystrokes per run. max_nodes only bounds how many screens
# get allocated, so on its own it lets a run act indefinitely while
# allocating almost nothing. This is the hard stop.
DEFAULT_MAX_ACTIONS = 400

# Control types whose accessible name is live content, not structure. A
# list row is titled after the record it shows, a text box after whatever
# was typed into it. Hashing those makes a screen look new every time
# anyone touches it, including when the mapper types its own mock value.
VOLATILE_NAME_TYPES = frozenset({"ListItem", "TreeItem", "Edit", "ComboBox", "Spinner"})

# How alike two screens' shapes must be to count as the same screen.
# Covers a toast appearing, a button greying out, one control relayouting.
SCREEN_SIMILARITY = 0.82

# Similarity is only consulted for screens with at least this many
# elements. Below it a single element swing moves the ratio too far, so
# small screens have to match exactly. That is what keeps a wizard of
# near-identical steps from collapsing into one node.
MIN_SHAPE_FOR_SIMILARITY = 5


def _screen_shape(root: AccessibilityElement, interactables: list) -> frozenset:
    """
    What a screen is recognised by: the controls on it, each as its type,
    its name and its id.

    The window title is deliberately absent. It used to be the first
    thing in the hash, and it is the single least reliable thing the
    accessibility layer offers: it carries the open document, a dirty
    marker, an unread count, a download percentage. A screen whose title
    ticks is a new screen on every reading, and a new screen is explored
    from the top.

    Ids earn their place because without them two screens that merely
    look alike collapse into one, and a wizard of near-identical steps
    would map as a single node. An AutomationId or a positional path
    changes only when the screen really changes.

    Names of controls whose name is content rather than structure are
    dropped, ids included, so twenty search results and forty search
    results are the same screen. A set, not a list, for the same reason.
    """
    shape = {f"root:{root.control_type}"}
    for el in interactables:
        if el.control_type in VOLATILE_NAME_TYPES:
            shape.add(f"{el.control_type}:")
        else:
            shape.add(f"{el.control_type}:{el.name}:{el.id}")
    return frozenset(shape)


def _signature(shape: frozenset) -> str:
    return hashlib.sha1("|".join(sorted(shape)).encode("utf-8", "ignore")).hexdigest()[:16]


def _similarity(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _slugify(text: str) -> str:
    from ai.actions import _sanitize_macro_name
    return _sanitize_macro_name(text or "unnamed")


def _context_of(el: AccessibilityElement) -> str:
    """
    Which part of the UI an element sits in, as the parent portion of
    its positional path.

    "Pane[0]/Group[1]/Button[3]" has the context "Pane[0]/Group[1]". An
    element identified by AutomationId, or one with no path, has no
    context - an AutomationId is already unique within the application
    and does not need one.
    """
    path = (el.id or "")
    if "/" not in path:
        return ""
    return path.rsplit("/", 1)[0]


def _element_key(el: AccessibilityElement, counts: dict) -> str:
    """
    What the tried set is keyed by. An AutomationId wins when the app
    gave one. Otherwise type plus accessible name, which survives
    relayout where a positional path does not, and falls back to the
    path for controls with no name at all. Duplicates get an ordinal so
    three identically named buttons stay three actions.

    The name is scoped by the element's container. `ControlType:Name`
    alone collides across contexts, and the collisions are the common
    case rather than the exotic one: "OK", "Cancel", "Close", "Open",
    "Save", "Settings" appear on the main window, inside a dialog, and
    again in an open menu, all in the same application. Keyed by name
    alone, clicking the toolbar's "Open" marked the dialog's "Open"
    tried as well, and the dialog's button was then never explored -
    which is the same class of bug as clicking one thing repeatedly,
    seen from the other side.

    An AutomationId keeps no context, deliberately: it is already unique
    across the application, and attaching a container path to it would
    make it change whenever the layout shifted, which is the property
    AutomationIds exist to avoid.
    """
    base = (el.automation_id or "").strip()
    if not base:
        if el.name:
            context = _context_of(el)
            base = f"{context}|{el.control_type}:{el.name}" if context else f"{el.control_type}:{el.name}"
        else:
            base = f"path:{el.id}"
    seen = counts.get(base, 0)
    counts[base] = seen + 1
    return base if seen == 0 else f"{base}#{seen}"


@dataclass
class _Frame:
    """One screen on the traversal stack. It holds no worklist: what has
    been tried lives on the node, so re-entering a screen resumes it."""
    node_id: str
    depth: int


@dataclass
class _Screen:
    """A mapped screen, plus the bookkeeping the traversal needs and the
    saved tree does not carry."""
    node_id: str
    shape: frozenset
    tried: set = field(default_factory=set)


def _build_node(app_name: str, node_id: str, backend: AccessibilityBackend, root: AccessibilityElement) -> AppStateNode:
    interactables = backend.list_interactables(root)
    back_el = backend.find_back_control(root)
    backtrack = (
        BacktrackAction(type="NATIVE_ACCESSIBILITY_BACK", accessibility_id=back_el.id, fallback_key="ESCAPE")
        if back_el is not None
        else BacktrackAction(type="KEY_FALLBACK", accessibility_id=None, fallback_key="ESCAPE")
    )

    discovered = []
    counts: dict = {}
    for i, el in enumerate(interactables):
        action_id = f"action_{_slugify(el.name or el.control_type)}_{i}"
        discovered.append(DiscoveredAction(
            action_id=action_id,
            element_type=el.control_type,
            accessibility_path=el.id,
            element_key=_element_key(el, counts),
            bounding_box=dict(el.bounding_box),
            requires_input=el.is_input,
            sample_value=DEFAULT_MOCK_VALUE if el.is_input else None,
            target_node_id=None,
            is_destructive=(
                safety.is_destructive_label(el.name)
                or safety.is_destructive_label(action_id)
                or safety.is_likely_window_chrome_button(el.bounding_box, root.bounding_box)
            ),
        ))

    return AppStateNode(
        app_name=app_name,
        current_node_id=node_id,
        screen_status=ScreenStatus.EXPLORING if len(discovered) > 1 else ScreenStatus.SINGLE_CHOICE_LEFT,
        backtrack_action=backtrack,
        discovered_actions=discovered,
    )


def _backtrack(backend: AccessibilityBackend, node: AppStateNode) -> None:
    ba = node.backtrack_action
    if ba.type == "NATIVE_ACCESSIBILITY_BACK" and ba.accessibility_id:
        try:
            fake = AccessibilityElement(id=ba.accessibility_id, name="", control_type="Button")
            if backend.activate(fake):
                return
        except Exception as e:
            print(f"[discovery] Native back control failed ({e}), falling back to {ba.fallback_key}")
    backend.send_key(ba.fallback_key)


def _stop_requested() -> bool:
    """
    Whether the user has asked for automation to stop.

    Reads the same ai.actions.emergency_stop flag every action tool
    checks, so all three routes to it work on a mapping run too: the
    global stop shortcut, the Esc abort, and the mouse-corner fail-safe.

    Imported inside the function rather than at module scope, because
    ai.actions imports this package's runtime module and a top-level
    import here would close the cycle. Reading the attribute fresh each
    call also matters: `from ai.actions import emergency_stop` would
    bind the value once and never see it change.
    """
    try:
        import ai.actions as actions
        return bool(actions.emergency_stop)
    except Exception:
        return False


def map_application_ui(
    app_name: str,
    backend: "AccessibilityBackend | None" = None,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    mock_value: str = DEFAULT_MOCK_VALUE,
    max_actions: int = DEFAULT_MAX_ACTIONS,
    on_progress=None,
) -> ActionTree:
    """
    Maps `app_name`'s currently-open window and returns the ActionTree.
    Nothing is written to disk here; ai/actions.py's
    tool_discover_application_ui saves and compiles it.

    backend: inject a fake for testing. Defaults to
    ai.discovery.accessibility.get_backend(app_name), which raises
    DiscoveryError if no window matches or the platform has no backend.

    max_actions: ceiling on clicks and keystrokes for the whole run.

    on_progress: optional callable(nodes_mapped, actions_found), called
    after each node is added. app/mapping_monitor.py uses it to keep its
    live count current without polling.
    """
    backend = backend or get_backend(app_name)
    tree = ActionTree(app_name=app_name)

    screens: dict = {}          # node_id -> _Screen
    by_signature: dict = {}     # exact signature -> node_id
    node_counter = 0
    actions_taken = 0

    def _next_id() -> str:
        nonlocal node_counter
        node_counter += 1
        return f"screen_{_slugify(app_name)}_{node_counter:03d}"

    def _report_progress() -> None:
        if on_progress is not None:
            try:
                on_progress(tree.node_count(), tree.action_count())
            except Exception as e:
                print(f"[discovery] on_progress callback failed: {e}")

    def _match_screen(shape: frozenset) -> "str | None":
        """
        The node this screen already is, or None if it is new. Exact
        first, then similarity, so a stray toast or a button greying out
        does not split one screen into two nodes.
        """
        exact = by_signature.get(_signature(shape))
        if exact is not None:
            return exact
        if len(shape) < MIN_SHAPE_FOR_SIMILARITY:
            return None
        best_id, best_score = None, 0.0
        for screen in screens.values():
            if len(screen.shape) < MIN_SHAPE_FOR_SIMILARITY:
                continue
            score = _similarity(shape, screen.shape)
            if score > best_score:
                best_id, best_score = screen.node_id, score
        if best_id is not None and best_score >= SCREEN_SIMILARITY:
            # Remember this variant so the next arrival matches on the
            # cheap exact path instead of scanning every known screen.
            by_signature[_signature(shape)] = best_id
            return best_id
        return None

    def _register(root: AccessibilityElement, shape: frozenset) -> str:
        node_id = _next_id()
        node = _build_node(app_name, node_id, backend, root)
        tree.add_node(node)
        screens[node_id] = _Screen(node_id=node_id, shape=shape)
        by_signature[_signature(shape)] = node_id
        _report_progress()
        return node_id

    def _read_screen() -> tuple:
        """(root, shape) for whatever is on screen now."""
        root = backend.capture_state()
        return root, _screen_shape(root, backend.list_interactables(root))

    def _resync(depth: int) -> None:
        """
        Put the stack back in step with the app after a backtrack.

        Where a back control actually lands is not something the mapper
        gets to decide, and acting on a screen it has already left is how
        the same few controls end up being clicked over and over. So:
        read the screen, and follow it. An ancestor unwinds the stack, a
        screen mapped elsewhere gets pushed so it resumes where it left
        off, and something unseen is mapped as the new screen it is.
        """
        root, shape = _read_screen()
        landed = _match_screen(shape)
        if landed is not None and _unwind_to(stack, landed):
            return
        if landed is None:
            if node_counter >= max_nodes:
                return
            landed = _register(root, shape)
        if not stack or stack[-1].node_id != landed:
            stack.append(_Frame(node_id=landed, depth=depth))

    def _next_action(node: AppStateNode) -> "tuple[DiscoveredAction | None, int]":
        """The next action on this node nobody has tried yet, and how many
        remain after it."""
        tried = screens[node.current_node_id].tried
        untried = [a for a in node.discovered_actions if a.element_key not in tried]
        return (untried[0] if untried else None), max(0, len(untried) - 1)

    root_element = backend.get_root()
    root_id = _register(
        root_element,
        _screen_shape(root_element, backend.list_interactables(root_element)),
    )
    tree.root_node_id = root_id

    print(
        f"[discovery] Mapping {app_name!r}, root node {root_id} "
        f"({len(tree.nodes[root_id].discovered_actions)} interactable(s))"
    )

    stack = [_Frame(node_id=root_id, depth=0)]

    while stack and node_counter < max_nodes and actions_taken < max_actions:
        # The user's stop, checked every iteration.
        #
        # A mapping run could not be interrupted at all. Its loop
        # consulted only its own caps - 60 nodes and 400 actions - and
        # nothing else: not the emergency-stop flag every other tool
        # checks, not the progress callback (whose return value is
        # discarded), not the monitor window. The corner fail-safe could
        # not help either, because the mapper drives the UI through
        # uiautomation rather than pyautogui, so pyautogui's fail-safe
        # never fires during a run. Once started, four hundred clicks
        # into someone's application were going to happen.
        #
        # Everything mapped so far is already in `tree`, and every exit
        # path returns it, so stopping here still saves the work.
        if _stop_requested():
            print("[discovery] Stop requested - ending the mapping run early.")
            break

        frame = stack[-1]
        node = tree.nodes[frame.node_id]
        action, remaining = _next_action(node)

        if frame.depth >= max_depth or action is None:
            if frame.depth >= max_depth and action is not None:
                node.screen_status = ScreenStatus.STOP_BRANCH_MAX
                print(f"[discovery] {node.current_node_id}: max depth reached, backing out")
            stack.pop()
            if not stack:
                break
            # Leaving a screen is the one move that has to be undone in
            # the app itself. Backing out of the root would close or
            # disturb the target window, so it only happens below it.
            _backtrack(backend, node)
            _resync(max(0, frame.depth - 1))
            continue

        screens[frame.node_id].tried.add(action.element_key)
        node.screen_status = ScreenStatus.EXPLORING if remaining else ScreenStatus.SINGLE_CHOICE_LEFT

        target = AccessibilityElement(
            id=action.accessibility_path,
            name="",
            control_type=action.element_type,
            bounding_box=action.bounding_box,
        )

        if action.is_destructive:
            # Recorded, never triggered. Hovering is free and can surface
            # a tooltip with more than the static accessible name, so it
            # is worth doing, but it must never abort the run.
            try:
                backend.hover(target)
            except Exception as e:
                print(f"[discovery] {node.current_node_id}: hover on {action.action_id!r} failed, continuing: {e}")
            print(f"[discovery] {node.current_node_id}: skipping destructive action {action.action_id!r}")
            continue

        if _stop_requested():
            print("[discovery] Stop requested - not taking this action.")
            break

        actions_taken += 1
        ok = backend.input_value(target, mock_value) if action.requires_input else backend.activate(target)
        if not ok:
            print(f"[discovery] {node.current_node_id}: could not trigger {action.action_id!r}, skipping")
            continue

        root_now, shape = _read_screen()
        landed = _match_screen(shape)

        if landed == frame.node_id:
            # The click did nothing navigational. Stay put; the action is
            # already marked tried, so the loop moves on to the next one.
            action.target_node_id = landed
            if not remaining:
                node.screen_status = ScreenStatus.STOP_BRANCH_MAX
            continue

        if landed is not None:
            action.target_node_id = landed
            print(f"[discovery] {node.current_node_id} --{action.action_id}--> known node {landed}")
            if _unwind_to(stack, landed):
                # It was an ancestor, so this is a normal return upward
                # and the stack now agrees with the screen.
                continue
            # A sideways jump to a screen mapped down some other branch.
            # Its tried set came with it, so this resumes rather than
            # restarts, which is what stops the same controls being
            # clicked on every arrival.
            stack.append(_Frame(node_id=landed, depth=frame.depth + 1))
            continue

        new_id = _register(root_now, shape)
        new_node = tree.nodes[new_id]
        action.target_node_id = new_id
        print(
            f"[discovery] {node.current_node_id} --{action.action_id}--> new node {new_id} "
            f"({len(new_node.discovered_actions)} interactable(s))"
        )

        if not new_node.discovered_actions:
            new_node.screen_status = ScreenStatus.STOP_BRANCH_MAX
            _backtrack(backend, new_node)
            _resync(frame.depth)
            continue

        stack.append(_Frame(node_id=new_id, depth=frame.depth + 1))

    if node_counter >= max_nodes:
        print(f"[discovery] Stopped at the node cap ({max_nodes}). What was mapped is still saved and usable.")
    if actions_taken >= max_actions:
        print(f"[discovery] Stopped at the action cap ({max_actions}). What was mapped is still saved and usable.")

    print(f"[discovery] Mapped {app_name!r}: {tree.node_count()} node(s), {tree.action_count()} action(s)")
    return tree


def _unwind_to(stack: list, node_id: str) -> bool:
    """
    Pop back to `node_id` if it is on the stack, because that is where
    the app actually is now. False means it was not there and the caller
    has to decide what to do instead.

    Without this the traversal keeps taking actions from a screen it has
    already navigated away from. Those actions resolve against whatever
    is on screen now, which is how the same few controls get clicked
    over and over.
    """
    for i in range(len(stack) - 1, -1, -1):
        if stack[i].node_id == node_id:
            del stack[i + 1:]
            return True
    return False
