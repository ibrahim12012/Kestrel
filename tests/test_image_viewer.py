# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_image_viewer.py
============================
app/image_viewer.py opens a screenshot inside the window it was clicked
in, over the area the window offers (MainWindow.overlay_host), instead
of in a window of its own.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QMainWindow, QVBoxLayout, QWidget  # noqa: E402

import app.image_viewer as image_viewer  # noqa: E402


class _Window(QMainWindow):
    """A window that, like MainWindow, offers part of itself for overlays."""

    def __init__(self):
        super().__init__()
        central = QWidget()
        layout = QVBoxLayout(central)
        self.title = QWidget()
        self.title.setFixedHeight(40)
        self.content = QWidget()
        self.thumb = QWidget(self.content)
        layout.addWidget(self.title)
        layout.addWidget(self.content, 1)
        self.setCentralWidget(central)
        self.resize(900, 600)

    def overlay_host(self):
        return self.content


@pytest.fixture
def screenshot(tmp_path, qapp):
    from PyQt6.QtGui import QColor, QPixmap
    pixmap = QPixmap(640, 360)
    pixmap.fill(QColor("#336699"))
    path = tmp_path / "shot.png"
    pixmap.save(str(path))
    return str(path)


def test_it_opens_inside_the_window_over_the_offered_area(qapp, screenshot):
    window = _Window()
    window.show()
    qapp.processEvents()
    viewer = image_viewer.open_images([screenshot], parent=window.thumb)
    try:
        assert viewer.isWindow() is False
        assert viewer.parentWidget() is window.content
        assert viewer.geometry() == window.content.rect()
        assert viewer.isVisible() is True
    finally:
        viewer.close()
        window.close()


def test_it_follows_the_window_when_resized(qapp, screenshot):
    window = _Window()
    window.show()
    viewer = image_viewer.open_images([screenshot], parent=window.thumb)
    try:
        window.resize(1200, 800)
        qapp.processEvents()
        assert viewer.geometry() == window.content.rect()
    finally:
        viewer.close()
        window.close()


def test_opening_another_closes_the_first(qapp, screenshot):
    window = _Window()
    window.show()
    first = image_viewer.open_images([screenshot], parent=window.thumb)
    second = image_viewer.open_images([screenshot], parent=window.thumb)
    try:
        assert first.isVisible() is False
        assert second.isVisible() is True
    finally:
        second.close()
        window.close()


def test_without_a_parent_it_is_a_window_of_its_own(qapp, screenshot):
    viewer = image_viewer.open_images([screenshot])
    try:
        assert viewer.isWindow() is True
    finally:
        viewer.close()


def test_a_window_without_an_offer_gets_its_central_widget_covered(qapp, screenshot):
    window = QMainWindow()
    central = QWidget()
    window.setCentralWidget(central)
    window.show()
    viewer = image_viewer.open_images([screenshot], parent=central)
    try:
        assert viewer.parentWidget() is central
    finally:
        viewer.close()
        window.close()
