"""Directory-polling behaviour: slow writes, bursts, and auto-load retry.

These drive `DirectoryPoller._check_pending()` directly rather than sleeping on a real
observer thread. A timing test that waits on wall-clock passes or fails by luck; calling
the settle tick explicitly makes "two unchanged samples means settled" the thing actually
under test, and keeps the suite fast and deterministic.
"""

import os

import numpy as np
import pytest
from astropy.io import fits

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
        # Force a strictly increasing mtime so "newest" is unambiguous.
        os.utime(path, ns=(1_000_000_000 + i, 1_000_000_000 + i))
        poller._add_candidate(path)
        paths.append(path)

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


def test_auto_load_retries_then_succeeds(qapp, tmp_path, monkeypatch):
    """A file that fails to parse on the first attempt (NFS attribute cache lag) must
    be retried rather than producing an immediate error dialog."""
    from PySide6.QtWidgets import QMessageBox
    from pyql3.gui.main_window import MainWindow

    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a))
    )
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *a, **k: warnings.append(a))
    )

    path = str(tmp_path / "late.fits")
    _write_cube(path)

    win = MainWindow()
    calls = {"n": 0}
    real_load = win.load_fits

    def flaky_load(filepath, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return False  # first parse fails, as a truncated read would
        return real_load(filepath, **kwargs)

    monkeypatch.setattr(win, "load_fits", flaky_load)

    win._attempt_auto_load(path, 0)
    assert calls["n"] == 1
    assert warnings == [], "must not warn on the first failure"

    # Second attempt is scheduled on a timer; invoke it directly.
    win._attempt_auto_load(path, 1)
    assert calls["n"] == 2
    assert warnings == [], "a successful retry must not warn"
    assert win.image_viewer.raw_data is not None
    win.close()


def test_auto_load_warns_only_after_exhausting_retries(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from pyql3.gui.main_window import MainWindow

    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a))
    )

    win = MainWindow()
    monkeypatch.setattr(win, "load_fits", lambda *a, **k: False)

    last = len(win.AUTO_LOAD_RETRY_DELAYS) - 1
    for attempt in range(last):
        win._attempt_auto_load(str(tmp_path / "never.fits"), attempt)
        assert warnings == [], f"warned early on attempt {attempt}"

    win._attempt_auto_load(str(tmp_path / "never.fits"), last)
    assert len(warnings) == 1, "should warn exactly once, after the last attempt"
    assert "still be transferring" in warnings[0][2]
    win.close()


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
