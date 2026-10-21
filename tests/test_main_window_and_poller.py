import pytest
from PySide6.QtWidgets import QMessageBox
from pyql3.gui.main_window import MainWindow
from pyql3.services.poller import DirectoryPoller


def test_main_window_init_and_load(qapp, sample_3d_fits, sample_2d_fits):
    """Test MainWindow initialization and loading 3D cubes vs 2D images."""
    win = MainWindow()
    assert win.image_viewer is not None, "MainWindow missing image_viewer widget"

    # Load 3D spectral cube
    win.load_fits(sample_3d_fits)
    assert win.image_viewer.transposed_data is not None
    assert win.image_viewer.transposed_data.ndim == 3

    # Load 2D FITS image
    win.load_fits(sample_2d_fits)
    assert win.image_viewer.transposed_data is not None
    assert win.image_viewer.transposed_data.ndim == 2
    win.close()


def test_main_window_tool_lifecycle(qapp, sample_3d_fits):
    """Test MainWindow opening, tracking, and refreshing all analytical tool dialogs."""
    win = MainWindow()
    win.load_fits(sample_3d_fits)

    # Open depth plot
    win.open_depth_plot()
    assert hasattr(win, '_depth_plot_dialog') and win._depth_plot_dialog is not None
    assert win._depth_plot_dialog.isVisible()

    # Open profile cuts
    win.open_horizontal_cut()
    assert hasattr(win, '_hcut_dialog') and win._hcut_dialog is not None

    win.open_vertical_cut()
    assert hasattr(win, '_vcut_dialog') and win._vcut_dialog is not None

    win.open_diagonal_cut()
    assert hasattr(win, '_dcut_dialog') and win._dcut_dialog is not None

    # Open analysis tools
    win.open_gaussian_fit()
    assert hasattr(win, '_gauss_dialog') and win._gauss_dialog is not None

    win.open_statistics()
    assert hasattr(win, '_stats_dialog') and win._stats_dialog is not None

    win.open_photometry()
    assert hasattr(win, '_phot_dialog') and win._phot_dialog is not None

    win.open_strehl_tool()
    assert hasattr(win, '_strehl_dialog') and win._strehl_dialog is not None

    win.open_rotate()
    assert hasattr(win, '_rotate_dialog') and win._rotate_dialog is not None

    win.open_arithmetic_tool()
    assert hasattr(win, '_arith_dialog') and win._arith_dialog is not None

    win.open_plot_catalog()
    assert hasattr(win, '_plot_catalog_dialog') and win._plot_catalog_dialog is not None

    # Every open tool follows a display unit change
    win.set_display_unit(True)

    win.close()


def test_main_window_2d_depth_plot_guard(qapp, sample_2d_fits, monkeypatch):
    """Verify that open_depth_plot on 2D images shows an informative message instead of popping up an empty dialog."""
    message_box_shown = False

    def mock_info(*args, **kwargs):
        nonlocal message_box_shown
        message_box_shown = True

    monkeypatch.setattr(QMessageBox, "information", mock_info)

    win = MainWindow()
    win.load_fits(sample_2d_fits)

    win.open_depth_plot()
    assert message_box_shown, "MainWindow did not display 3D requirement info message when opening Depth Plot on 2D data"
    assert not hasattr(win, '_depth_plot_dialog') or not win._depth_plot_dialog.isVisible()
    win.close()


def test_directory_poller_service(tmp_path):
    """Test DirectoryPoller lifecycle (initialize, start_polling, and stop_polling)."""
    poller = DirectoryPoller()
    watch_dir = str(tmp_path)

    poller.start_polling(watch_dir)
    assert poller.observer is not None, "DirectoryPoller observer was not initialized"
    assert poller.watch_path == watch_dir

    poller.stop_polling()


