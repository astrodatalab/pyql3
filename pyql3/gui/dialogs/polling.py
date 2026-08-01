from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QCheckBox, QDoubleSpinBox,
)

from pyql3.services.poller import DEFAULT_POLL_INTERVAL


class PollingDialog(QDialog):
    def __init__(self, poller, parent=None, config=None):
        super().__init__(parent)
        self.setWindowTitle("Directory Polling Configuration")
        self.poller = poller
        self.config = config
        self.resize(460, 190)

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

        h2 = QHBoxLayout()
        self.spin_interval = QDoubleSpinBox()
        self.spin_interval.setRange(0.5, 60.0)
        self.spin_interval.setSingleStep(0.5)
        self.spin_interval.setDecimals(1)
        self.spin_interval.setSuffix(" s")
        self.spin_interval.setValue(getattr(poller, "interval", DEFAULT_POLL_INTERVAL))
        self.spin_interval.setToolTip(
            "How often the watch directory is scanned.\n\n"
            "The directory is scanned rather than watched for filesystem events, so "
            "that files written by another host onto an NFS share are seen. Scan cost "
            "grows with the number of files in the directory, so raise this for a "
            "directory holding a whole run."
        )
        self.spin_interval.valueChanged.connect(self.set_interval)
        h2.addWidget(QLabel("Scan Interval:"))
        h2.addWidget(self.spin_interval)
        h2.addStretch()
        layout.addLayout(h2)

        self.chk_active = QCheckBox("Enable Polling")
        self.chk_active.setChecked(self.poller.is_polling())
        self.chk_active.toggled.connect(self.toggle_polling)
        layout.addWidget(self.chk_active)

        lbl_help = QLabel(
            "New files are displayed once their size stops changing. When several "
            "arrive together, only the newest is shown."
        )
        lbl_help.setWordWrap(True)
        layout.addWidget(lbl_help)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select Directory to Watch")
        if path:
            self.txt_dir.setText(path)
            if self.chk_active.isChecked():
                self.poller.start_polling(path)

    def set_interval(self, seconds):
        # Assigning this restarts an active observer so the new period takes effect.
        self.poller.interval = seconds
        if self.config is not None:
            self.config.set("polling_interval", seconds)

    def toggle_polling(self, checked):
        path = self.txt_dir.text()
        if checked and path:
            self.poller.start_polling(path)
        else:
            self.poller.stop_polling()
