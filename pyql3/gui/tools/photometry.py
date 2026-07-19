import numpy as np
from PySide6.QtWidgets import QGridLayout, QLabel, QDoubleSpinBox
import pyqtgraph as pg
from photutils.aperture import CircularAperture, CircularAnnulus, aperture_photometry
from pyql3.gui.tools.base_tool import BaseToolDialog

class PhotometryDialog(BaseToolDialog):
    def __init__(self, parent=None, image_viewer=None):
        super().__init__(parent, image_viewer, "Aperture Photometry")
        self.resize(350, 300)
        
        grid = QGridLayout()
        self.setup_draw_button(self.layout)
        self.layout.addLayout(grid)
        
        self.spin_aperture = QDoubleSpinBox()
        self.spin_aperture.setValue(5.0)
        self.spin_inner = QDoubleSpinBox()
        self.spin_inner.setValue(10.0)
        self.spin_outer = QDoubleSpinBox()
        self.spin_outer.setValue(20.0)
        
        for spin in [self.spin_aperture, self.spin_inner, self.spin_outer]:
            spin.setRange(1.0, 1000.0)
            spin.valueChanged.connect(self.update_photometry)
            
        grid.addWidget(QLabel("Aperture Radius:"), 0, 0)
        grid.addWidget(self.spin_aperture, 0, 1)
        grid.addWidget(QLabel("Inner Sky Radius:"), 1, 0)
        grid.addWidget(self.spin_inner, 1, 1)
        grid.addWidget(QLabel("Outer Sky Radius:"), 2, 0)
        grid.addWidget(self.spin_outer, 2, 1)
        
        self.lbl_flux = QLabel("N/A")
        self.lbl_sky = QLabel("N/A")
        self.lbl_total = QLabel("N/A")
        
        grid.addWidget(QLabel("Raw Flux:"), 3, 0)
        grid.addWidget(self.lbl_flux, 3, 1)
        grid.addWidget(QLabel("Sky Background:"), 4, 0)
        grid.addWidget(self.lbl_sky, 4, 1)
        grid.addWidget(QLabel("Subtracted Flux:"), 5, 0)
        grid.addWidget(self.lbl_total, 5, 1)
        
        if self.image_viewer and self.image_viewer.display_data is not None:
            shape = self.image_viewer.display_data.shape
            if len(shape) == 3:
                center_x, center_y = shape[1]//2, shape[2]//2
            else:
                center_x, center_y = shape[0]//2, shape[1]//2
        else:
            center_x, center_y = 0, 0
            
        r = 5.0
        self.roi = pg.CircleROI([center_x - r, center_y - r], [r*2, r*2], pen=pg.mkPen((255, 0, 0), width=3), hoverPen=pg.mkPen((255, 0, 0), width=5))
        self.add_roi_to_viewer(self.roi)
        
        self.roi_inner = pg.CircleROI([center_x - 10, center_y - 10], [20, 20], pen=pg.mkPen('y', width=2, style=pg.QtCore.Qt.DashLine), hoverPen=pg.mkPen('y', width=4, style=pg.QtCore.Qt.DashLine), movable=False, resizable=False)
        self.roi_inner.removeHandle(0)
        self.image_viewer.imv.getView().addItem(self.roi_inner)
        
        self.roi_outer = pg.CircleROI([center_x - 20, center_y - 20], [40, 40], pen=pg.mkPen('y', width=2, style=pg.QtCore.Qt.DashLine), hoverPen=pg.mkPen('y', width=4, style=pg.QtCore.Qt.DashLine), movable=False, resizable=False)
        self.roi_outer.removeHandle(0)
        self.image_viewer.imv.getView().addItem(self.roi_outer)
        
        self.update_photometry()
        
    def on_roi_changed(self):
        r = self.roi.size().x() / 2.0
        self.spin_aperture.blockSignals(True)
        self.spin_aperture.setValue(r)
        self.spin_aperture.blockSignals(False)
        self.update_photometry()
        
    def closeEvent(self, event):
        if self.image_viewer and self.image_viewer.imv:
            self.image_viewer.imv.getView().removeItem(self.roi_inner)
            self.image_viewer.imv.getView().removeItem(self.roi_outer)
        super().closeEvent(event)
        
    def update_photometry(self):
        if self.image_viewer is None or self.image_viewer.display_data is None:
            return
            
        r = self.spin_aperture.value()
        current_r = self.roi.size().x() / 2.0
        if abs(r - current_r) > 0.01:
            center = self.roi.pos() + self.roi.size()/2.0
            self.roi.blockSignals(True)
            self.roi.setPos(center.x() - r, center.y() - r)
            self.roi.setSize([r*2, r*2])
            self.roi.blockSignals(False)
            
        img_data = self.image_viewer.display_data
        if img_data.ndim == 3:
            img_data = img_data[self.image_viewer.imv.currentIndex]
            
        data = img_data
        pos = self.roi.pos()
        size = self.roi.size()
        cx = pos.x() + size.x()/2.0
        cy = pos.y() + size.y()/2.0
        
        r_in = self.spin_inner.value()
        r_out = self.spin_outer.value()
        
        # Update the concentric rings
        self.roi_inner.setPos(cx - r_in, cy - r_in)
        self.roi_inner.setSize([r_in*2, r_in*2])
        self.roi_outer.setPos(cx - r_out, cy - r_out)
        self.roi_outer.setSize([r_out*2, r_out*2])
        
        # Photutils expects (ny, nx) data where the 2nd axis is X and 1st axis is Y.
        # Since our data is (nx, ny), we MUST transpose it for aperture_photometry to map (cx, cy) correctly!
        data_for_phot = data.T
        
        positions = [(cx, cy)]
        aperture = CircularAperture(positions, r=r)
        annulus = CircularAnnulus(positions, r_in=r_in, r_out=r_out)
        
        phot_table = aperture_photometry(data_for_phot, aperture)
        annulus_masks = annulus.to_mask(method='center')
        
        try:
            mask = annulus_masks[0]
            annulus_data = mask.multiply(data_for_phot)
            annulus_data_1d = annulus_data[mask.data > 0]
            
            bkg_median = np.nanmedian(annulus_data_1d)
            bkg_sum = bkg_median * aperture.area
            
            raw_flux = phot_table['aperture_sum'][0]
            final_flux = raw_flux - bkg_sum
            
            self.lbl_flux.setText(f"{raw_flux:.4f}")
            self.lbl_sky.setText(f"{bkg_sum:.4f}")
            self.lbl_total.setText(f"{final_flux:.4f}")
        except Exception as e:
            self.lbl_flux.setText("Error")
            self.lbl_sky.setText("Error")
            self.lbl_total.setText("Error")
