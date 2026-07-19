from PySide6.QtWidgets import QVBoxLayout, QRadioButton, QButtonGroup, QCheckBox, QPushButton
from pyql3.gui.tools.base_tool import BaseToolDialog

class RotateDialog(BaseToolDialog):
    def __init__(self, parent=None, image_viewer=None):
        super().__init__(parent, image_viewer, "Rotate Image")
        self.resize(250, 150)
        
        main_layout = QVBoxLayout()
        self.layout.addLayout(main_layout)
        
        self.btn_group = QButtonGroup(self)
        self.rad_0 = QRadioButton("0°")
        self.rad_90 = QRadioButton("90°")
        self.rad_180 = QRadioButton("180°")
        self.rad_270 = QRadioButton("270°")
        
        self.btn_group.addButton(self.rad_0, 0)
        self.btn_group.addButton(self.rad_90, 90)
        self.btn_group.addButton(self.rad_180, 180)
        self.btn_group.addButton(self.rad_270, 270)
        
        # Set default
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
            
        main_layout.addWidget(self.rad_0)
        main_layout.addWidget(self.rad_90)
        main_layout.addWidget(self.rad_180)
        main_layout.addWidget(self.rad_270)
        
        self.btn_group.idClicked.connect(self.on_rotate_changed)
        
        self.chk_flip = QCheckBox("Flip Horizontal")
        if self.image_viewer:
            self.chk_flip.setChecked(self.image_viewer.flip)
        self.chk_flip.stateChanged.connect(self.on_flip_changed)
        main_layout.addWidget(self.chk_flip)
        
        main_layout.addStretch()
        
    def on_rotate_changed(self, angle):
        if self.image_viewer:
            self.image_viewer.rot_angle = angle
            self.image_viewer.refresh_display()
            
    def on_flip_changed(self, state):
        if self.image_viewer:
            self.image_viewer.flip = bool(state)
            self.image_viewer.refresh_display()