def test_extension_combo_excludes_table_hdus(qapp, tmp_path):
    """B6: the Extension combo was populated from a looser definition of 'image' than
    load() used, so a BINTABLE appeared in the dropdown."""
    import numpy as np
    from astropy.io import fits

    path = str(tmp_path / "with_table.fits")
    cols = [fits.Column(name="WAVELENGTH", format="E", array=np.arange(4, dtype=np.float32))]
    fits.HDUList([
        fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)),
        fits.ImageHDU(np.ones((4, 4), dtype=np.float32), name="SCI"),
        fits.BinTableHDU.from_columns(cols, name="WAVETAB"),
    ]).writeto(path, overwrite=True)

    win = MainWindow()
    win.load_fits(path)
    combo = win.image_viewer.combo_ext
    labels = [combo.itemText(i) for i in range(combo.count())]

    assert not any("WAVETAB" in label for label in labels), labels
    assert labels == ["0: PRIMARY", "1: SCI"]
    win.close()


def test_selecting_a_table_extension_clears_the_view(qapp, tmp_path):
    """B6: set_data fell through both the 2-D and 3-D branches and returned silently, so
    the previous extension's image stayed on screen as if it were the table."""
    import numpy as np
    from astropy.io import fits

    path = str(tmp_path / "with_table.fits")
    cols = [fits.Column(name="WAVELENGTH", format="E", array=np.arange(4, dtype=np.float32))]
    fits.HDUList([
        fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)),
        fits.BinTableHDU.from_columns(cols, name="WAVETAB"),
    ]).writeto(path, overwrite=True)

    win = MainWindow()
    win.load_fits(path)
    assert win.image_viewer.transposed_data is not None

    win.load_fits(path, ext=1)          # force-select the table, as the old combo could

    v = win.image_viewer
    assert v.transposed_data is None, "stale image left on screen for a table extension"
    assert v.display_data is None
    assert v.imv.getImageItem().image is None
    assert "Cannot display" in v.lbl_slice_info.text()
    win.close()


def test_reload_and_redisplay_pick_up_a_rewritten_file(qapp, tmp_path, rewrite_fits_in_place):
    """B5 through the GUI: the poller's auto-load and Display -> Redisplay image both go
    through load_fits, which served the cached HDUList for an unchanged path."""
    import numpy as np
    from astropy.io import fits

    path = str(tmp_path / "live.fits")
    fits.PrimaryHDU(np.full((8, 8), 10.0, dtype=np.float32)).writeto(path, overwrite=True)

    win = MainWindow()
    win.load_fits(path)
    assert win.image_viewer.raw_data.mean() == 10.0

    # the instrument writes a new frame to the same path
    rewrite_fits_in_place(path, fits.PrimaryHDU(np.full((8, 8), 1010.0, dtype=np.float32)))

    win.load_fits(path)                 # what on_file_detected does
    assert win.image_viewer.raw_data.mean() == 1010.0

    rewrite_fits_in_place(path, fits.PrimaryHDU(np.full((8, 8), 2020.0, dtype=np.float32)))
    win.redisplay_image()
    assert win.image_viewer.raw_data.mean() == 2020.0
    win.close()


def test_data_less_file_warns_and_leaves_the_current_image_alone(qapp, tmp_path,
                                                                 sample_3d_fits,
                                                                 monkeypatch):
    """Loading a FITS with no image HDU must warn and not disturb what is displayed.

    Previously FitsReader substituted a 10x10 zero array, so `data is not None` held,
    the "No valid data found" branch was unreachable, and the good cube on screen was
    replaced by a black square.
    """
    from astropy.io import fits

    win = MainWindow()
    win.load_fits(sample_3d_fits)
    good_shape = win.image_viewer.raw_data.shape
    good_title = win.windowTitle()

    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a))
    )

    empty = str(tmp_path / "no_image.fits")
    fits.PrimaryHDU(header=fits.Header()).writeto(empty)

    assert win.load_fits(empty) is False, "load_fits must report failure"
    assert len(warnings) == 1, "the user must be told there is no image data"
    assert "No valid data" in warnings[0][2]

    # The previously displayed cube is untouched.
    assert win.image_viewer.raw_data.shape == good_shape
    assert win.windowTitle() == good_title
    win.close()


