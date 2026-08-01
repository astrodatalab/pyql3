"""Watch a directory for new FITS files and announce them once they are complete.

Why this polls instead of using filesystem notifications
--------------------------------------------------------
`watchdog.observers.Observer` resolves to a *kernel* backend — FSEvents on macOS,
inotify on Linux. Both only report changes made through the local kernel. When the
OSIRIS DRP writes from another host onto an NFS share, the watching client's kernel
never learns anything happened and no event is ever delivered. We therefore use
`PollingObserver`, which diffs directory snapshots and so sees remote writes.

Why a file is not announced the moment it appears
-------------------------------------------------
A file is created before it is written. Loading at first sight yields a truncated
FITS, which astropy rejects (`memmap=False` makes it fail loudly rather than serving
padded garbage). We therefore wait for `(st_size, st_mtime_ns)` to hold steady across
`SETTLE_CHECKS` consecutive samples before announcing.

Note that on NFS this is necessary but *not sufficient*: clients cache file attributes
(`acregmin`/`acregmax`, typically 3-30 s), so a file still growing on the server can
look settled here. Stability decides *when to try*, never *whether the file is good* —
the caller must treat a failed parse as "retry later", not "corrupt". `MainWindow`
does exactly that.
"""

import os

from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver
from PySide6.QtCore import QObject, QTimer, Signal

#: Seconds between directory scans and between settle checks.
DEFAULT_POLL_INTERVAL = 2.0

#: Consecutive unchanged (size, mtime) samples before a file counts as settled.
SETTLE_CHECKS = 2

#: While a batch is still arriving we hold back rather than displaying frames that
#: would be replaced moments later. This caps how long that hold can last, so a
#: directory under continuous write still shows something.
MAX_HOLD_TICKS = 8

FITS_SUFFIXES = (".fits", ".fit", ".fts", ".fits.gz", ".fit.gz", ".fts.gz")


def is_fits_path(path):
    """True for FITS filenames, including the compressed forms.

    `os.path.splitext` is deliberately not used: it returns ``.gz`` for
    ``frame.fits.gz`` and would drop compressed cubes on the floor.
    """
    return os.path.basename(path).lower().endswith(FITS_SUFFIXES)


class FITSFileHandler(FileSystemEventHandler):
    """Feeds candidate paths to the poller. Runs on the observer's thread."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def _consider(self, path, is_directory):
        if not is_directory and is_fits_path(path):
            self.callback(path)

    def on_created(self, event):
        self._consider(event.src_path, event.is_directory)

    def on_modified(self, event):
        # Required for the slow-write case: the file is created empty and only
        # becomes loadable later. Without this, a file that is still being written
        # when it is first seen would never be revisited.
        self._consider(event.src_path, event.is_directory)

    def on_moved(self, event):
        self._consider(event.dest_path, event.is_directory)


class DirectoryPoller(QObject):
    """Emits :attr:`file_detected` with the newest FITS file once writing has stopped."""

    #: Path of a settled FITS file that the application should display.
    file_detected = Signal(str)
    #: (number of files suppressed, path actually emitted) for a coalesced batch.
    batch_coalesced = Signal(int, str)

    # Internal: marshals a candidate from the observer thread onto the GUI thread.
    _candidate_seen = Signal(str)

    def __init__(self, parent=None, interval=DEFAULT_POLL_INTERVAL):
        super().__init__(parent)
        self.observer = None
        self.watch_path = None
        self._interval = float(interval)

        # path -> [last_signature, consecutive_stable_count]
        self._pending = {}
        # Newest file that has settled while we wait for a batch to finish arriving.
        self._held_path = None
        self._held_count = 0
        self._hold_ticks = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_pending)
        self._candidate_seen.connect(self._add_candidate)

    # ------------------------------------------------------------------ config

    @property
    def interval(self):
        """Seconds between directory scans. Applies on the next `start_polling`."""
        return self._interval

    @interval.setter
    def interval(self, seconds):
        self._interval = max(0.1, float(seconds))
        if self.observer is not None:
            # Restart so the observer picks up the new scan period.
            self.start_polling(self.watch_path)

    def is_polling(self):
        return self.observer is not None

    # ----------------------------------------------------------------- control

    def start_polling(self, path):
        self.stop_polling()

        if not path or not os.path.isdir(path):
            return False

        self.watch_path = path
        handler = FITSFileHandler(self._candidate_seen.emit)
        self.observer = PollingObserver(timeout=self._interval)
        self.observer.schedule(handler, path, recursive=False)
        self.observer.start()
        self._timer.start(int(self._interval * 1000))
        return True

    def stop_polling(self):
        self._timer.stop()
        self._pending.clear()
        self._held_path = None
        self._held_count = 0
        self._hold_ticks = 0
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None

    # ------------------------------------------------------------------ innards

    def _add_candidate(self, path):
        """Register a path to watch for stability. GUI thread only."""
        self._pending.setdefault(path, [None, 0])

    @staticmethod
    def _signature(path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_size, st.st_mtime_ns)

    @staticmethod
    def _mtime(path):
        try:
            return os.stat(path).st_mtime_ns
        except OSError:
            return -1

    def _check_pending(self):
        """One settle tick: promote stable files, then decide whether to announce."""
        settled = []
        for path, state in list(self._pending.items()):
            signature = self._signature(path)
            if signature is None:  # deleted or replaced while we watched it
                del self._pending[path]
                continue

            if signature == state[0] and signature[0] > 0:
                state[1] += 1
            else:
                state[0] = signature
                state[1] = 0

            if state[1] >= SETTLE_CHECKS:
                del self._pending[path]
                settled.append(path)

        if settled:
            # Keep only the newest: during a bulk copy the earlier frames would be
            # on screen for a fraction of a second before the next one replaced them.
            candidates = settled + ([self._held_path] if self._held_path else [])
            self._held_count += len(settled)
            self._held_path = max(candidates, key=self._mtime)

        if self._held_path is None:
            self._hold_ticks = 0
            return

        # Still files arriving: hold, so a batch produces one load instead of N.
        # The cap keeps a continuously-written directory from never displaying.
        if self._pending and self._hold_ticks < MAX_HOLD_TICKS:
            self._hold_ticks += 1
            return

        path, suppressed = self._held_path, self._held_count - 1
        self._held_path = None
        self._held_count = 0
        self._hold_ticks = 0

        if suppressed > 0:
            self.batch_coalesced.emit(suppressed, path)
        self.file_detected.emit(path)
