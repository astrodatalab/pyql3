import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox
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


def test_selecting_a_table_extension_clears_the_view(qapp, tmp_path, monkeypatch):
    """B6: set_data fell through both the 2-D and 3-D branches and returned silently, so
    the previous extension's image stayed on screen as if it were the table.

    Since R12 a table is refused before the reader switches to it, so the image on screen
    and the reader both stay on extension 0, and the user is told why."""
    import numpy as np
    from astropy.io import fits

    path = str(tmp_path / "with_table.fits")
    cols = [fits.Column(name="WAVELENGTH", format="E", array=np.arange(4, dtype=np.float32))]
    fits.HDUList([
        fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)),
        fits.BinTableHDU.from_columns(cols, name="WAVETAB"),
    ]).writeto(path, overwrite=True)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warnings.append(a)))

    win = MainWindow()
    win.load_fits(path)
    assert win.image_viewer.transposed_data is not None

    # force-select the table, as the old combo could
    assert win.load_fits(path, ext=1) is False

    assert len(warnings) == 1 and "No valid data" in warnings[0][2]
    assert win.fits_reader.current_ext == 0
    assert win.image_viewer.raw_data is win.fits_reader.get_data()
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
    good_hdul = win.fits_reader.hdul

    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a))
    )

    empty = str(tmp_path / "no_image.fits")
    fits.PrimaryHDU(header=fits.Header()).writeto(empty)

    assert win.load_fits(empty) is False, "load_fits must report failure"
    assert len(warnings) == 1, "the user must be told there is no image data"
    assert "No valid data" in warnings[0][2]

    # The previously displayed cube is untouched, and so is the reader underneath it (R12):
    # the window keeps showing, and editing the header of, the file it displays.
    assert win.image_viewer.raw_data.shape == good_shape
    assert win.windowTitle() == good_title
    assert win.fits_reader.filepath == sample_3d_fits
    assert win.fits_reader.hdul is good_hdul
    win.close()


def test_failed_load_leaves_the_reader_on_the_displayed_file(qapp, tmp_path, monkeypatch):
    """After a failed auto-load of a frame still arriving, the reader pointed at that
    frame while the window still showed the cube: Edit Header said "load a file first",
    Save As said "No data to save", and choosing extension 1 opened the partial file and
    raised (R12)."""
    import io
    import numpy as np
    from astropy.io import fits
    from astropy.utils.exceptions import AstropyUserWarning
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog

    good = str(tmp_path / "good_cube.fits")
    fits.HDUList([fits.PrimaryHDU(np.full((6, 5, 5), 3.0, dtype="f4")),
                  fits.ImageHDU(np.full((6, 5, 5), 4.0, dtype="f4"), name="SCI")]
                 ).writeto(good)
    buf = io.BytesIO()
    fits.PrimaryHDU(np.ones((6, 5, 5), dtype="f4")).writeto(buf)
    partial = str(tmp_path / "arriving.fits")
    with open(partial, "wb") as f:
        f.write(buf.getvalue()[:2944])          # the header and part of the data

    messages = []
    record = staticmethod(lambda *a, **k: messages.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", record)
    monkeypatch.setattr(QMessageBox, "critical", record)
    opened = []
    monkeypatch.setattr(HeaderEditorDialog, "exec", lambda self: opened.append(self) or 0)

    win = MainWindow()
    assert win.load_fits(good)
    with pytest.warns(AstropyUserWarning, match="truncated"):
        assert win.load_fits(partial, show_errors=False) is False   # one auto-load attempt

    assert win.windowTitle() == "QuickLook 3 - good_cube.fits"
    assert win.fits_reader.filepath == good
    assert win.fits_reader.get_data() is not None

    win.edit_header()
    assert len(opened) == 1, f"Edit FITS Header refused: {messages}"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))   # the user cancels
    win.save_file_as()
    assert messages == [], "Save FITS As refused before asking for a file name"

    win.image_viewer.combo_ext.setCurrentIndex(1)           # "1: SCI" of the cube on screen
    assert messages == []
    assert win.fits_reader.current_ext == 1
    assert float(win.image_viewer.raw_data.mean()) == 4.0
    win.close()



def test_a_4d_file_leaves_the_reader_and_title_on_the_displayed_file(qapp, tmp_path,
                                                                     sample_3d_fits,
                                                                     monkeypatch):
    """A file whose only image is 4-D has nothing the viewer can show. It must be refused
    before the reader commits, or the reader and the window disagree (R12)."""
    import numpy as np
    from astropy.io import fits

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warnings.append(a)))

    win = MainWindow()
    assert win.load_fits(sample_3d_fits)
    good_title = win.windowTitle()
    four = str(tmp_path / "four.fits")
    fits.PrimaryHDU(np.ones((1, 6, 5, 5), dtype="f4")).writeto(four)

    assert win.load_fits(four) is False
    assert len(warnings) == 1 and "No valid data" in warnings[0][2]
    assert win.fits_reader.filepath == sample_3d_fits
    assert win.windowTitle() == good_title
    assert win.image_viewer.raw_data.ndim == 3
    win.close()