# ------------------------------------------------- the header editor and auto-load (R4)


def _write_frame(path, obj, crval):
    import numpy as np
    from astropy.io import fits

    header = fits.Header()
    header["OBJECT"] = obj
    header["CRVAL2"] = crval
    fits.PrimaryHDU(np.ones((8, 6, 6), dtype="f4"), header=header).writeto(path)


@pytest.fixture
def header_edit_window(qapp, tmp_path, monkeypatch):
    """A polling window showing frame 1, with every message box answered or recorded.

    `QMessageBox.question` answers Yes, as the user confirming the overwrite would.
    Warnings are collected in `window.warnings`.
    """
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(
        lambda *a, **k: pytest.fail(f"error dialog: {a[2]}")))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda *a, **k: warnings.append(a)))

    watched = tmp_path / "watched"
    watched.mkdir()
    win = MainWindow()
    win.warnings = warnings
    assert win.poller.start_polling(str(watched))
    win.frame1 = str(tmp_path / "s251021_a001001.fits")
    win.frame2 = str(tmp_path / "s251021_a002001.fits")
    _write_frame(win.frame1, "GC_field_A", 266.40)
    _write_frame(win.frame2, "GC_field_B", 266.55)
    assert win.load_fits(win.frame1)
    yield win
    win.close()


def _edit_value(editor, keyword, value):
    """Type `value` into the Value cell of `keyword`'s row, as the user would."""
    for row in range(editor.table.rowCount()):
        if editor.table.item(row, 0).text() == keyword:
            editor.table.item(row, 1).setText(value)
            return
    raise KeyError(keyword)


def test_auto_load_during_header_edit_does_not_rewrite_the_new_frame(header_edit_window,
                                                                    monkeypatch):
    """Frame 2 arrived while Edit FITS Header was open on frame 1. Save Header then
    wrote frame 1's cards into frame 2's header and saved them to frame 2's path, and
    the user's edit to frame 1 was lost."""
    from astropy.io import fits
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog

    win = header_edit_window

    def run_dialog(dialog):
        # What happens while the modal editor's event loop runs: the user edits a
        # card, the poller announces frame 2, then the user clicks Save Header and
        # answers Yes.
        _edit_value(dialog, "OBJECT", "GC_field_A_rev")
        win.on_file_detected(win.frame2)
        dialog.save_header()
        return 0

    monkeypatch.setattr(HeaderEditorDialog, "exec", run_dialog)
    win.edit_header()                              # File -> Edit FITS Header

    assert win.warnings == [], "the save was refused instead of going to frame 1"
    assert fits.getheader(win.frame1)["OBJECT"] == "GC_field_A_rev", \
        "the user's edit to frame 1 was not saved"
    h2 = fits.getheader(win.frame2)
    assert h2["OBJECT"] == "GC_field_B"
    assert h2["CRVAL2"] == 266.55
    assert win.windowTitle() == "QuickLook 3 - s251021_a002001.fits", \
        "the held frame was not displayed once the editor closed"


def test_an_auto_load_held_by_the_header_editor_runs_after_it_closes(header_edit_window,
                                                                    tmp_path, monkeypatch):
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog

    win = header_edit_window
    frame3 = str(tmp_path / "s251021_a003001.fits")
    _write_frame(frame3, "GC_field_C", 266.70)

    loads = []
    real_load = win.load_fits

    def counting_load(filepath, **kwargs):
        loads.append(filepath)
        return real_load(filepath, **kwargs)

    monkeypatch.setattr(win, "load_fits", counting_load)

    def run_dialog(dialog):
        # Two frames arrive while the editor is open; the user then cancels.
        win.on_file_detected(win.frame2)
        win.on_file_detected(frame3)
        assert loads == [], "an auto-load ran while the editor was open"
        assert win.windowTitle() == "QuickLook 3 - s251021_a001001.fits"
        return 0

    monkeypatch.setattr(HeaderEditorDialog, "exec", run_dialog)
    win.edit_header()

    assert loads == [frame3], "only the newest held frame should load"
    assert win.windowTitle() == "QuickLook 3 - s251021_a003001.fits"


