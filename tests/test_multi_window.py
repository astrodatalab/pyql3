"""Several main windows open at once.

Each window is meant to be a self-contained view of one cube: its own reader, viewer,
display settings, tool dialogs and directory watch. What needs testing is therefore not
that a second window can be constructed -- it always could -- but the things that are
process-wide and so could leak between windows or be lost when one closes:

- the destination of a file that arrives without a window (Finder, or a shell while the
  application is already running),
- the settings file, which every window writes to,
- a directory watch, which two windows must not hold at once,
- and everything a closing window has to release.

Windows here are deliberately constructed without `show()`: an unshown window is never
the active window, so `most_recent()` is decided by the recorded order rather than by
whatever the offscreen platform decides to activate.
"""
import pytest
from PySide6.QtWidgets import QMessageBox

from pyql3.gui import window_manager
from pyql3.gui.main_window import MainWindow
from pyql3.gui.window_manager import WindowManager
from pyql3.services.poller import DirectoryPoller, watcher_of


@pytest.fixture
def manager(qapp, monkeypatch):
    """A fresh window list, so tests neither see nor leave stray windows.

    `MainWindow` finds the manager through `get_window_manager()`, so replacing the
    module-level instance is enough to isolate every window built during the test.
    """
    mgr = WindowManager()
    monkeypatch.setattr(window_manager, "_manager", mgr)
    yield mgr
    for win in mgr.windows():
        win.close()


@pytest.fixture
def second_cube(tmp_path):
    """A second synthetic cube, distinguishable from `sample_3d_fits` by its values."""
    import numpy as np
    from astropy.io import fits

    path = str(tmp_path / "other_cube.fits")
    fits.PrimaryHDU(np.full((6, 5, 5), 42.0, dtype=np.float32)).writeto(path)
    return path


# ------------------------------------------------------------------ independence


def test_two_windows_hold_separate_data(manager, sample_3d_fits, second_cube):
    first, second = MainWindow(), MainWindow()
    first.load_fits(sample_3d_fits)
    second.load_fits(second_cube)

    assert first.fits_reader is not second.fits_reader
    assert first.image_viewer is not second.image_viewer
    assert first.image_viewer.raw_data.shape != second.image_viewer.raw_data.shape
    assert second.image_viewer.raw_data.mean() == 42.0
    assert first.image_viewer.raw_data.mean() != 42.0
    assert manager.count() == 2


def test_tools_and_display_units_stay_within_their_window(manager, sample_3d_fits, second_cube):
    """A tool reads the viewer it was given, so nothing crosses between windows."""
    first, second = MainWindow(), MainWindow()
    first.load_fits(sample_3d_fits)
    second.load_fits(sample_3d_fits)

    first.open_depth_plot()
    second.open_depth_plot()

    assert first._depth_plot_dialog is not second._depth_plot_dialog
    assert first._depth_plot_dialog.image_viewer is first.image_viewer
    assert second._depth_plot_dialog.image_viewer is second.image_viewer

    first.set_display_unit(True)
    assert first.image_viewer.disp_as_dn is True
    assert second.image_viewer.disp_as_dn is False, "unit change leaked into the other window"


def test_windows_share_one_settings_object(manager):
    """Two ConfigManagers over one file would silently drop each other's updates."""
    first, second = MainWindow(), MainWindow()
    assert first.config is second.config


def test_new_window_registers_and_can_preload_a_file(manager, sample_3d_fits):
    first = MainWindow()
    second = first.new_window(sample_3d_fits)

    assert second is not first
    assert manager.count() == 2
    assert second in manager.windows()
    assert second.image_viewer.raw_data is not None
    assert first.image_viewer.raw_data is None, "opening a new window disturbed this one"


# -------------------------------------------------------------------- routing


def test_an_opened_path_goes_to_the_most_recently_used_window(manager, sample_3d_fits):
    first, second = MainWindow(), MainWindow()
    manager.touch(second)

    assert manager.open_path(sample_3d_fits) is True
    assert second.image_viewer.raw_data is not None
    assert first.image_viewer.raw_data is None


def test_routing_survives_closing_the_window_it_last_used(manager, sample_3d_fits):
    """Regression: the open-document handler used to be bound to one window's method.

    `main.py` handed `FileOpenHandler` the first window's `load_fits`. Closing that
    window left the handler holding a bound method of a deleted C++ object, so a later
    double-click in Finder called into nothing.
    """
    first, second = MainWindow(), MainWindow()
    manager.touch(second)
    second.close()

    assert manager.open_path(sample_3d_fits) is True
    assert first.image_viewer.raw_data is not None


def test_opening_a_path_with_no_windows_left_creates_one(manager, sample_3d_fits):
    win = MainWindow()
    win.close()
    assert manager.count() == 0

    manager.open_path(sample_3d_fits)

    assert manager.count() == 1
    assert manager.windows()[0].image_viewer.raw_data is not None


# ------------------------------------------------------------------- lifecycle


def test_closing_a_window_releases_poller_tools_and_file(manager, tmp_path, sample_3d_fits):
    """Each of these outlives its window if `closeEvent` skips it."""
    win = MainWindow()
    win.load_fits(sample_3d_fits)
    win.open_depth_plot()
    win.poller.start_polling(str(tmp_path))
    dialog = win._depth_plot_dialog
    assert win.poller.is_polling() and dialog.isVisible()

    win.close()

    assert win.poller.is_polling() is False, "a PollingObserver thread kept scanning"
    assert dialog.isVisible() is False, "tool window left on screen after its window closed"
    assert win.fits_reader.hdul is None, "FITS handle still open (unreplaceable on Windows)"
    assert manager.count() == 0
    assert watcher_of(str(tmp_path)) is None


