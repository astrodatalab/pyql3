"""Keeps track of the open `MainWindow`s, and decides which one a file opens in.

Several main windows can be open at once, each with its own FITS file, its own display
settings and its own tool dialogs. Nothing about that needs coordinating *except* the
question "which window does this file go to?", which arises whenever a path arrives
with no window attached to it:

- Finder sends an open-document event (see `pyql3.gui.file_open`).
- `quicklook3 cube.fits` is run while the application is already open.
- **File -> New Window** has to put its window somewhere on screen.

The destination is the most recently used window. That is deliberately *not* "the
window that happened to be created first": binding to one particular window is the bug
this module exists to remove -- `main.py` used to hand `window.load_fits` to the
open-document handler, so once that window was closed a double-clicked file called a
method on a deleted C++ object.

Auto-loaded frames from directory polling do **not** come through here. A poller
belongs to the window that started it and loads into that window, so a watch set up to
follow a reduction cannot hijack a window someone has since opened to compare
something (`pyql3.services.poller`, "Why only one poller may watch a directory").
"""

from PySide6.QtCore import QPoint


def _is_alive(window):
    """True if `window` still has a live C++ object behind it.

    A closed window is normally unregistered by `MainWindow.closeEvent`, but Qt can
    delete a widget without Python noticing, and touching one that has gone raises
    RuntimeError. Treat that as "not a candidate" rather than letting it escape into
    the middle of opening a file.
    """
    try:
        window.isVisible()
    except RuntimeError:
        return False
    return True


class WindowManager:
    """The set of open main windows, in creation order, plus a most-recently-used order."""

    #: Offset of each new window from the last, so a new window is not hidden exactly
    #: behind the one it was opened from.
    CASCADE_OFFSET = 32

    def __init__(self):
        self._windows = []
        self._mru = []

    # ------------------------------------------------------------------ registry

    def register(self, window):
        if window not in self._windows:
            self._windows.append(window)
        self.touch(window)

    def unregister(self, window):
        if window in self._windows:
            self._windows.remove(window)
        if window in self._mru:
            self._mru.remove(window)

    def touch(self, window):
        """Record `window` as the most recently used one. Called on activation."""
        if window in self._mru:
            self._mru.remove(window)
        self._mru.insert(0, window)

    def windows(self):
        """Live windows in creation order, which is the order the Window menu lists."""
        self._windows = [w for w in self._windows if _is_alive(w)]
        self._mru = [w for w in self._mru if w in self._windows]
        return list(self._windows)

    def count(self):
        return len(self.windows())

    # --------------------------------------------------------------- destination

    def most_recent(self):
        """The window a new file should open in, or None if no window is open.

        The active window wins when there is one; otherwise the most recently
        activated. On a cold launch nothing has been activated yet, so this falls back
        to the first window created -- which is the only one there is.
        """
        live = self.windows()
        if not live:
            return None
        for window in live:
            if window.isActiveWindow():
                return window
        for window in self._mru:
            return window
        return live[0]

    # ------------------------------------------------------------------- actions

    def new_window(self, show=True, near=None):
        """Create, register and return a new `MainWindow`.

        `near` is the window to cascade away from; it defaults to the most recent one.
        """
        from pyql3.gui.main_window import MainWindow

        anchor = near if near is not None and _is_alive(near) else self.most_recent()
        window = MainWindow()
        if anchor is not None:
            step = self.CASCADE_OFFSET
            window.move(anchor.pos() + QPoint(step, step))
            window.resize(anchor.size())
        if show:
            window.show()
            window.raise_()
            window.activateWindow()
        return window

    def open_path(self, path, ext=None):
        """Open `path` in the most recently used window, creating one if none is open.

        This is the callable handed to `FileOpenHandler.set_loader`, so its signature
        has to match `MainWindow.load_fits`'s first arguments.
        """
        window = self.most_recent()
        if window is None:
            window = self.new_window()
        loaded = window.load_fits(path, ext=ext)
        window.raise_()
        window.activateWindow()
        return loaded


#: Process-wide manager. There is one window list per running application.
_manager = None


def get_window_manager():
    global _manager
    if _manager is None:
        _manager = WindowManager()
    return _manager
