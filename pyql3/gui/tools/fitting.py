import numpy as np
from scipy.optimize import curve_fit
from PySide6.QtWidgets import QGridLayout, QLabel, QComboBox, QPushButton, QSpinBox, QVBoxLayout, QDialog, QHBoxLayout
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from pyql3.gui.tools.base_tool import BaseToolDialog

def gaussian_2d(xy, amplitude, xo, yo, wx, wy, theta, offset):
    x, y = xy
    xo = float(xo)
    yo = float(yo)    
    a = (np.cos(theta)**2)/(2*wx**2) + (np.sin(theta)**2)/(2*wy**2)
    b = -(np.sin(2*theta))/(4*wx**2) + (np.sin(2*theta))/(4*wy**2)
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
    def __init__(self, parent=None, raw_data=None, fit_data=None, x0=0, y0=0):
        super().__init__(parent)
        self.setWindowTitle("Display Peak Fit")
        self.resize(800, 400)
        
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QHBoxLayout
        layout = QHBoxLayout(self)
        
        try:
            # Raw Data Surface
            self.gl_raw = gl.GLViewWidget()
            layout.addWidget(self.gl_raw)
        except Exception as e:
            lbl = QLabel(f"3D Peak Fit View requires OpenGL support.\n\nOpenGL initialization message: {e}")
            lbl.setWordWrap(True)
            layout.addWidget(lbl)
            return
        
        raw_label = QLabel("Raw Data", self.gl_raw)
        raw_label.setStyleSheet("color: white; font-weight: bold; font-size: 16px; background: transparent;")
        raw_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        raw_vbox = QVBoxLayout(self.gl_raw)
        raw_vbox.setContentsMargins(0, 10, 0, 0)
        raw_vbox.addWidget(raw_label)
        raw_vbox.addStretch()
        
        self.gl_raw.opts['distance'] = max(raw_data.shape) * 1.5
        
        z_raw = raw_data
        z_min = z_raw.min()
        
        surface_raw = gl.GLSurfacePlotItem(z=z_raw, color=(0.3, 0.5, 1.0, 0.8), shader='shaded', computeNormals=True)
        surface_raw.translate(-z_raw.shape[0]/2, -z_raw.shape[1]/2, -z_min)
        self.gl_raw.addItem(surface_raw)
        
        ax_raw_x = z_raw.shape[0]/1.5
        ax_raw_y = z_raw.shape[1]/1.5
        ax_raw_z = (z_raw.max() - z_min)/1.5
        
        ax_raw = gl.GLAxisItem()
        ax_raw.setSize(ax_raw_x, ax_raw_y, ax_raw_z)
        self.gl_raw.addItem(ax_raw)
        
        # Fit Data Surface
        self.gl_fit = gl.GLViewWidget()
        layout.addWidget(self.gl_fit)
        
        fit_label = QLabel("Mathematical Fit", self.gl_fit)
        fit_label.setStyleSheet("color: white; font-weight: bold; font-size: 16px; background: transparent;")
        fit_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fit_vbox = QVBoxLayout(self.gl_fit)
        fit_vbox.setContentsMargins(0, 10, 0, 0)
        fit_vbox.addWidget(fit_label)
        fit_vbox.addStretch()
        
        self.gl_fit.opts['distance'] = max(fit_data.shape) * 1.5
        
        z_fit = fit_data
        surface_fit = gl.GLSurfacePlotItem(z=z_fit, color=(1.0, 0.3, 0.3, 0.8), shader='shaded', computeNormals=True)
        surface_fit.translate(-z_fit.shape[0]/2, -z_fit.shape[1]/2, -z_min)
        self.gl_fit.addItem(surface_fit)
        
        ax_fit_x = z_fit.shape[0]/1.5
        ax_fit_y = z_fit.shape[1]/1.5
        ax_fit_z = (z_fit.max() - z_min)/1.5
        
        ax_fit = gl.GLAxisItem()
        ax_fit.setSize(ax_fit_x, ax_fit_y, ax_fit_z)
        self.gl_fit.addItem(ax_fit)

class GaussianFitDialog(BaseToolDialog):
    def __init__(self, parent=None, image_viewer=None, initial_center=None):
        super().__init__(parent, image_viewer, "Peak Fit")
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
        
        self.update_fit()
        
    def set_center(self, center):
        if center is None or self.roi is None:
            return
        cx, cy = center
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

    def update_fit(self):
        if self.image_viewer is None or self.image_viewer.display_data is None:
            return
            
        img_data = self.image_viewer.display_data
        if img_data.ndim == 3:
            img_data = img_data[self.image_viewer.imv.currentIndex]
            
        data = self.roi.getArrayRegion(img_data, self.image_viewer.imv.getImageItem())
        if data is None or data.size < 9:
            return
            
        if np.isnan(data).any():
            data = np.nan_to_num(data, nan=np.nanmedian(data))
            
        self.last_raw_data = data
            
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
            
            pos = self.roi.pos()
            global_xo = pos.x() + xo
            global_yo = pos.y() + yo
            
            self.value_labels["Peak Amplitude"].setText(f"{amp:.4f}")
            self.value_labels["Center X"].setText(f"{global_xo:.2f}")
            self.value_labels["Center Y"].setText(f"{global_yo:.2f}")
            self.value_labels["FWHM X"].setText(f"{fwhm_x:.2f}")
            self.value_labels["FWHM Y"].setText(f"{fwhm_y:.2f}")
            self.value_labels["Angle (deg)"].setText(f"{np.degrees(th):.1f}")
            self.value_labels["Background"].setText(f"{off:.4f}")
            
            self.last_fit_data = func((x, y), *popt).reshape(ny, nx)
            
        except RuntimeError:
            for key in ["Peak Amplitude", "Center X", "Center Y", "FWHM X", "FWHM Y", "Angle (deg)", "Background"]:
                self.value_labels[key].setText("Fit Failed")
            self.last_fit_data = None

    def display_peak_fit(self):
        if self.last_raw_data is None or self.last_fit_data is None:
            return
            
        self.disp_dialog = DisplayPeakFitDialog(self, self.last_raw_data, self.last_fit_data)
        self.disp_dialog.show()
