import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QGridLayout, QLabel, QComboBox, QCheckBox, QSpinBox, QHBoxLayout, QWidget, QVBoxLayout, QGroupBox, QPushButton, QDoubleSpinBox
from PySide6.QtCore import Qt
from pyql3.gui.tools.base_tool import BaseToolDialog


class WorldCoordinateAxis(pg.AxisItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.wcs = None
        self.z_idx = None
        self.fixed_coords = None
        
    def tickStrings(self, values, scale, spacing):
        if self.wcs is None or self.z_idx is None or self.fixed_coords is None:
            return super().tickStrings(values, scale, spacing)
            
        coords = np.zeros((len(values), self.wcs.naxis))
        for i in range(self.wcs.naxis):
            if i == self.z_idx:
                coords[:, i] = values
            else:
                coords[:, i] = self.fixed_coords[i]
                
        try:
            world = self.wcs.wcs_pix2world(coords, 0)
            wave = world[:, self.z_idx]
            unit = self.wcs.wcs.cunit[self.z_idx]
            if str(unit).strip().lower() == 'm':
                wave *= 1e6
            return [f"{w:.4f}" for w in wave]
        except Exception:
            return super().tickStrings(values, scale, spacing)

class DepthPlotDialog(BaseToolDialog):
    def __init__(self, parent=None, image_viewer=None):
        super().__init__(parent, image_viewer, "Plot Window")
        self.resize(700, 800)
        
        self.setup_draw_button(self.layout)
        
        # Clear base layout if we want, but it's a QVBoxLayout.
        # Top Controls
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("Plot Type:"))
        
        self.combo_type = QComboBox()
        self.combo_type.addItems(["Depth Plot", "Horizontal Cut", "Vertical Cut"])
        self.combo_type.currentIndexChanged.connect(self.update_plot)
        top_layout.addWidget(self.combo_type)
        
        top_layout.addWidget(QLabel("calc using:"))
        self.combo_calc = QComboBox()
        self.combo_calc.addItems(["Average", "Median", "Total"])
        self.combo_calc.currentIndexChanged.connect(self.update_plot)
        top_layout.addWidget(self.combo_calc)
        
        top_layout.addWidget(QLabel("Shape:"))
        self.combo_shape = QComboBox()
        self.combo_shape.addItems(["Rectangle", "Circle"])
        self.combo_shape.currentIndexChanged.connect(self.toggle_roi_shape)
        top_layout.addWidget(self.combo_shape)
        
        top_layout.addStretch()
        self.layout.addLayout(top_layout)
        
        # Plot Widget
        self.world_axis = WorldCoordinateAxis(orientation='top')
        self.plot_widget = pg.PlotWidget(background='w', axisItems={'top': self.world_axis})
        
        self.plot_widget.setLabel('bottom', "Slice Index / X / Y")
        unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
        self.plot_widget.setLabel('left', f"Intensity ({unit})")
        
        # Dual axis setup
        self.plot_widget.showAxis('top')
        self.plot_widget.getAxis('top').setPen('k')
        self.plot_widget.getAxis('top').setTextPen('k')
        self.plot_widget.getAxis('top').setLabel("Wavelength", units="µm")
        
        self.plot_widget.getAxis('bottom').setPen('k')
        self.plot_widget.getAxis('bottom').setTextPen('k')
        self.plot_widget.getAxis('left').setPen('k')
        self.plot_widget.getAxis('left').setTextPen('k')
        
        self.plot_widget.getAxis('right').setPen('k')
        self.plot_widget.showAxis('right')
        
        self.layout.addWidget(self.plot_widget, stretch=1)
        
        self.plot_data = self.plot_widget.plot([], [], pen=pg.mkPen('k', width=1.5))
        
        # Crosshair / Hover Label
        self.lbl_cursor = QLabel("X: --  Y: --")
        self.layout.addWidget(self.lbl_cursor)
        
        # Proxy for mouse move
        self.proxy = pg.SignalProxy(self.plot_widget.scene().sigMouseMoved, rateLimit=60, slot=self.mouse_moved)
        
        # Plot Axes GroupBox
        group_axes = QGroupBox("PLOT AXES")
        axes_layout = QGridLayout(group_axes)
        
        self.spin_x_min = QDoubleSpinBox(); self.spin_x_min.setRange(-1e9, 1e9); self.spin_x_min.setDecimals(4)
        self.spin_x_max = QDoubleSpinBox(); self.spin_x_max.setRange(-1e9, 1e9); self.spin_x_max.setDecimals(4)
        btn_set_x = QPushButton("SET")
        btn_auto_x = QPushButton("Auto")
        self.chk_fix_x = QCheckBox("Fix")
        self.chk_log_x = QCheckBox("Log")
        
        axes_layout.addWidget(QLabel("X Range:"), 0, 0)
        axes_layout.addWidget(self.spin_x_min, 0, 1)
        axes_layout.addWidget(QLabel("to"), 0, 2)
        axes_layout.addWidget(self.spin_x_max, 0, 3)
        axes_layout.addWidget(btn_set_x, 0, 4)
        axes_layout.addWidget(btn_auto_x, 0, 5)
        axes_layout.addWidget(self.chk_fix_x, 0, 6)
        axes_layout.addWidget(self.chk_log_x, 0, 7)
        
        self.spin_y_min = QDoubleSpinBox(); self.spin_y_min.setRange(-1e9, 1e9); self.spin_y_min.setDecimals(4)
        self.spin_y_max = QDoubleSpinBox(); self.spin_y_max.setRange(-1e9, 1e9); self.spin_y_max.setDecimals(4)
        btn_set_y = QPushButton("SET")
        btn_auto_y = QPushButton("Auto")
        self.chk_fix_y = QCheckBox("Fix")
        self.chk_log_y = QCheckBox("Log")
        
        axes_layout.addWidget(QLabel("Y Range:"), 1, 0)
        axes_layout.addWidget(self.spin_y_min, 1, 1)
        axes_layout.addWidget(QLabel("to"), 1, 2)
        axes_layout.addWidget(self.spin_y_max, 1, 3)
        axes_layout.addWidget(btn_set_y, 1, 4)
        axes_layout.addWidget(btn_auto_y, 1, 5)
        axes_layout.addWidget(self.chk_fix_y, 1, 6)
        axes_layout.addWidget(self.chk_log_y, 1, 7)
        
        self.layout.addWidget(group_axes)
        
        btn_set_x.clicked.connect(self.apply_x_range)
        btn_auto_x.clicked.connect(self.auto_x_range)
        self.chk_fix_x.stateChanged.connect(self.toggle_fix_x)
        self.chk_log_x.stateChanged.connect(self.toggle_log_scale)
        
        btn_set_y.clicked.connect(self.apply_y_range)
        btn_auto_y.clicked.connect(self.auto_y_range)
        self.chk_fix_y.stateChanged.connect(self.toggle_fix_y)
        self.chk_log_y.stateChanged.connect(self.toggle_log_scale)
        
        # Region Controls GroupBox
        group_region = QGroupBox("INPUT DATA FROM CUBE")
        region_layout = QGridLayout(group_region)
        
        self.spin_x0 = QSpinBox(); self.spin_x0.setRange(0, 10000)
        self.spin_x1 = QSpinBox(); self.spin_x1.setRange(0, 10000)
        self.spin_y0 = QSpinBox(); self.spin_y0.setRange(0, 10000)
        self.spin_y1 = QSpinBox(); self.spin_y1.setRange(0, 10000)
        
        for spin in [self.spin_x0, self.spin_x1, self.spin_y0, self.spin_y1]:
            spin.valueChanged.connect(self.on_spin_changed)
            
        region_layout.addWidget(QLabel("X Region:"), 0, 0)
        region_layout.addWidget(self.spin_x0, 0, 1)
        region_layout.addWidget(QLabel("to"), 0, 2)
        region_layout.addWidget(self.spin_x1, 0, 3)
        
        region_layout.addWidget(QLabel("Y Region:"), 1, 0)
        region_layout.addWidget(self.spin_y0, 1, 1)
        region_layout.addWidget(QLabel("to"), 1, 2)
        region_layout.addWidget(self.spin_y1, 1, 3)
        
        self.layout.addWidget(group_region)
        
        # Setup signals for view range changed to update spinboxes
        self.plot_widget.getViewBox().sigXRangeChanged.connect(self.on_x_range_changed)
        self.plot_widget.getViewBox().sigYRangeChanged.connect(self.on_y_range_changed)
        
        if self.image_viewer and self.image_viewer.display_data is not None:
            shape = self.image_viewer.display_data.shape
            if len(shape) == 3:
                center_x, center_y = shape[1]//2, shape[2]//2
            else:
                center_x, center_y = shape[0]//2, shape[1]//2
        else:
            center_x, center_y = 2, 2
            
        roi = pg.RectROI([center_x - 2, center_y - 2], [4, 4], pen=pg.mkPen((0, 255, 0), width=3), hoverPen=pg.mkPen((0, 255, 0), width=5))
        roi.addScaleHandle([1, 1], [0, 0])
        roi.addScaleHandle([0, 0], [1, 1])
        self.add_roi_to_viewer(roi)
        
        self._updating_spins = False
        self._updating_range_spins = False
        
        self.update_plot()
        
    def mouse_moved(self, evt):
        pos = evt[0]
        if self.plot_widget.sceneBoundingRect().contains(pos):
            mousePoint = self.plot_widget.plotItem.vb.mapSceneToView(pos)
            self.lbl_cursor.setText(f"X: {mousePoint.x():.4f}   Y: {mousePoint.y():.4f}")
            
    def apply_x_range(self):
        self.plot_widget.setXRange(self.spin_x_min.value(), self.spin_x_max.value(), padding=0)
        self.chk_fix_x.setChecked(True)
        
    def apply_y_range(self):
        self.plot_widget.setYRange(self.spin_y_min.value(), self.spin_y_max.value(), padding=0)
        self.chk_fix_y.setChecked(True)
        
    def auto_x_range(self):
        self.chk_fix_x.setChecked(False)
        self.plot_widget.enableAutoRange(axis=pg.ViewBox.XAxis)
        
    def auto_y_range(self):
        self.chk_fix_y.setChecked(False)
        self.plot_widget.enableAutoRange(axis=pg.ViewBox.YAxis)
        
    def toggle_fix_x(self):
        if self.chk_fix_x.isChecked():
            self.plot_widget.disableAutoRange(axis=pg.ViewBox.XAxis)
        else:
            self.plot_widget.enableAutoRange(axis=pg.ViewBox.XAxis)
            
    def toggle_fix_y(self):
        if self.chk_fix_y.isChecked():
            self.plot_widget.disableAutoRange(axis=pg.ViewBox.YAxis)
        else:
            self.plot_widget.enableAutoRange(axis=pg.ViewBox.YAxis)
            
    def on_x_range_changed(self, _, range_val):
        if not self._updating_range_spins:
            self._updating_range_spins = True
            self.spin_x_min.setValue(range_val[0])
            self.spin_x_max.setValue(range_val[1])
            self._updating_range_spins = False
            
    def on_y_range_changed(self, _, range_val):
        if not self._updating_range_spins:
            self._updating_range_spins = True
            self.spin_y_min.setValue(range_val[0])
            self.spin_y_max.setValue(range_val[1])
            self._updating_range_spins = False


    def toggle_log_scale(self):
        self.plot_widget.setLogMode(x=self.chk_log_x.isChecked(), y=self.chk_log_y.isChecked())
        
    def on_spin_changed(self):
        if self._updating_spins:
            return
        x0 = self.spin_x0.value()
        x1 = self.spin_x1.value()
        y0 = self.spin_y0.value()
        y1 = self.spin_y1.value()
        
        w = max(1, x1 - x0)
        h = max(1, y1 - y0)
        
        self.roi.blockSignals(True)
        self.roi.setPos([x0, y0])
        self.roi.setSize([w, h])
        self.roi.blockSignals(False)
        self.update_plot()
        
    def on_roi_changed(self):
        pos = self.roi.pos()
        size = self.roi.size()
        
        x0, y0 = int(pos.x()), int(pos.y())
        w, h = int(size.x()), int(size.y())
        
        self._updating_spins = True
        self.spin_x0.setValue(x0)
        self.spin_x1.setValue(x0 + w)
        self.spin_y0.setValue(y0)
        self.spin_y1.setValue(y0 + h)
        self._updating_spins = False
        
        self.update_plot()
        
    def toggle_roi_shape(self):
        shape = self.combo_shape.currentText()
        pos = self.roi.pos()
        size = self.roi.size()
        
        self.remove_roi_from_viewer()
        
        if shape == "Circle":
            roi = pg.CircleROI(pos, size, pen=pg.mkPen((0, 255, 0), width=3), hoverPen=pg.mkPen((0, 255, 0), width=5))
        else:
            roi = pg.RectROI(pos, size, pen=pg.mkPen((0, 255, 0), width=3), hoverPen=pg.mkPen((0, 255, 0), width=5))
            roi.addScaleHandle([1, 1], [0, 0])
            roi.addScaleHandle([0, 0], [1, 1])
            
        self.add_roi_to_viewer(roi)
        self.update_plot()

    def update_plot(self):
        if self.image_viewer is None or self.image_viewer.transposed_data is None:
            return
            
        if self.image_viewer.transposed_data.ndim != 3:
            return
            
        # Transform the 3D cube to match the display coordinates (rotation, flip)
        cube = self.image_viewer.transposed_data
        if self.image_viewer.flip:
            cube = np.flip(cube, axis=1)
        k = self.image_viewer.rot_angle // 90
        if k != 0:
            cube = np.rot90(cube, k=k, axes=(1, 2))
            
        pos = self.roi.pos()
        size = self.roi.size()
        
        x0, y0 = int(pos.x()), int(pos.y())
        w, h = int(size.x()), int(size.y())
        
        shape = cube.shape
        z_len, x_len, y_len = shape
        
        x0 = max(0, min(x0, x_len-1))
        y0 = max(0, min(y0, y_len-1))
        x1 = max(x0+1, min(x0+w, x_len))
        y1 = max(y0+1, min(y0+h, y_len))
        
        plot_type = self.combo_type.currentText()
        calc_method = self.combo_calc.currentText()
        
        if plot_type == "Depth Plot":
            region = cube[:, x0:x1, y0:y1]
            if region.size == 0:
                return
                
            # If circle, apply mask
            if self.combo_shape.currentText() == "Circle":
                yy, xx = np.mgrid[:(x1-x0), :(y1-y0)]
                cx, cy = (x1-x0)/2.0 - 0.5, (y1-y0)/2.0 - 0.5
                r = min((x1-x0)/2.0, (y1-y0)/2.0)
                mask = ((xx - cy)**2 + (yy - cx)**2) <= r**2
                region = np.where(mask, region, np.nan)
                
            if calc_method == "Average":
                spectrum = np.nanmean(region, axis=(1, 2))
            elif calc_method == "Median":
                spectrum = np.nanmedian(region, axis=(1, 2))
            else:
                spectrum = np.nansum(region, axis=(1, 2))
                
            x_axis = np.arange(z_len)
            
            if self.image_viewer.wcs is not None and self.image_viewer.wcs_z_idx is not None:
                wcs = self.image_viewer.wcs
                z_idx = self.image_viewer.wcs_z_idx
                ctype_raw = str(wcs.wcs.ctype[z_idx]).upper()
                ctype = ctype_raw.split('-')[0] if '-' in ctype_raw else ctype_raw
                
                try:
                    cunit = str(wcs.wcs.cunit[z_idx]).strip()
                    if cunit.lower() == 'm':
                        cunit = 'µm'
                except Exception:
                    cunit = ""
                
                # Compute fixed coordinates for the remaining axes
                # The data in pyqtgraph is typically (X, Y) which corresponds to cube (x_len, y_len).
                # The wcs might have a different mapping, but we know the z_idx in WCS space.
                # In image_viewer.py, WCS coordinates are built based on transpose and flip.
                # To be precise, we need the exact pixel coordinates in the original cube.
                # Actually, image_viewer.py maintains the mapping. 
                # A simpler approximation is that x_axis (WCS axis 0) is roughly x0 + w/2, etc.
                # Let's map from the current display coordinates (center of ROI) back to original cube.
                
                cx, cy = x0 + w/2.0, y0 + h/2.0
                
                # Un-flip and un-rotate to get coords in transposed_data space
                k = self.image_viewer.rot_angle // 90
                # Reverse rotation
                for _ in range((4 - k) % 4):
                    cx, cy = cy, x_len - 1 - cx
                
                if self.image_viewer.flip:
                    cx = x_len - 1 - cx
                
                # Now cx, cy correspond to transposed_data spatial axes.
                # In image_viewer:
                # if z_axis == 'x': transposed_data is [X, Y, Z] -> slice over X. Spatial are Y and Z.
                # Actually, image_viewer.transposed_data ALWAYS has the Z-slice axis FIRST.
                # i.e., transposed_data is shape (slice_len, spatial_1, spatial_2).
                # But wait, we need to map spatial_1 and spatial_2 to WCS axes (0, 1, 2).
                # The viewer does this:
                # if z_axis == 'x': x_idx=2, y_idx=1, z_idx=0
                # elif z_axis == 'y': x_idx=2, y_idx=0, z_idx=1
                # else: x_idx=0, y_idx=1, z_idx=2
                
                z_axis_str = getattr(self.image_viewer, 'z_axis', 'z')
                if z_axis_str == 'x':
                    x_idx, y_idx = 2, 1
                elif z_axis_str == 'y':
                    x_idx, y_idx = 2, 0
                else:
                    x_idx, y_idx = 0, 1
                    
                fixed_coords = np.zeros(wcs.naxis)
                if wcs.naxis > max(x_idx, y_idx):
                    fixed_coords[x_idx] = cx
                    fixed_coords[y_idx] = cy
                
                self.world_axis.wcs = wcs
                self.world_axis.z_idx = z_idx
                self.world_axis.fixed_coords = fixed_coords
                
                self.plot_widget.showAxis('top')
                unit_str = f" ({cunit})" if cunit else ""
                
                label = "Wavelength" if 'WAVE' in ctype else ctype
                
                self.plot_widget.getAxis('top').setLabel(f"{label}{unit_str}")
            else:
                self.world_axis.wcs = None
                self.plot_widget.hideAxis('top')
            self.plot_widget.setLabel('bottom', "Slice Index (pixels)")
            self.plot_data.setData(x_axis, spectrum * self.image_viewer.data_multiplier)
            
        elif plot_type == "Horizontal Cut":
            # For 2D cuts, we use the currently displayed Z slice
            z_idx = self.image_viewer.imv.currentIndex
            region = cube[z_idx, x0:x1, y0:y1]
            if region.size == 0:
                return
            if calc_method == "Average":
                cut = np.nanmean(region, axis=1) # collapse Y
            elif calc_method == "Median":
                cut = np.nanmedian(region, axis=1)
            else:
                cut = np.nansum(region, axis=1)
                
            self.world_axis.wcs = None
            self.plot_widget.hideAxis('top')
                
            x_axis = np.arange(x0, x1)
            self.plot_widget.setLabel('bottom', "X Pixel")
            unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
            self.plot_widget.setLabel('left', f"Intensity ({unit})")
            self.plot_data.setData(x_axis, cut * self.image_viewer.data_multiplier)
            
        elif plot_type == "Vertical Cut":
            z_idx = self.image_viewer.imv.currentIndex
            region = cube[z_idx, x0:x1, y0:y1]
            if region.size == 0:
                return
            if calc_method == "Average":
                cut = np.nanmean(region, axis=0) # collapse X
            elif calc_method == "Median":
                cut = np.nanmedian(region, axis=0)
            else:
                cut = np.nansum(region, axis=0)
                
            self.world_axis.is_wavelength = False
            self.plot_widget.hideAxis('top')
            x_axis = np.arange(y0, y1)
            self.plot_widget.setLabel('bottom', "Y Pixel")
            unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
            self.plot_widget.setLabel('left', f"Intensity ({unit})")
            self.plot_data.setData(x_axis, cut * self.image_viewer.data_multiplier)