def test_a_file_opened_by_hand_cancels_a_held_auto_load(header_edit_window, tmp_path):
    """A Finder open reaches `load_fits` even while a modal dialog holds auto-loads;
    the held frame must not replace that file when the hold ends."""
    win = header_edit_window
    with win.auto_load_held():
        win.on_file_detected(win.frame2)
        assert win.load_fits(win.frame1)       # what WindowManager.open_path calls

    assert win.windowTitle() == "QuickLook 3 - s251021_a001001.fits"


def test_header_editor_refuses_to_save_after_its_file_changed(header_edit_window):
    """The reader can still change file under an open editor (a Finder open goes
    straight to `load_fits`). The editor must then refuse rather than write its
    table into the other file."""
    from astropy.io import fits
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog

    win = header_edit_window
    editor = HeaderEditorDialog(win.fits_reader, win)      # opened on frame 1
    assert win.load_fits(win.frame2)

    editor.apply_table_edits(ext=0)
    assert win.fits_reader.get_header()["OBJECT"] == "GC_field_B", \
        "frame 1's table was written into frame 2's header in memory"
    assert len(win.warnings) == 1

    editor.save_header()
    assert len(win.warnings) == 2
    assert fits.getheader(win.frame2)["OBJECT"] == "GC_field_B"
    assert fits.getheader(win.frame2)["CRVAL2"] == 266.55
    assert fits.getheader(win.frame1)["OBJECT"] == "GC_field_A"
    editor.close()


# ------------------------------------------------- Save FITS As and auto-load


@pytest.mark.parametrize("target", ["copy_of_frame1.fits", "frame1 itself"])
def test_auto_load_during_save_as_does_not_change_the_saved_frame(header_edit_window,
                                                                  tmp_path, monkeypatch,
                                                                  target):
    """Frame 2 arrived while the Save FITS As dialog was open on frame 1. The save
    then wrote frame 2 -- to the copy, or over frame 1 itself when that was the name
    chosen."""
    from astropy.io import fits
    from PySide6.QtWidgets import QFileDialog

    win = header_edit_window
    out = win.frame1 if target == "frame1 itself" else str(tmp_path / target)

    def run_dialog(*args, **kwargs):
        # getSaveFileName runs a nested event loop; the poller announces frame 2.
        win.on_file_detected(win.frame2)
        return out, ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(run_dialog))
    win.save_file_as()

    assert win.warnings == []
    assert fits.getheader(out)["OBJECT"] == "GC_field_A", "the saved file is not frame 1"
    assert fits.getheader(win.frame2)["OBJECT"] == "GC_field_B"
    assert win.windowTitle() == "QuickLook 3 - s251021_a002001.fits", \
        "the held frame was not displayed once the save finished"


def test_save_as_refuses_after_the_file_changed_under_its_dialog(header_edit_window,
                                                                 tmp_path, monkeypatch):
    """A Finder open goes straight to `load_fits` and is not held, so the reader can
    still change file while the dialog is open. The save must then be refused."""
    from PySide6.QtWidgets import QFileDialog

    win = header_edit_window
    out = tmp_path / "copy_of_frame1.fits"

    def run_dialog(*args, **kwargs):
        assert win.load_fits(win.frame2)       # what WindowManager.open_path calls
        return str(out), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(run_dialog))
    win.save_file_as()

    assert len(win.warnings) == 1, "the user must be told why nothing was saved"
    assert not out.exists(), "frame 2 was written under the name chosen for frame 1"
