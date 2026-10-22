"""Directory-polling behaviour: slow writes, bursts, and auto-load retry.

These drive `DirectoryPoller._check_pending()` directly rather than sleeping on a real
observer thread. A timing test that waits on wall-clock passes or fails by luck; calling
the settle tick explicitly makes "two unchanged samples means settled" the thing actually
under test, and keeps the suite fast and deterministic.
"""

import io
import os

import numpy as np
import pytest
from astropy.io import fits
from astropy.utils.exceptions import AstropyUserWarning

from pyql3.services.poller import DirectoryPoller, SETTLE_CHECKS, is_fits_path


def _write_cube(path, value=42.0, shape=(10, 8, 8)):
    fits.PrimaryHDU(np.full(shape, value, dtype="f4")).writeto(path, overwrite=True)


def _tick(poller, times=1):
    for _ in range(times):
        poller._check_pending()


def _settle(poller):
    """Enough ticks for an unchanging file to be promoted."""
    _tick(poller, SETTLE_CHECKS + 1)


# --------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    "name,expected",
    [
        ("frame.fits", True),
        ("frame.FITS", True),
        ("frame.fit", True),
        ("frame.fts", True),
        ("frame.fits.gz", True),   # os.path.splitext would have said ".gz"
        ("frame.txt", False),
        ("frame.gz", False),
    ],
)
def test_is_fits_path_covers_compressed_and_case(name, expected):
    assert is_fits_path("/data/" + name) is expected


# ----------------------------------------------------------- settle behaviour


def test_file_still_growing_is_not_announced(qapp, tmp_path):
    """The bug this replaces: a file was loaded 500 ms after creation whether or not
    the DRP had finished writing it."""
    poller = DirectoryPoller()
    seen = []
    poller.file_detected.connect(seen.append)

    path = str(tmp_path / "growing.fits")
    with open(path, "wb") as handle:
        handle.write(b"\0" * 2880)
        handle.flush()
        poller._add_candidate(path)

        # Keeps changing between ticks, so it must never be announced.
        for _ in range(6):
            _tick(poller)
            handle.write(b"\0" * 2880)
            handle.flush()

    assert seen == [], "a file that is still growing must not be announced"


def test_file_is_announced_once_it_stops_changing(qapp, tmp_path):
    poller = DirectoryPoller()
    seen = []
    poller.file_detected.connect(seen.append)

    path = str(tmp_path / "done.fits")
    _write_cube(path)
    poller._add_candidate(path)

    _tick(poller)
    assert seen == [], "must not fire on the very first sample"

    _settle(poller)
    assert seen == [path]


def test_zero_length_file_is_never_announced(qapp, tmp_path):
    """A 0-byte file is stable but unreadable; on_created fires at exactly this moment."""
    poller = DirectoryPoller()
    seen = []
    poller.file_detected.connect(seen.append)

    path = str(tmp_path / "empty.fits")
    open(path, "wb").close()
    poller._add_candidate(path)
    _settle(poller)

    assert seen == []


def test_vanished_file_is_dropped(qapp, tmp_path):
    poller = DirectoryPoller()
    seen = []
    poller.file_detected.connect(seen.append)

    path = str(tmp_path / "transient.fits")
    _write_cube(path)
    poller._add_candidate(path)
    _tick(poller)
    os.remove(path)
    _settle(poller)

    assert seen == []
    assert poller._pending == {}


# ------------------------------------------------------------------- batching


