"""Several main windows open at once.

Each window is meant to be a self-contained view of one cube: its own reader, viewer,
display settings, tool dialogs and directory watch. What needs testing is therefore not
that a second window can be constructed -- it always could -- but the things that are
process-wide and so could leak between windows or be lost when one closes:

- the destination of a file that arrives without a window (a Finder open-document event),
- the settings file, which every window writes to,
- a directory watch, which two windows must not hold at once,
- and everything a closing window has to release.

Windows here are deliberately constructed without `show()`: an unshown window is never
the active window, so `most_recent()` is decided by the recorded order rather than by
whatever the offscreen platform decides to activate.
"""
import os

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QDialog, QMessageBox

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


def test_closing_a_window_releases_its_cube(manager, sample_3d_fits):
    """Qt does not destroy a closed window, so the cube has to be let go of explicitly.

    A `lambda: self.x()` connected to a QAction is held by that action, which is held by a menu,
    which is held by the widget — a cycle running through C++ that Python's collector cannot
    break. The viewer therefore survives its window for the life of the process. Measured before
    this was fixed: five open-and-close cycles left five viewers alive holding five cubes.

    The weakref is the real assertion. Setting the attributes to None proves nothing on its own:
    what matters is that no one else — the reader, the ImageItem, a tool — is still holding the
    array.
    """
    import gc
    import weakref

    win = MainWindow()
    win.load_fits(sample_3d_fits)
    cube = weakref.ref(win.image_viewer.raw_data)
    assert cube() is not None

    win.close()
    gc.collect()

    assert win.image_viewer.raw_data is None
    assert win.image_viewer.transposed_data is None
    assert win.image_viewer.display_data is None
    assert cube() is None, "the cube is still in memory after its window closed"


#: Every opener of a cached tool dialog, with the attribute it caches the dialog in.
TOOL_OPENERS = {
    "open_depth_plot": "_depth_plot_dialog",
    "open_horizontal_cut": "_hcut_dialog",
    "open_vertical_cut": "_vcut_dialog",
    "open_diagonal_cut": "_dcut_dialog",
    "open_surface_plot": "_surf_dialog",
    "open_contour_plot": "_cont_dialog",
    "open_plot_catalog": "_plot_catalog_dialog",
    "open_rotate": "_rotate_dialog",
    "open_statistics": "_stats_dialog",
    "open_photometry": "_phot_dialog",
    "open_gaussian_fit": "_gauss_dialog",
    "open_strehl_tool": "_strehl_dialog",
    "open_arithmetic_tool": "_arith_dialog",
    "open_region_list": "_region_list_dialog",
}


def test_every_opener_is_covered_and_tracked():
    """The two tests below are only as complete as this table."""
    assert set(TOOL_OPENERS.values()) == set(MainWindow.TOOL_DIALOG_ATTRS)


@pytest.mark.parametrize("opener", list(TOOL_OPENERS))
def test_opening_a_tool_twice_reuses_it(manager, sample_3d_fits, opener):
    """A second click raises the open dialog rather than building another (`BUGS.md` R16).

    The cut, Surface and Contour openers built a new dialog on every click: the first stayed on
    screen, out of `TOOL_DIALOG_ATTRS`, with its own ROI still drawn on the image. Everything
    the tool draws is counted, not only `pg.ROI`s: the viewer has two ROIs of its own, the cuts
    draw lines, and Photometry draws an aperture and two annulus rings.
    """
    win = MainWindow()
    win.load_fits(sample_3d_fits)
    scene = win.image_viewer.imv.getView().scene()

    first = getattr(win, opener)()
    drawn = len(scene.items())
    second = getattr(win, opener)()

    assert first is not None
    assert second is first, "a second dialog was built while the first was on screen"
    assert getattr(win, TOOL_OPENERS[opener]) is first
    assert len(scene.items()) == drawn, "the second click drew the tool's ROI a second time"


def test_reopening_a_closed_tool_leaves_one_instance(manager, sample_3d_fits, qapp):
    """A closed dialog is replaced on the next open, and the one it replaces is deleted."""
    from pyql3.gui.tools.cuts import CutPlotDialog

    win = MainWindow()
    win.load_fits(sample_3d_fits)
    for _ in range(20):
        win.open_horizontal_cut()
        win._hcut_dialog.close()
    # processEvents() alone does not run deleteLater() outside a running event loop.
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert len(win.findChildren(CutPlotDialog)) == 1


def test_every_tool_dialog_closes_with_its_window(manager, sample_3d_fits):
    """No tool dialog of a closed window is left on screen (`BUGS.md` R16).

    Each tool is opened twice, because the second click is what used to orphan the first
    dialog, and a region property dialog is opened as well: those are kept in
    `_region_property_dialogs`, which `close_tool_dialogs` did not walk. So is Peak Fit's
    Display Peak Fit window, a child window of a tool rather than of the main window.
    """
    win = MainWindow()
    win.load_fits(sample_3d_fits)
    for opener in TOOL_OPENERS:
        getattr(win, opener)()
        getattr(win, opener)()
    win.open_region_properties(win.region_layer.place("circle", 5.5, 5.5))
    show_peak_fit_display(win)
    assert [d for d in win.findChildren(QDialog) if d.isVisible()], "nothing was opened"

    win.close()

    left = sorted(d.windowTitle() for d in win.findChildren(QDialog) if d.isVisible())
    assert left == [], f"still on screen after the window closed: {left}"


