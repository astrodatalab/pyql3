import numpy as np
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, 
                               QLabel, QPushButton, QComboBox, QRadioButton, 
                               QButtonGroup, QWidget, QLineEdit, QFileDialog, 
                               QDoubleSpinBox, QMessageBox, QGroupBox)
from PySide6.QtCore import Qt
from astropy.io import fits

class ArithmeticDialog(QDialog):
    def __init__(self, parent=None, image_viewer=None):
        super().__init__(parent)
        self.image_viewer = image_viewer
        self.setWindowTitle("Image Arithmetic")
        self.resize(600, 250)
        
        main_layout = QVBoxLayout(self)
        cols_layout = QHBoxLayout()
        
        # Operand 1
        group1 = QGroupBox("Operand 1")
        vbox1 = QVBoxLayout(group1)
        
        self.rb1_active = QRadioButton("Active Image")
        self.rb1_file = QRadioButton("File")
        self.rb1_num = QRadioButton("Number")
        self.rb1_active.setChecked(True)
        
        self.bg1 = QButtonGroup(self)
        self.bg1.addButton(self.rb1_active, 0)
        self.bg1.addButton(self.rb1_file, 1)
        self.bg1.addButton(self.rb1_num, 2)
        
        self.txt1_file = QLineEdit()
        self.txt1_file.setReadOnly(True)
        self.btn1_browse = QPushButton("Browse")
        self.btn1_browse.clicked.connect(lambda: self.browse_file(self.txt1_file))
        
        file1_layout = QHBoxLayout()
        file1_layout.addWidget(self.txt1_file)
        file1_layout.addWidget(self.btn1_browse)
        
        self.spin1 = QDoubleSpinBox()
        self.spin1.setRange(-1e10, 1e10)
        self.spin1.setValue(0.0)
        
        vbox1.addWidget(self.rb1_active)
        vbox1.addWidget(self.rb1_file)
        vbox1.addLayout(file1_layout)
        vbox1.addWidget(self.rb1_num)
        vbox1.addWidget(self.spin1)
        
        self.bg1.idToggled.connect(self.update_ui_state)
        
        # Operation
        group_op = QGroupBox("Operation")
        vbox_op = QVBoxLayout(group_op)
        
        self.rb_add = QRadioButton("+ (Add)")
        self.rb_sub = QRadioButton("- (Subtract)")
        self.rb_mul = QRadioButton("* (Multiply)")
        self.rb_div = QRadioButton("/ (Divide)")
        self.rb_add.setChecked(True)
        
        self.bg_op = QButtonGroup(self)
        self.bg_op.addButton(self.rb_add, 0)
        self.bg_op.addButton(self.rb_sub, 1)
        self.bg_op.addButton(self.rb_mul, 2)
        self.bg_op.addButton(self.rb_div, 3)
        
        vbox_op.addWidget(self.rb_add)
        vbox_op.addWidget(self.rb_sub)
        vbox_op.addWidget(self.rb_mul)
        vbox_op.addWidget(self.rb_div)
        vbox_op.addStretch()
        
        # Operand 2
        group2 = QGroupBox("Operand 2")
        vbox2 = QVBoxLayout(group2)
        
        self.rb2_active = QRadioButton("Active Image")
        self.rb2_file = QRadioButton("File")
        self.rb2_num = QRadioButton("Number")
        self.rb2_num.setChecked(True)
        
        self.bg2 = QButtonGroup(self)
        self.bg2.addButton(self.rb2_active, 0)
        self.bg2.addButton(self.rb2_file, 1)
        self.bg2.addButton(self.rb2_num, 2)
        
        self.txt2_file = QLineEdit()
        self.txt2_file.setReadOnly(True)
        self.btn2_browse = QPushButton("Browse")
        self.btn2_browse.clicked.connect(lambda: self.browse_file(self.txt2_file))
        
        file2_layout = QHBoxLayout()
        file2_layout.addWidget(self.txt2_file)
        file2_layout.addWidget(self.btn2_browse)
        
        self.spin2 = QDoubleSpinBox()
        self.spin2.setRange(-1e10, 1e10)
        self.spin2.setValue(0.0)
        
        vbox2.addWidget(self.rb2_active)
        vbox2.addWidget(self.rb2_file)
        vbox2.addLayout(file2_layout)
        vbox2.addWidget(self.rb2_num)
        vbox2.addWidget(self.spin2)
        
        self.bg2.idToggled.connect(self.update_ui_state)
        
        cols_layout.addWidget(group1)
        cols_layout.addWidget(group_op)
        cols_layout.addWidget(group2)
        
        main_layout.addLayout(cols_layout)
        
        # Bottom Buttons
        btn_layout = QHBoxLayout()
        self.btn_calc = QPushButton("Calculate")
        self.btn_calc.clicked.connect(self.calculate)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_calc)
        btn_layout.addWidget(self.btn_close)
        
        main_layout.addLayout(btn_layout)
        
        self.update_ui_state()
        
    def update_ui_state(self):
        # Op 1
        id1 = self.bg1.checkedId()
        self.txt1_file.setEnabled(id1 == 1)
        self.btn1_browse.setEnabled(id1 == 1)
        self.spin1.setEnabled(id1 == 2)
        
        # Op 2
        id2 = self.bg2.checkedId()
        self.txt2_file.setEnabled(id2 == 1)
        self.btn2_browse.setEnabled(id2 == 1)
        self.spin2.setEnabled(id2 == 2)
        
    def browse_file(self, target_lineedit):
        filepath, _ = QFileDialog.getOpenFileName(self, "Open FITS File", "", "FITS Files (*.fits *.fit *.fits.gz);;All Files (*)")
        if filepath:
            target_lineedit.setText(filepath)
            
    def get_operand_data(self, bg_id, txt_box, spin_box):
        if bg_id == 0: # Active Image
            if self.image_viewer is None or getattr(self.image_viewer, 'raw_data', None) is None:
                raise ValueError("No active image loaded in the viewer.")
            return self.image_viewer.raw_data.copy(), self.image_viewer.header.copy() if hasattr(self.image_viewer, 'header') and self.image_viewer.header is not None else fits.Header(), "ActiveImage"
        elif bg_id == 1: # File
            path = txt_box.text()
            if not path:
                raise ValueError("No file selected.")
            data, header = fits.getdata(path, header=True)
            import os
            return data.astype(float), header, os.path.basename(path)
        elif bg_id == 2: # Number
            return float(spin_box.value()), None, str(spin_box.value())
            
    def calculate(self):
        try:
            d1, h1, name1 = self.get_operand_data(self.bg1.checkedId(), self.txt1_file, self.spin1)
            d2, h2, name2 = self.get_operand_data(self.bg2.checkedId(), self.txt2_file, self.spin2)
            
            # Check shapes if both are arrays
            if isinstance(d1, np.ndarray) and isinstance(d2, np.ndarray):
                if d1.shape != d2.shape:
                    QMessageBox.warning(self, "Warning", f"Images are not the same size!\nOp1: {d1.shape}\nOp2: {d2.shape}")
                    return
                    
            op_id = self.bg_op.checkedId()
            op_char = "+"
            
            with np.errstate(divide='ignore', invalid='ignore'):
                if op_id == 0:
                    result = d1 + d2
                    op_char = "+"
                elif op_id == 1:
                    result = d1 - d2
                    op_char = "-"
                elif op_id == 2:
                    result = d1 * d2
                    op_char = "*"
                elif op_id == 3:
                    if isinstance(d2, np.ndarray) and np.any(d2 == 0):
                        pass # errstate will handle zero div by producing inf or nan
                    elif isinstance(d2, float) and d2 == 0.0:
                        QMessageBox.warning(self, "Warning", "Cannot divide by zero.")
                        return
                    result = d1 / d2
                    op_char = "/"
                    
            # Build header
            final_header = h1 if h1 is not None else (h2 if h2 is not None else fits.Header())
            op_str = f"({name1} {op_char} {name2})"
            final_header.add_history(f"Arithmetic: {op_str}")
            
            # Spawning a new main window!
            from pyql3.gui.main_window import MainWindow
            
            # To avoid the window being garbage collected immediately, parent it (or store a ref)
            # Parent must be None so it acts as an independent top-level window.
            self.result_win = MainWindow()
            self.result_win.load_from_memory(result, final_header, title=op_str)
            self.result_win.show()
            
            # Prevent python from garbage collecting the window immediately
            if not hasattr(self, 'child_windows'):
                self.child_windows = []
            self.child_windows.append(self.result_win)
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Arithmetic error:\n{str(e)}")