def test_burst_of_files_displays_only_the_newest(qapp, tmp_path):
    """Copying many files at once must not flash each one on screen in turn."""
    poller = DirectoryPoller()
    seen = []
    batches = []
    poller.file_detected.connect(seen.append)
    poller.batch_coalesced.connect(lambda n, p: batches.append((n, p)))

    paths = []
    for i in range(5):
        path = str(tmp_path / f"burst{i}.fits")
        _write_cube(path)
        # Force a strictly increasing mtime so "newest" is unambiguous. Space them a whole
        # second apart, not the nanosecond this used to use: NTFS timestamps have 100 ns
        # resolution, so five files one nanosecond apart are all *exactly* the same age on
        # Windows and `max` returns whichever came first -- burst0, not burst4.
        stamp = (i + 1) * 1_000_000_000
        os.utime(path, ns=(stamp, stamp))
        poller._add_candidate(path)
        paths.append(path)

    # Premise: the filesystem really did record five distinct ages. Asserted rather than
    # assumed, so a filesystem coarser still says so instead of failing as if the poller
    # had picked the wrong file.
    ages = [os.stat(p).st_mtime_ns for p in paths]
    assert ages == sorted(set(ages)), f"test premise: mtimes not distinct and increasing: {ages}"

    _settle(poller)

    assert seen == [paths[-1]], "only the newest file of a batch should be displayed"
    assert batches == [(4, paths[-1])], "the 4 skipped files should be reported"


def test_files_arriving_one_at_a_time_are_each_displayed(qapp, tmp_path):
    """Coalescing must not suppress normal observing, where frames arrive spaced out."""
    poller = DirectoryPoller()
    seen = []
    poller.file_detected.connect(seen.append)

    first = str(tmp_path / "frame1.fits")
    _write_cube(first)
    poller._add_candidate(first)
    _settle(poller)

    second = str(tmp_path / "frame2.fits")
    _write_cube(second)
    poller._add_candidate(second)
    _settle(poller)

    assert seen == [first, second]


def test_batch_still_arriving_holds_back_then_releases(qapp, tmp_path):
    """While files are still landing we hold; once the directory goes quiet we emit."""
    poller = DirectoryPoller()
    seen = []
    poller.file_detected.connect(seen.append)

    settled = str(tmp_path / "settled.fits")
    _write_cube(settled)
    os.utime(settled, ns=(1_000_000_000, 1_000_000_000))
    poller._add_candidate(settled)

    # A second file is registered but never stabilises while the first settles.
    growing = str(tmp_path / "growing.fits")
    with open(growing, "wb") as handle:
        handle.write(b"\0" * 2880)
        handle.flush()
        poller._add_candidate(growing)

        for _ in range(SETTLE_CHECKS + 2):
            _tick(poller)
            handle.write(b"\0" * 2880)
            handle.flush()

        assert seen == [], "must hold while the batch is still arriving"

    # The writer stops; now everything settles and exactly one file is announced.
    _settle(poller)
    assert len(seen) == 1
    assert seen[0] == growing, "the newest file in the batch wins"


# ------------------------------------------------------- auto-load retry path


#: Retry schedule for the auto-load tests: one retry, 20 ms after the first attempt.
#: Waiting `RETRY_WAIT_MS` gives the retry ten times its delay to fire.
FAST_RETRY = (0.0, 0.02)
RETRY_WAIT_MS = 200


def _cube_bytes(value):
    buf = io.BytesIO()
    fits.PrimaryHDU(np.full((6, 5, 5), value, dtype="f4")).writeto(buf)
    return buf.getvalue()


def _write_partial(path, value):
    """The header and part of the data, as a frame still being copied looks."""
    with open(path, "wb") as f:
        f.write(_cube_bytes(value)[:2980])


def _write_whole(path, value):
    with open(path, "wb") as f:
        f.write(_cube_bytes(value))


@pytest.fixture
def polling_window(qapp, tmp_path, monkeypatch):
    """A window that is polling an empty directory, with a 20 ms retry schedule.

    The frames are written beside the watched directory and announced by calling
    `on_file_detected`, so the poller's own scans never announce anything.
    """
    from PySide6.QtWidgets import QMessageBox
    from pyql3.gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(MainWindow, "AUTO_LOAD_RETRY_DELAYS", FAST_RETRY)
    watched = tmp_path / "watched"
    watched.mkdir()
    win = MainWindow()
    assert win.poller.start_polling(str(watched))
    yield win
    win.close()