def test_a_4d_extension_is_not_offered(qapp, tmp_path):
    """The Extension combo offers only what the viewer can show (B6): a 4-D extension is
    not listed, so it cannot be chosen and refused."""
    import numpy as np
    from astropy.io import fits

    path = str(tmp_path / "with_4d.fits")
    fits.HDUList([fits.PrimaryHDU(np.zeros((6, 5, 5), dtype="f4")),
                  fits.ImageHDU(np.ones((1, 6, 5, 5), dtype="f4"), name="FOURD"),
                  fits.ImageHDU(np.ones((6, 5, 5), dtype="f4"), name="SCI")]).writeto(path)

    win = MainWindow()
    assert win.load_fits(path)
    combo = win.image_viewer.combo_ext
    assert [combo.itemText(i) for i in range(combo.count())] == ["0: PRIMARY", "2: SCI"]
    win.close()


def test_a_refused_extension_puts_the_combo_back(qapp, tmp_path, monkeypatch):
    """Choosing an extension whose data cannot be read (here cut short, as a file still being
    copied is) is refused and the reader stays where it was, so the Extension combo must go
    back to the extension on screen rather than name the refused one (R12)."""
    import io
    import warnings as warnings_module
    import numpy as np
    from astropy.io import fits

    buffer = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(np.zeros((6, 5, 5), dtype="f4")),
                  fits.ImageHDU(np.ones((60, 50, 50), dtype="f4"), name="SCI")]
                 ).writeto(buffer)
    path = str(tmp_path / "cut_short.fits")
    with open(path, "wb") as f:
        f.write(buffer.getvalue()[:-200000])       # the SCI header whole, its data cut short
    errors = []
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: errors.append(a)))

    win = MainWindow()
    with warnings_module.catch_warnings():
        warnings_module.simplefilter("ignore")     # astropy warns about the truncation
        assert win.load_fits(path, ext=0)
        combo = win.image_viewer.combo_ext
        assert [combo.itemText(i) for i in range(combo.count())] == ["0: PRIMARY", "1: SCI"]

        combo.setCurrentIndex(1)

    assert len(errors) == 1 and "Failed to open" in errors[0][2]
    assert win.fits_reader.current_ext == 0
    assert combo.currentData() == 0
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


def test_saving_the_header_in_a_watched_directory_keeps_the_view(qapp, tmp_path,
                                                                 monkeypatch):
    """R70: Save Header replaces the displayed file with `os.replace`, so the poller sees
    a changed file and announces it. Reloading the window's own save reset a 5 x 5 px
    zoom to the full frame."""
    import numpy as np
    from astropy.io import fits
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog
    from pyql3.services.poller import SETTLE_CHECKS

    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(
        lambda *a, **k: pytest.fail(f"error dialog: {a[2]}")))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda *a, **k: pytest.fail(f"warning dialog: {a[2]}")))

    watched = tmp_path / "watched"
    watched.mkdir()
    frame = str(watched / "s251021_a001001.fits")
    header = fits.Header()
    header["OBJECT"] = "GC_field_A"
    fits.PrimaryHDU(np.ones((8, 30, 30), dtype="f4"), header=header).writeto(frame)

    win = MainWindow()
    assert win.poller.start_polling(str(watched))
    assert win.load_fits(frame)
    view = win.image_viewer.imv.getView()
    view.setRange(xRange=(10, 15), yRange=(10, 15), padding=0)
    zoomed = view.viewRange()

    def run_dialog(dialog):
        _edit_value(dialog, "OBJECT", "GC_field_A_rev")
        dialog.save_header()                       # answers Yes: saved in place
        return 0

    monkeypatch.setattr(HeaderEditorDialog, "exec", run_dialog)
    win.edit_header()
    assert fits.getheader(frame)["OBJECT"] == "GC_field_A_rev"

    announced = []
    win.poller.file_detected.connect(announced.append)
    win.poller._add_candidate(frame)               # the scan that sees the replaced file
    for _ in range(SETTLE_CHECKS + 1):
        win.poller._check_pending()

    assert announced == [frame], "the premise: the poller announces the saved file"
    assert view.viewRange() == zoomed, "the window reloaded its own save and reset the view"
    win.close()


