import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
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

    # Update tools for display unit change
    win.update_tools_for_unit()

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


def test_reload_and_redisplay_pick_up_a_rewritten_file(qapp, tmp_path):
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
    fits.PrimaryHDU(np.full((8, 8), 1010.0, dtype=np.float32)).writeto(path, overwrite=True)

    win.load_fits(path)                 # what on_file_detected does
    assert win.image_viewer.raw_data.mean() == 1010.0

    fits.PrimaryHDU(np.full((8, 8), 2020.0, dtype=np.float32)).writeto(path, overwrite=True)
    win.redisplay_image()
    assert win.image_viewer.raw_data.mean() == 2020.0
    win.close()
