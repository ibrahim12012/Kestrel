# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_main_window_init_order.py
======================================
Guards one specific way MainWindow can fail to start at all.

MainWindow.__init__ is long, and it calls helper methods partway down
(_connect_page_signals, register_global_shortcuts, and friends). Each of
those reads attributes that __init__ sets - and nothing stops you adding
a line to a helper that reads an attribute __init__ does not assign
until fifty lines LATER. The result is an AttributeError before the
window ever appears:

    File "app/main_window.py", line 1355, in _connect_page_signals
        self._wakeword_worker.set_macro_control)
    AttributeError: 'MainWindow' object has no attribute '_wakeword_worker'

That is a total startup failure from a one-line edit in a file the
author was not looking at, and it reaches a user because constructing a
real MainWindow needs a display and a QApplication, so no test in this
suite instantiates one. This reads the source instead, which needs
neither.

A read inside a lambda or a nested def is NOT a problem: connecting
`lambda: self._wakeword_worker.set_recording_mode(True)` is fine even
before the worker exists, because the body only runs when the signal
fires. Only reads evaluated at call time count, which is exactly the
distinction the checker below draws.
"""

import ast
import os

SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "main_window.py",
)


def _class_def(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found in main_window.py")


def _self_attr(node):
    """'self.foo' -> 'foo', anything else -> None."""
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return node.attr
    return None


def _assigned_self_attrs(func):
    """{attr: first line it is assigned} for `self.x = ...` in `func`."""
    out = {}
    for node in ast.walk(func):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            attr = _self_attr(target)
            if attr is not None:
                out.setdefault(attr, node.lineno)
    return out


def _guarded_self_attrs(func):
    """
    Attributes the function itself checks for before using, via
    `hasattr(self, "x")` or `getattr(self, "x", default)`.

    _apply_window_chrome_theme does exactly this: it is called before
    self._chrome exists, on purpose, and guards the one line that needs
    it. That is a correct pattern, not the bug this file is about.
    """
    out = set()
    for node in ast.walk(func):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("hasattr", "getattr")
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            out.add(node.args[1].value)
    return out


def _immediate_self_reads(func):
    """
    {attr: line} for every `self.x` READ in `func` that is evaluated when
    the function runs, skipping anything inside a lambda or a nested def
    (whose body runs later, if ever) and anything the function guards
    with hasattr/getattr.
    """
    out = {}
    assigned = set(_assigned_self_attrs(func)) | _guarded_self_attrs(func)

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue                      # deferred: not our problem
            attr = _self_attr(child)
            if attr is not None and attr not in assigned:
                out.setdefault(attr, child.lineno)
            walk(child)

    walk(func)
    return out


def test_helpers_called_from_init_only_touch_attributes_that_exist_yet():
    with open(SOURCE, encoding="utf-8") as handle:
        main_window_class = _class_def(ast.parse(handle.read()), "MainWindow")
    methods = {
        node.name: node
        for node in main_window_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    init = methods["__init__"]
    assigned_at = _assigned_self_attrs(init)

    problems = []
    for node in ast.walk(init):
        if not isinstance(node, ast.Call):
            continue
        called = _self_attr(node.func)
        helper = methods.get(called) if called else None
        if helper is None or called == "__init__":
            continue

        for attr, read_line in _immediate_self_reads(helper).items():
            first_assigned = assigned_at.get(attr)
            if first_assigned is not None and first_assigned > node.lineno:
                problems.append(
                    f"__init__ calls self.{called}() at line {node.lineno}, "
                    f"which reads self.{attr} at line {read_line}, but "
                    f"self.{attr} is not assigned until line {first_assigned}"
                )

    assert not problems, (
        "MainWindow would raise AttributeError before the window appears:\n  "
        + "\n  ".join(problems)
    )


def test_the_checker_actually_catches_the_bug_it_is_for():
    """
    A guard with a bug of its own guards nothing. This is the real
    failure, reduced: a helper reading an attribute assigned later.
    """
    source = '''
class MainWindow:
    def __init__(self):
        self.recorder = object()
        self._connect_page_signals()
        self._wakeword_worker = object()

    def _connect_page_signals(self):
        self.recorder.saved.connect(self._wakeword_worker.on_saved)
'''
    cls = _class_def(ast.parse(source), "MainWindow")
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    assigned_at = _assigned_self_attrs(methods["__init__"])
    reads = _immediate_self_reads(methods["_connect_page_signals"])

    assert "_wakeword_worker" in reads
    assert assigned_at["_wakeword_worker"] > assigned_at["recorder"]
    # and the read happens before the assignment, which is the bug
    call_line = next(
        node.lineno for node in ast.walk(methods["__init__"])
        if isinstance(node, ast.Call) and _self_attr(node.func) == "_connect_page_signals"
    )
    assert assigned_at["_wakeword_worker"] > call_line


def test_the_checker_does_not_flag_a_deferred_read():
    """A lambda body runs when the signal fires, not when it is connected."""
    source = '''
class MainWindow:
    def __init__(self):
        self.recorder = object()
        self._connect_page_signals()
        self._wakeword_worker = object()

    def _connect_page_signals(self):
        self.recorder.saved.connect(lambda: self._wakeword_worker.on_saved())
'''
    cls = _class_def(ast.parse(source), "MainWindow")
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    assert "_wakeword_worker" not in _immediate_self_reads(methods["_connect_page_signals"])
