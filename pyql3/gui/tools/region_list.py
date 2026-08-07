"""The Region List: a table of every region drawn on the viewer, editable in place.

The table is a view of `ImageViewer.region_layer`, not a second copy of the data. Editing a cell
writes straight to the model region and asks the layer to re-place it; dragging a region on the
image writes back the other way and the table refreshes. Both directions go through
`regions_changed`, so the two can never drift apart.

Columns are deliberately dense, as the `ql2` reference interface was: type, position, size, angle,
label, colour and visibility all on one row, so a night's worth of regions can be scanned without
opening anything.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from pyql3.core.regions_model import Arrow, Box, Circle, Text, resolve_color, sizes_of
from pyql3.gui.tools.base_tool import BaseToolDialog

#: Column order. "Size" is a radius for a circle, a width for a box, a length for an arrow; only
#: a box uses "Size 2".
COLUMNS = ("Type", "X", "Y", "Size", "Size 2", "Angle", "Label", "Colour", "Show")
COL_TYPE, COL_X, COL_Y, COL_SIZE, COL_SIZE2, COL_ANGLE, COL_LABEL, COL_COLOUR, COL_SHOW = range(9)

#: How much of the view a "zoom to region" fills, as a multiple of the region's size.
ZOOM_MARGIN = 6.0

#: Rows above which the table is not filled in.
#:
#: A `QTableWidget` builds a widget-backed item per cell, which is nine per region: measured at
#: 0.1 s for 1,000 regions, 2.2 s for 5,000 and 16 s for 10,000 (`TODO_regions.md`, Phase 5). Since
#: `regions_changed` fires on every drag frame, that cost lands on every mouse move. Above this the
#: dialog says how many there are and offers to list none of them, rather than freezing.
LIST_LIMIT = 2000


class RegionListDialog(BaseToolDialog):
    """Lists and edits the viewer's regions."""

    def __init__(self, parent=None, image_viewer=None):
        super().__init__(parent, image_viewer, "Regions")
        self.resize(760, 320)
        self._updating = False

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_LABEL, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.on_item_changed)
        self.table.cellDoubleClicked.connect(self.on_cell_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.layout.addWidget(self.table)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.layout.addWidget(self.lbl_status)

        buttons = QHBoxLayout()
        for text, slot in (("Zoom To", self.zoom_to_selected),
                           ("Delete", self.delete_selected),
                           ("Delete All", self.delete_all)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        self.layout.addLayout(buttons)

        if self.layer is not None:
            self.layer.regions_changed.connect(self.update_plot)
        self.update_plot()

    # ------------------------------------------------------------------ plumbing

    @property
    def layer(self):
        return getattr(self.image_viewer, 'region_layer', None)

    def update_plot(self):
        """Rebuild the table from the layer. Named for `update_tools_for_unit`'s benefit."""
        if self.layer is None:
            return

        count = len(self.layer)
        listed = count <= LIST_LIMIT

        self._updating = True
        try:
            regions = self.layer.regions if listed else []
            self.table.setRowCount(len(regions))
            for row, region in enumerate(regions):
                self._fill_row(row, region)
        finally:
            self._updating = False

        if not count:
            self.lbl_status.setText("No regions. Draw one from the Region menu.")
        elif not listed:
            # Said out loud rather than quietly showing the first 2,000, which would look like
            # the rest had been lost.
            self.lbl_status.setText(
                f"{count:,} regions — too many to list; the table holds up to {LIST_LIMIT:,}. "
                "They are all still drawn, saved and exported. Delete some to edit them here.")
        else:
            self.lbl_status.setText(
                f"{count} region{'s' if count != 1 else ''}. "
                "Double-click the Type cell for properties; edit any other cell in place.")

    def _fill_row(self, row, region):
        size, size2 = sizes_of(region)
        angle = getattr(region, 'angle', None)

        self._set_cell(row, COL_TYPE, region.TYPE, editable=False)
        self._set_cell(row, COL_X, f"{region.x:.2f}")
        self._set_cell(row, COL_Y, f"{region.y:.2f}")
        self._set_cell(row, COL_SIZE, "" if size is None else f"{size:.2f}",
                       editable=size is not None)
        self._set_cell(row, COL_SIZE2, "" if size2 is None else f"{size2:.2f}",
                       editable=size2 is not None)
        self._set_cell(row, COL_ANGLE, "" if angle is None else f"{angle:.1f}",
                       editable=angle is not None)
        self._set_cell(row, COL_LABEL, region.text)

        colour = QTableWidgetItem(region.color)
        colour.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        swatch = QColor(resolve_color(region.color))
        if swatch.isValid():
            colour.setBackground(swatch)
            # Keep the name readable against its own colour.
            colour.setForeground(QColor("black" if swatch.lightness() > 128 else "white"))
        self.table.setItem(row, COL_COLOUR, colour)

        self.table.setCellWidget(row, COL_SHOW, self._visibility_box(region))

    def _set_cell(self, row, column, text, editable=True):
        item = QTableWidgetItem(str(text))
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if editable:
            flags |= Qt.ItemFlag.ItemIsEditable
        item.setFlags(flags)
        self.table.setItem(row, column, item)

    def _visibility_box(self, region):
        box = QCheckBox()
        box.setChecked(region.visible)
        box.toggled.connect(lambda checked, r=region: self._set_visible(r, checked))
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return holder

    def _region_at(self, row):
        regions = self.layer.regions if self.layer is not None else []
        return regions[row] if 0 <= row < len(regions) else None

    def selected_regions(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [region for region in (self._region_at(row) for row in rows) if region is not None]

    # -------------------------------------------------------------------- editing

    def on_item_changed(self, item):
        """Write one edited cell back to its region."""
        if self._updating or self.layer is None:
            return
        region = self._region_at(item.row())
        if region is None:
            return

        column, text = item.column(), item.text().strip()

        if column == COL_LABEL:
            if isinstance(region, Text) and not text:
                self._reject("A text region needs a label; the change was not applied.")
                return
            region.text = text
            self.layer.restyle(region)
            return

        value = _as_number(text)
        if value is None:
            self._reject(f"{text!r} is not a number; the change was not applied.")
            return

        if column == COL_X:
            region.x = value
        elif column == COL_Y:
            region.y = value
        elif column == COL_SIZE:
            if not _set_size(region, value):
                self._reject(f"{text!r} is not a usable size for a {region.TYPE}.")
                return
        elif column == COL_SIZE2:
            if isinstance(region, Box) and value > 0:
                region.height = value
            else:
                self._reject(f"{text!r} is not a usable height for a {region.TYPE}.")
                return
        elif column == COL_ANGLE:
            if hasattr(region, 'angle'):
                region.angle = value % 360.0

        self.layer.refresh()
        self.update_plot()

    def _reject(self, message):
        """Put the table back as it was and say why.

        The order matters: `update_plot` rewrites the status label with the region count, so
        explaining first and reverting second loses the explanation.
        """
        self.update_plot()
        self.lbl_status.setText(message)

    def _set_visible(self, region, visible):
        if self._updating or self.layer is None:
            return
        region.visible = bool(visible)
        self.layer.update_channel_visibility()

    def choose_colour(self, region):
        chosen = QColorDialog.getColor(QColor(resolve_color(region.color)), self,
                                       "Region Colour")
        if chosen.isValid():
            region.color = chosen.name()
            self.layer.restyle(region)

    # -------------------------------------------------------------------- actions

    def delete_selected(self):
        """Delete the selected rows in one redraw rather than one per region."""
        if self.layer is None:
            return
        self.layer.remove_many(self.selected_regions())

    def delete_all(self):
        if self.layer is not None:
            self.layer.clear()

    def zoom_to_selected(self):
        regions = self.selected_regions()
        if regions:
            self.zoom_to(regions[0])

    def zoom_to(self, region):
        """Centre the view on `region`, with room around it.

        Goes through the layer's own placement so the view lands where the region is *drawn*,
        whatever flip or rotation is in force.
        """
        if self.layer is None or self.image_viewer is None:
            return
        placed = self.layer._to_item(region.x, region.y)
        if placed is None:
            return

        size, size2 = sizes_of(region)
        extent = max(size or 0.0, size2 or 0.0, 3.0) * ZOOM_MARGIN
        view = self.image_viewer.imv.getView()
        view.setRange(xRange=(placed[0] - extent, placed[0] + extent),
                      yRange=(placed[1] - extent, placed[1] + extent), padding=0)

    def copy_coordinates(self, region):
        """Put a region's position on the clipboard, with its sky position when there is one."""
        from pyql3.gui.dialogs.region_properties import copy_region_coordinates

        # Shared with the viewer's own context menu, so the two cannot describe a region
        # differently.
        self.lbl_status.setText(f"Copied: {copy_region_coordinates(region, self.image_viewer)}")

    def open_properties(self, region):
        """Open the region's property dialog, through the window so it is not opened twice."""
        window = self.parent()
        if window is not None and hasattr(window, 'open_region_properties'):
            window.open_region_properties(region)
            return
        from pyql3.gui.dialogs.region_properties import RegionPropertiesDialog

        dialog = RegionPropertiesDialog(region, self.layer, self)
        dialog.show()

    # ------------------------------------------------------------------- context

    def on_cell_double_clicked(self, row, column):
        region = self._region_at(row)
        if region is None:
            return
        if column == COL_COLOUR:
            self.choose_colour(region)
        elif column == COL_TYPE:
            # The one non-editable column, so a double-click there is free for the editor.
            self.open_properties(region)

    def show_context_menu(self, position):
        row = self.table.rowAt(position.y())
        region = self._region_at(row)
        if region is None:
            return

        menu = QMenu(self)
        menu.addAction("Properties...").triggered.connect(lambda: self.open_properties(region))
        menu.addAction("Zoom To").triggered.connect(lambda: self.zoom_to(region))
        menu.addAction("Colour...").triggered.connect(lambda: self.choose_colour(region))
        menu.addAction("Copy Coordinates").triggered.connect(
            lambda: self.copy_coordinates(region))
        menu.addSeparator()
        menu.addAction("Delete").triggered.connect(lambda: self.layer.remove(region))
        menu.exec(self.table.viewport().mapToGlobal(position))


def _set_size(region, value):
    """Apply an edited size to whichever field the shape keeps it in. False if it cannot."""
    if value <= 0:
        return False
    if isinstance(region, Circle):
        region.radius = value
    elif isinstance(region, Box):
        region.width = value
    elif isinstance(region, Arrow):
        region.length = value
    else:
        return False
    return True


def _as_number(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float('inf') else None