def test_a_pending_auto_load_retry_does_not_revive_a_closed_window(manager, tmp_path,
                                                                   sample_3d_fits, monkeypatch):
    """Retries run on a timer, so one can fire after its window has been closed."""
    win = MainWindow()
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda *a, **k: pytest.fail("warned from a closed window")))
    win.close()

    last = len(MainWindow.AUTO_LOAD_RETRY_DELAYS) - 1
    win._attempt_auto_load(sample_3d_fits, last)          # what the queued timer does

    assert win.image_viewer.raw_data is None, "loaded a file into a closed window"


# ---------------------------------------------------------------- watch owner


def test_only_one_poller_watches_a_directory(qapp, tmp_path):
    """Two watches on one directory scan it twice and load every frame twice."""
    first, second = DirectoryPoller(), DirectoryPoller()
    try:
        assert first.start_polling(str(tmp_path)) is True
        assert watcher_of(str(tmp_path)) is first

        assert second.start_polling(str(tmp_path)) is True

        assert first.is_polling() is False, "the earlier watch was left running"
        assert second.is_polling() is True
        assert watcher_of(str(tmp_path)) is second
    finally:
        first.stop_polling()
        second.stop_polling()
    assert watcher_of(str(tmp_path)) is None


def test_watch_ownership_compares_canonical_paths(qapp, tmp_path):
    """A trailing slash or a symlink is the same directory, and must collide."""
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "real", target_is_directory=True)
    (tmp_path / "real").mkdir(exist_ok=True)

    poller = DirectoryPoller()
    try:
        poller.start_polling(str(tmp_path / "real") + "/")
        assert watcher_of(str(link)) is poller
        assert watcher_of(str(tmp_path / "real")) is poller
    finally:
        poller.stop_polling()


def test_taking_a_watch_from_another_window_is_confirmed(manager, tmp_path, monkeypatch):
    """Moving a watch changes where every new frame appears, so it has to be asked."""
    from pyql3.gui.dialogs.polling import PollingDialog

    owner, other = MainWindow(), MainWindow()
    owner.setWindowTitle("QuickLook 3 - watched.fits")
    try:
        owner.poller.start_polling(str(tmp_path))

        answers = [QMessageBox.StandardButton.No]
        prompts = []

        def fake_question(parent, title, text, *args, **kwargs):
            prompts.append(text)
            return answers.pop(0)

        monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))

        dialog = PollingDialog(other.poller, other,
                               confirm_takeover=other.confirm_watch_takeover)
        dialog.txt_dir.setText(str(tmp_path))

        assert dialog.start_watch(str(tmp_path)) is False
        assert other.poller.is_polling() is False
        assert watcher_of(str(tmp_path)) is owner.poller, "watch moved despite a refusal"
        assert "watched.fits" in prompts[0], "the prompt must name the window that has it"

        # ... and accepting moves it.
        answers.append(QMessageBox.StandardButton.Yes)
        assert dialog.start_watch(str(tmp_path)) is True
        assert watcher_of(str(tmp_path)) is other.poller
        assert owner.poller.is_polling() is False
    finally:
        owner.poller.stop_polling()
        other.poller.stop_polling()


def test_no_prompt_when_this_window_already_holds_the_watch(manager, tmp_path, monkeypatch):
    win = MainWindow()
    try:
        win.poller.start_polling(str(tmp_path))
        monkeypatch.setattr(QMessageBox, "question", staticmethod(
            lambda *a, **k: pytest.fail("asked to take a watch from itself")))
        assert win.confirm_watch_takeover(str(tmp_path)) is True
        assert win.confirm_watch_takeover(str(tmp_path / "elsewhere")) is True
    finally:
        win.poller.stop_polling()


# -------------------------------------------------------------- window menu


def test_window_menu_is_flat_for_a_single_window(manager, sample_3d_fits):
    win = MainWindow()
    win.load_fits(sample_3d_fits)
    win.open_statistics()
    win.update_window_menu()

    titles = [a.text() for a in win.window_menu.actions()]
    assert any("Statistics" in t for t in titles), titles
    assert all(a.menu() is None for a in win.window_menu.actions()), "no submenus expected"


def test_window_menu_groups_each_windows_tools_under_it(manager, sample_3d_fits):
    """With several windows, one flat list of identically-named dialogs is unusable."""
    first, second = MainWindow(), MainWindow()
    first.load_fits(sample_3d_fits)
    second.load_fits(sample_3d_fits)
    first.setWindowTitle("first.fits")
    second.setWindowTitle("second.fits")
    first.open_statistics()
    second.open_photometry()

    first.update_window_menu()
    actions = first.window_menu.actions()
    submenus = {a.text(): a.menu() for a in actions if a.menu() is not None}

    assert set(submenus) == {"first.fits", "second.fits"}, [a.text() for a in actions]
    assert any("Statistics" in a.text() for a in submenus["first.fits"].actions())
    assert any("Photometry" in a.text() for a in submenus["second.fits"].actions())
    assert not any("Photometry" in a.text() for a in submenus["first.fits"].actions()), \
        "a tool was listed under the window that does not own it"
