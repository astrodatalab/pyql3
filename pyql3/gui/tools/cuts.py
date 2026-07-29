import math
import numpy as np
from PySide6.QtWidgets import QVBoxLayout, QGridLayout, QLabel, QCheckBox, QSpinBox, QComboBox
import pyqtgraph as pg
from pyql3.gui.tools.base_tool import BaseToolDialog

class CutPlotDialog(BaseToolDialog):
    def __init__(self, cut_type, parent=None, image_viewer=None):
        title = f"{cut_type.capitalize()} Cut"
        super().__init__(parent, image_viewer, title)
        self.cut_type = cut_type
        self.resize(500, 500)
        
        main_layout = QVBoxLayout()
        self.layout.addLayout(main_layout)
        
        # Controls Layout
        controls_layout = QGridLayout()
        
        self.combo_calc = QComboBox()
        self.combo_calc.addItems(["Average", "Median", "Total"])
        self.combo_calc.currentIndexChanged.connect(self.update_plot)
        
        self.chk_log_x = QCheckBox("Log X")
        self.chk_log_x.stateChanged.connect(self.toggle_log_scale)
        self.chk_log_y = QCheckBox("Log Y")
        self.chk_log_y.stateChanged.connect(self.toggle_log_scale)
        
        controls_layout.addWidget(QLabel("Calc using:"), 0, 0)
        controls_layout.addWidget(self.combo_calc, 0, 1)
        controls_layout.addWidget(self.chk_log_x, 0, 2)
        controls_layout.addWidget(self.chk_log_y, 0, 3)
        
        main_layout.addLayout(controls_layout)
        
        self.plot_widget = pg.PlotWidget(title=f"{title} Profile", background='w')
        unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
        self.plot_widget.setLabel('left', f'Intensity ({unit})')
        self.plot_widget.setLabel('bottom', 'Position')
        
        self.plot_widget.getAxis('bottom').setPen('k')
        self.plot_widget.getAxis('bottom').setTextPen('k')
        self.plot_widget.getAxis('left').setPen('k')
        self.plot_widget.getAxis('left').setTextPen('k')
        
        self.plot_curve = self.plot_widget.plot([], [], pen=pg.mkPen('k', width=1.5))
        main_layout.addWidget(self.plot_widget)
        
        # Coordinate Inputs
        coord_layout = QGridLayout()
        
        self.spin_x0 = QSpinBox()
        self.spin_y0 = QSpinBox()
        self.spin_x1 = QSpinBox()
        self.spin_y1 = QSpinBox()
        
        self.spin_w = QSpinBox()
        
        self._updating_spins = False
        
        for spin in [self.spin_x0, self.spin_y0, self.spin_x1, self.spin_y1, self.spin_w]:
            spin.setRange(0, 10000)
            spin.valueChanged.connect(self.on_spin_changed)
            
        if self.cut_type == 'horizontal':
            coord_layout.addWidget(QLabel("Y Region:"), 0, 0)
            coord_layout.addWidget(self.spin_y0, 0, 1)
            coord_layout.addWidget(QLabel("to"), 0, 2)
            coord_layout.addWidget(self.spin_y1, 0, 3)
        elif self.cut_type == 'vertical':
            coord_layout.addWidget(QLabel("X Region:"), 0, 0)
            coord_layout.addWidget(self.spin_x0, 0, 1)
            coord_layout.addWidget(QLabel("to"), 0, 2)
            coord_layout.addWidget(self.spin_x1, 0, 3)
        elif self.cut_type == 'diagonal':
            self.spin_w.setValue(5)
            coord_layout.addWidget(QLabel("Point 1:"), 0, 0)
            coord_layout.addWidget(self.spin_x0, 0, 1)
            coord_layout.addWidget(self.spin_y0, 0, 2)
            coord_layout.addWidget(QLabel("Point 2:"), 1, 0)
            coord_layout.addWidget(self.spin_x1, 1, 1)
            coord_layout.addWidget(self.spin_y1, 1, 2)
            coord_layout.addWidget(QLabel("Width:"), 2, 0)
            coord_layout.addWidget(self.spin_w, 2, 1)
            
        main_layout.addLayout(coord_layout)
        
        self.roi = None
        self.setup_roi()
        self.update_plot()
        
        if self.image_viewer and self.image_viewer.imv:
            self.image_viewer.imv.sigTimeChanged.connect(self.update_plot)
            
    def toggle_log_scale(self):
        self.plot_widget.setLogMode(x=self.chk_log_x.isChecked(), y=self.chk_log_y.isChecked())
        
    def set_diagonal_endpoints(self, p0, p1, width=None):
        """Position the diagonal LineROI so its line runs from p0 to p1 with the
        given extraction width.

        This is the inverse of pg.LineROI.__init__: the ROI is a rotated
        rectangle whose size is (length, width) and whose pos is the *corner*,
        offset half a width perpendicular to the line. Keeping the same math
        guarantees the spin -> ROI -> spin round trip through
        sync_spins_and_plot() is stable.
        """
        if self.roi is None:
            return

        if width is None:
            width = self.spin_w.value()
        width = max(1.0, float(width))

        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            # Degenerate line: keep the current angle and just nudge the length
            length = 1e-3
            angle_rad = math.radians(self.roi.angle())
        else:
            angle_rad = math.atan2(dy, dx)

        self.roi.setSize([length, width])
        self.roi.setAngle(math.degrees(angle_rad))
        corner_x = p0[0] + width / 2.0 * math.sin(angle_rad)
        corner_y = p0[1] - width / 2.0 * math.cos(angle_rad)
        self.roi.setPos(corner_x, corner_y)

    def on_spin_changed(self):
        if self._updating_spins or self.roi is None:
            return

        self.roi.blockSignals(True)
        try:
            if self.cut_type == 'horizontal':
                y0, y1 = self.spin_y0.value(), self.spin_y1.value()
                self.roi.setRegion([min(y0, y1), max(y0, y1)])
            elif self.cut_type == 'vertical':
                x0, x1 = self.spin_x0.value(), self.spin_x1.value()
                self.roi.setRegion([min(x0, x1), max(x0, x1)])
            elif self.cut_type == 'diagonal':
                self.set_diagonal_endpoints(
                    (self.spin_x0.value(), self.spin_y0.value()),
                    (self.spin_x1.value(), self.spin_y1.value()),
                    width=self.spin_w.value(),
                )
        finally:
            self.roi.blockSignals(False)
        self.update_plot()

    def setup_roi(self):
        if self.image_viewer is None or self.image_viewer.imv is None:
            return
            
        view = self.image_viewer.imv.getView()
        
        if self.image_viewer.display_data is not None:
            shape = self.image_viewer.display_data.shape
            if len(shape) == 3:
                nx, ny = shape[1], shape[2]
            else:
                nx, ny = shape[0], shape[1]
        else:
            nx, ny = 100, 100
            
        cx, cy = nx // 2, ny // 2
        
        img_item = self.image_viewer.imv.getImageItem() if self.image_viewer and hasattr(self.image_viewer, 'imv') else None
        
        if self.cut_type == 'horizontal':
            self.roi = pg.LinearRegionItem([cy - 2, cy + 2], orientation='horizontal', pen=pg.mkPen('g', width=3), hoverPen=pg.mkPen('g', width=5), brush=(0, 255, 0, 50))
            self.roi.sigRegionChanged.connect(self.sync_spins_and_plot)
            if img_item: self.roi.setParentItem(img_item)
            else: view.addItem(self.roi)
            
        elif self.cut_type == 'vertical':
            self.roi = pg.LinearRegionItem([cx - 2, cx + 2], orientation='vertical', pen=pg.mkPen('g', width=3), hoverPen=pg.mkPen('g', width=5), brush=(0, 255, 0, 50))
            self.roi.sigRegionChanged.connect(self.sync_spins_and_plot)
            if img_item: self.roi.setParentItem(img_item)
            else: view.addItem(self.roi)
            
        elif self.cut_type == 'diagonal':
            self.roi = pg.LineROI([cx - 20, cy - 20], [cx + 20, cy + 20], width=5, pen=pg.mkPen('g', width=3), hoverPen=pg.mkPen('g', width=5))
            self.roi.sigRegionChanged.connect(self.sync_spins_and_plot)
            if img_item: self.roi.setParentItem(img_item)
            else: view.addItem(self.roi)
            
        self.sync_spins_and_plot()
            
    def sync_spins_and_plot(self):
        if self.roi is None:
            return
            
        self._updating_spins = True
        if self.cut_type == 'horizontal':
            r = self.roi.getRegion()
            self.spin_y0.setValue(int(round(r[0])))
            self.spin_y1.setValue(int(round(r[1])))
        elif self.cut_type == 'vertical':
            r = self.roi.getRegion()
            self.spin_x0.setValue(int(round(r[0])))
            self.spin_x1.setValue(int(round(r[1])))
        elif self.cut_type == 'diagonal':
            handles = self.roi.getHandles()
            if len(handles) >= 2:
                # Get scene positions and map back to view coordinates
                pos0 = self.roi.mapToParent(handles[0].pos())
                pos1 = self.roi.mapToParent(handles[1].pos())
                self.spin_x0.setValue(int(round(pos0.x())))
                self.spin_y0.setValue(int(round(pos0.y())))
                self.spin_x1.setValue(int(round(pos1.x())))
                self.spin_y1.setValue(int(round(pos1.y())))
            # Keep the Width box in step when the width handle is dragged
            self.spin_w.setValue(int(round(self.roi.size().y())))
        self._updating_spins = False
        
        self.update_plot()
            
    def update_plot(self):
        if self.image_viewer is None or self.image_viewer.display_data is None or self.roi is None:
            return
            
        img_data = self.image_viewer.display_data
        if img_data.ndim == 3:
            img_data = img_data[self.image_viewer.imv.currentIndex]
            
        # For pyqtgraph cuts, image_viewer.imv image item is (nx, ny)
        # So X-axis is axis 0, Y-axis is axis 1
        
        if self.cut_type == 'horizontal':
            r = self.roi.getRegion()
            y0 = max(0, int(round(min(r[0], r[1]))))
            y1 = min(img_data.shape[1], int(round(max(r[0], r[1]))))
            
            if y1 > y0:
                region = img_data[:, y0:y1]
                calc_method = self.combo_calc.currentText()
                if calc_method == "Average":
                    profile = np.nanmean(region, axis=1)
                elif calc_method == "Median":
                    profile = np.nanmedian(region, axis=1)
                else:
                    profile = np.nansum(region, axis=1)
                    
                self.plot_curve.setData(np.arange(len(profile)), profile * self.image_viewer.data_multiplier)
            else:
                self.plot_curve.setData([], [])
                
        elif self.cut_type == 'vertical':
            r = self.roi.getRegion()
            x0 = max(0, int(round(min(r[0], r[1]))))
            x1 = min(img_data.shape[0], int(round(max(r[0], r[1]))))
            
            if x1 > x0:
                region = img_data[x0:x1, :]
                calc_method = self.combo_calc.currentText()
                if calc_method == "Average":
                    profile = np.nanmean(region, axis=0)
                elif calc_method == "Median":
                    profile = np.nanmedian(region, axis=0)
                else:
                    profile = np.nansum(region, axis=0)
                    
                self.plot_curve.setData(np.arange(len(profile)), profile * self.image_viewer.data_multiplier)
            else:
                self.plot_curve.setData([], [])
                
        elif self.cut_type == 'diagonal':
            img_item = self.image_viewer.imv.getImageItem()
            region = self.roi.getArrayRegion(img_data, img_item)
            if region is not None and region.size > 0:
                # region shape is typically (length, width)
                calc_method = self.combo_calc.currentText()
                if calc_method == "Average":
                    profile = np.nanmean(region, axis=1)
                elif calc_method == "Median":
                    profile = np.nanmedian(region, axis=1)
                else:
                    profile = np.nansum(region, axis=1)
                    
                self.plot_curve.setData(np.arange(len(profile)), profile * self.image_viewer.data_multiplier)
            else:
                self.plot_curve.setData([], [])
                
        unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
        self.plot_widget.setLabel('left', f'Intensity ({unit})')

    def closeEvent(self, event):
        if self.image_viewer and self.image_viewer.imv:
            try:
                self.image_viewer.imv.sigTimeChanged.disconnect(self.update_plot)
            except (TypeError, RuntimeError):
                pass
            if self.roi:
                try:
                    self.image_viewer.imv.getView().removeItem(self.roi)
                except Exception:
                    pass
        super().closeEvent(event)
