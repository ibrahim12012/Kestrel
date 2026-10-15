# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/image_viewer.py
=====================
Seeing a screenshot properly.

Screenshots used to appear as a 320px thumbnail in the chat, or a 64px
tile on a message about to be sent, and that was all: nothing opened
them, so a 1920px screen shrunk to a sixth of its width was the only
view there was, and what the assistant had actually looked at could not
be read.

Everything that shows a screenshot now opens it here: the chat's
screenshot bubbles, the images on a sent message, the tiles on a
message being written, and the screens an autonomous run looked at
(several at once, with previous/next).

It opens inside the app's own window, over everything below the title
bar, rather than as a window of its own: the screenshot is part of the
conversation, and a second window to find, move and close was one
thing too many. The window a host provides (MainWindow.overlay_host) is
what it covers; without one it falls back to a window of its own.

  - shows the whole screenshot when it opens; scroll or +/- to zoom,
    drag to pan, double-click to switch between fit and actual size
  - Left/Right (or the arrows) move between the images of a set
  - copy it, open it in the default image app, or show it in its folder
  - Esc or the X closes it

Also home to ClickableThumbnail and rounded_thumbnail(), the small
pieces the chat uses to draw a thumbnail that opens this.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPainterPath, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout,
    QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

import app.icons as icons
import app.theme as theme
from app.i18n import tr


@dataclass
class ViewerImage:
    path: str
    caption: str = ""


def rounded_thumbnail(source: "QPixmap | str", max_width: int, max_height: int, radius: float = 10.0) -> "QPixmap | None":
    """
    `source` scaled to fit max_width x max_height, aspect kept, with the
    corners rounded into the pixmap itself (a stylesheet border-radius
    on the QLabel would round the label, not the picture). Sharp on a
    scaled screen: rendered at the screen's pixel ratio. None if the
    image can't be read.
    """
    pixmap = QPixmap(source) if isinstance(source, str) else source
    if pixmap is None or pixmap.isNull():
        return None
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    scaled = pixmap.scaled(
        int(max_width * ratio), int(max_height * ratio),
        Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
    )
    rounded = QPixmap(scaled.size())
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, scaled.width(), scaled.height()), radius * ratio, radius * ratio)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    rounded.setDevicePixelRatio(ratio)
    return rounded


class ClickableThumbnail(QLabel):
    """A thumbnail that opens the viewer: pointing-hand cursor, a faint
    accent outline on hover, and a tooltip saying what a click does."""

    clicked = pyqtSignal()

    def __init__(self, pixmap: QPixmap, theme_name: str = "dark", tooltip: str = "", parent=None):
        super().__init__(parent)
        c = theme.colors(theme_name)
        self.setPixmap(pixmap)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip or tr("Open"))
        self.setObjectName("clickableThumb")
        size = pixmap.deviceIndependentSize().toSize()
        self.setFixedSize(size + QSize(4, 4))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "QLabel#clickableThumb { background: transparent; border: 2px solid transparent; border-radius: 12px; }"
            f"QLabel#clickableThumb:hover {{ border-color: rgba({c['accent_rgb']},0.55); }}"
        )

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _ImageView(QGraphicsView):
    """The picture itself: zooms around the pointer, pans by dragging."""

    zoomChanged = pyqtSignal(float)
    doubleClicked = pyqtSignal()

    MIN_ZOOM = 0.05
    MAX_ZOOM = 8.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._item)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QColor("#0e0e11"))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._fit = True

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.fit()

    def zoom(self) -> float:
        return self.transform().m11()

    def fit(self) -> None:
        self._fit = True
        if self._item.pixmap().isNull():
            return
        self.resetTransform()
        self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)
        # Never blow a small image up past its real size just to fill space.
        if self.zoom() > 1.0:
            self.resetTransform()
        self.zoomChanged.emit(self.zoom())

    def actual_size(self) -> None:
        self._fit = False
        self.resetTransform()
        self.zoomChanged.emit(self.zoom())

    def zoom_by(self, factor: float) -> None:
        target = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self.zoom() * factor))
        if target == self.zoom():
            return
        self._fit = False
        self.scale(target / self.zoom(), target / self.zoom())
        self.zoomChanged.emit(self.zoom())

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.zoom_by(1.2 ** steps)
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        self.doubleClicked.emit()
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit:
            self.fit()


def _toolbar_style(c: dict) -> str:
    return """
QWidget#viewerBar {
    background-color: #16161a;
    border-bottom: 1px solid #26262d;
}
QLabel#viewerTitle {
    color: #f2f2f4;
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 600;
    background: transparent;
}
QLabel#viewerMeta {
    color: #9a9aa3;
    font-family: "Segoe UI";
    font-size: 11.5px;
    background: transparent;
}
QPushButton#viewerBtn {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    min-width: 30px; max-width: 30px;
    min-height: 30px; max-height: 30px;
    padding: 0px;
}
QPushButton#viewerBtn:hover {
    background-color: #24242a;
    border-color: #34343c;
}
QPushButton#viewerBtn:disabled {
    background-color: transparent;
    border-color: transparent;
}
QLabel#viewerZoom {
    color: #c9c9cf;
    font-family: "Segoe UI";
    font-size: 11.5px;
    background: transparent;
    min-width: 44px;
}
QLabel#viewerMissing {
    color: #9a9aa3;
    font-family: "Segoe UI";
    font-size: 13px;
    background: #0e0e11;
}
"""


