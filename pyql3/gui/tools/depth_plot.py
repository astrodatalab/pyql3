import os
import re
import pathlib
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QGridLayout, QLabel, QComboBox, QCheckBox, QSpinBox, QHBoxLayout, QGroupBox, QPushButton, QDoubleSpinBox, QFileDialog
from PySide6.QtCore import Qt, QDir
from pyql3.gui.tools.base_tool import BaseToolDialog, as_center


def latex_to_html(text):
    if not text:
        return ""
    
    s = text.strip()
    # Strip wrapping $$...$$ or $...$
    if s.startswith("$$") and s.endswith("$$") and len(s) >= 4:
        s = s[2:-2].strip()
    elif s.startswith("$") and s.endswith("$") and len(s) >= 2:
        s = s[1:-1].strip()
        
    def replace_math(match):
        return match.group(1) or match.group(2) or ""

    s = re.sub(r"\$\$(.*?)\$\$|\$(.*?)\$", replace_math, s)

    # Greek letters
    greek = [
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
        "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau",
        "upsilon", "phi", "chi", "psi", "omega",
        "Alpha", "Beta", "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi",
        "Sigma", "Phi", "Psi", "Omega"
    ]
    for g in greek:
        pattern = "\\\\" + g + "(?![a-zA-Z])"
        s = re.sub(pattern, "&" + g + ";", s)

    # Spacing & symbols
    s = s.replace("\\;", "&nbsp;").replace("\\ ", "&nbsp;").replace("\\quad", "&nbsp;&nbsp;")
    s = s.replace("\\AA", "&#8491;").replace("\\angstrom", "&#8491;").replace("\\pm", "&plusmn;")

    # Subscripts: _{abc} or _abc
    s = re.sub(r"_\{([^}]+)\}", r"<sub>\1</sub>", s)
    s = re.sub(r"_([a-zA-Z0-9&;#]+)", r"<sub>\1</sub>", s)

    # Superscripts: ^{abc} or ^abc
    s = re.sub(r"\^\{([^}]+)\}", r"<sup>\1</sup>", s)
    s = re.sub(r"\^([a-zA-Z0-9&;#]+)", r"<sup>\1</sup>", s)

    return s