def _layout(viewer):
    """The axes on screen and the plane's shape."""
    return (viewer.current_x_axis, viewer.current_y_axis, viewer.current_z_axis,
            viewer.current_plane().shape)


def test_error_extension_keeps_the_science_axis_layout(qapp, s3d_like_fits):
    """R25: a JWST ERR or DQ extension has no WCS cards. It used to open with wavelength across
    the screen and no sky readout, where SCI shows RA by Dec with wavelength on Z."""
    win = MainWindow()
    viewer = win.image_viewer
    assert win.load_fits(s3d_like_fits, ext=1)
    science = _layout(viewer)
    assert science == ("AXIS 1", "AXIS 2", "AXIS 3", (7, 9))

    for ext in (2, 3):
        assert win.load_fits(s3d_like_fits, ext=ext)
        assert _layout(viewer) == science, f"ext {ext}"
        assert viewer.wcs is not None and viewer.wcs.has_celestial, f"ext {ext}"
        assert viewer.wcs_z_idx == 2, f"ext {ext}: no wavelengths on the slice axis"
    win.close()


def test_noise_extension_uses_the_primary_integration_time(qapp, osiris_like_fits_with_noise):
    """R25: OSIRIS noise and quality extensions have empty headers. The noise extension used to
    multiply by 1.0 for Total DN; both used to open with wavelength across the screen (Task 20's
    default for a header that names no instrument), where the primary shows RA by Dec. The
    noise is float data in the primary's unit, so it borrows the primary's `BUNIT` and time."""
    win = MainWindow()
    viewer = win.image_viewer
    viewer.disp_as_dn = True
    assert win.load_fits(osiris_like_fits_with_noise, ext=0)
    science = (_layout(viewer), viewer.data_multiplier)
    assert science == (("AXIS 3", "AXIS 2", "AXIS 1", (7, 9)), pytest.approx(899.92 * 2))

    assert win.load_fits(osiris_like_fits_with_noise, ext=1)
    assert (_layout(viewer), viewer.data_multiplier) == science
    assert viewer.data_keywords['BUNIT'] == 'ADU per second per coadd'
    assert viewer.data_unit.label == "DN/s"
    assert viewer.wcs_z_idx == 0, "no wavelengths on the slice axis"
    win.close()


def test_a_quality_extension_borrows_no_unit(qapp, osiris_like_fits_with_noise):
    """The OSIRIS quality extension is uint8 flags. It borrowed the primary's `BUNIT` and time,
    so it read "DN/s" and under Total DN each flag was multiplied by 899.92. It keeps the
    primary's axes and WCS, but its unit is Unknown and Total DN is not offered."""
    win = MainWindow()
    viewer = win.image_viewer
    assert win.load_fits(osiris_like_fits_with_noise, ext=0)
    win.set_display_unit(True)
    assert viewer.disp_as_dn

    assert win.load_fits(osiris_like_fits_with_noise, ext=2)

    assert _layout(viewer) == ("AXIS 3", "AXIS 2", "AXIS 1", (7, 9))
    assert viewer.wcs_z_idx == 0
    assert viewer.data_unit.label == "Unknown"
    assert not viewer.total_dn_available and not viewer.disp_as_dn
    assert viewer.data_multiplier == 1.0
    assert not win.action_tot_dn.isEnabled()
    win.close()


def test_jwst_extensions_do_not_borrow_the_science_unit(qapp, s3d_like_fits):
    """JWST's SCI states `BUNIT = 'MJy/sr'`; the ERR, DQ, WMAP and VAR_* extensions beside it
    are not all in that unit (DQ is flags, VAR_* squared). They read "MJy/sr" because SCI was
    their reference header. Only the primary header lends a unit, so an extension that states
    none reads Unknown. The WCS is still SCI's."""
    win = MainWindow()
    viewer = win.image_viewer
    assert win.load_fits(s3d_like_fits, ext=1)
    assert viewer.data_unit.label == "MJy/sr"

    for ext in (2, 3):                             # ERR (float), DQ (uint8); neither has BUNIT
        assert win.load_fits(s3d_like_fits, ext=ext)
        assert viewer.data_unit.label == "Unknown", f"ext {ext}"
        assert 'BUNIT' not in viewer.data_keywords, f"ext {ext}"
        assert viewer.wcs is not None and viewer.wcs.has_celestial, f"ext {ext}"
    win.close()


