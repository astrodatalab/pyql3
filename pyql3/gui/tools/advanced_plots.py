import numpy as np
from PySide6.QtWidgets import QVBoxLayout, QGridLayout, QLabel, QSpinBox, QCheckBox
import pyqtgraph as pg
from pyql3.gui.tools.base_tool import BaseToolDialog

class ContourDialog(BaseToolDialog):
    def __init__(self, parent=None, image_viewer=None):
        super().__init__(parent, image_viewer, "Contour Overlay")
        self.resize(300, 150)
        
        main_layout = QVBoxLayout()
        self.layout.addLayout(main_layout)
        
        grid = QGridLayout()
        main_layout.addLayout(grid)
        
        self.spin_levels = QSpinBox()
        self.spin_levels.setRange(2, 50)
        self.spin_levels.setValue(10)
        self.spin_levels.valueChanged.connect(self.update_plot)
        
        grid.addWidget(QLabel("Number of Levels:"), 0, 0)
        grid.addWidget(self.spin_levels, 0, 1)
        
        self.chk_show = QCheckBox("Show Contours")
        self.chk_show.setChecked(True)
        self.chk_show.toggled.connect(self.toggle_contours)
        grid.addWidget(self.chk_show, 1, 0, 1, 2)
        
        self.iso_items = []
        self.update_plot()

    def on_plane_changed(self):
        self.update_plot()
        
    def clear_contours(self):
        """Take the contours off the image and retire them.

        Retired, not dropped: `update_plot` builds the new contours straight after this, and
        dropping the last reference to the old ones let the collector free one inside the
        construction of its replacement (`BUGS.md` M18, R87). `retire_roi` takes any graphics
        item, and `tear_down` releases what it holds.
        """
        for item in self.iso_items:
            self.retire_roi(item)
        self.iso_items = []
        
    def toggle_contours(self, checked):
        if checked:
            self.update_plot()
        else:
            self.clear_contours()
            
    def update_plot(self):
        """Draw the contours of the plane on screen.

        While the number of levels is unchanged the existing `IsocurveItem`s are given the new
        plane and levels in place. Replacing them retires the old ones, and releasing retired
        items runs a `gc.collect()`, which cost 35-38 ms per slider step in a session holding a
        large catalog. Only a change in the number of levels builds new items.
        """
        if not self.chk_show.isChecked():
            return

        if self.image_viewer is None or self.image_viewer.display_data is None:
            self.clear_contours()
            return
            
        img_data = self.image_viewer.current_plane()
        if img_data is None:
            self.clear_contours()
            return
        img_data = img_data * self.image_viewer.data_multiplier
            
        # Calculate levels
        min_val = np.nanmin(img_data)
        max_val = np.nanmax(img_data)
        
        if np.isnan(min_val) or np.isnan(max_val) or min_val == max_val:
            self.clear_contours()
            return
            
        num_levels = self.spin_levels.value()
        # Create linearly spaced levels
        levels = np.linspace(min_val, max_val, num_levels + 2)[1:-1]

        if len(self.iso_items) == len(levels):
            for iso, level in zip(self.iso_items, levels, strict=True):
                iso.setData(img_data, level=level)
            return

        self.clear_contours()
        for level in levels:
            iso = pg.IsocurveItem(data=img_data, level=level, pen='r')
            # The IsocurveItem must be linked to the image item to overlay correctly
            iso.setParentItem(self.image_viewer.imv.getImageItem())
            self.iso_items.append(iso)
            
    def tear_down(self):
        if self._torn_down:
            return
        self.clear_contours()
        super().tear_down()
