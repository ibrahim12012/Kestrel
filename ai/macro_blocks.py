# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/macro_blocks.py
=====================
The block vocabulary behind the macro editor's Scratch-style step list -
one source of truth shared by the three places that would otherwise each
invent their own copy of it:

  - app/macro_editor_dialog.py  renders blocks from BLOCK_SPECS (label
                                 text, inline "pipe" slots, category
                                 colour, nesting indentation).
  - ai/actions.py               replays them (play_actions' interpreter
                                 loop for literal macros).
  - ai/macro_generalizer.py     compiles them to Python source for
                                 generalized macros.

Control flow is stored FLAT, with explicit `end` (and `else`) markers,
rather than as nested children:

    [{"type": "repeat", "times": 3},
     {"type": "click", "x": 100, "y": 200},
     {"type": "end"}]

Flat keeps every existing list-shaped thing about macros working
unchanged - the on-disk specs (ai/macro_store.py), the editor's
move-up/move-down/delete, the generalizer's own step validation - where
a nested tree would have meant reworking all of it at once. Indentation
in the UI and real Python nesting in the compiled output are both
recovered from these markers by block_depths() below.

The Scratch bits deliberately stop at what's genuinely useful for
desktop automation: waiting, repeating, looping forever, branching and
waiting on what's actually on screen. No variables, no operators, no
sprites - a macro that clicks buttons has no use for them, and every
one of those would be a whole sub-language to edit, store and compile.
"""

from __future__ import annotations

# ── Categories (Scratch's own colour language, compactly) ─────────────
# Named rather than hex-coded here so app/theme.py stays the only place
# that knows about actual colours; the editor maps these to real ones.
CATEGORY_ACTION = "action"     # clicking, typing, opening - the recorded stuff
CATEGORY_CONTROL = "control"   # wait / repeat / forever / if / stop
CATEGORY_SENSING = "sensing"   # looking at what's on screen (colour)

CATEGORY_ORDER = (CATEGORY_ACTION, CATEGORY_CONTROL, CATEGORY_SENSING)
CATEGORY_LABELS = {
    CATEGORY_ACTION: "Actions",
    CATEGORY_CONTROL: "Control",
    CATEGORY_SENSING: "Sensing",
}

# ── Pipe (input slot) kinds ───────────────────────────────────────────
# What an inline editable slot inside a block holds. The editor picks
# the widget from this; everything else just reads the value.
PIPE_TEXT = "text"
PIPE_INT = "int"
PIPE_NUMBER = "number"
PIPE_COLOR = "color"
PIPE_POINT = "point"  # an (x, y) screen position, edited as one slot


class Pipe:
    """One editable slot inside a block - Scratch's rounded input."""

    def __init__(self, key: str, kind: str, default=None, label: str = "", minimum=None, maximum=None):
        self.key = key
        self.kind = kind
        self.default = default
        self.label = label or key
        self.minimum = minimum
        self.maximum = maximum


class BlockSpec:
    """
    One block type. `template` is the block's text with {pipe} tokens
    where its slots go - the editor splits on those to lay out
    "text [slot] text [slot]" the way a Scratch block reads, and
    describe() below reuses the exact same string for plain-text
    summaries so the two can't drift apart.

    opens/closes describe nesting: `opens` blocks (repeat, forever,
    if_colour) are followed by their contents and a matching `end`;
    `closes` unwinds one level; `midpoint` (else) does both.
    """

    def __init__(
        self,
        type_: str,
        category: str,
        template: str,
        pipes: "list[Pipe] | None" = None,
        opens: bool = False,
        closes: bool = False,
        midpoint: bool = False,
        addable: bool = True,
        menu_label: str = "",
    ):
        self.type = type_
        self.category = category
        self.template = template
        self.pipes = pipes or []
        self.opens = opens
        self.closes = closes
        self.midpoint = midpoint
        # False for blocks that only ever appear as a side effect of
        # adding another one - `end` is created with its opener, never
        # on its own, or a list could never be balanced by construction.
        self.addable = addable
        self.menu_label = menu_label or template

    def pipe(self, key: str) -> "Pipe | None":
        for p in self.pipes:
            if p.key == key:
                return p
        return None


