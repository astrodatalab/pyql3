"""Per-region properties, in the shape of ds9's dialog: double-click a region and edit it.

Every field the model carries is here, because the alternative is a region you can draw but not
change. The geometry rows are in **orig** coordinates, the same numbers the file stores and the
Region List shows, so what is typed here is what is saved.

Apply writes to the region and asks the layer to rebuild its items; Cancel puts back the values
the dialog opened with. The dialog holds the *region*, never its graphics item, because applying a
style change destroys and rebuilds the item.
"""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from pyql3.core.regions_model import Arrow, Box, Circle, Text

#: Coordinates and sizes are pixel counts; three decimals is well past what anyone can point at.
COORD_RANGE = (-1e6, 1e6)
SIZE_RANGE = (0.01, 1e6)


class RegionPropertiesDialog(QDialog):
    """Edit one region's geometry, style and label."""

    def __init__(self, region, layer, parent=None):
        super().__init__(parent)
        self.region = region
        self.layer = layer
        self._original = _snapshot(region)

        self.setWindowTitle(f"{region.TYPE.title()} Region")
        self.setModal(False)          # as ds9's are: keep working on the image with it open

        outer = QVBoxLayout(self)

        geometry = QGroupBox("Position and size")
        form = QFormLayout(geometry)
        self.spin_x = _coord_spin(region.x)
        self.spin_y = _coord_spin(region.y)
        form.addRow("X (pixels):", self.spin_x)
        form.addRow("Y (pixels):", self.spin_y)

        self.spin_size = self.spin_size2 = self.spin_angle = None
        if isinstance(region, Circle):
            self.spin_size = _size_spin(region.radius)
            form.addRow("Radius:", self.spin_size)
        elif isinstance(region, Box):
            self.spin_size = _size_spin(region.width)
            self.spin_size2 = _size_spin(region.height)
            form.addRow("Width:", self.spin_size)
            form.addRow("Height:", self.spin_size2)
        elif isinstance(region, Arrow):
            self.spin_size = _size_spin(region.length)
            form.addRow("Length:", self.spin_size)

        if hasattr(region, 'angle'):
            self.spin_angle = _angle_spin(region.angle)
            form.addRow("Angle (deg):", self.spin_angle)
        outer.addWidget(geometry)

        appearance = QGroupBox("Appearance")
        style = QFormLayout(appearance)

        self.txt_label = QLineEdit(region.text)
        self.txt_label.setToolTip("Drawn beside the region, and written as ds9's text={...}")
        style.addRow("Text:" if isinstance(region, Text) else "Label:", self.txt_label)

        self.btn_colour = QPushButton()
        self.btn_colour.clicked.connect(self.choose_colour)
        self._colour = region.color
        self._show_colour()
        colour_row = QHBoxLayout()
        colour_row.addWidget(self.btn_colour)
        colour_row.addStretch()
        style.addRow("Colour:", colour_row)
        self.btn_colour.setToolTip("Used for the outline and for the text")

        self.spin_width = QSpinBox()
        self.spin_width.setRange(1, 20)
        self.spin_width.setValue(region.line_width)
        style.addRow("Line width:", self.spin_width)

        self.chk_dash = QCheckBox("Dashed")
        self.chk_dash.setChecked(region.dash)
        style.addRow("", self.chk_dash)

        self.spin_font = QSpinBox()
        self.spin_font.setRange(4, 96)
        self.spin_font.setValue(region.font_size)
        style.addRow("Text size:", self.spin_font)

        self.txt_tag = QLineEdit(region.tag)
        self.txt_tag.setToolTip("Free-form group name, carried through to ds9 as tag={...}")
        style.addRow("Tag:", self.txt_tag)

        self.chk_visible = QCheckBox("Visible")
        self.chk_visible.setChecked(region.visible)
        style.addRow("", self.chk_visible)
        outer.addWidget(appearance)

        channels = QGroupBox("Channel range")
        channel_row = QHBoxLayout(channels)
        self.chk_channels = QCheckBox("Only show for channels")
        self.chk_channels.setChecked(region.z_range is not None)
        self.spin_zmin = QSpinBox()
        self.spin_zmin.setRange(0, 100000)
        self.spin_zmax = QSpinBox()
        self.spin_zmax.setRange(0, 100000)
        if region.z_range is not None:
            self.spin_zmin.setValue(region.z_range[0])
            self.spin_zmax.setValue(region.z_range[1])
        channel_row.addWidget(self.chk_channels)
        channel_row.addWidget(self.spin_zmin)
        channel_row.addWidget(QLabel("to"))
        channel_row.addWidget(self.spin_zmax)
        channel_row.addStretch()
        channels.setToolTip("A region that marks a feature in one part of the cube only. "
                            "Not written to ds9 files, which have nowhere to put it.")
        outer.addWidget(channels)

        self.lbl_sky = QLabel()
        self.lbl_sky.setWordWrap(True)
        outer.addWidget(self.lbl_sky)
        self._show_sky()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Apply
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        outer.addWidget(buttons)

    # ---------------------------------------------------------------- colour

    def choose_colour(self):
        chosen = QColorDialog.getColor(QColor(self._colour), self, "Region Colour")
        if chosen.isValid():
            self._colour = chosen.name()
            self._show_colour()

    def _show_colour(self):
        colour = QColor(self._colour)
        self.btn_colour.setText(self._colour)
        if colour.isValid():
            text = "black" if colour.lightness() > 128 else "white"
            self.btn_colour.setStyleSheet(
                f"background-color: {colour.name()}; color: {text}; padding: 4px 12px;")

    def _show_sky(self):
        """Show where this region is on the sky, when the frame has a WCS to say."""
        viewer = getattr(self.layer, 'viewer', None)
        if viewer is None or getattr(viewer, 'wcs', None) is None:
            self.lbl_sky.setVisible(False)
            return
        from pyql3.core.sky import CelestialMap

        mapping = CelestialMap(viewer.wcs, *viewer.display_axis_indices())
        sky = mapping.to_sky(self.spin_x.value(), self.spin_y.value())
        if sky is None:
            self.lbl_sky.setVisible(False)
            return
        self.lbl_sky.setText(f"Sky position: RA {sky[0]:.6f}°, Dec {sky[1]:+.6f}°")
        self.lbl_sky.setVisible(True)

    # ----------------------------------------------------------------- applying

    def apply(self):
        """Write the fields onto the region and redraw it."""
        region = self.region

        region.x = self.spin_x.value()
        region.y = self.spin_y.value()
        if self.spin_size is not None:
            _apply_size(region, self.spin_size.value())
        if self.spin_size2 is not None and isinstance(region, Box):
            region.height = self.spin_size2.value()
        if self.spin_angle is not None:
            region.angle = self.spin_angle.value() % 360.0

        text = self.txt_label.text().strip()
        if isinstance(region, Text) and not text:
            # A text region with no text draws nothing at all, so there would be no way to find
            # it again on the image.
            self.txt_label.setText(region.text)
        else:
            region.text = text

        region.color = self._colour
        region.line_width = self.spin_width.value()
        region.dash = self.chk_dash.isChecked()
        region.font_size = self.spin_font.value()
        region.tag = self.txt_tag.text().strip()
        region.visible = self.chk_visible.isChecked()

        if self.chk_channels.isChecked():
            low, high = self.spin_zmin.value(), self.spin_zmax.value()
            region.z_range = (min(low, high), max(low, high))
        else:
            region.z_range = None

        if self.layer is not None:
            # restyle rebuilds the items, which is what a colour or width change needs; it also
            # re-places them, so the geometry above lands too.
            self.layer.restyle(region)
        self._show_sky()

    def accept(self):
        self.apply()
        super().accept()

    def reject(self):
        """Put back what the dialog opened with, including anything a previous Apply wrote."""
        _restore(self.region, self._original)
        if self.layer is not None:
            self.layer.restyle(self.region)
        super().reject()


