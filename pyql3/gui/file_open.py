"""Routing for files opened from the desktop rather than from argv.

Double-clicking a FITS file in Finder (or dropping one on the Dock icon) does **not** pass
the path in `sys.argv`. macOS launches the application and sends it an open-document Apple
Event, which Qt delivers as a `QEvent.Type.FileOpen` to the `QApplication` object. An
application that only reads `sys.argv` therefore opens an empty window.

This is also why `QuickLook3.spec` leaves PyInstaller's `argv_emulation` off: emulation
tries to translate those events into argv before Qt starts, and handling the event properly
is both more reliable and works for every later open, not just the launching one.

On a cold launch the event usually arrives before `MainWindow` exists, so paths are queued
until `set_loader()` supplies something to open them with.
"""

from PySide6.QtCore import QEvent, QObject


class FileOpenHandler(QObject):
    """An event filter for the `QApplication` that forwards open-document requests.

    Install it on the application *before* the main window is built::

        handler = FileOpenHandler(app)
        app.installEventFilter(handler)
        ...
        handler.set_loader(window.load_fits)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loader = None
        self.pending = []

    def set_loader(self, loader):
        """Attach the callable that opens a path, and flush anything already queued."""
        self._loader = loader
        queued, self.pending = self.pending, []
        for path in queued:
            loader(path)

    def open_path(self, path):
        """Open `path` now, or queue it until a loader is attached."""
        if not path:
            return
        if self._loader is None:
            self.pending.append(path)
        else:
            self._loader(path)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FileOpen:
            # QFileOpenEvent.file() is empty for a URL-only request (a dropped bookmark,
            # say), in which case the local path has to come off the QUrl instead.
            path = event.file() or event.url().toLocalFile()
            if path:
                self.open_path(path)
                return True
        return super().eventFilter(watched, event)