def test_a_primary_wcs_with_fewer_axes_is_not_borrowed(qapp, tmp_path, nifs_like_header):
    """R25, R81: a NIFS primary header carries a 2-axis RA/Dec WCS of the raw detector frame
    and the exposure keywords; the 3-D SCI cube has only IRAF `LINEAR` axes. The primary's
    `EXPTIME` and `COADDS` are borrowed; its WCS is not."""
    from astropy.io import fits
    from astropy.wcs import WCS
    import numpy as np

    detector = WCS(naxis=2)
    detector.wcs.ctype = ['RA---TAN', 'DEC--TAN']
    detector.wcs.cdelt = [-1e-5, 1e-5]
    primary = detector.to_header()
    primary['EXPTIME'] = 600.0
    primary['COADDS'] = 1
    primary['INSTRUME'] = 'NIFS'
    path = str(tmp_path / "nifs_like.fits")
    fits.HDUList([fits.PrimaryHDU(header=primary),
                  fits.ImageHDU(np.ones((40, 12, 10), dtype=np.float32), nifs_like_header,
                                name='SCI')]).writeto(path)

    win = MainWindow()
    viewer = win.image_viewer
    assert win.load_fits(path)
    assert win.fits_reader.current_ext == 1

    assert viewer.wcs is not None and not viewer.wcs.has_celestial, "the detector WCS was used"
    assert _layout(viewer) == ("AXIS 1", "AXIS 2", "AXIS 3", (10, 12))
    assert viewer.wcs_z_idx == 2
    assert viewer.data_keywords['EXPTIME'] == 600.0
    assert viewer.data_keywords['COADDS'] == 1
    win.close()


@pytest.fixture
def run_main(qapp, monkeypatch):
    """Run `main.main()` with the given arguments, returning at once instead of entering the loop."""
    import importlib.util
    import pathlib
    import sys
    from PySide6.QtWidgets import QApplication

    # main.py sits at the repository root, which is not on the import path.
    spec = importlib.util.spec_from_file_location(
        "pyql3_main_script", pathlib.Path(__file__).resolve().parent.parent / "main.py")
    main_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main_module)

    handlers = []

    class RecordedHandler(main_module.FileOpenHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            handlers.append(self)

    monkeypatch.setattr(main_module, "FileOpenHandler", RecordedHandler)
    monkeypatch.setattr(main_module, "QApplication", lambda argv: qapp)
    monkeypatch.setattr(QApplication, "exec", staticmethod(lambda: 0))

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["main.py", *args])
        with pytest.raises(SystemExit):
            main_module.main()

    yield run
    for handler in handlers:
        qapp.removeEventFilter(handler)


def test_poll_dir_opens_the_newest_compressed_frame(run_main, tmp_path):
    """The initial file is chosen with `is_fits_path`, so `.fits.gz` and `.FITS` count (R59)."""
    import os
    import numpy as np
    from astropy.io import fits
    from pyql3.gui.window_manager import get_window_manager

    frames = {"s0001.fits": 1000, "s0002.fits.gz": 3000, "s0003.FITS": 2000, "notes.txt": 4000}
    for name, mtime in frames.items():
        path = tmp_path / name
        if name.endswith("txt"):
            path.write_text("not a frame")
        else:
            fits.PrimaryHDU(np.ones((4, 4), "f4")).writeto(path)
        os.utime(path, (mtime, mtime))

    run_main("--poll-dir", str(tmp_path))

    windows = get_window_manager().windows()
    try:
        assert "s0002.fits.gz" in windows[0].windowTitle()
    finally:
        for win in windows:
            win.close()


def test_a_missing_poll_dir_says_so(run_main, tmp_path, capsys):
    from pyql3.gui.window_manager import get_window_manager

    missing = tmp_path / "no_such_dir"
    run_main("--poll-dir", str(missing))

    assert f"pyql3: no directory at {missing}" in capsys.readouterr().err
    for win in get_window_manager().windows():
        win.close()


def test_an_unreadable_poll_dir_warns_and_launches_without_the_watch(run_main, tmp_path,
                                                                     monkeypatch, capsys):
    """`os.listdir` raises where the old glob returned nothing; the launch must go on."""
    import os
    from pyql3.gui.window_manager import get_window_manager
    from pyql3.services.poller import watcher_of

    real_listdir = os.listdir

    def listdir(path="."):
        if str(path) == str(tmp_path):
            raise PermissionError(13, "Permission denied", str(path))
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", listdir)

    run_main("--poll-dir", str(tmp_path))

    windows = get_window_manager().windows()
    try:
        assert len(windows) == 1
        assert f"cannot read {tmp_path}" in capsys.readouterr().err
        assert "cannot read" in windows[0].statusBar().currentMessage()
        assert watcher_of(str(tmp_path)) is None, "the watch was started anyway"
    finally:
        for win in windows:
            win.close()
