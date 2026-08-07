"""A small vertical toolbar of region tools, in the spirit of ds9's button bar.

Off by default and toggled from the **Region** menu, because the image is the point of the window
and a permanent bar takes width from it. The choice is remembered in `~/.pyql3/config.json`, so it
is made once rather than every session.

The icons are painted here rather than shipped as files. Four outline shapes and a letter are less
work to draw than to draw, export, bundle and verify — and painting them means they take their
colour from the running palette, so they stay legible in a light or a dark theme, and stay crisp at
any icon size. It also keeps `QuickLook3.spec` and the build's asset checks untouched.
"""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QActionGroup, QFont, QIcon, QPainter, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import QToolBar

#: Icon names that are not region shapes: the two action buttons below the separator.
EXTRA_ICONS = ("list", "clear")

#: The shapes offered, in order, as `(region kind, tooltip)`.
SHAPES = (
    ("circle", "Draw a circle: drag from its centre"),
    ("box", "Draw a box: drag between opposite corners"),
    ("arrow", "Draw an arrow: drag from tail to head"),
    ("text", "Place a label: click where it should go"),
)

#: Icon size in pixels. Small, since this sits alongside the image.
ICON_SIZE = 20

#: Size the icons are painted at before Qt scales them down, for a clean edge on a Retina display.
PAINT_SIZE = 64


def region_icon(kind, colour, size=PAINT_SIZE):
    """An icon for one region shape, painted rather than loaded.

    `colour` should come from the widget's palette so the icon suits the current theme.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(colour, max(2.0, size / 14.0), Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        margin = size * 0.18
        box = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)

        if kind == "circle":
            painter.drawEllipse(box)
        elif kind == "box":
            painter.drawRect(box)
        elif kind == "arrow":
            _draw_arrow(painter, box, colour)
        elif kind == "text":
            _draw_letter(painter, size, colour)
        elif kind == "list":
            _draw_list(painter, box)
        elif kind == "clear":
            _draw_cross(painter, box)
    finally:
        painter.end()

    return QIcon(pixmap)


def _draw_arrow(painter, box, colour):
    """A diagonal shaft with a filled head, pointing up and to the right."""
    tail = QPointF(box.left(), box.bottom())
    tip = QPointF(box.right(), box.top())
    painter.drawLine(tail, tip)

    # A head drawn as a filled triangle, so it reads at 20 pixels.
    head = box.width() * 0.34
    painter.setBrush(colour)
    painter.drawPolygon(QPolygonF([
        tip,
        QPointF(tip.x() - head, tip.y() + head * 0.35),
        QPointF(tip.x() - head * 0.35, tip.y() + head),
    ]))


def _draw_list(painter, box):
    """Three stacked rules, the usual glyph for a list."""
    for fraction in (0.15, 0.5, 0.85):
        y = box.top() + box.height() * fraction
        painter.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))


def _draw_cross(painter, box):
    """A cross, for delete-everything."""
    painter.drawLine(box.topLeft(), box.bottomRight())
    painter.drawLine(box.topRight(), box.bottomLeft())


def _draw_letter(painter, size, colour):
    """A capital A, the usual glyph for a text tool."""
    font = QFont()
    font.setPixelSize(int(size * 0.78))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QPen(colour))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "A")


class RegionToolBar(QToolBar):
    """Shape buttons, the region list and a clear-all, down the side of the window.

    Owns no state: every button calls the window, which is what the Region menu does too, so the
    two cannot drift apart. The shape buttons are exclusive and follow the layer's drawing mode, so
    the pressed button says what a drag will draw — and un-presses itself when a tool dialog takes
    the drag away.
    """

    def __init__(self, window):
        super().__init__("Region Tools", window)
        self.window = window
        self.setObjectName("region_toolbar")
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        # Vertical by intent: along the top it would push the image down instead of narrowing it.
        self.setAllowedAreas(Qt.ToolBarArea.LeftToolBarArea | Qt.ToolBarArea.RightToolBarArea)
        self.setOrientation(Qt.Orientation.Vertical)

        colour = self.palette().windowText().color()

        self.shape_group = QActionGroup(self)
        self.shape_group.setExclusive(True)
        self.shape_actions = {}
        for kind, tooltip in SHAPES:
            action = self.addAction(region_icon(kind, colour), kind.title())
            action.setToolTip(tooltip)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, k=kind: self._start(k))
            self.shape_group.addAction(action)
            self.shape_actions[kind] = action

        self.addSeparator()

        self.list_action = self.addAction(region_icon("list", colour), "List")
        self.list_action.setToolTip("Show the list of regions")
        self.list_action.triggered.connect(lambda checked=False: window.open_region_list())

        self.clear_action = self.addAction(region_icon("clear", colour), "Clear")
        self.clear_action.setToolTip("Delete every region")
        self.clear_action.triggered.connect(lambda checked=False: window.delete_all_regions())

        layer = getattr(window.image_viewer, 'region_layer', None)
        if layer is not None:
            layer.draw_mode_changed.connect(self.follow_draw_mode)

    def _start(self, kind):
        self.window.start_drawing_region(kind)
        # The window may have refused — nothing loaded, or a cancelled label prompt — so the button
        # is set from what actually happened rather than from the click.
        self.follow_draw_mode(None)

    def follow_draw_mode(self, _active=None):
        """Press the button for the shape being drawn, and release them all when drawing stops."""
        layer = getattr(self.window.image_viewer, 'region_layer', None)
        drawing = None if layer is None else layer.draw_kind

        for kind, action in self.shape_actions.items():
            action.setChecked(kind == drawing)
