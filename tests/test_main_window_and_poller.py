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

    win.open_surface_plot()
    assert hasattr(win, '_surf_dialog') and win._surf_dialog is not None

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
