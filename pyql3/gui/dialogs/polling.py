from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog, QCheckBox
from pyql3.services.poller import DirectoryPoller

class PollingDialog(QDialog):
    def __init__(self, poller, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Directory Polling Configuration")
        self.poller = poller
        self.resize(400, 150)
        
        layout = QVBoxLayout(self)
        
        h1 = QHBoxLayout()
        self.txt_dir = QLineEdit()
        self.txt_dir.setReadOnly(True)
        if self.poller.watch_path:
            self.txt_dir.setText(self.poller.watch_path)
            
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self.browse)
        h1.addWidget(QLabel("Watch Directory:"))
        h1.addWidget(self.txt_dir)
        h1.addWidget(btn_browse)
        layout.addLayout(h1)
        
        self.chk_active = QCheckBox("Enable Polling")
        self.chk_active.setChecked(self.poller.observer is not None)
        self.chk_active.toggled.connect(self.toggle_polling)
        layout.addWidget(self.chk_active)
        
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)
        
    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select Directory to Watch")
        if path:
            self.txt_dir.setText(path)
            if self.chk_active.isChecked():
                self.poller.start_polling(path)
                
    def toggle_polling(self, checked):
        path = self.txt_dir.text()
        if checked and path:
            self.poller.start_polling(path)
        else:
            self.poller.stop_polling()
