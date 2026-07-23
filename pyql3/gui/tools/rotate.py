from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QRadioButton, QButtonGroup, QCheckBox, 
    QPushButton, QGroupBox, QLabel, QDoubleSpinBox, QGridLayout
)
from pyql3.gui.tools.base_tool import BaseToolDialog

class RotateDialog(BaseToolDialog):
    def __init__(self, parent=None, image_viewer=None):
        super().__init__(parent, image_viewer, "Rotate Image")
        self.resize(320, 300)
        
        main_layout = QVBoxLayout()
        self.layout.addLayout(main_layout)
        
        # 1. Array Rotation Group (Pixel Grid)
        group_array = QGroupBox("Array Rotation (90° Steps)")
        array_layout = QVBoxLayout(group_array)
        
        radio_layout = QHBoxLayout()
        self.btn_group = QButtonGroup(self)
        self.rad_0 = QRadioButton("0°")
        self.rad_90 = QRadioButton("90°")
        self.rad_180 = QRadioButton("180°")
        self.rad_270 = QRadioButton("270°")
        
        self.btn_group.addButton(self.rad_0, 0)
        self.btn_group.addButton(self.rad_90, 90)
        self.btn_group.addButton(self.rad_180, 180)
        self.btn_group.addButton(self.rad_270, 270)
        
        if self.image_viewer:
            angle = self.image_viewer.rot_angle
            if angle == 90:
                self.rad_90.setChecked(True)
            elif angle == 180:
                self.rad_180.setChecked(True)
            elif angle == 270:
                self.rad_270.setChecked(True)
            else:
                self.rad_0.setChecked(True)
        else:
            self.rad_0.setChecked(True)
            
        radio_layout.addWidget(self.rad_0)
        radio_layout.addWidget(self.rad_90)
        radio_layout.addWidget(self.rad_180)
        radio_layout.addWidget(self.rad_270)
        array_layout.addLayout(radio_layout)
        
        self.btn_group.idClicked.connect(self.on_rotate_changed)
        
        self.chk_flip = QCheckBox("Flip Horizontal")
        if self.image_viewer:
            self.chk_flip.setChecked(self.image_viewer.flip)
        self.chk_flip.stateChanged.connect(self.on_flip_changed)
        array_layout.addWidget(self.chk_flip)
        
        main_layout.addWidget(group_array)
        
        # 2. View Rotation Group (Visual / Fine Alignment)
        group_view = QGroupBox("View Rotation (Visual Only)")
        view_layout = QGridLayout(group_view)
        
        view_layout.addWidget(QLabel("View Angle:"), 0, 0)
        self.spin_view_rot = QDoubleSpinBox()
        self.spin_view_rot.setRange(-360.0, 360.0)
        self.spin_view_rot.setSingleStep(0.5)
        self.spin_view_rot.setDecimals(1)
        if self.image_viewer:
            self.spin_view_rot.setValue(self.image_viewer.view_rotation)
        self.spin_view_rot.valueChanged.connect(self.on_view_spin_changed)
        view_layout.addWidget(self.spin_view_rot, 0, 1)
        
        self.btn_north_up = QPushButton("North Up")
        self.btn_north_up.setToolTip("Rotate view so North points straight up")
        self.btn_north_up.clicked.connect(self.on_north_up_clicked)
        view_layout.addWidget(self.btn_north_up, 1, 0)
        
        self.btn_reset_view = QPushButton("Reset View")
        self.btn_reset_view.setToolTip("Reset visual rotation to 0°")
        self.btn_reset_view.clicked.connect(self.on_reset_view_clicked)
        view_layout.addWidget(self.btn_reset_view, 1, 1)
        
        main_layout.addWidget(group_view)
        
        # 3. Position Angle Status Label
        self.lbl_pa = QLabel("North Angle: --")
        self.lbl_pa.setStyleSheet("font-weight: bold; padding: 4px;")
        main_layout.addWidget(self.lbl_pa)
        
        main_layout.addStretch()
        
        self.update_pa_info()
        
    def update_pa_info(self):
        if self.image_viewer is None:
            self.lbl_pa.setText("North Angle: --")
            self.btn_north_up.setEnabled(False)
            return

        theta_n_base, _, is_wcs = self.image_viewer.get_north_angle_base()
        if not is_wcs or theta_n_base is None:
            self.lbl_pa.setText("North Angle: No WCS / PA info")
            self.btn_north_up.setEnabled(False)
            return

        self.btn_north_up.setEnabled(True)

        # Compute current visual angle of North
        theta_n_vis = (theta_n_base + self.image_viewer.rot_angle + self.image_viewer.view_rotation) % 360.0
        if self.image_viewer.flip:
            theta_n_vis = (180.0 - theta_n_vis) % 360.0

        # Offset from UP (+Y, 90.0 deg)
        offset = ((theta_n_vis - 90.0 + 180.0) % 360.0) - 180.0
        if abs(offset) < 0.05:
            self.lbl_pa.setText("North Angle: North is Up")
        else:
            direction = "CW" if offset < 0 else "CCW"
            self.lbl_pa.setText(f"North Angle: {abs(offset):.1f}° {direction} from up")

    def on_rotate_changed(self, angle):
        if self.image_viewer:
            self.image_viewer.rot_angle = angle
            self.image_viewer.refresh_display()
            self.update_pa_info()

    def on_flip_changed(self, state):
        if self.image_viewer:
            self.image_viewer.flip = bool(state)
            self.image_viewer.refresh_display()
            self.update_pa_info()

    def on_view_spin_changed(self, val):
        if self.image_viewer:
            self.image_viewer.apply_view_rotation(val)
            self.update_pa_info()

    def on_north_up_clicked(self):
        if self.image_viewer is None:
            return

        theta_n_base, _, is_wcs = self.image_viewer.get_north_angle_base()
        if not is_wcs or theta_n_base is None:
            return

        # Current angle of North after array rotation & flip (excluding view_rotation)
        theta_n_array = (theta_n_base + self.image_viewer.rot_angle) % 360.0
        if self.image_viewer.flip:
            theta_n_array = (180.0 - theta_n_array) % 360.0

        # We want (theta_n_array + view_rot) == 90.0 (straight UP)
        target_view_rot = ((90.0 - theta_n_array + 180.0) % 360.0) - 180.0
        
        self.spin_view_rot.blockSignals(True)
        self.spin_view_rot.setValue(target_view_rot)
        self.spin_view_rot.blockSignals(False)
        
        self.image_viewer.apply_view_rotation(target_view_rot)
        self.update_pa_info()

    def on_reset_view_clicked(self):
        if self.image_viewer:
            self.spin_view_rot.blockSignals(True)
            self.spin_view_rot.setValue(0.0)
            self.spin_view_rot.blockSignals(False)
            
            self.image_viewer.apply_view_rotation(0.0)
            self.update_pa_info()