BLOCK_SPECS: "dict[str, BlockSpec]" = {
    # ── Actions (what the recorder produces) ─────────────────────────
    "click": BlockSpec(
        "click", CATEGORY_ACTION, "click {target}",
        [Pipe("target", PIPE_TEXT, "", "what to click"), Pipe("point", PIPE_POINT, None, "position")],
        menu_label="Click",
    ),
    "right_click": BlockSpec(
        "right_click", CATEGORY_ACTION, "right-click {target}",
        [Pipe("target", PIPE_TEXT, "", "what to click"), Pipe("point", PIPE_POINT, None, "position")],
        menu_label="Right-click",
    ),
    "write": BlockSpec(
        "write", CATEGORY_ACTION, "type {value}",
        [Pipe("value", PIPE_TEXT, "", "text")],
        menu_label="Type text",
    ),
    "enter": BlockSpec("enter", CATEGORY_ACTION, "press Enter", menu_label="Press Enter"),
    # Ctrl+C, Alt+Tab, Ctrl+Shift+N. The recorder captures these while
    # typing (see app/workers/recorder_worker.py); typing mode used to
    # swallow them, so a recording of "select all, copy" came back as
    # nothing at all.
    "hotkey": BlockSpec(
        "hotkey", CATEGORY_ACTION, "press {keys}",
        [Pipe("keys", PIPE_TEXT, "ctrl+c", "keys")],
        menu_label="Press a key combo",
    ),
    "new_tab": BlockSpec("new_tab", CATEGORY_ACTION, "open a new browser tab", menu_label="New browser tab"),
    "open_app": BlockSpec(
        "open_app", CATEGORY_ACTION, "open app {app}",
        [Pipe("app", PIPE_TEXT, "", "application")],
        menu_label="Open app",
    ),
    # One saved macro, called from inside another.
    #
    # This is what turns a library of macros into something you can
    # build with. Before it, every macro was a flat recording that could
    # only be extended by re-recording it: "log in, then open the
    # report" meant recording the login again, and a login that changed
    # meant fixing it in every macro that happened to contain it.
    #
    # Added from the palette's Macros section, which lists whatever is
    # saved (see app/block_list.py). Recursion is refused at run time
    # rather than here, because what a name refers to can change between
    # the block being added and the macro being run.
    "call_macro": BlockSpec(
        "call_macro", CATEGORY_ACTION, "run macro {macro}",
        [Pipe("macro", PIPE_TEXT, "", "macro name")],
        menu_label="Run a macro",
    ),

    # ── Control ───────────────────────────────────────────────────────
    "wait": BlockSpec(
        "wait", CATEGORY_CONTROL, "wait {seconds} seconds",
        [Pipe("seconds", PIPE_NUMBER, 1.0, "seconds", minimum=0.0, maximum=600.0)],
        menu_label="Wait",
    ),
    "repeat": BlockSpec(
        "repeat", CATEGORY_CONTROL, "repeat {times}",
        [Pipe("times", PIPE_INT, 3, "times", minimum=1, maximum=1000)],
        opens=True, menu_label="Repeat",
    ),
    "forever": BlockSpec(
        "forever", CATEGORY_CONTROL, "forever (max {max_loops})",
        [Pipe("max_loops", PIPE_INT, 100, "iterations", minimum=1, maximum=100000)],
        opens=True, menu_label="Forever",
    ),
    # The loop that waits for the screen to say it's done: keep doing
    # this until the pixel turns the colour that means finished. A plain
    # repeat has to guess a count; this one watches.
    "repeat_until_color": BlockSpec(
        "repeat_until_color", CATEGORY_CONTROL,
        "repeat until colour at {point} is {color}",
        [
            Pipe("point", PIPE_POINT, None, "position"),
            Pipe("color", PIPE_COLOR, "#00ff00", "colour"),
            Pipe("tolerance", PIPE_INT, 12, "tolerance", minimum=0, maximum=255),
            Pipe("max_loops", PIPE_INT, 100, "give up after", minimum=1, maximum=100000),
        ],
        opens=True, menu_label="Repeat until colour…",
    ),
    "if_color": BlockSpec(
        "if_color", CATEGORY_CONTROL, "if colour at {point} is {color}",
        [
            Pipe("point", PIPE_POINT, None, "position"),
            Pipe("color", PIPE_COLOR, "#ffffff", "colour"),
            Pipe("tolerance", PIPE_INT, 12, "tolerance", minimum=0, maximum=255),
        ],
        opens=True, menu_label="If colour is…",
    ),
    "else": BlockSpec("else", CATEGORY_CONTROL, "else", midpoint=True, addable=False),
    "end": BlockSpec("end", CATEGORY_CONTROL, "end", closes=True, addable=False),
    "stop": BlockSpec("stop", CATEGORY_CONTROL, "stop this macro", menu_label="Stop"),

    # ── Sensing ───────────────────────────────────────────────────────
    # The colour-change watcher: waits for a pixel to become anything
    # other than what it started as, with a slot for the one colour it
    # must NOT become. Landing on that colour aborts the macro instead
    # of carrying on - it's a guard ("if this turns red, something went
    # wrong, don't keep clicking"), not just a delay.
    "wait_color_change": BlockSpec(
        "wait_color_change", CATEGORY_SENSING,
        "wait for colour at {point} to change, but not to {forbidden}",
        [
            Pipe("point", PIPE_POINT, None, "position"),
            Pipe("forbidden", PIPE_COLOR, "#ff0000", "excluded colour"),
            Pipe("tolerance", PIPE_INT, 12, "tolerance", minimum=0, maximum=255),
            Pipe("timeout", PIPE_NUMBER, 30.0, "timeout (s)", minimum=0.5, maximum=3600.0),
        ],
        menu_label="Wait for colour change",
    ),
}

