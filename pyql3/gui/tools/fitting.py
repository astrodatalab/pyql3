import numpy as np
from scipy.optimize import curve_fit
from PySide6.QtWidgets import QGridLayout, QLabel, QComboBox, QPushButton, QSpinBox, QVBoxLayout, QDialog, QHBoxLayout
import pyqtgraph as pg
from pyql3.core import coords
from pyql3.gui.tools.base_tool import BaseToolDialog, as_center

def gaussian_2d(xy, amplitude, xo, yo, wx, wy, theta, offset):
    x, y = xy
    xo = float(xo)
    yo = float(yo)    
    # The expansion of (x_rot / wx)**2 / 2 + (y_rot / wy)**2 / 2 with the same rotation as
    # `lorentzian_2d` and `moffat_2d`: theta counter-clockwise from +X, wx along theta.
    # The opposite sign of b mirrored the reported angle (R72).
    a = (np.cos(theta)**2)/(2*wx**2) + (np.sin(theta)**2)/(2*wy**2)
    b = (np.sin(2*theta))/(4*wx**2) - (np.sin(2*theta))/(4*wy**2)
    c = (np.sin(theta)**2)/(2*wx**2) + (np.cos(theta)**2)/(2*wy**2)
    g = offset + amplitude*np.exp( - (a*((x-xo)**2) + 2*b*(x-xo)*(y-yo) + c*((y-yo)**2)))
    return g.ravel()

def lorentzian_2d(xy, amplitude, xo, yo, wx, wy, theta, offset):
    x, y = xy
    xo = float(xo)
    yo = float(yo)
    x_rot = (x - xo) * np.cos(theta) + (y - yo) * np.sin(theta)
    y_rot = -(x - xo) * np.sin(theta) + (y - yo) * np.cos(theta)
    l = offset + amplitude / (1 + (x_rot / wx)**2 + (y_rot / wy)**2)
    return l.ravel()

def moffat_2d(xy, amplitude, xo, yo, wx, wy, theta, offset):
    beta = 2.5
    x, y = xy
    xo = float(xo)
    yo = float(yo)
    x_rot = (x - xo) * np.cos(theta) + (y - yo) * np.sin(theta)
    y_rot = -(x - xo) * np.sin(theta) + (y - yo) * np.cos(theta)
    m = offset + amplitude * (1 + (x_rot / wx)**2 + (y_rot / wy)**2)**(-beta)
    return m.ravel()

class DisplayPeakFitDialog(QDialog):
    """The box's pixels, the fitted model and data minus model, as three flat images.

    Drawn with pyqtgraph's 2D items, so it needs no OpenGL: the 3D surfaces it replaced failed
    wherever OpenGL 2.1 is missing, CI's Linux runner included. Each image sits at the box's own
    pixel coordinates (orig, the same as the fit's Center X and Y), and the three views pan and
    zoom together. Data and model share one colour scale so they compare by eye; the residual
    uses a diverging map centred on zero.
    """

    def __init__(self, parent=None, raw_data=None, fit_data=None, x0=0, y0=0):
        super().__init__(parent)
        self.setWindowTitle("Display Peak Fit")
        self.resize(1000, 380)

        residual = raw_data - fit_data
        shared = (float(min(raw_data.min(), fit_data.min())),
                  float(max(raw_data.max(), fit_data.max())))
        if shared[0] == shared[1]:
            shared = (shared[0] - 0.5, shared[1] + 0.5)
        spread = float(np.abs(residual).max()) or 1.0

        layout = QHBoxLayout(self)
        self.images = {}
        first = None
        panels = [("data", "Data", raw_data, shared, "viridis"),
                  ("model", "Model", fit_data, shared, "viridis"),
                  ("residual", "Residual (data − model)", residual, (-spread, spread),
                   "CET-D1")]
        for key, title, values, levels, cmap_name in panels:
            plot = pg.PlotWidget(title=title)
            plot.setAspectLocked(True)
            plot.setLabel('bottom', "X (pixel)")
            plot.setLabel('left', "Y (pixel)")
            item = pg.ImageItem(values, levels=levels)
            item.setRect(x0, y0, values.shape[0], values.shape[1])
            plot.addItem(item)
            colormap = pg.colormap.get(cmap_name)
            bar = pg.ColorBarItem(values=levels, colorMap=colormap, interactive=False)
            bar.setImageItem(item, insert_in=plot.getPlotItem())
            if first is None:
                first = plot
            else:
                plot.setXLink(first)
                plot.setYLink(first)
            layout.addWidget(plot)
            self.images[key] = item