def show_peak_fit_display(win):
    """Open Peak Fit and show its Display Peak Fit window.

    The fit result is set directly. Whether a fit converges on `sample_3d_fits` depends on its
    random noise, and the fit is not what these tests are about.
    """
    import numpy as np

    peak_fit = win.open_gaussian_fit()
    peak_fit.last_raw_data = np.ones((5, 5))
    peak_fit.last_fit_data = np.ones((5, 5))
    peak_fit.display_peak_fit()
    assert peak_fit.disp_dialog.isVisible()
    return peak_fit.disp_dialog


def test_closing_peak_fit_closes_its_display_window(manager, sample_3d_fits, qapp):
    """Qt does not hide a child window with its parent, so Display Peak Fit stayed on screen.

    Reopening Peak Fit then deleted the closed dialog, and the display window with it, while
    the user was still looking at it.
    """
    win = MainWindow()
    win.load_fits(sample_3d_fits)
    display = show_peak_fit_display(win)

    win._gauss_dialog.close()
    assert not display.isVisible(), "Display Peak Fit is still on screen after Peak Fit closed"

    win.open_gaussian_fit()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    left = [d.windowTitle() for d in win.findChildren(QDialog)
            if d.isVisible() and d.windowTitle() == "Display Peak Fit"]
    assert left == []


def test_a_surface_plot_opengl_failure_is_reported_not_raised(manager, sample_3d_fits,
                                                             monkeypatch, capsys):
    """The message pyqtgraph raises from `initializeGL` without OpenGL 2.1 is caught."""
    from pyql3.gui.tools.advanced_plots import SurfacePlotDialog

    def no_opengl(self):
        raise RuntimeError("pyqtgraph.opengl: Requires >= OpenGL 2.1; Found None")

    monkeypatch.setattr(SurfacePlotDialog, "show", no_opengl)
    win = MainWindow()
    win.load_fits(sample_3d_fits)

    assert win.open_surface_plot() is None
    assert "Surface Plot unavailable" in capsys.readouterr().out


def test_a_surface_plot_bug_is_not_reported_as_missing_opengl(manager, sample_3d_fits,
                                                              monkeypatch):
    from pyql3.gui.tools.advanced_plots import SurfacePlotDialog

    def broken(self, *args, **kwargs):
        raise ValueError("a bug in the dialog")

    monkeypatch.setattr(SurfacePlotDialog, "__init__", broken)
    win = MainWindow()
    win.load_fits(sample_3d_fits)

    with pytest.raises(ValueError, match="a bug in the dialog"):
        win.open_surface_plot()


def test_a_pending_auto_load_retry_does_not_revive_a_closed_window(manager, tmp_path,
                                                                   sample_3d_fits, monkeypatch):
    """Retries run on a timer, so one can fire after its window has been closed."""
    import shutil
    from PySide6.QtTest import QTest

    monkeypatch.setattr(MainWindow, "AUTO_LOAD_RETRY_DELAYS", (0.0, 0.02))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda *a, **k: pytest.fail("warned from a closed window")))
    watched = tmp_path / "watched"
    watched.mkdir()
    win = MainWindow()
    assert win.poller.start_polling(str(watched))

    frame = str(tmp_path / "frame_0001.fits")
    with open(frame, "wb") as f:
        f.write(b"not yet a FITS file")
    win.on_file_detected(frame)                 # fails; a retry is queued
    win.close()

    shutil.copy(sample_3d_fits, frame)          # the frame finishes arriving
    QTest.qWait(200)                            # ten times the retry delay

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
    """A trailing slash, a symlink or a change of case is the same directory, and must collide."""
    (tmp_path / "real").mkdir(exist_ok=True)

    link = tmp_path / "link"
    try:
        link.symlink_to(tmp_path / "real", target_is_directory=True)
    except (OSError, NotImplementedError):
        # Windows refuses this with WinError 1314 unless the process holds
        # SeCreateSymbolicLinkPrivilege (Developer Mode, or an elevated shell). The trailing
        # separator and the case fold below still cover the canonicalisation there.
        link = None

    poller = DirectoryPoller()
    try:
        poller.start_polling(str(tmp_path / "real") + "/")
        assert watcher_of(str(tmp_path / "real")) is poller, "trailing separator"
        if link is not None:
            assert watcher_of(str(link)) is poller, "symlink"
        if os.path.normcase("A") == "a":
            # Only where the filesystem really is case-insensitive to the path API, which is
            # Windows -- `normcase` is a no-op on macOS even on a case-insensitive volume.
            assert watcher_of(str(tmp_path / "real").upper()) is poller, "change of case"
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


# --------------------------------------------------------- Exit and Recent Files


def test_exit_closes_every_window(manager):
    """File -> Exit closed only its own window, so the application kept running (R49)."""
    first = MainWindow()
    first.show()
    first.new_window().show()
    assert manager.count() == 2

    exit_action = next(a for a in first.file_menu.actions() if a.text() == "Exit")
    exit_action.trigger()

    assert manager.count() == 0


def test_recent_files_menu_is_current_in_every_window(manager, sample_3d_fits):
    """A file opened in window A is in B's menu once B's menu is about to show (R58)."""
    a, b = MainWindow(), MainWindow()
    a.config.clear_recent_files()       # the settings file lives as long as the session
    b.recent_menu.aboutToShow.emit()
    assert [act.text() for act in b.recent_menu.actions()] == ["No Recent Files"]

    a.load_fits(sample_3d_fits)
    b.recent_menu.aboutToShow.emit()

    texts = [act.text() for act in b.recent_menu.actions()]
    assert texts[0] == f"1. {os.path.basename(sample_3d_fits)}"