def test_auto_load_retries_then_succeeds(qapp, tmp_path, polling_window, monkeypatch):
    """A file that fails to parse on the first attempt (NFS attribute cache lag) must
    be retried rather than producing an immediate error dialog."""
    from PySide6.QtWidgets import QMessageBox

    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a))
    )
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *a, **k: warnings.append(a))
    )

    path = str(tmp_path / "late.fits")
    _write_cube(path)

    win = polling_window
    calls = {"n": 0}
    real_load = win.load_fits

    def flaky_load(filepath, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return False  # first parse fails, as a truncated read would
        return real_load(filepath, **kwargs)

    monkeypatch.setattr(win, "load_fits", flaky_load)

    win._attempt_auto_load(path, 0, win._auto_load_generation)
    assert calls["n"] == 1
    assert warnings == [], "must not warn on the first failure"

    # Second attempt is scheduled on a timer; invoke it directly.
    win._attempt_auto_load(path, 1, win._auto_load_generation)
    assert calls["n"] == 2
    assert warnings == [], "a successful retry must not warn"
    assert win.image_viewer.raw_data is not None


def test_auto_load_warns_only_after_exhausting_retries(qapp, tmp_path, polling_window,
                                                       monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a))
    )

    win = polling_window
    monkeypatch.setattr(win, "load_fits", lambda *a, **k: False)

    last = len(win.AUTO_LOAD_RETRY_DELAYS) - 1
    for attempt in range(last):
        win._attempt_auto_load(str(tmp_path / "never.fits"), attempt,
                               win._auto_load_generation)
        assert warnings == [], f"warned early on attempt {attempt}"

    win._attempt_auto_load(str(tmp_path / "never.fits"), last, win._auto_load_generation)
    assert len(warnings) == 1, "should warn exactly once, after the last attempt"
    assert "still be transferring" in warnings[0][2]


# ------------------------------------------- which auto-load is allowed to land (R11)


def test_a_stale_retry_does_not_replace_a_newer_frame(qapp, tmp_path, polling_window):
    from PySide6.QtTest import QTest

    win = polling_window
    f1, f2 = str(tmp_path / "frame_0001.fits"), str(tmp_path / "frame_0002.fits")
    _write_partial(f1, 1.0)
    with pytest.warns(AstropyUserWarning, match="truncated"):
        win.on_file_detected(f1)                # fails; a retry is queued
    assert win.image_viewer.raw_data is None

    _write_whole(f2, 2.0)
    win.on_file_detected(f2)
    assert win.windowTitle() == "QuickLook 3 - frame_0002.fits"

    _write_whole(f1, 1.0)                       # frame 1 finishes arriving
    QTest.qWait(RETRY_WAIT_MS)

    assert win.windowTitle() == "QuickLook 3 - frame_0002.fits"
    assert win.image_viewer.raw_data.mean() == 2.0


def test_a_retry_does_not_replace_a_file_opened_by_hand(qapp, tmp_path, polling_window,
                                                        monkeypatch):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog

    win = polling_window
    f3, chosen = str(tmp_path / "frame_0003.fits"), str(tmp_path / "user_choice.fits")
    _write_partial(f3, 3.0)
    with pytest.warns(AstropyUserWarning, match="truncated"):
        win.on_file_detected(f3)                # fails; a retry is queued

    _write_whole(chosen, 7.0)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (chosen, "")))
    win.open_file()                             # File -> Open
    assert win.windowTitle() == "QuickLook 3 - user_choice.fits"

    _write_whole(f3, 3.0)
    QTest.qWait(RETRY_WAIT_MS)

    assert win.windowTitle() == "QuickLook 3 - user_choice.fits"
    assert win.image_viewer.raw_data.mean() == 7.0


def test_no_retry_after_polling_stops(qapp, tmp_path, polling_window):
    from PySide6.QtTest import QTest

    win = polling_window
    f1 = str(tmp_path / "frame_0001.fits")
    _write_partial(f1, 1.0)
    with pytest.warns(AstropyUserWarning, match="truncated"):
        win.on_file_detected(f1)                # fails; a retry is queued

    win.poller.stop_polling()                   # Enable Polling unticked
    _write_whole(f1, 1.0)
    QTest.qWait(RETRY_WAIT_MS)

    assert win.image_viewer.raw_data is None, "a retry loaded a frame after polling stopped"
    assert win.windowTitle() == "QuickLook 3"