# ------------------------------------------------------------------ shared helpers

def region_coordinate_text(region, viewer=None):
    """One line describing where a region is, for the clipboard.

    Shared by the Region List and the viewer's own context menu so the two cannot disagree.
    """
    parts = [f"{region.TYPE} x={region.x:.3f} y={region.y:.3f}"]

    if viewer is not None and getattr(viewer, 'wcs', None) is not None:
        from pyql3.core.sky import CelestialMap

        mapping = CelestialMap(viewer.wcs, *viewer.display_axis_indices())
        sky = mapping.to_sky(region.x, region.y)
        if sky is not None:
            parts.append(f"RA={sky[0]:.6f} Dec={sky[1]:.6f}")

    if region.text:
        parts.append(f"({region.text})")
    return "  ".join(parts)


def copy_region_coordinates(region, viewer=None):
    """Put `region`'s position on the clipboard and return what was copied."""
    text = region_coordinate_text(region, viewer)
    QApplication.clipboard().setText(text)
    return text


# ------------------------------------------------------------------ private helpers

def _coord_spin(value):
    spin = QDoubleSpinBox()
    spin.setRange(*COORD_RANGE)
    spin.setDecimals(3)
    spin.setSingleStep(0.5)
    spin.setValue(value)
    return spin


def _size_spin(value):
    spin = QDoubleSpinBox()
    spin.setRange(*SIZE_RANGE)
    spin.setDecimals(3)
    spin.setSingleStep(0.5)
    spin.setValue(value)
    return spin


def _angle_spin(value):
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 360.0)
    spin.setDecimals(1)
    spin.setWrapping(True)
    spin.setValue(value % 360.0)
    return spin


def _apply_size(region, value):
    if isinstance(region, Circle):
        region.radius = value
    elif isinstance(region, Box):
        region.width = value
    elif isinstance(region, Arrow):
        region.length = value


#: Fields the dialog can change, and therefore has to be able to put back.
_EDITABLE = ("x", "y", "text", "color", "line_width", "dash", "font_size", "tag", "visible",
             "z_range", "radius", "width", "height", "length", "angle")


def _snapshot(region):
    return {name: getattr(region, name) for name in _EDITABLE if hasattr(region, name)}


def _restore(region, snapshot):
    for name, value in snapshot.items():
        setattr(region, name, value)
