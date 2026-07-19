import numpy as np
from PySide6.QtWidgets import QVBoxLayout, QGridLayout, QLabel, QSpinBox, QCheckBox
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from pyql3.gui.tools.base_tool import BaseToolDialog

class SurfacePlotDialog(BaseToolDialog):
    def __init__(self, parent=None, image_viewer=None):
        super().__init__(parent, image_viewer, "Surface Plot")
        self.resize(600, 600)
        
        main_layout = QVBoxLayout()
        self.layout.addLayout(main_layout)
        
        self.gl_widget = gl.GLViewWidget()
        main_layout.addWidget(self.gl_widget)
        
        self.surface_item = None
        self.update_plot()
        
        if self.image_viewer and self.image_viewer.imv:
            self.image_viewer.imv.sigTimeChanged.connect(self.update_plot)
        
    def update_plot(self):
        if self.image_viewer is None or self.image_viewer.display_data is None:
            return
            
        img_data = self.image_viewer.display_data
        if img_data.ndim == 3:
            img_data = img_data[self.image_viewer.imv.currentIndex]
            
        # Downsample the data for the surface plot to maintain performance
        # 3D surface plots of e.g. 2048x2048 arrays will freeze the UI
        ds = max(1, img_data.shape[0] // 256)
        
        data = img_data[::ds, ::ds]
        data = data.astype(np.float32)
        data = np.nan_to_num(data, nan=np.nanmedian(data))
        # Smooth and scale a bit
        
        # Center the data coordinates
        x = np.arange(data.shape[0]) - data.shape[0]/2
        y = np.arange(data.shape[1]) - data.shape[1]/2
        
        if self.surface_item is not None:
            self.gl_widget.removeItem(self.surface_item)
            
        # Using a height-based colormap
        cmap = pg.colormap.get('viridis')
        
        # Normalize data for colormap mapping
        min_val = np.nanmin(data)
        max_val = np.nanmax(data)
        if max_val > min_val:
            norm_data = (data - min_val) / (max_val - min_val)
        else:
            norm_data = np.zeros_like(data)
            
        colors = cmap.map(norm_data)
        
        self.surface_item = gl.GLSurfacePlotItem(x=x, y=y, z=data, colors=colors, computeNormals=False)
        self.gl_widget.addItem(self.surface_item)
        
        # Only set camera on first load
        if not hasattr(self, '_camera_set'):
            self.gl_widget.setCameraPosition(distance=max(data.shape)*1.5, elevation=30, azimuth=45)
            self._camera_set = True
            
    def closeEvent(self, event):
        if self.image_viewer and self.image_viewer.imv:
            try:
                self.image_viewer.imv.sigTimeChanged.disconnect(self.update_plot)
            except (TypeError, RuntimeError):
                pass
        super().closeEvent(event)


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
        
        if self.image_viewer and self.image_viewer.imv:
            self.image_viewer.imv.sigTimeChanged.connect(self.update_plot)
        
    def clear_contours(self):
        if self.image_viewer and self.image_viewer.imv:
            view = self.image_viewer.imv.getView()
            for item in self.iso_items:
                try:
                    view.removeItem(item)
                except Exception:
                    pass
        self.iso_items = []
        
    def toggle_contours(self, checked):
        if checked:
            self.update_plot()
        else:
            self.clear_contours()
            
    def update_plot(self):
        if not self.chk_show.isChecked():
            return
            
        self.clear_contours()
        
        if self.image_viewer is None or self.image_viewer.display_data is None:
            return
            
        img_data = self.image_viewer.display_data
        if img_data.ndim == 3:
            img_data = img_data[self.image_viewer.imv.currentIndex]
            
        view = self.image_viewer.imv.getView()
        
        # Calculate levels
        min_val = np.nanmin(img_data)
        max_val = np.nanmax(img_data)
        
        if np.isnan(min_val) or np.isnan(max_val) or min_val == max_val:
            return
            
        num_levels = self.spin_levels.value()
        # Create linearly spaced levels
        levels = np.linspace(min_val, max_val, num_levels + 2)[1:-1]
        
        for level in levels:
            iso = pg.IsocurveItem(data=img_data, level=level, pen='r')
            # The IsocurveItem must be linked to the image item to overlay correctly
            iso.setParentItem(self.image_viewer.imv.getImageItem())
            self.iso_items.append(iso)
            
    def closeEvent(self, event):
        self.clear_contours()
        if self.image_viewer and self.image_viewer.imv:
            try:
                self.image_viewer.imv.sigTimeChanged.disconnect(self.update_plot)
            except (TypeError, RuntimeError):
                pass
        super().closeEvent(event)