def test_a_retry_does_not_survive_a_restarted_watch(qapp, tmp_path, polling_window):
    """Browse... in the polling dialog restarts the watch at once, so polling is on
    again by the time the old directory's retry fires."""
    from PySide6.QtTest import QTest

    win = polling_window
    f1 = str(tmp_path / "frame_0001.fits")
    _write_partial(f1, 1.0)
    with pytest.warns(AstropyUserWarning, match="truncated"):
        win.on_file_detected(f1)                # fails; a retry is queued

    other = tmp_path / "other_night"
    other.mkdir()
    assert win.poller.start_polling(str(other))
    _write_whole(f1, 1.0)
    QTest.qWait(RETRY_WAIT_MS)

    assert win.image_viewer.raw_data is None, "a retry from the previous watch loaded"


def test_changing_the_interval_keeps_a_pending_retry(qapp, tmp_path, polling_window):
    """R43: the Scan Interval restarts the observer on the directory already watched.
    That restart must not cancel the retry of a file the new observer will not
    announce again."""
    from PySide6.QtTest import QTest

    win = polling_window
    f1 = str(tmp_path / "frame_0001.fits")
    _write_partial(f1, 1.0)
    with pytest.warns(AstropyUserWarning, match="truncated"):
        win.on_file_detected(f1)                # fails; a retry is queued

    win.poller.interval = 0.6                   # the Scan Interval spin box
    assert win.poller.is_polling()
    _write_whole(f1, 1.0)
    QTest.qWait(RETRY_WAIT_MS)

    assert win.windowTitle() == "QuickLook 3 - frame_0001.fits", \
        "the interval change cancelled the retry"
    assert win.image_viewer.raw_data.mean() == 1.0


def test_the_windows_own_save_does_not_cancel_a_pending_retry(qapp, tmp_path,
                                                              polling_window):
    """R70: the poller re-announces the file this window just saved. That announcement
    is not a new frame, so it must not supersede the retry queued for one."""
    from PySide6.QtTest import QTest

    win = polling_window
    saved, arriving = str(tmp_path / "frame_0001.fits"), str(tmp_path / "frame_0002.fits")
    _write_whole(saved, 1.0)
    assert win.load_fits(saved)
    _write_partial(arriving, 2.0)
    with pytest.warns(AstropyUserWarning, match="truncated"):
        win.on_file_detected(arriving)          # fails; a retry is queued

    win.fits_reader.update_header_card("OBJECT", "GC")
    win.fits_reader.save()                      # Save Header, in place
    win.on_file_detected(saved)                 # the poller sees the replaced file
    _write_whole(arriving, 2.0)
    QTest.qWait(RETRY_WAIT_MS)

    assert win.windowTitle() == "QuickLook 3 - frame_0002.fits", \
        "the re-announced save cancelled the retry"
    assert win.image_viewer.raw_data.mean() == 2.0


def test_a_retry_queued_before_the_header_editor_opens_waits_for_it(qapp, tmp_path,
                                                                    polling_window,
                                                                    monkeypatch):
    """The hold covers retries as well as new detections: a retry that fires inside
    the editor's event loop must not change the file the editor writes to."""
    from PySide6.QtTest import QTest
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog

    win = polling_window
    f0, f1 = str(tmp_path / "frame_0000.fits"), str(tmp_path / "frame_0001.fits")
    _write_whole(f0, 0.5)
    assert win.load_fits(f0)
    # A failed load leaves the reader on frame 0 (R12), so Edit FITS Header still opens.
    _write_partial(f1, 1.0)
    with pytest.warns(AstropyUserWarning, match="truncated"):
        win.on_file_detected(f1)                # fails; a retry is queued
    _write_whole(f1, 1.0)

    loads = []
    real_load = win.load_fits

    def counting_load(filepath, **kwargs):
        loads.append(filepath)
        return real_load(filepath, **kwargs)

    monkeypatch.setattr(win, "load_fits", counting_load)

    def run_dialog(dialog):
        QTest.qWait(RETRY_WAIT_MS)              # the retry comes due in here
        assert loads == [], "a retry loaded while the header editor was open"
        assert win.windowTitle() == "QuickLook 3 - frame_0000.fits"
        return 0

    monkeypatch.setattr(HeaderEditorDialog, "exec", run_dialog)
    win.edit_header()

    assert loads == [f1], "the held frame should load once, after the editor closes"
    assert win.windowTitle() == "QuickLook 3 - frame_0001.fits"


