import pyqtgraph as pg
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, 
                               QLabel, QPushButton, QLineEdit, QComboBox, 
                               QCheckBox, QRadioButton, QGroupBox, QSlider, QFrame,
                               QButtonGroup, QTabWidget, QStyle, QStyleOptionSlider)
from PySide6.QtCore import Qt
import numpy as np
from astropy.wcs import WCS
from astropy.coordinates import SkyCoord
import astropy.units as u
import warnings

class JumpSlider(QSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            pos = event.position().toPoint()
            control = self.style().hitTestComplexControl(QStyle.CC_Slider, opt, pos, self)
            if control == QStyle.SC_SliderGroove:
                val = self.style().sliderValueFromPosition(self.minimum(), self.maximum(), pos.x(), self.width())
                self.setValue(val)
                event.accept()
                return
        super().mousePressEvent(event)

class ImageViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        # Top: The Image View
        self.imv = pg.ImageView()
        self.imv.ui.histogram.hide()
        self.imv.ui.roiBtn.hide()
        self.imv.ui.menuBtn.hide()
        self.imv.getView().setAspectLocked(True)
        self.imv.getView().invertY(False)
        self.imv.ui.roiPlot.setMinimumHeight(65)
        self.imv.ui.roiPlot.setMaximumHeight(85)
        self.layout.addWidget(self.imv, stretch=1)
        
        # Add region for highlighting stacked Z slices in the timeline
        self.z_range_region = pg.LinearRegionItem([0, 1], brush=(255, 255, 0, 120), pen=pg.mkPen((255, 255, 0, 200), width=3))
        self.z_range_region.setZValue(10)
        self.z_range_region.hide()
        self.z_range_region.sigRegionChangeFinished.connect(self.on_region_change_finished)
        self.imv.ui.roiPlot.addItem(self.z_range_region)
        self._updating_region = False
        
        self.imv.ui.roiPlot.scene().sigMouseClicked.connect(self.on_roi_plot_clicked)
        


        # Top axis for Wavelength
        self.top_axis = self.imv.ui.roiPlot.plotItem.getAxis('top')
        self.top_axis.setStyle(showValues=True)
        self._original_tickStrings = self.top_axis.tickStrings
        self.top_axis.tickStrings = self._custom_tickStrings
        self.wcs_z_idx = None
        
        self.imv.timeLine.setPen(pg.mkPen('y', width=3))
        self.imv.timeLine.setHoverPen(pg.mkPen('y', width=5))
        
        self.set_colormap('cmc.oslo')
        
        # Data state
        self.raw_data = None
        self.transposed_data = None
        self.display_data = None
        self.wcs = None
        
        # Info Bar directly below image
        self.setup_info_panel()
        
        # Bottom: The Control Panels
        self.tabs = QTabWidget()
        self.layout.addWidget(self.tabs)
        
        self.tab_display = QWidget()
        self.display_layout = QVBoxLayout(self.tab_display)
        self.display_layout.setContentsMargins(4, 4, 4, 4)
        
        self.tab_advanced = QWidget()
        self.advanced_layout = QVBoxLayout(self.tab_advanced)
        self.advanced_layout.setContentsMargins(4, 4, 4, 4)
        
        self.tabs.addTab(self.tab_display, "Display")
        self.tabs.addTab(self.tab_advanced, "Advanced Data Cube")
        # Transforms state
        self.disp_as_dn = False
        self.rot_angle = 0
        self.flip = False
        self.pa_arrow = None
        self._itime_coadds = 1.0
        
        self.setup_zoom_contrast_panel()
        self.setup_extension_panel()
        self.setup_axis_panel()
        self.setup_z_axis_panel()
        
        self.tabs.currentChanged.connect(self.on_tab_changed)
        # Call it once after all layouts are fully constructed
        self.on_tab_changed(self.tabs.currentIndex())
        
        # Connect mouse movement and zoom scale
        self.proxy = pg.SignalProxy(self.imv.scene.sigMouseMoved, rateLimit=60, slot=self.mouse_moved)
        self.imv.timeLine.sigPositionChanged.connect(self.update_slice_info)

    def get_wavelength_for_slice(self, z):
        if getattr(self, 'wcs', None) is None or getattr(self, 'wcs_z_idx', None) is None:
            return None
        coords = [0] * self.wcs.naxis
        coords[self.wcs_z_idx] = z
        try:
            world = self.wcs.wcs_pix2world([coords], 0)[0]
            wave = world[self.wcs_z_idx]
            unit = self.wcs.wcs.cunit[self.wcs_z_idx]
            if str(unit).strip().lower() == 'm':
                wave *= 1e6
            return wave
        except Exception:
            return None

    def update_slice_info(self, *args, **kwargs):
        if getattr(self, 'transposed_data', None) is None:
            if hasattr(self, 'lbl_slice_info'):
                self.lbl_slice_info.setText("Slice: N/A")
            return
            
        if hasattr(self, 'radio_range') and self.radio_range.isChecked():
            try:
                zmin = max(0, int(self.txt_zmin.text()))
                zmax = min(self.transposed_data.shape[0]-1, int(self.txt_zmax.text()))
                text = f"Collapsed: {zmin}-{zmax}"
                
                wave_min = self.get_wavelength_for_slice(zmin)
                wave_max = self.get_wavelength_for_slice(zmax)
                if wave_min is not None and wave_max is not None:
                    text += f", Wavelengths: {wave_min:.4f}-{wave_max:.4f} µm"
                        
                self.lbl_slice_info.setText(text)
            except ValueError:
                self.lbl_slice_info.setText("Collapsed: Invalid Range")
        else:
            if self.transposed_data.ndim == 3:
                z = self.slider_slice.value()
                boxcar = 1
                try:
                    boxcar = int(self.txt_boxcar.text())
                except ValueError:
                    pass
                if boxcar > 1:
                    half = boxcar // 2
                    zmin = max(0, z - half)
                    zmax = min(self.transposed_data.shape[0]-1, z + half)
                    text = f"Slice (Boxcar): {zmin}-{zmax}"
                    wave_min = self.get_wavelength_for_slice(zmin)
                    wave_max = self.get_wavelength_for_slice(zmax)
                    if wave_min is not None and wave_max is not None:
                        text += f", Wavelengths: {wave_min:.4f}-{wave_max:.4f} µm"
                else:
                    text = f"Slice: {z}"
                    wave = self.get_wavelength_for_slice(z)
                    if wave is not None:
                        text += f", Wavelength: {wave:.4f} µm"
                
                self.lbl_slice_info.setText(text)
            else:
                self.lbl_slice_info.setText("Slice: N/A (2D Image)")

    def on_tab_changed(self, index):
        widget = self.tabs.widget(index)
        height = widget.sizeHint().height()
        bar_height = self.tabs.tabBar().sizeHint().height()
        total_height = height + bar_height + 8
        self.tabs.setMaximumHeight(total_height)
        self.tabs.setMinimumHeight(total_height)

    def _custom_tickStrings(self, values, scale, spacing):
        if self.wcs is None or self.wcs_z_idx is None:
            return self._original_tickStrings(values, scale, spacing)
            
        strings = []
        for val in values:
            if val < 0:
                strings.append("")
                continue
            coords = [0] * self.wcs.naxis
            coords[self.wcs_z_idx] = val
            try:
                world = self.wcs.wcs_pix2world([coords], 0)[0]
                wave = world[self.wcs_z_idx]
                unit = self.wcs.wcs.cunit[self.wcs_z_idx]
                if str(unit).strip().lower() == 'm':
                    wave *= 1e6
                strings.append(f"{wave:.4f} µm")
            except Exception:
                strings.append(str(val))
        return strings

    def setup_info_panel(self):
        hbox1 = QHBoxLayout()
        hbox1.setContentsMargins(4, 2, 4, 0)
        
        self.lbl_x = QLabel("X: N/A")
        self.lbl_y = QLabel("Y: N/A")
        self.lbl_val = QLabel("Value: N/A")
        self.lbl_slice_info = QLabel("Slice: N/A")
        
        sep1 = QFrame(); sep1.setFrameShape(QFrame.VLine); sep1.setFrameShadow(QFrame.Sunken)
        
        hbox1.addWidget(self.lbl_x)
        hbox1.addWidget(self.lbl_y)
        hbox1.addWidget(self.lbl_val)
        hbox1.addWidget(sep1)
        hbox1.addWidget(self.lbl_slice_info)
        hbox1.addStretch()
        
        hbox2 = QHBoxLayout()
        hbox2.setContentsMargins(4, 0, 4, 2)
        self.lbl_wcs = QLabel("WCS: N/A")
        hbox2.addWidget(self.lbl_wcs)
        hbox2.addStretch()
        
        vbox = QVBoxLayout()
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.addLayout(hbox1)
        vbox.addLayout(hbox2)
        
        info_widget = QWidget()
        info_widget.setLayout(vbox)
        self.layout.addWidget(info_widget)
        
    def setup_zoom_contrast_panel(self):
        hbox = QHBoxLayout()
        hbox.setContentsMargins(0, 0, 0, 0)
        self.btn_zoom_out = QPushButton("-")
        self.btn_zoom_out.setFixedWidth(30)
        self.btn_zoom_out.clicked.connect(self.main_zoom_out)
        
        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_in.setFixedWidth(30)
        self.btn_zoom_in.clicked.connect(self.main_zoom_in)
        
        self.btn_zoom_11 = QPushButton("1:1")
        self.btn_zoom_11.setFixedWidth(40)
        self.btn_zoom_11.clicked.connect(self.main_zoom_11)
        
        self.btn_zoom_fit = QPushButton("Fit")
        self.btn_zoom_fit.setFixedWidth(40)
        self.btn_zoom_fit.clicked.connect(self.main_zoom_fit)
        
        hbox.addWidget(self.btn_zoom_out)
        hbox.addWidget(self.btn_zoom_in)
        hbox.addWidget(self.btn_zoom_11)
        hbox.addWidget(self.btn_zoom_fit)
        hbox.addSpacing(10)
        
        hbox.addWidget(QLabel("Min:"))
        self.txt_min = QLineEdit("0.000")
        self.txt_min.setFixedWidth(60)
        self.txt_min.editingFinished.connect(self.apply_contrast)
        hbox.addWidget(self.txt_min)
        
        hbox.addWidget(QLabel("Max:"))
        self.txt_max = QLineEdit("1.000")
        self.txt_max.setFixedWidth(60)
        self.txt_max.editingFinished.connect(self.apply_contrast)
        hbox.addWidget(self.txt_max)
        
        self.btn_apply_contrast = QPushButton("Apply")
        self.btn_apply_contrast.clicked.connect(self.apply_contrast)
        hbox.addWidget(self.btn_apply_contrast)
        
        hbox.addSpacing(10)
        hbox.addWidget(QLabel("Scale:"))
        self.combo_scale = QComboBox()
        self.combo_scale.addItems(["Linear", "Negative", "HistEq", "Logarithmic", "Sqrt", "AsinH"])
        self.combo_scale.currentIndexChanged.connect(self.apply_contrast)
        hbox.addWidget(self.combo_scale)
        
        hbox.addStretch()
        self.display_layout.addLayout(hbox)
        self.display_layout.addStretch()
        
    def setup_extension_panel(self):
        hbox = QHBoxLayout()
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.addWidget(QLabel("Extension:"))
        self.combo_ext = QComboBox()
        self.combo_ext.addItem("Image")
        hbox.addWidget(self.combo_ext)
        hbox.addSpacing(20)
        hbox.addWidget(QLabel("Collapse:"))
        self.combo_collapse = QComboBox()
        self.combo_collapse.addItems(["Median", "Mean", "Sum"])
        self.combo_collapse.currentIndexChanged.connect(self.on_collapse_changed)
        hbox.addWidget(self.combo_collapse)
        hbox.addStretch()
        self.advanced_layout.addLayout(hbox)
        
    def setup_axis_panel(self):
        hbox = QHBoxLayout()
        hbox.setContentsMargins(0, 0, 0, 0)
        
        # X Axis Group
        group_x = QGroupBox()
        layout_x = QVBoxLayout(group_x)
        layout_x.setContentsMargins(2, 2, 2, 2)
        layout_x.setSpacing(2)
        row1_x = QHBoxLayout()
        row1_x.setContentsMargins(0, 0, 0, 0)
        row1_x.addWidget(QLabel("X:"))
        self.combo_x = QComboBox()
        self.combo_x.addItems(["AXIS 1", "AXIS 2", "AXIS 3"])
        self.combo_x.setCurrentText("AXIS 3")
        self.combo_x.currentIndexChanged.connect(self.on_axis_changed)
        row1_x.addWidget(self.combo_x)
        layout_x.addLayout(row1_x)
        
        row2_x = QHBoxLayout()
        row2_x.setContentsMargins(0, 0, 0, 0)
        self.btn_x_minus = QPushButton("-")
        self.btn_x_plus = QPushButton("+")
        self.btn_x_11 = QPushButton("1:1")
        self.btn_x_fit = QPushButton("Fit")
        
        for btn in [self.btn_x_minus, self.btn_x_plus]: btn.setFixedWidth(25)
        for btn in [self.btn_x_11, self.btn_x_fit]: btn.setFixedWidth(35)
        
        self.btn_x_minus.clicked.connect(lambda: self.independent_zoom(x=2.0))
        self.btn_x_plus.clicked.connect(lambda: self.independent_zoom(x=0.5))
        self.btn_x_11.clicked.connect(self.zoom_x_1_1)
        self.btn_x_fit.clicked.connect(self.zoom_x_fit)
        
        row2_x.addWidget(self.btn_x_minus)
        row2_x.addWidget(self.btn_x_plus)
        row2_x.addWidget(self.btn_x_11)
        row2_x.addWidget(self.btn_x_fit)
        layout_x.addLayout(row2_x)
        hbox.addWidget(group_x)
        
        # Y Axis Group
        group_y = QGroupBox()
        layout_y = QVBoxLayout(group_y)
        layout_y.setContentsMargins(2, 2, 2, 2)
        layout_y.setSpacing(2)
        row1_y = QHBoxLayout()
        row1_y.setContentsMargins(0, 0, 0, 0)
        row1_y.addWidget(QLabel("Y:"))
        self.combo_y = QComboBox()
        self.combo_y.addItems(["AXIS 1", "AXIS 2", "AXIS 3"])
        self.combo_y.setCurrentText("AXIS 2")
        self.combo_y.currentIndexChanged.connect(self.on_axis_changed)
        row1_y.addWidget(self.combo_y)
        layout_y.addLayout(row1_y)
        
        row2_y = QHBoxLayout()
        row2_y.setContentsMargins(0, 0, 0, 0)
        self.btn_y_minus = QPushButton("-")
        self.btn_y_plus = QPushButton("+")
        self.btn_y_11 = QPushButton("1:1")
        self.btn_y_fit = QPushButton("Fit")
        
        for btn in [self.btn_y_minus, self.btn_y_plus]: btn.setFixedWidth(25)
        for btn in [self.btn_y_11, self.btn_y_fit]: btn.setFixedWidth(35)
        
        self.btn_y_minus.clicked.connect(lambda: self.independent_zoom(y=2.0))
        self.btn_y_plus.clicked.connect(lambda: self.independent_zoom(y=0.5))
        self.btn_y_11.clicked.connect(self.zoom_y_1_1)
        self.btn_y_fit.clicked.connect(self.zoom_y_fit)
        
        row2_y.addWidget(self.btn_y_minus)
        row2_y.addWidget(self.btn_y_plus)
        row2_y.addWidget(self.btn_y_11)
        row2_y.addWidget(self.btn_y_fit)
        layout_y.addLayout(row2_y)
        hbox.addWidget(group_y)
        
        hbox.addStretch()
        self.advanced_layout.addLayout(hbox)
        
    def setup_z_axis_panel(self):
        group_z = QGroupBox()
        layout_z = QVBoxLayout(group_z)
        layout_z.setContentsMargins(2, 2, 2, 2)
        layout_z.setSpacing(2)
        
        # Z Info
        info_hbox = QHBoxLayout()
        self.lbl_zmin = QLabel("ZMin: 0")
        self.lbl_zmax = QLabel("ZMax: 0")
        self.lbl_zsize = QLabel("ZSize: 0")
        info_hbox.addWidget(self.lbl_zmin)
        info_hbox.addWidget(self.lbl_zmax)
        info_hbox.addWidget(self.lbl_zsize)
        info_hbox.addStretch()
        layout_z.addLayout(info_hbox)
        
        self.z_mode_group = QButtonGroup(self)
        
        # Range row
        self.radio_range = QRadioButton("Collapse Region")
        self.z_mode_group.addButton(self.radio_range)
        
        range_hbox = QHBoxLayout()
        range_hbox.setContentsMargins(0, 0, 0, 0)
        range_hbox.addWidget(self.radio_range)
        range_hbox.addWidget(QLabel("Z Min:"))
        self.txt_zmin = QLineEdit("0")
        self.txt_zmin.setFixedWidth(50)
        self.txt_zmin.editingFinished.connect(self.apply_z_range)
        range_hbox.addWidget(self.txt_zmin)
        range_hbox.addWidget(QLabel("Z Max:"))
        self.txt_zmax = QLineEdit("0")
        self.txt_zmax.setFixedWidth(50)
        self.txt_zmax.editingFinished.connect(self.apply_z_range)
        range_hbox.addWidget(self.txt_zmax)
        self.btn_apply_range = QPushButton("Apply")
        self.btn_apply_range.clicked.connect(self.apply_z_range)
        range_hbox.addWidget(self.btn_apply_range)
        range_hbox.addStretch()
        layout_z.addLayout(range_hbox)
        
        # Slice row
        self.radio_slice = QRadioButton("Z Slice")
        self.radio_slice.setChecked(True)
        self.z_mode_group.addButton(self.radio_slice)
        
        slice_hbox = QHBoxLayout()
        slice_hbox.setContentsMargins(0, 0, 0, 0)
        slice_hbox.addWidget(self.radio_slice)
        
        slice_inner_vbox = QVBoxLayout()
        self.lbl_slice_val = QLabel("0")
        self.lbl_slice_val.setAlignment(Qt.AlignCenter)
        self.slider_slice = JumpSlider(Qt.Horizontal)
        self.slider_slice.valueChanged.connect(self.on_slider_changed)
        slice_inner_vbox.addWidget(self.lbl_slice_val)
        slice_inner_vbox.addWidget(self.slider_slice)
        slice_hbox.addLayout(slice_inner_vbox)
        
        slice_hbox.addWidget(QLabel("Boxcar:"))
        self.txt_boxcar = QLineEdit("1")
        self.txt_boxcar.setFixedWidth(40)
        self.txt_boxcar.editingFinished.connect(self.apply_z_slice)
        slice_hbox.addWidget(self.txt_boxcar)
        self.btn_apply_slice = QPushButton("Apply")
        self.btn_apply_slice.clicked.connect(self.apply_z_slice)
        slice_hbox.addWidget(self.btn_apply_slice)
        slice_hbox.addStretch()
        layout_z.addLayout(slice_hbox)
        
        self.radio_range.toggled.connect(self.z_mode_changed)
        self.advanced_layout.addWidget(group_z)



    def main_zoom_out(self):
        self.imv.getView().setAspectLocked(True)
        self.imv.getView().scaleBy((2.0, 2.0))

    def main_zoom_in(self):
        self.imv.getView().setAspectLocked(True)
        self.imv.getView().scaleBy((0.5, 0.5))

    def main_zoom_fit(self):
        self.imv.getView().setAspectLocked(True)
        self.imv.autoRange()

    def main_zoom_11(self):
        self.imv.getView().setAspectLocked(True)
        if self.display_data is not None:
            w, h = self.display_data.shape[-2:]
            self.imv.getView().setRange(xRange=(0, w), yRange=(0, h), padding=0)

    def independent_zoom(self, x=None, y=None):
        self.imv.getView().setAspectLocked(False)
        self.imv.getView().scaleBy(x=x, y=y)
            
    def zoom_x_1_1(self):
        self.imv.getView().setAspectLocked(False)
        if self.display_data is not None:
            w = self.display_data.shape[-2]
            self.imv.getView().setRange(xRange=(0, w), padding=0)
            
    def zoom_y_1_1(self):
        self.imv.getView().setAspectLocked(False)
        if self.display_data is not None:
            h = self.display_data.shape[-1]
            self.imv.getView().setRange(yRange=(0, h), padding=0)
            
    def zoom_x_fit(self):
        self.imv.getView().setAspectLocked(False)
        self.imv.getView().enableAutoRange(axis=pg.ViewBox.XAxis)

    def zoom_y_fit(self):
        self.imv.getView().setAspectLocked(False)
        self.imv.getView().enableAutoRange(axis=pg.ViewBox.YAxis)

    def apply_contrast(self):
        is_range = hasattr(self, 'radio_range') and self.radio_range.isChecked()
        boxcar = 1
        if hasattr(self, 'txt_boxcar'):
            try:
                boxcar = int(self.txt_boxcar.text())
            except ValueError:
                pass
                
        needs_bypass = is_range or (boxcar > 1 and getattr(self, 'transposed_data', None) is not None and self.transposed_data.ndim == 3)
        current_slice = getattr(self, 'slider_slice', None).value() if hasattr(self, 'slider_slice') else None
        
        self.update_image_display(use_manual_levels=True, bypass_imv=needs_bypass, set_index=current_slice)

    def z_mode_changed(self):
        is_range = self.radio_range.isChecked()
        self.txt_zmin.setEnabled(is_range)
        self.txt_zmax.setEnabled(is_range)
        self.btn_apply_range.setEnabled(is_range)
        
        self.slider_slice.setEnabled(not is_range)
        self.txt_boxcar.setEnabled(not is_range)
        self.btn_apply_slice.setEnabled(not is_range)
        
        # Always allow moving the region itself, but lock the edges in slice mode
        self.z_range_region.setMovable(True)
        for line in self.z_range_region.lines:
            line.setMovable(is_range)
        
        if self.transposed_data is not None and self.transposed_data.ndim == 3:
            if not is_range:
                self.apply_z_slice()
            else:
                self.apply_z_range()

    def on_collapse_changed(self):
        if self.radio_range.isChecked():
            self.apply_z_range()

    def on_slider_changed(self, value):
        self.lbl_slice_val.setText(str(value))
        if self.radio_slice.isChecked() and self.transposed_data is not None and self.transposed_data.ndim == 3:
            boxcar = 1
            try:
                boxcar = int(self.txt_boxcar.text())
            except ValueError:
                pass
            
            if boxcar <= 1:
                if getattr(self, 'z_range_region', None) and self.z_range_region.isVisible():
                    self.apply_z_slice()
                else:
                    self.imv.setCurrentIndex(value)
                    self.update_slice_info()
            else:
                self.apply_z_slice()

    def apply_z_range(self):
        if self.transposed_data is None or self.transposed_data.ndim != 3:
            return
        if self._updating_region:
            return
        try:
            if getattr(self.imv, 'timeLine', None) is not None:
                self.imv.timeLine.hide()
                
            zmin = max(0, int(self.txt_zmin.text()))
            zmax = min(self.transposed_data.shape[0]-1, int(self.txt_zmax.text()))
            collapse_method = self.combo_collapse.currentText()
            
            subcube = self.transposed_data[zmin:zmax+1, :, :]
            if collapse_method == "Median":
                collapsed = np.nanmedian(subcube, axis=0)
            elif collapse_method == "Mean":
                collapsed = np.nanmean(subcube, axis=0)
            elif collapse_method == "Sum":
                collapsed = np.nansum(subcube, axis=0)
            else:
                collapsed = subcube[0]
                
            self.display_data = collapsed
            self.apply_transforms()
            
            # Highlight the range on the pyqtgraph slider
            self._updating_region = True
            self.z_range_region.setRegion([zmin, zmax])
            self._updating_region = False
            self.z_range_region.show()
            
            # bypass_imv=True prevents pg.ImageView from realizing the data is now 2D and hiding the 3D timeline
            self.update_image_display(bypass_imv=True)
            self.lbl_zmin.setText(f"ZMin: {zmin}")
            self.lbl_zmax.setText(f"ZMax: {zmax}")
            self.update_slice_info()
        except ValueError:
            pass

    def on_roi_plot_clicked(self, event):
        if event.button() == Qt.LeftButton:
            # Check if click is actually within the plot area bounding box
            if self.imv.ui.roiPlot.sceneBoundingRect().contains(event.scenePos()):
                pos = self.imv.ui.roiPlot.getViewBox().mapSceneToView(event.scenePos())
                x_val = int(round(pos.x()))
                
                if self.transposed_data is not None:
                    max_val = self.transposed_data.shape[0] - 1
                    x_val = max(0, min(x_val, max_val))
                    
                    if not self.radio_range.isChecked():
                        # Using setValue on the slider will correctly trigger the signals
                        self.slider_slice.setValue(x_val)

    def on_region_change_finished(self):
        if self._updating_region:
            return
        
        r_min, r_max = self.z_range_region.getRegion()
        
        if not self.radio_range.isChecked():
            # Z Slice mode with Boxcar > 1
            # User dragged the yellow boxcar region. Update the slice slider to the center!
            center = int(round((r_min + r_max) / 2.0))
            if self.transposed_data is not None:
                center = max(0, min(self.transposed_data.shape[0]-1, center))
            self.slider_slice.setValue(center)
            return
            
        zmin = max(0, int(round(r_min)))
        
        if self.transposed_data is not None:
            zmax = min(self.transposed_data.shape[0]-1, int(round(r_max)))
        else:
            zmax = int(round(r_max))
            
        self.txt_zmin.setText(str(zmin))
        self.txt_zmax.setText(str(zmax))
        self.apply_z_range()

    def apply_z_slice(self):
        if self.transposed_data is None or self.transposed_data.ndim != 3:
            return
        try:
            val = self.slider_slice.value()
            boxcar = max(1, int(self.txt_boxcar.text()))
            
            if boxcar == 1:
                if getattr(self.imv, 'timeLine', None) is not None:
                    self.imv.timeLine.show()
                self.z_range_region.hide()
                self.display_data = self.transposed_data.copy()
                self.apply_transforms()
                self.update_image_display(set_index=val)
            else:
                if getattr(self.imv, 'timeLine', None) is not None:
                    self.imv.timeLine.hide()
                half = boxcar // 2
                zmin = max(0, val - half)
                zmax = min(self.transposed_data.shape[0]-1, val + half)
                subcube = self.transposed_data[zmin:zmax+1, :, :]
                collapsed = np.nanmedian(subcube, axis=0)
                self.display_data = collapsed
                self.apply_transforms()
                
                self._updating_region = True
                self.z_range_region.setRegion([zmin, zmax])
                self._updating_region = False
                self.z_range_region.show()
                
                self.update_image_display(bypass_imv=True)
                
            self.lbl_zmin.setText(f"ZMin: {val}")
            self.lbl_zmax.setText(f"ZMax: {val}")
            self.update_slice_info()
        except ValueError:
            pass

    def set_data(self, data, header=None):
        """Sets the FITS data into the viewer."""
        self.raw_data = data
        self.header = header
        self._is_new_data = True
        if data is None:
            self.imv.clear()
            self.wcs = None
            return
            
        if header:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                self.wcs = WCS(header)
                
            itime = header.get('ITIME', 1.0)
            if itime == 0:
                itime = header.get('TRUITIME', 1.0)
            coadds = header.get('COADDS', 1.0)
            self._itime_coadds = itime * coadds
        else:
            self.wcs = None
            self._itime_coadds = 1.0
            
        if data.ndim == 2:
            self.transposed_data = data.T
            self.display_data = self.transposed_data
            self.apply_transforms()
            self.update_image_display()
            self.lbl_zsize.setText("ZSize: 1")
            self.tab_advanced.setEnabled(False) # Disable Z controls for 2D
        elif data.ndim == 3:
            self.tab_advanced.setEnabled(True)
            
            # Check CTYPE1 to determine default axes
            # OSIRIS has WAVE as CTYPE1, but non-OSIRIS usually has RA---TAN
            # Default to AXIS 1 if RA is first, else OSIRIS-default AXIS 3
            is_non_osiris = False
            if header and 'CTYPE1' in header:
                if 'RA' in header['CTYPE1'].upper():
                    is_non_osiris = True
                    
            default_x = "AXIS 1" if is_non_osiris else "AXIS 3"
            
            self.combo_x.blockSignals(True)
            self.combo_x.setCurrentText(default_x)
            self.combo_x.blockSignals(False)
            
            self.combo_y.blockSignals(True)
            self.combo_y.setCurrentText("AXIS 2")
            self.combo_y.blockSignals(False)
            
            self.apply_axis_mapping()
            
    def on_axis_changed(self):
        if self.raw_data is None or self.raw_data.ndim != 3:
            return
        self.apply_axis_mapping()
        
    def apply_axis_mapping(self):
        if self.raw_data is None or self.raw_data.ndim != 3:
            return
            
        axes = ["AXIS 1", "AXIS 2", "AXIS 3"]
        x_axis = self.combo_x.currentText()
        y_axis = self.combo_y.currentText()
        self.current_x_axis = x_axis
        self.current_y_axis = y_axis
        
        if x_axis == y_axis:
            # Avoid duplicate axes by picking the first available one
            available = [a for a in axes if a != x_axis]
            y_axis = available[0]
            # Update the UI without triggering the signal again
            self.combo_y.blockSignals(True)
            self.combo_y.setCurrentText(y_axis)
            self.combo_y.blockSignals(False)
            
        z_axis = [a for a in axes if a not in (x_axis, y_axis)][0]
        self.current_z_axis = z_axis
        
        # FITS AXIS 1 -> py 2 (Nx)
        # FITS AXIS 2 -> py 1 (Ny)
        # FITS AXIS 3 -> py 0 (Nz)
        fits_to_py = {"AXIS 1": 2, "AXIS 2": 1, "AXIS 3": 0}
        
        py_z = fits_to_py[z_axis]
        py_x = fits_to_py[x_axis]
        py_y = fits_to_py[y_axis]
        
        # Check if Z axis is wavelength
        wcs_z_idx = int(z_axis.split()[1]) - 1
        is_wavelength = False
        if self.wcs is not None and self.wcs.naxis > wcs_z_idx:
            ctype = str(self.wcs.wcs.ctype[wcs_z_idx]).upper()
            if 'WAVE' in ctype:
                is_wavelength = True
                
        if is_wavelength:
            self.wcs_z_idx = wcs_z_idx
            self.imv.ui.roiPlot.showAxis('top')
        else:
            self.wcs_z_idx = None
            self.imv.ui.roiPlot.hideAxis('top')
        
        self.transposed_data = self.raw_data.transpose(py_z, py_x, py_y)
        self.display_data = self.transposed_data.copy()
        self.apply_transforms()
        
        zs = self.transposed_data.shape[0]
        self.slider_slice.setMaximum(zs - 1)
        self.lbl_zsize.setText(f"ZSize: {zs}")
        cur_val = self.slider_slice.value()
        if cur_val >= zs:
            self.slider_slice.setValue(zs-1)
            
        self.txt_zmax.setText(str(zs-1))
        
        self.refresh_display()
        
    def refresh_display(self):
        if self.transposed_data is None:
            return
            
        self.display_data = self.transposed_data.copy()
        self.apply_transforms()
        self.update_image_display(bypass_imv=(self.display_data.ndim == 2))
    @property
    def data_multiplier(self):
        if self.disp_as_dn:
            return getattr(self, '_itime_coadds', 1.0)
        return 1.0

    def apply_transforms(self):
        """Applies DN scaling, rotation, and flips to display_data"""
        if self.display_data is None:
            return
            
        # 1. Total DN scaling
        if self.disp_as_dn:
            self.display_data = self.display_data * self._itime_coadds
            
        # 2. Rotation & Flip
        if self.display_data.ndim == 3:
            # 3D: (z, x, y) - rotate spatial dimensions (axes 1 and 2)
            if self.flip:
                self.display_data = np.flip(self.display_data, axis=1) # flip X
            k = self.rot_angle // 90
            if k != 0:
                self.display_data = np.rot90(self.display_data, k=k, axes=(1, 2))
        else:
            # 2D: (x, y)
            if self.flip:
                self.display_data = np.flip(self.display_data, axis=0) # flip X
            k = self.rot_angle // 90
            if k != 0:
                self.display_data = np.rot90(self.display_data, k=k)
                
    def toggle_position_angle(self, checked):
        self.show_pa = bool(checked)

        # Always remove any existing compass items first to prevent accumulating multiple arrows
        for item_attr in ('pa_arrow_n', 'pa_text_n', 'pa_arrow_e', 'pa_text_e'):
            item = getattr(self, item_attr, None)
            if item is not None:
                try:
                    self.imv.getView().removeItem(item)
                except Exception:
                    pass
                setattr(self, item_attr, None)

        if not self.show_pa or self.display_data is None or self.transposed_data is None:
            return

        # Display array shape (ny, nx) or (nz, ny, nx) in current display orientation
        disp_shape = self.display_data.shape
        nx = disp_shape[-1]
        ny = disp_shape[-2]
        cx = (nx - 1) / 2.0
        cy = (ny - 1) / 2.0

        import math

        # 1. Derive base North and East angles from unrotated WCS (or header)
        has_wcs_pa = False
        if self.wcs is not None and getattr(self.wcs, 'naxis', 0) >= 2:
            try:
                wcs_ctypes = [str(c).upper() for c in self.wcs.wcs.ctype]
                ra_axis = -1
                dec_axis = -1
                for idx, ctype in enumerate(wcs_ctypes):
                    if 'RA' in ctype:
                        ra_axis = idx
                    elif 'DEC' in ctype:
                        dec_axis = idx

                if ra_axis >= 0 and dec_axis >= 0:
                    x_axis_str = getattr(self, 'current_x_axis', 'AXIS 3')
                    y_axis_str = getattr(self, 'current_y_axis', 'AXIS 2')
                    x_axis_idx = int(x_axis_str.split()[-1]) - 1
                    y_axis_idx = int(y_axis_str.split()[-1]) - 1

                    # Un-rotated transposed data shape
                    trans_shape = self.transposed_data.shape
                    raw_cx = (trans_shape[-1] - 1) / 2.0
                    raw_cy = (trans_shape[-2] - 1) / 2.0

                    center_pix = [0.0] * self.wcs.naxis
                    center_pix[x_axis_idx] = raw_cx
                    center_pix[y_axis_idx] = raw_cy

                    world0 = list(self.wcs.pixel_to_world_values(*center_pix))
                    dec0 = world0[dec_axis]
                    ra0 = world0[ra_axis]

                    delta_deg = 0.001
                    world_n = list(world0)
                    world_n[dec_axis] = dec0 + delta_deg
                    pix_n = self.wcs.world_to_pixel_values(*world_n)
                    dx_n = pix_n[x_axis_idx] - raw_cx
                    dy_n = pix_n[y_axis_idx] - raw_cy

                    world_e = list(world0)
                    cos_dec = math.cos(math.radians(dec0)) if abs(dec0) < 89.5 else 1.0
                    world_e[ra_axis] = ra0 + (delta_deg / cos_dec)
                    pix_e = self.wcs.world_to_pixel_values(*world_e)
                    dx_e = pix_e[x_axis_idx] - raw_cx
                    dy_e = pix_e[y_axis_idx] - raw_cy

                    if math.hypot(dx_n, dy_n) > 1e-6 and math.hypot(dx_e, dy_e) > 1e-6:
                        theta_n_base = math.degrees(math.atan2(dy_n, dx_n))
                        theta_e_base = math.degrees(math.atan2(dy_e, dx_e))
                        has_wcs_pa = True
            except Exception:
                has_wcs_pa = False

        if not has_wcs_pa:
            header = self.header or {}
            rotposn = float(header.get('ROTPOSN', 0.0))
            instangl = float(header.get('INSTANGL', 0.0))
            instr = str(header.get('INSTR', '')).strip().lower()
            tel = str(header.get('TELESCOP', '')).strip()

            iangle = 42.5 if tel == 'Keck I' else 47.5
            if instr == 'spec':
                north_pa = rotposn - instangl
            elif instr == 'imag':
                north_pa = rotposn - instangl + iangle
            else:
                north_pa = rotposn - instangl

            # Unrotated: North is UP (theta_n_base = 90 deg), East is LEFT (theta_e_base = 180 deg)
            theta_n_base = 90.0 + north_pa
            theta_e_base = theta_n_base + 90.0

        # 2. Apply GUI view rotation (rot_angle in deg CCW) and horizontal flip
        theta_n_vis = (theta_n_base + self.rot_angle) % 360.0
        theta_e_vis = (theta_e_base + self.rot_angle) % 360.0

        if self.flip:
            theta_n_vis = (180.0 - theta_n_vis) % 360.0
            theta_e_vis = (180.0 - theta_e_vis) % 360.0

        # Compass size relative to image dimensions
        L = max(nx, ny) * 0.18
        headLen = L * 0.35
        tailWidth = max(max(nx, ny) * 0.012, 1.0)

        # --- Draw North arrow ---
        rad_n = math.radians(theta_n_vis)
        tip_x_n = cx + L * math.cos(rad_n)
        tip_y_n = cy + L * math.sin(rad_n)
        angle_n = (theta_n_vis + 180.0) % 360.0

        self.pa_arrow_n = pg.ArrowItem(pos=(tip_x_n, tip_y_n), angle=angle_n, headLen=headLen, tailLen=L, tailWidth=tailWidth, pen='r', brush='r', pxMode=False)
        self.imv.getView().addItem(self.pa_arrow_n)

        txt_x_n = cx + (L + headLen * 1.6) * math.cos(rad_n)
        txt_y_n = cy + (L + headLen * 1.6) * math.sin(rad_n)
        self.pa_text_n = pg.TextItem('N', color='r', anchor=(0.5, 0.5))
        self.pa_text_n.setPos(txt_x_n, txt_y_n)
        self.imv.getView().addItem(self.pa_text_n)

        # --- Draw East arrow ---
        rad_e = math.radians(theta_e_vis)
        tip_x_e = cx + L * math.cos(rad_e)
        tip_y_e = cy + L * math.sin(rad_e)
        angle_e = (theta_e_vis + 180.0) % 360.0

        self.pa_arrow_e = pg.ArrowItem(pos=(tip_x_e, tip_y_e), angle=angle_e, headLen=headLen, tailLen=L, tailWidth=tailWidth, pen='r', brush='r', pxMode=False)
        self.imv.getView().addItem(self.pa_arrow_e)

        txt_x_e = cx + (L + headLen * 1.6) * math.cos(rad_e)
        txt_y_e = cy + (L + headLen * 1.6) * math.sin(rad_e)
        self.pa_text_e = pg.TextItem('E', color='r', anchor=(0.5, 0.5))
        self.pa_text_e.setPos(txt_x_e, txt_y_e)
        self.imv.getView().addItem(self.pa_text_e)
        
    def update_image_display(self, set_index=None, bypass_imv=False, use_manual_levels=False, reset_view=False):
        if self.display_data is None:
            return

        is_new = getattr(self, '_is_new_data', False) or reset_view
        self._is_new_data = False

        try:
            if use_manual_levels:
                vmin = float(self.txt_min.text())
                vmax = float(self.txt_max.text())
            else:
                valid_data = self.display_data[~np.isnan(self.display_data)]
                if valid_data.size > 0:
                    sample = valid_data[::4] if valid_data.size > 10000 else valid_data
                    vmin = np.percentile(sample, 1)
                    vmax = np.percentile(sample, 99)
                else:
                    vmin, vmax = 0, 1
                if vmin == vmax:
                    vmax = vmin + 1
        except Exception:
            vmin, vmax = 0, 1
            
        if not use_manual_levels:
            self.txt_min.setText(f"{vmin:.3f}")
            self.txt_max.setText(f"{vmax:.3f}")
            
        scale_type = self.combo_scale.currentText()
        if scale_type == "Linear":
            render_data = self.display_data
            render_vmin, render_vmax = vmin, vmax
        elif scale_type == "Negative":
            render_data = -self.display_data
            render_vmin, render_vmax = -vmax, -vmin
        elif scale_type == "Logarithmic":
            render_data = np.log10(np.clip(self.display_data - vmin + 1, 1, None))
            render_vmin, render_vmax = 0.0, np.log10(max(1.0, vmax - vmin + 1))
        elif scale_type == "Sqrt":
            render_data = np.sqrt(np.clip(self.display_data - vmin, 0, None))
            render_vmin, render_vmax = 0.0, np.sqrt(max(0.0, vmax - vmin))
        elif scale_type == "AsinH":
            noise = (vmax - vmin) / 100.0 if vmax > vmin else 1.0
            render_data = np.arcsinh((self.display_data - vmin) / noise)
            render_vmin, render_vmax = 0.0, np.arcsinh((vmax - vmin) / noise)
        elif scale_type == "HistEq":
            valid_mask = ~np.isnan(self.display_data)
            valid_data = self.display_data[valid_mask]
            if valid_data.size > 50000:
                valid_data = np.random.choice(valid_data, 50000)
            sorted_flat = np.sort(valid_data)
            
            render_data = np.zeros_like(self.display_data)
            render_data[valid_mask] = np.searchsorted(sorted_flat, self.display_data[valid_mask]) / float(len(sorted_flat))
            render_vmin, render_vmax = 0.0, 1.0
            
        view = self.imv.getView()
        view_rect = None if is_new else (view.viewRect() if self.imv.image is not None else None)

        if bypass_imv:
            self.imv.getImageItem().setImage(render_data, autoLevels=False, levels=(render_vmin, render_vmax))
        else:
            self.imv.setImage(render_data, autoRange=is_new, autoLevels=False, levels=(render_vmin, render_vmax))
            if set_index is not None and hasattr(self.imv, 'tVals'):
                self.imv.setCurrentIndex(set_index)

        if is_new:
            view.autoRange()
        elif view_rect is not None and not view_rect.isEmpty():
            view.setRange(rect=view_rect, padding=0)

        self.update_slice_info()
        if getattr(self, 'show_pa', False):
            self.toggle_position_angle(True)

    def mouse_moved(self, evt):
        if self.display_data is None:
            return
            
        pos = evt[0]  # using signal proxy returns tuple of args
        if self.imv.getView().sceneBoundingRect().contains(pos):
            mouse_point = self.imv.getView().mapSceneToView(pos)
            x, y = int(mouse_point.x()), int(mouse_point.y())
            
            shape = self.display_data.shape
            # If 3D, shape is (z, x, y)
            is_3d = (self.display_data.ndim == 3)
            max_x = shape[1] if is_3d else shape[0]
            max_y = shape[2] if is_3d else shape[1]
            
            if 0 <= x < max_x and 0 <= y < max_y:
                self.lbl_x.setText(f"X: {x}")
                self.lbl_y.setText(f"Y: {y}")
                
                if is_3d:
                    z = self.slider_slice.value()
                    val = self.display_data[z, x, y]
                else:
                    val = self.display_data[x, y]
                unit = "DN" if self.disp_as_dn else "DN/s"
                self.lbl_val.setText(f"Value: {val:.5g} {unit}")
                
                if self.wcs and self.wcs.naxis >= 2:
                    try:
                        # Inverse rotation and flip to get original pixel coordinates
                        orig_x, orig_y = x, y
                        
                        # Invert rotation
                        k = self.rot_angle // 90
                        if k != 0:
                            if k == 1: # rotated 90
                                orig_x, orig_y = orig_y, max_x - 1 - orig_x
                            elif k == 2: # rotated 180
                                orig_x, orig_y = max_x - 1 - orig_x, max_y - 1 - orig_y
                            elif k == 3: # rotated 270
                                orig_x, orig_y = max_y - 1 - orig_y, orig_x
                                
                        # Invert flip
                        if self.flip:
                            orig_max_x = max_y if k % 2 == 1 else max_x
                            orig_x = orig_max_x - 1 - orig_x
                            
                        # WCS pixel to world
                        if self.wcs.naxis == 2:
                            p1 = orig_x; p2 = orig_y
                            world = self.wcs.pixel_to_world(p1, p2)
                            self.lbl_wcs.setText(f"WCS: {world.ra.to_string(unit=u.hour, sep='hms', precision=3)}  {world.dec.to_string(unit=u.deg, sep='dms', precision=2)}")
                        if self.wcs.naxis >= 3 and self.wcs_z_idx is not None:
                            if hasattr(self, 'radio_range') and self.radio_range.isChecked():
                                try:
                                    z_min = max(0, int(self.txt_zmin.text() or 0))
                                    z_max = max(0, int(self.txt_zmax.text() or 0))
                                    z = (z_min + z_max) // 2
                                except ValueError:
                                    z = 0
                            else:
                                z = self.slider_slice.value()
                            
                            val_dict = {
                                getattr(self, 'current_x_axis', 'AXIS 1'): orig_x,
                                getattr(self, 'current_y_axis', 'AXIS 2'): orig_y,
                                getattr(self, 'current_z_axis', 'AXIS 3'): z
                            }
                            p1 = val_dict.get('AXIS 1', 0)
                            p2 = val_dict.get('AXIS 2', 0)
                            p3 = val_dict.get('AXIS 3', 0)
                            
                            vals = self.wcs.pixel_to_world_values(p1, p2, p3)
                            disp_axes = [getattr(self, 'current_x_axis', 'AXIS 1'), getattr(self, 'current_y_axis', 'AXIS 2')]
                            wcs_parts = []
                            for ax in disp_axes:
                                ax_idx = int(ax.split()[-1]) - 1
                                if ax_idx < len(vals):
                                    val = vals[ax_idx]
                                    phys = self.wcs.world_axis_physical_types[ax_idx]
                                    if phys == 'pos.eq.ra':
                                        coord = SkyCoord(ra=val*u.deg, dec=0*u.deg)
                                        wcs_parts.append(f"RA: {coord.ra.to_string(unit=u.hour, sep='hms', precision=3)}")
                                    elif phys == 'pos.eq.dec':
                                        coord = SkyCoord(ra=0*u.deg, dec=val*u.deg)
                                        wcs_parts.append(f"DEC: {coord.dec.to_string(unit=u.deg, sep='dms', precision=2)}")
                                    else:
                                        unit = self.wcs.world_axis_units[ax_idx]
                                        wcs_parts.append(f"{phys}: {val:.5g} {unit}")
                            wcs_text = "  |  ".join(wcs_parts)
                                
                            self.lbl_wcs.setText(f"WCS: {wcs_text}")
                    except Exception as e:
                        print(f"WCS Error: {e}")
                        self.lbl_wcs.setText("WCS: Error computing")
                else:
                    self.lbl_wcs.setText("WCS: N/A")
            else:
                self.lbl_val.setText("Value: Out of Bounds")

    def set_colormap(self, cmap_name=None, invert=None):
        if cmap_name is not None:
            self.current_cmap_name = cmap_name
        else:
            cmap_name = getattr(self, 'current_cmap_name', 'cmc.oslo')
            
        if invert is not None:
            self.is_cmap_inverted = invert
        else:
            invert = getattr(self, 'is_cmap_inverted', False)
            
        lookup_name = cmap_name
        if invert:
            if lookup_name.endswith('_r'):
                lookup_name = lookup_name[:-2]
            else:
                lookup_name = lookup_name + '_r'
                
        try:
            import pyqtgraph as pg
            try:
                cmap = pg.colormap.get(lookup_name)
            except Exception:
                try:
                    import cmcrameri.cm
                except ImportError:
                    pass
                cmap = pg.colormap.getFromMatplotlib(lookup_name)
                
            self.imv.setColorMap(cmap)
        except Exception as e:
            print(f"Warning: Could not set colormap {lookup_name}: {e}")

    def toggle_colorbar(self, show: bool):
        if show:
            self.imv.ui.histogram.show()
            self.update_colorbar_label()
        else:
            self.imv.ui.histogram.hide()

    def update_colorbar_label(self):
        if self.imv.ui.histogram.isVisible():
            unit = "Total DN" if getattr(self, 'disp_as_dn', False) else "DN/s"
            self.imv.ui.histogram.axis.setLabel("Pixel Value", units=unit)
