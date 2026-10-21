import numpy as np
from PySide6.QtWidgets import QGridLayout, QLabel
import pyqtgraph as pg
from pyql3.gui.tools.base_tool import BaseToolDialog

class StatisticsDialog(BaseToolDialog):
    def __init__(self, parent=None, image_viewer=None):
        super().__init__(parent, image_viewer, "Region Statistics")
        self.resize(300, 200)
        
        grid = QGridLayout()
        self.setup_draw_button(self.layout)
        self.layout.addLayout(grid)
        
        labels = ["Min", "Max", "Mean", "Median", "StdDev", "Variance", "Total Pixels"]
        self.value_labels = {}
        for i, text in enumerate(labels):
            grid.addWidget(QLabel(f"{text}:"), i, 0)
            val = QLabel("N/A")
            grid.addWidget(val, i, 1)
            self.value_labels[text] = val

        # A box that misses the image has nothing to report, and says so here.
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #b00;")
        grid.addWidget(self.lbl_status, len(labels), 0, 1, 2)

        if self.image_viewer and self.image_viewer.display_data is not None:
            shape = self.image_viewer.display_data.shape
            if len(shape) == 3:
                center_x, center_y = shape[1]//2, shape[2]//2
            else:
                center_x, center_y = shape[0]//2, shape[1]//2
        else:
            center_x, center_y = 0, 0
            
        roi = pg.RectROI([center_x, center_y], [10, 10], pen=pg.mkPen((0, 255, 255), width=3), hoverPen=pg.mkPen((0, 255, 255), width=5))
        roi.addScaleHandle([1, 1], [0, 0])
        roi.addScaleHandle([0, 0], [1, 1])
        self.add_roi_to_viewer(roi)
        
        self.update_stats()
        
    def on_roi_changed(self):
        self.update_stats()
        
    def update_stats(self):
        if self.image_viewer is None or self.image_viewer.display_data is None:
            return

        measured = self.roi_plane_pixels()
        if measured is None:
            for val in self.value_labels.values():
                val.setText("N/A")
            self.lbl_status.setText("The box does not overlap the image.")
            return
        self.lbl_status.setText("")
        data, _ = measured

        valid_data = data[~np.isnan(data)]
        if valid_data.size == 0:
            for val in self.value_labels.values():
                val.setText("NaN")
            return
            
        self.value_labels["Min"].setText(f"{np.min(valid_data):.4f}")
        self.value_labels["Max"].setText(f"{np.max(valid_data):.4f}")
        self.value_labels["Mean"].setText(f"{np.mean(valid_data):.4f}")
        self.value_labels["Median"].setText(f"{np.median(valid_data):.4f}")
        self.value_labels["StdDev"].setText(f"{np.std(valid_data):.4f}")
        self.value_labels["Variance"].setText(f"{np.var(valid_data):.4f}")
        self.value_labels["Total Pixels"].setText(str(valid_data.size))