def test_the_failure_warning_quotes_this_files_error(qapp, tmp_path, polling_window,
                                                     monkeypatch):
    """The warning quoted whatever the last exception had left behind, here an
    earlier File -> Open of a missing file, rather than this file's own problem."""
    from PySide6.QtWidgets import QMessageBox
    from pyql3.gui.main_window import MainWindow

    win = polling_window
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    win.load_fits(str(tmp_path / "yesterday_frame.fits"))     # not found

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warnings.append(a)))
    monkeypatch.setattr(MainWindow, "AUTO_LOAD_RETRY_DELAYS", (0.0,))   # no retries
    table_only = str(tmp_path / "s251021_a001_tbl.fits")
    fits.HDUList([fits.PrimaryHDU(),
                  fits.BinTableHDU.from_columns([fits.Column("x", "E", array=[1.0])])]
                 ).writeto(table_only)
    win.on_file_detected(table_only)

    assert len(warnings) == 1
    message = warnings[0][2]
    assert "Last error: no image data" in message
    assert "yesterday_frame" not in message
    # A failed load leaves the window on the file it displays (R12), so Redisplay would
    # reload that file, not this one. The advice has to name a way to open this one.
    assert "Redisplay" not in message and "File → Open" in message


# ------------------------------------------------------------------ lifecycle


def test_start_and_stop_polling(qapp, tmp_path):
    poller = DirectoryPoller()
    assert poller.start_polling(str(tmp_path)) is True
    assert poller.observer is not None
    assert poller.is_polling() is True
    poller.stop_polling()
    assert poller.observer is None
    assert poller.is_polling() is False


def test_start_polling_rejects_a_missing_directory(qapp, tmp_path):
    poller = DirectoryPoller()
    assert poller.start_polling(str(tmp_path / "nope")) is False
    assert poller.is_polling() is False


def test_interval_is_configurable(qapp, tmp_path):
    poller = DirectoryPoller(interval=5.0)
    assert poller.interval == 5.0
    poller.interval = 3.0
    assert poller.interval == 3.0
    poller.stop_polling()