# Types the recorder itself can produce - everything else is added by
# hand in the editor. ai/macro_store.py's literal round-trip uses this
# to know which shapes have a legacy tuple form to convert back to.
RECORDED_TYPES = frozenset({
    "click", "right_click", "write", "enter", "new_tab", "open_app", "hotkey",
})
# Everything this module knows about, for validation elsewhere.
ALL_TYPES = frozenset(BLOCK_SPECS)
# Blocks offered in the editor's "add" palette, grouped by category.
ADDABLE_TYPES = tuple(t for t, spec in BLOCK_SPECS.items() if spec.addable)


def spec_for(step_type: str) -> "BlockSpec | None":
    return BLOCK_SPECS.get(step_type)


def is_control(step_type: str) -> bool:
    spec = BLOCK_SPECS.get(step_type)
    return bool(spec and spec.category == CATEGORY_CONTROL)


# ── Nesting ───────────────────────────────────────────────────────────

def block_depths(steps: "list[dict]") -> "list[int]":
    """
    Indentation level per step, recovered from the opens/closes markers.
    A closer (`end`) and a midpoint (`else`) are drawn at the level of
    the block they belong to, not at their contents' level - the same
    way Scratch draws the bottom lip of a C-block flush with its mouth.

    Never returns a negative depth: an unbalanced list (a hand-edited
    spec, or a mid-edit state where the user just deleted an opener)
    should render as something sane rather than crash the editor. The
    real complaint about that lives in validate_structure().
    """
    depths: "list[int]" = []
    depth = 0
    for step in steps:
        spec = BLOCK_SPECS.get(step.get("type", ""))
        if spec and (spec.closes or spec.midpoint):
            depth = max(0, depth - 1)
        depths.append(depth)
        if spec and (spec.opens or spec.midpoint):
            depth += 1
    return depths


def validate_structure(steps: "list[dict]") -> "tuple[bool, str]":
    """
    (ok, error) for the control-flow markers alone - every opener has a
    matching `end`, no stray `end`/`else`, and `else` only ever inside
    an `if colour`. Deliberately separate from the per-step field
    validation in ai/macro_generalizer.py: this is the part that has to
    hold for the compiled Python to even be syntactically valid, so
    both the editor (before saving) and the compiler (before emitting)
    check it.
    """
    stack: "list[str]" = []
    for i, step in enumerate(steps, 1):
        step_type = step.get("type", "")
        spec = BLOCK_SPECS.get(step_type)
        if spec is None:
            continue  # unknown/legacy steps are someone else's complaint
        if spec.midpoint:
            if not stack:
                return False, f"step {i}: “else” outside of an “if colour” block"
            if stack[-1] != "if_color":
                return False, f"step {i}: “else” can only follow an “if colour” block"
            stack[-1] = "else"
        elif spec.closes:
            if not stack:
                return False, f"step {i}: “end” with nothing open before it"
            stack.pop()
        elif spec.opens:
            stack.append(step_type)
    if stack:
        opener = BLOCK_SPECS.get(stack[-1])
        label = opener.menu_label if opener else stack[-1]
        return False, f"“{label}” is never closed - every repeat/forever/if needs an “end”."
    return True, ""


class BlockNode:
    """One block plus, for a C-block, the blocks nested inside it.

    parse_program() below turns the flat marker list into a list of
    these once, so the two things that actually have to UNDERSTAND the
    nesting - the replay interpreter in ai/actions.py and the Python
    codegen in ai/macro_generalizer.py - both walk a tree instead of
    each writing their own scan for matching `end` markers (two
    implementations of the same bracket-matching, drifting apart the
    first time a new C-block is added)."""

    def __init__(self, step: dict):
        self.step = step
        self.type = step.get("type", "")
        self.body: "list[BlockNode]" = []
        self.else_body: "list[BlockNode]" = []

    def __repr__(self) -> str:  # debugging aid only
        return f"<BlockNode {self.type} body={len(self.body)} else={len(self.else_body)}>"