class GaussianFitDialog(BaseToolDialog):
    #: The labels a fit fills in, as opposed to the two that describe the box's pixels.
    FIT_KEYS = ["Peak Amplitude", "Center X", "Center Y", "FWHM X", "FWHM Y", "Angle (deg)",
                "Background"]

    def __init__(self, parent=None, image_viewer=None, initial_center=None):
        super().__init__(parent, image_viewer, "Peak Fit")
        # A Qt signal may hand us its `checked` flag instead of a centre
        initial_center = as_center(initial_center)
        self.resize(350, 450)
        
        self.setup_draw_button(self.layout)
        
        grid = QGridLayout()
        self.layout.addLayout(grid)
        
        self.spin_x0 = QSpinBox()
        self.spin_x1 = QSpinBox()
        self.spin_y0 = QSpinBox()
        self.spin_y1 = QSpinBox()
        
        self._updating_spins = False
        
        for spin in [self.spin_x0, self.spin_x1, self.spin_y0, self.spin_y1]:
            spin.setRange(0, 10000)
            spin.valueChanged.connect(self.on_spin_changed)
            
        grid.addWidget(QLabel("X Range:"), 0, 0)
        grid.addWidget(self.spin_x0, 0, 1)
        grid.addWidget(QLabel("to"), 0, 2)
        grid.addWidget(self.spin_x1, 0, 3)
        
        grid.addWidget(QLabel("Y Range:"), 1, 0)
        grid.addWidget(self.spin_y0, 1, 1)
        grid.addWidget(QLabel("to"), 1, 2)
        grid.addWidget(self.spin_y1, 1, 3)
        
        labels = ["Min Pixel Value", "Max Pixel Value", "Peak Amplitude", "Center X", "Center Y", "FWHM X", "FWHM Y", "Angle (deg)", "Background"]
        self.value_labels = {}
        for i, text in enumerate(labels):
            row = i + 2
            grid.addWidget(QLabel(f"{text}:"), row, 0, 1, 2)
            val = QLabel("N/A")
            grid.addWidget(val, row, 2, 1, 2)
            self.value_labels[text] = val
            
        row = len(labels) + 2
        grid.addWidget(QLabel("Fit Type:"), row, 0, 1, 2)
        self.combo_type = QComboBox()
        self.combo_type.addItems(["Gaussian", "Lorentzian", "Moffat"])
        self.combo_type.currentIndexChanged.connect(self.update_fit)
        grid.addWidget(self.combo_type, row, 2, 1, 2)
        
        row += 1
        self.btn_display = QPushButton("Display Peak Fit")
        self.btn_display.clicked.connect(self.display_peak_fit)
        grid.addWidget(self.btn_display, row, 0, 1, 2)
        
        self.btn_recalc = QPushButton("Recalculate Peak Fit")
        self.btn_recalc.clicked.connect(self.update_fit)
        grid.addWidget(self.btn_recalc, row, 2, 1, 2)

        # A box that misses the image has nothing to fit, and says so here.
        row += 1
        self.lbl_status = QLabel("")
        self.layout.addWidget(self.lbl_status)
        self.lbl_status.setStyleSheet("color: #b00;")
        
        if initial_center is not None:
            center_x, center_y = initial_center
        elif self.image_viewer and self.image_viewer.display_data is not None:
            shape = self.image_viewer.display_data.shape
            if len(shape) == 3:
                shape = (shape[1], shape[2])
            center_x, center_y = shape[0]//2, shape[1]//2
        else:
            center_x, center_y = 2, 2
            
        roi = pg.RectROI([center_x - 2, center_y - 2], [5, 5], pen=pg.mkPen((255, 165, 0), width=3), hoverPen=pg.mkPen((255, 165, 0), width=5))
        roi.addScaleHandle([1, 1], [0, 0])
        roi.addScaleHandle([0, 0], [1, 1])
        self.add_roi_to_viewer(roi)
        
        self.last_raw_data = None
        self.last_fit_data = None
        self.last_fit_origin = (0, 0)
        
        self.update_fit()

    def tear_down(self):
        if self._torn_down:
            return
        # Qt does not hide a child window with its parent, so a Display Peak Fit window stayed
        # on screen after Peak Fit closed, until the next Peak Fit deleted it with this dialog.
        for display in self.findChildren(DisplayPeakFitDialog):
            display.close()
        super().tear_down()

    def set_center(self, center):
        center = as_center(center)
        if center is None or self.roi is None:
            return
        cx, cy = coords.index_to_item(center[0]), coords.index_to_item(center[1])
        w = self.roi.size().x()
        h = self.roi.size().y()
        self.roi.setPos([cx - w / 2.0, cy - h / 2.0])
        self.on_roi_changed()
        
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
        self.update_fit()
        
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
        
        self.update_fit()
        
    def update_stats(self):
        self.update_fit()

    def _clear_fit(self, reason):
        """Blank every result and say why there is no fit."""
        for key in ["Min Pixel Value", "Max Pixel Value", *self.FIT_KEYS]:
            self.value_labels[key].setText("N/A")
        self.lbl_status.setText(reason)
        self.last_raw_data = None
        self.last_fit_data = None

    def update_fit(self):
        if self.image_viewer is None or self.image_viewer.display_data is None:
            return
            
        measured = self.roi_plane_pixels()
        if measured is None:
            self._clear_fit("The box does not overlap the image.")
            return
        data, (x_slice, y_slice) = measured
        if data.size < 9:
            # Fewer pixels than the fit has free parameters to be worth reporting; the
            # previous numbers must not stay on screen as if they described this box.
            self._clear_fit(f"Only {data.size} pixel(s) of the box are on the image; "
                            "the fit needs at least 9.")
            return
        self.lbl_status.setText("")

        if np.isnan(data).any():
            data = np.nan_to_num(data, nan=np.nanmedian(data))
            
        self.last_raw_data = data
        self.last_fit_origin = (x_slice.start, y_slice.start)

        self.value_labels["Min Pixel Value"].setText(f"{data.min():.4f}")
        self.value_labels["Max Pixel Value"].setText(f"{data.max():.4f}")
            
        nx, ny = data.shape[0], data.shape[1]
        x = np.arange(0, nx)
        y = np.arange(0, ny)
        x, y = np.meshgrid(x, y, indexing='ij')
        
        amplitude = data.max() - data.min()
        offset = data.min()
        xo = nx / 2.0
        yo = ny / 2.0
        sigma_x = nx / 4.0
        sigma_y = ny / 4.0
        theta = 0.0
        
        initial_guess = (amplitude, xo, yo, sigma_x, sigma_y, theta, offset)
        bounds = (
            (0, 0, 0, 0.1, 0.1, -np.pi, -np.inf),
            (np.inf, nx, ny, nx, ny, np.pi, np.inf)
        )
        
        fit_type = self.combo_type.currentText()
        if fit_type == "Gaussian":
            func = gaussian_2d
            # FWHM = 2 * sqrt(2 * ln(2)) * sigma
            fwhm_factor = 2.35482 
        elif fit_type == "Lorentzian":
            func = lorentzian_2d
            # FWHM = 2 * gamma
            fwhm_factor = 2.0
        else:
            func = moffat_2d
            # FWHM = 2 * alpha * sqrt(2^(1/beta) - 1)
            # beta = 2.5 -> sqrt(2^(1/2.5) - 1) = sqrt(1.3195 - 1) = sqrt(0.3195) = 0.565
            # FWHM = 2 * 0.565 * alpha = 1.13 * alpha
            fwhm_factor = 1.13
            
        try:
            popt, _ = curve_fit(func, (x, y), data.ravel(), p0=initial_guess, bounds=bounds)
            amp, xo, yo, wx, wy, th, off = popt
            
            fwhm_x = abs(wx) * fwhm_factor
            fwhm_y = abs(wy) * fwhm_factor
            
            global_xo = x_slice.start + xo
            global_yo = y_slice.start + yo
            
            self.value_labels["Peak Amplitude"].setText(f"{amp:.4f}")
            self.value_labels["Center X"].setText(f"{global_xo:.2f}")
            self.value_labels["Center Y"].setText(f"{global_yo:.2f}")
            self.value_labels["FWHM X"].setText(f"{fwhm_x:.2f}")
            self.value_labels["FWHM Y"].setText(f"{fwhm_y:.2f}")
            self.value_labels["Angle (deg)"].setText(f"{np.degrees(th):.1f}")
            self.value_labels["Background"].setText(f"{off:.4f}")
            
            # The grid is (nx, ny), the shape of `data`.
            self.last_fit_data = func((x, y), *popt).reshape(data.shape)
            
        except RuntimeError:
            for key in self.FIT_KEYS:
                self.value_labels[key].setText("Fit Failed")
            self.last_fit_data = None

    def display_peak_fit(self):
        if self.last_raw_data is None or self.last_fit_data is None:
            return
            
        x0, y0 = self.last_fit_origin
        self.disp_dialog = DisplayPeakFitDialog(self, self.last_raw_data, self.last_fit_data, x0, y0)
        self.disp_dialog.show()
