import numpy as np
from PySide6.QtWidgets import QGridLayout, QLabel, QDoubleSpinBox, QVBoxLayout
import pyqtgraph as pg
from pyql3.gui.tools.base_tool import BaseToolDialog
from pyql3.analysis.strehl import calculate_strehl

class StrehlDialog(BaseToolDialog):
    def __init__(self, parent=None, image_viewer=None):
        super().__init__(parent, image_viewer, "Strehl Ratio")
        self.resize(500, 500)
        
        main_layout = QVBoxLayout()
        self.layout.addLayout(main_layout)
        
        grid = QGridLayout()
        main_layout.addLayout(grid)
        self.setup_draw_button(self.layout)
        
        self.spin_photrad = QDoubleSpinBox()
        self.spin_photrad.setValue(0.5)
        self.spin_photrad.setRange(0.01, 10.0)
        self.spin_photrad.setSingleStep(0.1)
        self.spin_photrad.valueChanged.connect(self.update_strehl)
        
        grid.addWidget(QLabel("Photometric Radius (\"):"), 0, 0)
        grid.addWidget(self.spin_photrad, 0, 1)
        
        self.lbl_strehl = QLabel("N/A")
        self.lbl_fwhm = QLabel("N/A")
        self.lbl_psf_fwhm = QLabel("N/A")
        
        grid.addWidget(QLabel("Strehl Ratio:"), 1, 0)
        grid.addWidget(self.lbl_strehl, 1, 1)
        grid.addWidget(QLabel("Star FWHM (\"):"), 2, 0)
        grid.addWidget(self.lbl_fwhm, 2, 1)
        grid.addWidget(QLabel("Theoretical FWHM (\"):"), 3, 0)
        grid.addWidget(self.lbl_psf_fwhm, 3, 1)
        
        # Plot widget for profiles
        self.plot_widget = pg.PlotWidget(title="Radial Profile (Normalized)")
        unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
        self.plot_widget.setLabel('left', f'Intensity ({unit})')
        self.plot_widget.setLabel('bottom', 'Radius (arcsec)')
        self.plot_widget.addLegend(offset=(-30, 30), brush=(50, 50, 50, 200), pen='w')
        self.star_plot = self.plot_widget.plot([], [], pen=None, symbol='o', symbolPen='y', symbolBrush='y', symbolSize=5, name="Star")
        self.psf_plot = self.plot_widget.plot([], [], pen=None, symbol='t', symbolPen='c', symbolBrush='c', symbolSize=5, name="Theoretical PSF")
        main_layout.addWidget(self.plot_widget)
        
        if self.image_viewer and self.image_viewer.display_data is not None:
            shape = self.image_viewer.display_data.shape
            if len(shape) == 3:
                center_x, center_y = shape[1]//2, shape[2]//2
            else:
                center_x, center_y = shape[0]//2, shape[1]//2
        else:
            center_x, center_y = 0, 0
            
        r = 10.0
        self.roi = pg.CircleROI([center_x - r, center_y - r], [r*2, r*2], pen=pg.mkPen((0, 255, 0), width=3), hoverPen=pg.mkPen((0, 255, 0), width=5))
        self.add_roi_to_viewer(self.roi)
        
        self.update_strehl()
        
    def on_roi_changed(self):
        self.update_strehl()
        
    def update_strehl(self):
        if self.image_viewer is None or self.image_viewer.display_data is None:
            return
            
        img_data = self.image_viewer.display_data
        if img_data.ndim == 3:
            img_data = img_data[self.image_viewer.imv.currentIndex]
            
        data = img_data.T # Transpose (nx, ny) -> (ny, nx) for analysis
        
        pos = self.roi.pos()
        size = self.roi.size()
        cx = pos.x() + size.x()/2.0
        cy = pos.y() + size.y()/2.0
        
        camname = '0.020'
        effwave = 2.1245
        if self.image_viewer.wcs:
            header = self.image_viewer.wcs.to_header()
            if 'CURRINST' in header:
                camname = header.get('CAMNAME', '0.020')
                effwave = header.get('EFFWAVE', 2.1245)
                
        # Estimate skyval (simple median of image for now, or could use an annulus)
        skyval = np.nanmedian(data)
        
        try:
            res = calculate_strehl(data, (cx, cy), skyval=skyval, photrad=self.spin_photrad.value(), camname=camname, effwave=effwave)
            if res is None:
                self.lbl_strehl.setText("Error: Subimage bounds")
                return
                
            self.lbl_strehl.setText(f"{res['strehl']:.4f}")
            self.lbl_fwhm.setText(f"{res['star_fwhm']:.4f}")
            self.lbl_psf_fwhm.setText(f"{res['psf_fwhm']:.4f}")
            
            r_star = res.get('r_star', [])
            val_star = np.array(res.get('val_star', []))
            r_psf = res.get('r_psf', [])
            val_psf = np.array(res.get('val_psf', []))
            
            # Since normalized, maybe we don't multiply by data_multiplier?
            # Wait, Strehl radial profile normalizes the peak to 1!
            # It's a normalized profile. So unit is arbitrary (Normalized Intensity)
            # Actually, let me check strehl.py: title="Radial Profile (Normalized)"
            self.star_plot.setData(r_star, val_star)
            self.psf_plot.setData(r_psf, val_psf)
            
            unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
            self.plot_widget.setLabel('left', f'Intensity ({unit})')
            
        except Exception as e:
            self.lbl_strehl.setText(f"Error: {e}")
            self.lbl_fwhm.setText("Error")
            self.lbl_psf_fwhm.setText("Error")