class PixelIndexAxis(pg.AxisItem):
    """Top axis displaying 0-indexed channel slice numbers when the bottom X-axis displays physical wavelengths."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.wavelengths = None

    def tickStrings(self, values, scale, spacing):
        if self.wavelengths is None or len(self.wavelengths) == 0:
            return super().tickStrings(values, scale, spacing)

        try:
            indices = np.interp(values, self.wavelengths, np.arange(len(self.wavelengths)))
            return [f"{int(round(idx))}" for idx in indices]
        except Exception:
            return super().tickStrings(values, scale, spacing)


class DepthPlotDialog(BaseToolDialog):
    def __init__(self, parent=None, image_viewer=None, initial_center=None):
        super().__init__(parent, image_viewer, "Plot Window")
        # A Qt signal may hand us its `checked` flag instead of a centre
        initial_center = as_center(initial_center)
        self.resize(700, 800)
        
        # Top Controls
        top_layout = QHBoxLayout()
        self.setup_draw_button(top_layout)

        self.btn_export = QPushButton("Export...")
        self.btn_export.setToolTip("Export plot data (CSV, Image, SVG, Vector)")
        self.btn_export.clicked.connect(self.open_export_dialog)
        top_layout.addWidget(self.btn_export)

        top_layout.addWidget(QLabel("Type:"))
        
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
        self.top_axis = PixelIndexAxis(orientation='top')
        self.plot_widget = pg.PlotWidget(background='w', axisItems={'top': self.top_axis})
        
        self.plot_widget.setLabel('bottom', "Wavelength", units="µm")
        unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
        self.plot_widget.setLabel('left', f"Intensity ({unit})")
        
        # Dual axis setup
        self.plot_widget.showAxis('top')
        self.plot_widget.getAxis('top').setPen('k')
        self.plot_widget.getAxis('top').setTextPen('k')
        self.plot_widget.getAxis('top').setLabel("Slice Index (pixels)")
        
        self.plot_widget.getAxis('bottom').setPen('k')
        self.plot_widget.getAxis('bottom').setTextPen('k')
        self.plot_widget.getAxis('left').setPen('k')
        self.plot_widget.getAxis('left').setTextPen('k')
        
        self.plot_widget.getAxis('right').setPen('k')
        self.plot_widget.showAxis('right')
        
        self.layout.addWidget(self.plot_widget, stretch=1)
        
        self.plot_legend = self.plot_widget.addLegend(offset=(10, 10))
        self.plot_data = self.plot_widget.plot([], [], pen=pg.mkPen('k', width=2.5), name="Source")
        self.plot_bg = self.plot_widget.plot([], [], pen=pg.mkPen((255, 140, 0), width=2.5, style=Qt.DashLine), name="Background")
        self.plot_sub = self.plot_widget.plot([], [], pen=pg.mkPen('r', width=2.5), name="Subtracted")
        
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

        # Background Region GroupBox
        self.group_bg = QGroupBox("BACKGROUND REGION")
        bg_layout = QGridLayout(self.group_bg)

        self.chk_enable_bg = QCheckBox("Enable Background Subtraction")
        self.combo_bg_calc = QComboBox()
        self.combo_bg_calc.addItems(["Median", "Average", "Total"])
        self.combo_bg_calc.setCurrentText("Median")
        self.combo_bg_calc.setEnabled(False)

        bg_layout.addWidget(self.chk_enable_bg, 0, 0, 1, 2)
        bg_layout.addWidget(QLabel("Calc using:"), 0, 2)
        bg_layout.addWidget(self.combo_bg_calc, 0, 3)

        self.spin_bg_x0 = QSpinBox(); self.spin_bg_x0.setRange(0, 10000); self.spin_bg_x0.setEnabled(False)
        self.spin_bg_x1 = QSpinBox(); self.spin_bg_x1.setRange(0, 10000); self.spin_bg_x1.setEnabled(False)
        self.spin_bg_y0 = QSpinBox(); self.spin_bg_y0.setRange(0, 10000); self.spin_bg_y0.setEnabled(False)
        self.spin_bg_y1 = QSpinBox(); self.spin_bg_y1.setRange(0, 10000); self.spin_bg_y1.setEnabled(False)

        self._updating_bg_spins = False
        for spin in [self.spin_bg_x0, self.spin_bg_x1, self.spin_bg_y0, self.spin_bg_y1]:
            spin.valueChanged.connect(self.on_bg_spin_changed)

        bg_layout.addWidget(QLabel("X Region:"), 1, 0)
        bg_layout.addWidget(self.spin_bg_x0, 1, 1)
        bg_layout.addWidget(QLabel("to"), 1, 2)
        bg_layout.addWidget(self.spin_bg_x1, 1, 3)

        bg_layout.addWidget(QLabel("Y Region:"), 2, 0)
        bg_layout.addWidget(self.spin_bg_y0, 2, 1)
        bg_layout.addWidget(QLabel("to"), 2, 2)
        bg_layout.addWidget(self.spin_bg_y1, 2, 3)

        # Add Cube Input Data and Background Region side-by-side
        regions_row_layout = QHBoxLayout()
        regions_row_layout.setContentsMargins(0, 0, 0, 0)
        regions_row_layout.setSpacing(6)
        regions_row_layout.addWidget(group_region)
        regions_row_layout.addWidget(self.group_bg)

        self.layout.addLayout(regions_row_layout)

        # Spectral Line List GroupBox in its own row
        self.group_linelist = QGroupBox("SPECTRAL LINE LIST")
        linelist_layout = QGridLayout(self.group_linelist)

        self.chk_enable_lines = QCheckBox("Overplot Line List")
        self.combo_linelist = QComboBox()
        self.btn_browse_linelist = QPushButton("Browse...")
        self.lbl_line_info = QLabel("")

        linelist_layout.addWidget(self.chk_enable_lines, 0, 0)
        linelist_layout.addWidget(QLabel("Line List:"), 0, 1)
        linelist_layout.addWidget(self.combo_linelist, 0, 2)
        linelist_layout.addWidget(self.btn_browse_linelist, 0, 3)
        linelist_layout.addWidget(self.lbl_line_info, 0, 4)

        self.layout.addWidget(self.group_linelist)

        self.line_items = []
        self.loaded_lines = []
        self.linelist_files = {}

        self.chk_enable_lines.stateChanged.connect(self.update_line_overlays)
        self.combo_linelist.currentIndexChanged.connect(self.on_linelist_selection_changed)
        self.btn_browse_linelist.clicked.connect(self.browse_custom_linelist)

        self._updating_spins = False
        self._updating_range_spins = False

        self.populate_linelists()

        # Setup signals for view range changed to update spinboxes
        self.plot_widget.getViewBox().sigXRangeChanged.connect(self.on_x_range_changed)
        self.plot_widget.getViewBox().sigYRangeChanged.connect(self.on_y_range_changed)
        
        if initial_center is not None:
            center_x, center_y = initial_center
        elif self.image_viewer and self.image_viewer.display_data is not None:
            shape = self.image_viewer.display_data.shape
            if len(shape) == 3:
                center_x, center_y = shape[1]//2, shape[2]//2
            else:
                center_x, center_y = shape[0]//2, shape[1]//2
        else:
            center_x, center_y = 2, 2
            
        # Must exist before the first update_plot(), which reads it
        self.bg_roi = None

        roi = pg.RectROI([center_x - 2, center_y - 2], [4, 4], pen=pg.mkPen((0, 255, 0), width=3), hoverPen=pg.mkPen((0, 255, 0), width=5))
        roi.addScaleHandle([1, 1], [0, 0])
        roi.addScaleHandle([0, 0], [1, 1])
        self.add_roi_to_viewer(roi)
        self.on_roi_changed()

        # Background-subtraction wiring belongs here, not in set_center(): the
        # dialog can be opened without an initial center (Plot -> Depth Plot),
        # and set_center() may be called repeatedly.
        self.chk_enable_bg.stateChanged.connect(self.toggle_background)
        self.combo_bg_calc.currentIndexChanged.connect(self.update_plot)

        self.update_plot()

    def set_center(self, center):
        center = as_center(center)
        if center is None or self.roi is None:
            return
        cx, cy = center
        w = self.roi.size().x()
        h = self.roi.size().y()
        self.roi.setPos([cx - w / 2.0, cy - h / 2.0])
        self.on_roi_changed()

    def open_export_dialog(self):
        """Open PyQtGraph native export dialog."""
        try:
            from pyqtgraph.GraphicsScene.exportDialog import ExportDialog
            scene = self.plot_widget.scene()
            scene.contextMenuItem = self.plot_widget.plotItem
            if getattr(scene, 'exportDialog', None) is None:
                scene.exportDialog = ExportDialog(scene)
            scene.exportDialog.show(self.plot_widget.plotItem)
        except Exception as e:
            print(f"Error opening export dialog: {e}")
        
    def mouse_moved(self, evt):
        pos = evt[0]
        if self.plot_widget.sceneBoundingRect().contains(pos):
            mousePoint = self.plot_widget.plotItem.vb.mapSceneToView(pos)
            x_val = mousePoint.x()
            y_val = mousePoint.y()
            if hasattr(self, 'current_wavelengths') and self.current_wavelengths is not None and len(self.current_wavelengths) > 0:
                pix_idx = int(round(np.interp(x_val, self.current_wavelengths, np.arange(len(self.current_wavelengths)))))
                unit_str = getattr(self, 'current_wavelength_unit', 'µm')
                self.lbl_cursor.setText(f"Wavelength: {x_val:.4f} {unit_str}  (Pixel: {pix_idx})   Intensity: {y_val:.4f}")
            else:
                self.lbl_cursor.setText(f"Pixel: {x_val:.1f}   Intensity: {y_val:.4f}")
            
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
        self.plot_widget.getViewBox().autoRange()
        self.update_line_overlays()
        
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
            self.update_line_overlays()
            
    def on_y_range_changed(self, _, range_val):
        if not self._updating_range_spins:
            self._updating_range_spins = True
            self.spin_y_min.setValue(range_val[0])
            self.spin_y_max.setValue(range_val[1])
            self._updating_range_spins = False
            self.update_line_overlays()

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

    def add_bg_roi(self):
        if self.bg_roi is not None:
            self.remove_bg_roi()

        if self.image_viewer is None or getattr(self.image_viewer, 'imv', None) is None:
            return

        shape = self.combo_shape.currentText()
        pos = self.roi.pos() if self.roi else [0, 0]
        size = self.roi.size() if self.roi else [4, 4]

        # Offset background ROI by width + 2 pixels
        bg_pos = [pos.x() + size.x() + 2, pos.y()]

        pen = pg.mkPen((255, 140, 0), width=3)
        hover_pen = pg.mkPen((255, 140, 0), width=5)

        if shape == "Circle":
            self.bg_roi = pg.CircleROI(bg_pos, size, pen=pen, hoverPen=hover_pen)
        else:
            self.bg_roi = pg.RectROI(bg_pos, size, pen=pen, hoverPen=hover_pen)
            self.bg_roi.addScaleHandle([1, 1], [0, 0])
            self.bg_roi.addScaleHandle([0, 0], [1, 1])

        img_item = self.image_viewer.imv.getImageItem()
        if img_item:
            self.bg_roi.setParentItem(img_item)
        else:
            self.image_viewer.imv.getView().addItem(self.bg_roi)
        self.bg_roi.sigRegionChanged.connect(self.on_bg_roi_changed)
        self.on_bg_roi_changed()

    def remove_bg_roi(self):
        if self.bg_roi is not None and self.image_viewer is not None:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    self.bg_roi.sigRegionChanged.disconnect(self.on_bg_roi_changed)
                except Exception:
                    pass
            try:
                self.bg_roi.setParentItem(None)
            except Exception:
                pass
            try:
                self.image_viewer.imv.getView().removeItem(self.bg_roi)
            except Exception:
                pass
            self.bg_roi = None

    def toggle_background(self, state=None):
        checked = self.chk_enable_bg.isChecked()
        if checked:
            if self.bg_roi is None:
                self.add_bg_roi()
            self.spin_bg_x0.setEnabled(True)
            self.spin_bg_x1.setEnabled(True)
            self.spin_bg_y0.setEnabled(True)
            self.spin_bg_y1.setEnabled(True)
            self.combo_bg_calc.setEnabled(True)
        else:
            self.remove_bg_roi()
            self.spin_bg_x0.setEnabled(False)
            self.spin_bg_x1.setEnabled(False)
            self.spin_bg_y0.setEnabled(False)
            self.spin_bg_y1.setEnabled(False)
            self.combo_bg_calc.setEnabled(False)
        self.update_plot()

    def on_bg_roi_changed(self):
        if self.bg_roi is None:
            return
        pos = self.bg_roi.pos()
        size = self.bg_roi.size()

        x0, y0 = int(pos.x()), int(pos.y())
        w, h = int(size.x()), int(size.y())

        self._updating_bg_spins = True
        self.spin_bg_x0.setValue(x0)
        self.spin_bg_x1.setValue(x0 + w)
        self.spin_bg_y0.setValue(y0)
        self.spin_bg_y1.setValue(y0 + h)
        self._updating_bg_spins = False

        self.update_plot()

    def on_bg_spin_changed(self):
        if getattr(self, '_updating_bg_spins', False) or self.bg_roi is None:
            return
        x0 = self.spin_bg_x0.value()
        x1 = self.spin_bg_x1.value()
        y0 = self.spin_bg_y0.value()
        y1 = self.spin_bg_y1.value()

        w = max(1, x1 - x0)
        h = max(1, y1 - y0)

        self.bg_roi.blockSignals(True)
        self.bg_roi.setPos([x0, y0])
        self.bg_roi.setSize([w, h])
        self.bg_roi.blockSignals(False)
        self.update_plot()

    def closeEvent(self, event):
        self.clear_line_overlays()
        self.remove_bg_roi()
        super().closeEvent(event)

    def get_data_dir(self):
        import sys
        import pyql3

        candidates = []
        if hasattr(sys, '_MEIPASS'):
            candidates.append(pathlib.Path(sys._MEIPASS) / "pyql3" / "data")
            candidates.append(pathlib.Path(sys._MEIPASS) / "data")

        try:
            from pyql3 import get_resource_path
            candidates.append(pathlib.Path(get_resource_path("pyql3/data")))
        except Exception:
            pass

        pyql3_dir = pathlib.Path(pyql3.__file__).resolve().parent
        candidates.append(pyql3_dir / "data")

        cur_dir = pathlib.Path(__file__).resolve().parent
        candidates.append(cur_dir.parents[1] / "data")
        candidates.append(cur_dir.parents[2] / "data")

        for cand in candidates:
            if cand.exists() and cand.is_dir():
                return cand

        return pyql3_dir / "data"

    def populate_linelists(self):
        self.combo_linelist.blockSignals(True)
        self.combo_linelist.clear()
        self.linelist_files.clear()

        data_dir = self.get_data_dir()
        if data_dir.exists():
            for p in sorted(data_dir.glob("*")):
                if p.suffix.lower() in [".txt", ".csv"]:
                    display_name = p.name
                    self.linelist_files[display_name] = str(p)
                    self.combo_linelist.addItem(display_name)

        self.combo_linelist.addItem("Load Custom CSV...")

        if "nir_stellar_lines.txt" in self.linelist_files:
            self.combo_linelist.setCurrentText("nir_stellar_lines.txt")
            self.loaded_lines = self.parse_line_list(self.linelist_files["nir_stellar_lines.txt"])
        elif "rayner_arcturus_atomic_line_list_reformat.txt" in self.linelist_files:
            self.combo_linelist.setCurrentText("rayner_arcturus_atomic_line_list_reformat.txt")
            self.loaded_lines = self.parse_line_list(self.linelist_files["rayner_arcturus_atomic_line_list_reformat.txt"])
        elif self.linelist_files:
            first_name = list(self.linelist_files.keys())[0]
            self.combo_linelist.setCurrentText(first_name)
            self.loaded_lines = self.parse_line_list(self.linelist_files[first_name])
        else:
            self.combo_linelist.setCurrentText("Load Custom CSV...")
            self.loaded_lines = []

        self.combo_linelist.blockSignals(False)

    def parse_line_list(self, filepath):
        lines = []
        if not filepath or not os.path.exists(filepath):
            return lines
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith(';'):
                        continue
                    parts = line.split(',')
                    if len(parts) >= 2:
                        try:
                            wl = float(parts[0].strip())
                            name = parts[1].strip()
                            lines.append((wl, name))
                        except ValueError:
                            continue
        except Exception as e:
            print(f"Error parsing line list {filepath}: {e}")
        return lines

    def on_linelist_selection_changed(self):
        text = self.combo_linelist.currentText()
        if text in self.linelist_files:
            filepath = self.linelist_files[text]
            self.loaded_lines = self.parse_line_list(filepath)
            self.update_line_overlays()

    def browse_custom_linelist(self):
        data_dir = self.get_data_dir()
        if data_dir and data_dir.exists():
            initial_dir = str(data_dir)
        else:
            initial_dir = QDir.homePath()

        filepath, _ = QFileDialog.getOpenFileName(
            self, "Select Spectral Line List CSV", initial_dir, "CSV / Text Files (*.csv *.txt);;All Files (*)"
        )
        if filepath:
            name = os.path.basename(filepath)
            self.linelist_files[name] = filepath
            idx = self.combo_linelist.findText("Load Custom CSV...")
            if idx >= 0:
                self.combo_linelist.insertItem(idx, name)
                self.combo_linelist.setCurrentIndex(idx)
            else:
                self.combo_linelist.addItem(name)
                self.combo_linelist.setCurrentText(name)
            self.loaded_lines = self.parse_line_list(filepath)
            self.update_line_overlays()
        else:
            if self.combo_linelist.currentText() == "Load Custom CSV..." and self.combo_linelist.count() > 1:
                self.combo_linelist.setCurrentIndex(0)

    def wavelength_to_pixel(self, wavelengths_um):
        if self.image_viewer is None or getattr(self.image_viewer, 'wcs', None) is None:
            return None
        if getattr(self.image_viewer, 'wcs_z_idx', None) is None:
            return None

        wcs = self.image_viewer.wcs
        z_idx = self.image_viewer.wcs_z_idx

        try:
            cunit = str(wcs.wcs.cunit[z_idx]).strip().lower()
        except Exception:
            cunit = "m"

        if cunit == 'm':
            scale = 1e-6
        elif cunit in ['um', 'micron', 'microns', 'µm']:
            scale = 1.0
        elif cunit == 'nm':
            scale = 1e3
        elif cunit in ['angstrom', 'a', 'angstroms']:
            scale = 1e4
        else:
            scale = 1e-6

        wls_wcs = np.array(wavelengths_um) * scale
        n_lines = len(wls_wcs)
        coords_world = np.zeros((n_lines, wcs.naxis))

        if hasattr(self, 'world_axis') and self.world_axis.fixed_coords is not None:
            for i in range(wcs.naxis):
                if i == z_idx:
                    coords_world[:, i] = wls_wcs
                else:
                    coords_world[:, i] = self.world_axis.fixed_coords[i]
        else:
            ref_pix = np.zeros((1, wcs.naxis))
            ref_world = wcs.wcs_pix2world(ref_pix, 0)[0]
            for i in range(wcs.naxis):
                if i == z_idx:
                    coords_world[:, i] = wls_wcs
                else:
                    coords_world[:, i] = ref_world[i]

        try:
            pix_coords = wcs.wcs_world2pix(coords_world, 0)[:, z_idx]
            return pix_coords
        except Exception:
            return None

    def clear_line_overlays(self):
        for item in self.line_items:
            try:
                if isinstance(item, tuple):
                    line_item, text_item = item
                    self.plot_widget.removeItem(line_item)
                    self.plot_widget.removeItem(text_item)
                else:
                    self.plot_widget.removeItem(item)
            except Exception:
                pass
        self.line_items.clear()

    def update_line_overlays(self):
        if not hasattr(self, 'chk_enable_lines'):
            return

        plot_type = self.combo_type.currentText()
        lines_enabled = self.chk_enable_lines.isChecked() and (plot_type == "Depth Plot")

        if not lines_enabled or not self.loaded_lines or self.image_viewer is None or getattr(self.image_viewer, 'wcs', None) is None:
            self.clear_line_overlays()
            if hasattr(self, 'lbl_line_info'):
                if not self.chk_enable_lines.isChecked():
                    self.lbl_line_info.setText("")
                elif plot_type != "Depth Plot":
                    self.lbl_line_info.setText("Line list only available in Depth Plot mode.")
                elif getattr(self.image_viewer, 'wcs', None) is None:
                    self.lbl_line_info.setText("No WCS present for wavelength mapping.")
            return

        z_idx = getattr(self.image_viewer, 'wcs_z_idx', None)
        if z_idx is not None:
            ctype_raw = str(self.image_viewer.wcs.wcs.ctype[z_idx]).upper()
            if 'WAVE' not in ctype_raw and 'AWAV' not in ctype_raw:
                self.clear_line_overlays()
                self.lbl_line_info.setText("Z-axis is not Wavelength.")
                return

        use_wavelength_x = hasattr(self, 'current_wavelengths') and self.current_wavelengths is not None and len(self.current_wavelengths) > 0

        view_box = self.plot_widget.getViewBox()
        (view_x_min, view_x_max), (view_y_min, view_y_max) = view_box.viewRange()

        if view_x_min == 0.0 and view_x_max == 1.0 and hasattr(self, 'plot_data'):
            x_data, _ = self.plot_data.getData()
            if x_data is not None and len(x_data) > 1:
                view_x_min, view_x_max = float(x_data[0]), float(x_data[-1])

        visible_lines = []
        if use_wavelength_x:
            for wl_um, name in self.loaded_lines:
                if view_x_min <= wl_um <= view_x_max:
                    visible_lines.append((wl_um, name, wl_um))
        else:
            wls_um = [item[0] for item in self.loaded_lines]
            pix_coords = self.wavelength_to_pixel(wls_um)
            if pix_coords is None:
                self.clear_line_overlays()
                self.lbl_line_info.setText("WCS conversion failed.")
                return
            # strict=: pix_coords is derived one-for-one from loaded_lines, so a
            # length mismatch is a bug, not something to silently truncate.
            for (wl_um, name), x_px in zip(self.loaded_lines, pix_coords, strict=True):
                if view_x_min <= x_px <= view_x_max:
                    visible_lines.append((x_px, name, wl_um))

        num_needed = len(visible_lines)

        while len(self.line_items) > num_needed:
            item = self.line_items.pop()
            try:
                if isinstance(item, tuple):
                    line_item, text_item = item
                    self.plot_widget.removeItem(line_item)
                    self.plot_widget.removeItem(text_item)
                else:
                    self.plot_widget.removeItem(item)
            except Exception:
                pass

        pen = pg.mkPen(color=(0, 100, 220), style=Qt.PenStyle.DotLine, width=1.5)
        stagger_levels = [0.08, 0.22, 0.36, 0.50]
        last_x = -9999.0
        level = 0
        y_span = view_y_max - view_y_min
        min_spacing = 0.005 * (view_x_max - view_x_min) if use_wavelength_x else 18.0

        for idx, (x_pos, name, _wl_um) in enumerate(visible_lines):
            if abs(x_pos - last_x) < min_spacing:
                level = (level + 1) % len(stagger_levels)
            else:
                level = 0
            last_x = x_pos

            pos_val = stagger_levels[level]
            y_pos = view_y_min + pos_val * y_span

            html_content = latex_to_html(name)
            html_text = f'<span style="color: rgb(0, 70, 180); font-size: 12pt; font-weight: bold;">{html_content}</span>'

            if idx < len(self.line_items):
                line_item, text_item = self.line_items[idx]
                line_item.setPos(x_pos)
                line_item.setVisible(True)

                text_item.setHtml(html_text)
                text_item.setAngle(90)
                text_item.setPos(x_pos, y_pos)
                text_item.setVisible(True)
            else:
                line_item = pg.InfiniteLine(pos=x_pos, angle=90, pen=pen)
                text_item = pg.TextItem(html=html_text, anchor=(0.0, 0.5))
                line_item.dataBounds = lambda ax, *args, **kwargs: (None, None)
                text_item.dataBounds = lambda ax, *args, **kwargs: (None, None)
                text_item.setAngle(90)
                text_item.setPos(x_pos, y_pos)

                self.plot_widget.addItem(line_item)
                self.plot_widget.addItem(text_item)
                self.line_items.append((line_item, text_item))

        self.lbl_line_info.setText(f"{len(visible_lines)} line(s) visible (out of {len(self.loaded_lines)} total)")
        
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

        if self.chk_enable_bg.isChecked() and self.bg_roi is not None:
            bg_pos = self.bg_roi.pos()
            bg_size = self.bg_roi.size()
            self.remove_bg_roi()
            pen = pg.mkPen((255, 140, 0), width=3)
            hover_pen = pg.mkPen((255, 140, 0), width=5)
            if shape == "Circle":
                self.bg_roi = pg.CircleROI(bg_pos, bg_size, pen=pen, hoverPen=hover_pen)
            else:
                self.bg_roi = pg.RectROI(bg_pos, bg_size, pen=pen, hoverPen=hover_pen)
                self.bg_roi.addScaleHandle([1, 1], [0, 0])
                self.bg_roi.addScaleHandle([0, 0], [1, 1])
            img_item = self.image_viewer.imv.getImageItem()
            if img_item:
                self.bg_roi.setParentItem(img_item)
            else:
                self.image_viewer.imv.getView().addItem(self.bg_roi)
            self.bg_roi.sigRegionChanged.connect(self.on_bg_roi_changed)

        self.update_plot()

    def update_plot(self):
        if self.image_viewer is None or self.image_viewer.transposed_data is None:
            return
            
        if self.image_viewer.transposed_data.ndim != 3:
            return
            
        plot_type = self.combo_type.currentText()
        calc_method = self.combo_calc.currentText()

        if hasattr(self, 'group_bg'):
            self.group_bg.setEnabled(plot_type == "Depth Plot")
        if hasattr(self, 'group_linelist'):
            self.group_linelist.setEnabled(plot_type == "Depth Plot")
            
        # Transform the 3D cube to match the display coordinates (rotation, flip)
        cube = self.image_viewer.apply_spatial_transforms(self.image_viewer.transposed_data)

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
        
        if plot_type == "Depth Plot":
            region = cube[:, x0:x1, y0:y1].astype(float, copy=True)
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

            bg_spectrum = None
            subtracted_spectrum = None

            if self.chk_enable_bg.isChecked() and self.bg_roi is not None:
                bg_pos = self.bg_roi.pos()
                bg_size = self.bg_roi.size()
                bg_x0, bg_y0 = int(bg_pos.x()), int(bg_pos.y())
                bg_w, bg_h = int(bg_size.x()), int(bg_size.y())

                bg_x0 = max(0, min(bg_x0, x_len-1))
                bg_y0 = max(0, min(bg_y0, y_len-1))
                bg_x1 = max(bg_x0+1, min(bg_x0+bg_w, x_len))
                bg_y1 = max(bg_y0+1, min(bg_y0+bg_h, y_len))

                bg_region = cube[:, bg_x0:bg_x1, bg_y0:bg_y1].astype(float, copy=True)
                if bg_region.size > 0:
                    if self.combo_shape.currentText() == "Circle":
                        yy_bg, xx_bg = np.mgrid[:(bg_x1-bg_x0), :(bg_y1-bg_y0)]
                        cx_bg, cy_bg = (bg_x1-bg_x0)/2.0 - 0.5, (bg_y1-bg_y0)/2.0 - 0.5
                        r_bg = min((bg_x1-bg_x0)/2.0, (bg_y1-bg_y0)/2.0)
                        mask_bg = ((xx_bg - cy_bg)**2 + (yy_bg - cx_bg)**2) <= r_bg**2
                        bg_region = np.where(mask_bg, bg_region, np.nan)

                    bg_calc_method = self.combo_bg_calc.currentText()
                    if bg_calc_method == "Average":
                        bg_spectrum = np.nanmean(bg_region, axis=(1, 2))
                    elif bg_calc_method == "Median":
                        bg_spectrum = np.nanmedian(bg_region, axis=(1, 2))
                    else:
                        bg_spectrum = np.nansum(bg_region, axis=(1, 2))

                    # Option B: Subtract background spectrum from each pixel's spectrum in the source data
                    subtracted_region = region - bg_spectrum[:, None, None]

                    if calc_method == "Average":
                        subtracted_spectrum = np.nanmean(subtracted_region, axis=(1, 2))
                    elif calc_method == "Median":
                        subtracted_spectrum = np.nanmedian(subtracted_region, axis=(1, 2))
                    else:
                        subtracted_spectrum = np.nansum(subtracted_region, axis=(1, 2))
                
            x_axis = np.arange(z_len)
            wavelengths = None
            cunit = ""
            ctype = ""
            
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
                    cunit = "µm"
                
                cx, cy = x0 + w/2.0, y0 + h/2.0
                
                # Un-flip and un-rotate to get coords in transposed_data space
                k = self.image_viewer.rot_angle // 90
                for _ in range((4 - k) % 4):
                    cx, cy = cy, x_len - 1 - cx
                
                if self.image_viewer.flip:
                    cx = x_len - 1 - cx
                
                current_x_axis = getattr(self.image_viewer, 'current_x_axis', 'AXIS 3')
                current_y_axis = getattr(self.image_viewer, 'current_y_axis', 'AXIS 2')
                x_idx = int(current_x_axis.split()[-1]) - 1
                y_idx = int(current_y_axis.split()[-1]) - 1
                    
                fixed_coords = np.zeros((z_len, wcs.naxis))
                if wcs.naxis > max(x_idx, y_idx):
                    fixed_coords[:, x_idx] = cx
                    fixed_coords[:, y_idx] = cy
                fixed_coords[:, z_idx] = np.arange(z_len)

                try:
                    world = wcs.wcs_pix2world(fixed_coords, 0)
                    wavelengths = world[:, z_idx]
                    try:
                        orig_cunit = str(wcs.wcs.cunit[z_idx]).strip().lower()
                        if orig_cunit == 'm':
                            wavelengths = wavelengths * 1e6
                    except Exception:
                        pass
                except Exception as e:
                    print(f"Warning: WCS pixel_to_world failed in DepthPlotDialog: {e}")
                    wavelengths = None

            if wavelengths is not None and len(wavelengths) == z_len:
                x_axis = wavelengths
                self.current_wavelengths = wavelengths
                self.current_wavelength_unit = cunit
                
                label = "Wavelength" if 'WAVE' in ctype else ctype
                unit_str = f" ({cunit})" if cunit else ""
                self.plot_widget.getAxis('bottom').setLabel(f"{label}{unit_str}")
                
                self.top_axis.wavelengths = wavelengths
                self.plot_widget.showAxis('top')
                self.plot_widget.getAxis('top').setLabel("Slice Index (pixels)")
            else:
                x_axis = np.arange(z_len)
                self.current_wavelengths = None
                self.current_wavelength_unit = ""
                self.plot_widget.setLabel('bottom', "Slice Index (pixels)")
                self.top_axis.wavelengths = None
                self.plot_widget.hideAxis('top')
            
            mult = self.image_viewer.data_multiplier
            self.plot_data.setData(x_axis, spectrum * mult)

            if bg_spectrum is not None and subtracted_spectrum is not None:
                self.plot_bg.setData(x_axis, bg_spectrum * mult)
                self.plot_sub.setData(x_axis, subtracted_spectrum * mult)
            else:
                self.plot_bg.setData([], [])
                self.plot_sub.setData([], [])
            
        elif plot_type == "Horizontal Cut":
            # Cut the plane that is actually on screen: in Boxcar or Z Range mode that is a
            # collapsed plane which exists in no single channel of the cube (B17).
            plane = self.image_viewer.current_plane()
            if plane is None or plane.ndim != 2:
                return
            region = plane[x0:x1, y0:y1]
            if region.size == 0:
                return
            if calc_method == "Average":
                cut = np.nanmean(region, axis=1) # collapse Y
            elif calc_method == "Median":
                cut = np.nanmedian(region, axis=1)
            else:
                cut = np.nansum(region, axis=1)
                
            self.top_axis.wavelengths = None
            self.plot_widget.hideAxis('top')
            self.current_wavelengths = None
            self.current_wavelength_unit = ""
                
            x_axis = np.arange(x0, x1)
            self.plot_widget.setLabel('bottom', "X Pixel")
            unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
            self.plot_widget.setLabel('left', f"Intensity ({unit})")
            self.plot_data.setData(x_axis, cut * self.image_viewer.data_multiplier)
            self.plot_bg.setData([], [])
            self.plot_sub.setData([], [])
            
        elif plot_type == "Vertical Cut":
            plane = self.image_viewer.current_plane()
            if plane is None or plane.ndim != 2:
                return
            region = plane[x0:x1, y0:y1]
            if region.size == 0:
                return
            if calc_method == "Average":
                cut = np.nanmean(region, axis=0) # collapse X
            elif calc_method == "Median":
                cut = np.nanmedian(region, axis=0)
            else:
                cut = np.nansum(region, axis=0)
                
            self.top_axis.wavelengths = None
            self.plot_widget.hideAxis('top')
            self.current_wavelengths = None
            self.current_wavelength_unit = ""
            x_axis = np.arange(y0, y1)
            self.plot_widget.setLabel('bottom', "Y Pixel")
            unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
            self.plot_widget.setLabel('left', f"Intensity ({unit})")
            self.plot_data.setData(x_axis, cut * self.image_viewer.data_multiplier)
            self.plot_sub.setData([], [])

        self.update_line_overlays()