def test_the_polling_dialog_applies_the_interval_when_editing_finishes(qapp, tmp_path):
    """R43: every spin-box step used to restart the observer. Stepping from 2.0 to 5.0
    restarted it six times; typing "10" restarted it at "1" first."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from pyql3.gui.dialogs.polling import PollingDialog

    poller = DirectoryPoller(interval=2.0)
    assert poller.start_polling(str(tmp_path))
    dialog = PollingDialog(poller)
    try:
        observer = poller.observer
        for _ in range(6):
            dialog.spin_interval.stepUp()
        assert poller.interval == 2.0 and poller.observer is observer, \
            "a spin-box step restarted the watch"

        QTest.keyClick(dialog.spin_interval, Qt.Key.Key_Return)
        assert poller.interval == 5.0
        assert poller.observer is not observer, "the new interval did not restart the scan"

        dialog.spin_interval.setValue(7.5)          # stepped, then Close clicked
        dialog.accept()
        assert poller.interval == 7.5
    finally:
        dialog.deleteLater()
        poller.stop_polling()


def test_closing_the_polling_dialog_unedited_keeps_the_interval(qapp, tmp_path):
    """The spin box shows one decimal from 0.5 s, so a hand-edited 2.25 s reads 2.3.
    Closing the dialog without editing applied that shown value: it restarted the watch
    and wrote 2.3 to the config."""
    from pyql3.gui.dialogs.polling import PollingDialog
    from pyql3.services.config import ConfigManager

    config = ConfigManager(tmp_path / "config.json")
    config.set("polling_interval", 2.25)
    poller = DirectoryPoller(interval=2.25)
    assert poller.start_polling(str(tmp_path))
    try:
        observer = poller.observer
        for close in ("reject", "accept"):          # Escape, then Close
            dialog = PollingDialog(poller, config=config)
            dialog.spin_interval.editingFinished.emit()     # focus left the box, unedited
            getattr(dialog, close)()
            dialog.deleteLater()
            assert poller.interval == 2.25, f"{close} changed the interval"
            assert poller.observer is observer, f"{close} restarted the watch"
            assert config.get("polling_interval") == 2.25
    finally:
        poller.stop_polling()


@pytest.mark.parametrize("restart", ["interval", "takeover"])
def test_restarting_a_watch_keeps_unsettled_files(qapp, tmp_path, restart):
    """R43: a file seen by a scan but not yet settled, and a settled file held while a
    batch finishes arriving, were dropped by a restart. The new observer's first scan
    treats both as already present, so neither was ever announced."""
    owner, successor = DirectoryPoller(), DirectoryPoller()
    seen, coalesced, stopped = [], [], []
    for poller in (owner, successor):
        poller.file_detected.connect(seen.append)
        poller.batch_coalesced.connect(lambda n, path: coalesced.append(n))
    owner.polling_stopped.connect(lambda: stopped.append(owner))
    try:
        assert owner.start_polling(str(tmp_path))

        held, unsettled = str(tmp_path / "frame_0001.fits"), str(tmp_path / "frame_0002.fits")
        _write_cube(held)
        _write_cube(unsettled)
        base = os.stat(held).st_mtime_ns
        os.utime(held, ns=(base, base))
        os.utime(unsettled, ns=(base + 1_000_000_000, base + 1_000_000_000))

        owner._add_candidate(held)
        _tick(owner)
        owner._add_candidate(unsettled)
        _tick(owner, SETTLE_CHECKS)
        # The premise: frame 1 has settled and is held while frame 2 is still pending.
        assert owner._held_path == held and unsettled in owner._pending
        assert seen == []

        if restart == "interval":
            owner.interval = 0.6                    # the Scan Interval spin box
            now_watching = owner
            assert stopped == [], "a restart on the same directory is not the end of a watch"
        else:
            assert successor.start_polling(str(tmp_path))
            now_watching = successor
            assert stopped == [owner], "the previous owner's watch has ended"
        assert now_watching.is_polling()

        _settle(now_watching)
        assert seen == [unsettled], "the newest file was not announced exactly once"
        assert coalesced == [1], "the held file was not counted in the batch"
    finally:
        owner.stop_polling()
        successor.stop_polling()


def test_hidden_files_are_ignored(qapp, tmp_path):
    """FitsReader.save() writes .pyql3_save_*.fits beside the file being saved.

    Saving into a watched directory would otherwise hand our own half-written temp
    file to the poller as if it were a new frame, racing the save.
    """
    assert is_fits_path("/data/.pyql3_save_ab12.fits") is False
    assert is_fits_path("/data/.hidden.fits") is False
    assert is_fits_path("/data/normal.fits") is True

    poller = DirectoryPoller()
    seen = []
    poller.file_detected.connect(seen.append)

    temp_like = str(tmp_path / ".pyql3_save_xy99.fits")
    _write_cube(temp_like)
    poller._add_candidate(temp_like)
    _settle(poller)
    assert seen == [], "a dotfile must never be announced"


def test_save_into_a_watched_directory_is_not_picked_up(qapp, tmp_path, sample_3d_fits):
    """End to end: the temp file FitsReader creates mid-save is invisible to the poller."""
    from pyql3.core.fits_reader import FitsReader

    poller = DirectoryPoller()
    seen = []
    poller.file_detected.connect(seen.append)

    target = str(tmp_path / "saved.fits")
    reader = FitsReader(sample_3d_fits)
    try:
        reader.save(target)
    finally:
        reader.close()

    # Whatever the save left behind, offer every one of them to the poller.
    for name in os.listdir(tmp_path):
        poller._add_candidate(str(tmp_path / name))
    _settle(poller)

    assert all(not os.path.basename(p).startswith(".") for p in seen), (
        f"a save temp file was announced: {seen}"
    )