class ImageViewer(QWidget):
    """
    One image or a set of them, over the app's content (`host`), or in a
    window of its own when there is no host. Dark whatever the app theme
    is, the way image viewers are: a screenshot should be judged against
    neutral dark grey, not against the chat's colours.
    """

    def __init__(self, images: "list[ViewerImage]", index: int = 0, theme_name: str = "dark",
                 host: "QWidget | None" = None):
        super().__init__(host)
        self._host = host
        self._images = [img for img in images if img.path] or images
        self._index = max(0, min(index, len(self._images) - 1))
        self._c = theme.colors(theme_name)
        self._pixmap = QPixmap()

        self.setObjectName("imageViewer")
        # A plain QWidget paints no stylesheet background without this.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(_toolbar_style(self._c) + "QWidget#imageViewer { background-color: #0e0e11; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget()
        bar.setObjectName("viewerBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar_row = QHBoxLayout(bar)
        bar_row.setContentsMargins(14, 8, 10, 8)
        bar_row.setSpacing(4)

        self._btn_prev = self._button("chevron-left", tr("Previous (Left arrow)"), self.show_previous)
        self._btn_next = self._button("chevron-right", tr("Next (Right arrow)"), self.show_next)
        bar_row.addWidget(self._btn_prev)
        bar_row.addWidget(self._btn_next)

        titles = QVBoxLayout()
        titles.setContentsMargins(8, 0, 8, 0)
        titles.setSpacing(0)
        self._title = QLabel()
        self._title.setObjectName("viewerTitle")
        self._meta = QLabel()
        self._meta.setObjectName("viewerMeta")
        titles.addWidget(self._title)
        titles.addWidget(self._meta)
        bar_row.addLayout(titles, 1)

        self._zoom_label = QLabel()
        self._zoom_label.setObjectName("viewerZoom")
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar_row.addWidget(self._zoom_label)
        bar_row.addWidget(self._button("zoom-out", tr("Zoom out (-)"), lambda: self._view.zoom_by(1 / 1.25)))
        bar_row.addWidget(self._button("zoom-in", tr("Zoom in (+)"), lambda: self._view.zoom_by(1.25)))
        bar_row.addWidget(self._button("fit", tr("Fit to window (0)"), self._toggle_fit))
        bar_row.addSpacing(8)
        bar_row.addWidget(self._button("copy", tr("Copy image (Ctrl+C)"), self.copy_image))
        bar_row.addWidget(self._button("external", tr("Open in your image viewer"), self.open_externally))
        bar_row.addWidget(self._button("folder-open", tr("Show in folder"), self.show_in_folder))
        bar_row.addSpacing(8)
        bar_row.addWidget(self._button("close", tr("Close (Esc)"), self.close))
        root.addWidget(bar)

        self._view = _ImageView()
        self._view.zoomChanged.connect(self._on_zoom_changed)
        self._view.doubleClicked.connect(self._toggle_fit)
        self._missing = QLabel(tr("This image is no longer available. It may have been moved or deleted."))
        self._missing.setObjectName("viewerMissing")
        self._missing.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._missing.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._view, 1)
        root.addWidget(self._missing, 1)

        for keys, handler in (
            (QKeySequence("Left"), self.show_previous),
            (QKeySequence("Right"), self.show_next),
            (QKeySequence("+"), lambda: self._view.zoom_by(1.25)),
            (QKeySequence("="), lambda: self._view.zoom_by(1.25)),
            (QKeySequence("-"), lambda: self._view.zoom_by(1 / 1.25)),
            (QKeySequence("0"), self._view.fit),
            (QKeySequence(QKeySequence.StandardKey.Copy), self.copy_image),
            (QKeySequence("Esc"), self.close),
        ):
            shortcut = QShortcut(keys, self)
            shortcut.activated.connect(handler)

        if host is not None:
            # Covers the host, and follows it when the window is resized.
            self.setGeometry(host.rect())
            host.installEventFilter(self)
        else:
            self.setWindowTitle(tr("Screenshot"))
            self._size_to_screen()
        self._show_current()

    # ── Building blocks ─────────────────────────────────────────────

    def _button(self, icon_name: str, tooltip: str, handler) -> QPushButton:
        button = QPushButton()
        button.setObjectName("viewerBtn")
        button.setIcon(icons.icon(icon_name, {"fg": "#e6e6ea"}, "fg"))
        button.setIconSize(QSize(16, 16))
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda _=False: handler())
        return button

    def eventFilter(self, watched, event) -> bool:
        if watched is self._host and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._host.rect())
        return False

    def _size_to_screen(self) -> None:
        """Only for the fallback, a window of its own."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1100, 720)
            return
        area = screen.availableGeometry()
        width, height = int(area.width() * 0.8), int(area.height() * 0.84)
        self.resize(width, height)
        self.move(area.center() - QPoint(width // 2, height // 2))

    # ── Navigation ─────────────────────────────────────────────────

    def _show_current(self) -> None:
        image = self._images[self._index]
        self._pixmap = QPixmap(image.path) if image.path and os.path.isfile(image.path) else QPixmap()
        available = not self._pixmap.isNull()
        self._view.setVisible(available)
        self._missing.setVisible(not available)
        if available:
            self._view.set_pixmap(self._pixmap)

        name = os.path.basename(image.path) if image.path else ""
        self._title.setText(image.caption or name or tr("Screenshot"))
        details = []
        if len(self._images) > 1:
            details.append(tr("{n} of {total}", n=self._index + 1, total=len(self._images)))
        if available:
            details.append(f"{self._pixmap.width()} × {self._pixmap.height()}")
        if image.caption and name:
            details.append(name)
        self._meta.setText("  ·  ".join(details))

        many = len(self._images) > 1
        self._btn_prev.setVisible(many)
        self._btn_next.setVisible(many)
        self._btn_prev.setEnabled(self._index > 0)
        self._btn_next.setEnabled(self._index < len(self._images) - 1)
        if self._host is None:
            self.setWindowTitle(image.caption or name or tr("Screenshot"))

    def show_previous(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._show_current()

    def show_next(self) -> None:
        if self._index < len(self._images) - 1:
            self._index += 1
            self._show_current()

    # ── Actions ──────────────────────────────────────────────────

    def _toggle_fit(self) -> None:
        if self._view._fit:
            self._view.actual_size()
        else:
            self._view.fit()

    def _on_zoom_changed(self, zoom: float) -> None:
        self._zoom_label.setText(f"{round(zoom * 100)}%")

    def current_path(self) -> str:
        return self._images[self._index].path

    def copy_image(self) -> None:
        if not self._pixmap.isNull():
            QGuiApplication.clipboard().setPixmap(self._pixmap)

    def open_externally(self) -> None:
        path = self.current_path()
        if path and os.path.isfile(path):
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def show_in_folder(self) -> None:
        path = self.current_path()
        if not path:
            return
        try:
            from app.file_reveal import reveal_in_explorer
            if reveal_in_explorer(path):
                return
        except Exception:
            pass
        if os.path.isdir(os.path.dirname(path)):
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def focusNextPrevChild(self, next: bool) -> bool:
        # Over the app, Tab would move the focus to the page underneath,
        # out of sight: typing would then go into the chat's message box,
        # and Enter would send it. There is nothing to tab to in here.
        if self._host is not None:
            return True
        return super().focusNextPrevChild(next)

    def closeEvent(self, event) -> None:
        if self._host is not None:
            self._host.removeEventFilter(self)
        super().closeEvent(event)


_open_viewers: list = []


def _host_for(parent) -> "QWidget | None":
    """What the viewer covers: the area the window offers for it
    (overlay_host(), below MainWindow's title bar), else its central
    widget, else the whole window. None without a parent."""
    if parent is None:
        return None
    window = parent.window()
    offer = getattr(window, "overlay_host", None)
    if callable(offer):
        try:
            host = offer()
        except Exception:
            host = None
        if host is not None:
            return host
    central = window.centralWidget() if hasattr(window, "centralWidget") else None
    return central or window


def close_viewers_over(host) -> None:
    """Close the viewer open over `host`, if there is one. MainWindow does
    this before it changes the page under it, which would otherwise cover
    the viewer and leave it taking the keys out of sight."""
    for open_viewer in list(_open_viewers):
        try:
            if open_viewer.parentWidget() is host:
                open_viewer.close()
        except RuntimeError:
            pass        # already deleted, its destroyed() not seen yet


def open_images(images: "list[ViewerImage] | list[str]", index: int = 0, theme_name: str = "dark", parent=None) -> ImageViewer:
    """Open the viewer on `images` (paths or ViewerImage) at `index`, over
    the window `parent` is in. One at a time: opening another closes the
    one already open there."""
    items = [img if isinstance(img, ViewerImage) else ViewerImage(path=str(img)) for img in images]
    host = _host_for(parent)
    if host is not None:
        close_viewers_over(host)
    viewer = ImageViewer(items, index=index, theme_name=theme_name, host=host)
    # Held until it closes: without a host it is a window nobody else
    # references, garbage-collected the moment this function returns.
    _open_viewers.append(viewer)
    viewer.destroyed.connect(lambda *_: _open_viewers.remove(viewer) if viewer in _open_viewers else None)
    viewer.show()
    viewer.raise_()
    if host is None:
        viewer.activateWindow()
    viewer.setFocus(Qt.FocusReason.OtherFocusReason)
    return viewer
