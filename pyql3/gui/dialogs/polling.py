from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QCheckBox, QDoubleSpinBox,
)

from pyql3.services.poller import DEFAULT_POLL_INTERVAL, watcher_of


class PollingDialog(QDialog):
    """Configure the watch for one window's poller.

    `confirm_takeover(path)` is called before starting a watch and may return False to
    abandon it. A directory is watched by exactly one window, so pointing this window at
    a directory another window already watches moves the watch -- and where auto-loaded
    frames appear -- which is not something to do without asking.
    """

    def __init__(self, poller, parent=None, config=None, confirm_takeover=None):
        super().__init__(parent)
        self.setWindowTitle("Directory Polling Configuration")
        self.poller = poller
        self.config = config
        self.confirm_takeover = confirm_takeover
        self.resize(460, 215)

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

        self.lbl_owner = QLabel()
        self.lbl_owner.setWordWrap(True)
        layout.addWidget(self.lbl_owner)

        lbl_help = QLabel(
            "New files are displayed once their size stops changing. When several "
            "arrive together, only the newest is shown. Files load into this window."
        )
        lbl_help.setWordWrap(True)
        layout.addWidget(lbl_help)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

        self.refresh_owner_hint()

    def refresh_owner_hint(self):
        """Say so when the directory in the box is already watched by another window."""
        path = self.txt_dir.text()
        other = watcher_of(path) if path else None
        if other is None or other is self.poller:
            self.lbl_owner.clear()
            self.lbl_owner.setVisible(False)
            return
        self.lbl_owner.setText(
            f"Note: {_poller_window_name(other)} is already watching this directory. "
            "Enabling polling here moves the watch to this window."
        )
        self.lbl_owner.setVisible(True)

    def start_watch(self, path):
        """Start watching `path`, asking first if that takes a watch from another window."""
        if not path:
            return False
        if self.confirm_takeover is not None and not self.confirm_takeover(path):
            return False
        started = self.poller.start_polling(path)
        self.refresh_owner_hint()
        return started

    def _set_active_silently(self, checked):
        """Reflect the real polling state without re-entering `toggle_polling`."""
        self.chk_active.blockSignals(True)
        self.chk_active.setChecked(checked)
        self.chk_active.blockSignals(False)

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select Directory to Watch")
        if path:
            self.txt_dir.setText(path)
            self.refresh_owner_hint()
            if self.chk_active.isChecked():
                self._set_active_silently(self.start_watch(path))

    def set_interval(self, seconds):
        # Assigning this restarts an active observer so the new period takes effect.
        self.poller.interval = seconds
        if self.config is not None:
            self.config.set("polling_interval", seconds)

    def toggle_polling(self, checked):
        path = self.txt_dir.text()
        if checked and path:
            # A declined takeover leaves polling off, so the box must go back up.
            self._set_active_silently(self.start_watch(path))
        else:
            self.poller.stop_polling()
            self.refresh_owner_hint()


def _poller_window_name(poller):
    """A human name for the window owning `poller`, for use in a sentence."""
    owner = poller.parent()
    title = owner.windowTitle() if owner is not None and hasattr(owner, 'windowTitle') else ""
    return f'"{title}"' if title else "another window"