def parse_program(steps: "list[dict]") -> "list[BlockNode]":
    """
    Flat step list (with `end`/`else` markers) → a tree of BlockNodes.

    Tolerant by design, because it runs against specs that may have
    been hand-edited: a stray `end` with nothing open is ignored rather
    than raising, and anything still open at the end of the list is
    closed implicitly. validate_structure() is what actually tells the
    user their blocks don't balance - by the time something is being
    replayed or compiled, doing the most reasonable thing with what's
    there beats refusing to run at all.
    """
    root: "list[BlockNode]" = []
    # Each frame: (list currently being appended to, the node that owns it)
    stack: "list[tuple[list, BlockNode]]" = []
    current = root

    for step in steps:
        step_type = step.get("type", "")
        spec = BLOCK_SPECS.get(step_type)

        if spec is not None and spec.midpoint:
            if stack and stack[-1][1].type == "if_color":
                # Everything after this goes into the owner's else_body.
                owner = stack[-1][1]
                stack[-1] = (owner.else_body, owner)
                current = owner.else_body
            continue

        if spec is not None and spec.closes:
            if stack:
                stack.pop()
                current = stack[-1][0] if stack else root
            continue

        node = BlockNode(step)
        current.append(node)

        if spec is not None and spec.opens:
            stack.append((node.body, node))
            current = node.body

    return root


def new_step(step_type: str) -> dict:
    """A block of `step_type` with every pipe at its default - what the
    editor inserts when one is picked from the palette."""
    spec = BLOCK_SPECS[step_type]
    step: dict = {"type": step_type}
    for pipe in spec.pipes:
        if pipe.kind == PIPE_POINT:
            step["x"], step["y"] = 0, 0
        elif pipe.default is not None:
            step[pipe.key] = pipe.default
    return step


def pipe_value(step: dict, pipe: Pipe):
    """The current value of one slot, falling back to its default - a
    spec written before a pipe existed (or hand-edited to drop it)
    still renders and still runs."""
    if pipe.kind == PIPE_POINT:
        return int(step.get("x", 0) or 0), int(step.get("y", 0) or 0)
    return step.get(pipe.key, pipe.default)


def format_pipe(value, pipe: Pipe) -> str:
    if pipe.kind == PIPE_POINT:
        x, y = value
        return f"{x}, {y}"
    if pipe.kind == PIPE_NUMBER:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        return f"{number:g}"
    if value in (None, ""):
        return "-"
    return str(value)


def describe(step: dict) -> str:
    """
    Plain-text one-liner for a step - the same template the editor
    renders as a block, with each slot's current value substituted in.
    Used for menus, tooltips and log lines.
    """
    step_type = step.get("type", "")
    spec = BLOCK_SPECS.get(step_type)
    if spec is None:
        return f"unrecognized step: {step.get('_raw', step)!r}"
    text = spec.template
    for pipe in spec.pipes:
        value = pipe_value(step, pipe)
        # A recorded click has no vision "target" to name - it's a raw
        # pixel position - so it reads by where it clicks rather than
        # as "click - ".
        if pipe.key == "target" and not str(value or "").strip() and "x" in step:
            text = text.replace("{target}", f"at {int(step.get('x', 0))}, {int(step.get('y', 0))}")
            continue
        text = text.replace("{" + pipe.key + "}", format_pipe(value, pipe))
    return text


# ── Colour helpers ────────────────────────────────────────────────────
# Shared by the editor (swatch rendering) and the runtime (comparing a
# sampled pixel against a slot's value), so "does this colour match"
# means exactly one thing across the app.

def hex_to_rgb(value: str) -> "tuple[int, int, int]":
    text = (value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return 0, 0, 0
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return 0, 0, 0


def rgb_to_hex(rgb) -> str:
    r, g, b = (max(0, min(255, int(v))) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def colors_match(a, b, tolerance: int = 12) -> bool:
    """
    Per-channel comparison rather than a Euclidean distance: a
    tolerance the user typed as "12" should mean "up to 12 off on any
    channel", which is what they can reason about when nudging it after
    a false match - a distance threshold makes the same number behave
    differently depending on how many channels happen to differ.
    """
    ar, ag, ab = a[:3]
    br, bg, bb = b[:3]
    return (
        abs(int(ar) - int(br)) <= tolerance
        and abs(int(ag) - int(bg)) <= tolerance
        and abs(int(ab) - int(bb)) <= tolerance
    )
