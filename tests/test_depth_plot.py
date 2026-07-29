import pytest
import numpy as np
from PySide6.QtWidgets import QApplication, QFileDialog
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.depth_plot import DepthPlotDialog, latex_to_html
from pyql3.core.fits_reader import FitsReader


def test_depth_plot_region_extraction(loaded_viewer):
    """Test spectral cube depth plot spectrum calculation and background subtraction."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    dialog.combo_calc.setCurrentText("Median")
    dialog.update_plot()
    assert dialog.plot_widget is not None, "Plot widget None in DepthPlotDialog"

    # Enable background subtraction
    dialog.chk_enable_bg.setChecked(True)
    dialog.toggle_background()
    dialog.combo_bg_calc.setCurrentText("Average")
    dialog.update_plot()
    assert dialog.bg_roi is not None, "Background ROI was not initialized"
    dialog.close()


def test_depth_plot_background_toggle_without_initial_center(loaded_viewer):
    """The background checkbox must work when the dialog is opened from the Plot menu.

    Regression: the chk_enable_bg / combo_bg_calc connections lived at the end of
    set_center(), which is only reached when an initial_center is supplied, so
    ticking the box did nothing on the Plot -> Depth Plot path.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)   # no initial_center
    assert dialog.bg_roi is None, "Background ROI should not exist before enabling"

    dialog.chk_enable_bg.setChecked(True)   # signal path only, no manual call
    assert dialog.bg_roi is not None, "Ticking the checkbox did not create the background ROI"
    assert dialog.spin_bg_x0.isEnabled(), "Background spinboxes were not enabled"
    assert dialog.combo_bg_calc.isEnabled(), "Background calc combo was not enabled"

    bg_x, _ = dialog.plot_bg.getData()
    sub_x, _ = dialog.plot_sub.getData()
    assert bg_x is not None and len(bg_x) > 0, "Background spectrum was not plotted"
    assert sub_x is not None and len(sub_x) > 0, "Subtracted spectrum was not plotted"

    dialog.chk_enable_bg.setChecked(False)
    assert dialog.bg_roi is None, "Unticking the checkbox did not remove the background ROI"
    dialog.close()


def test_depth_plot_background_handler_not_duplicated(loaded_viewer):
    """set_center() must not re-connect the background handlers on every call."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    dialog.set_center((22, 22))
    dialog.set_center((18, 18))

    calls = []
    original = dialog.toggle_background

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    dialog.toggle_background = counting
    dialog.chk_enable_bg.setChecked(True)
    assert len(calls) == 1, f"toggle_background ran {len(calls)}x for a single click (duplicate connections)"
    dialog.close()


def test_depth_plot_set_center_moves_roi(loaded_viewer):
    """set_center() must centre the ROI on the requested pixel."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)
    dialog.set_center((15, 25))

    pos, size = dialog.roi.pos(), dialog.roi.size()
    center = (pos.x() + size.x() / 2.0, pos.y() + size.y() / 2.0)
    assert center == pytest.approx((15.0, 25.0)), f"ROI centre is {center}, expected (15, 25)"
    dialog.close()


def test_depth_plot_latex_conversion():
    """Test conversion of LaTeX strings like $$H_\\alpha$$ and $$P_{2f}$$ to HTML for line labels."""
    test_cases = [
        ("$$H_\\alpha$$", "H<sub>&alpha;</sub>"),
        ("$$P_{2f}6.5$$", "P<sub>2f</sub>6.5"),
        ("Ti I", "Ti I"),
    ]
    for inp, expected in test_cases:
        res = latex_to_html(inp)
        assert expected in res, f"Expected '{expected}' in latex_to_html('{inp}'), got '{res}'"


def test_depth_plot_linelist_loading_and_overlays(loaded_viewer):
    """Test spectral line list loading, default selection, line item labels, rotation, and dataBounds exclusion."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)

    # Check populated default linelists
    items = [dialog.combo_linelist.itemText(i) for i in range(dialog.combo_linelist.count())]
    assert "nir_stellar_lines.txt" in items, "Default nir_stellar_lines.txt missing from linelist combo"
    assert dialog.combo_linelist.currentText() == "nir_stellar_lines.txt", "nir_stellar_lines.txt is not selected by default"
    assert len(dialog.loaded_lines) > 0, "Failed to parse default line list lines"

    # Turn on line list overlays
    dialog.chk_enable_lines.setChecked(True)
    dialog.update_line_overlays()

    # Verify line items have dataBounds set to (None, None) so they don't break Y-axis scaling
    for line_item, text_item in dialog.line_items:
        db_line = line_item.dataBounds(0)
        db_text = text_item.dataBounds(0)
        assert db_line == (None, None), "InfiniteLine dataBounds must return (None, None)"
        assert db_text == (None, None), "TextItem dataBounds must return (None, None)"

    dialog.close()


def test_depth_plot_auto_y_range(loaded_viewer):
    """Test that Auto Y-range scales around spectral data without being distorted by vertical line overlay labels."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)
    dialog.chk_enable_lines.setChecked(True)
    dialog.update_line_overlays()

    # Call auto_y_range
    dialog.auto_y_range()

    view_box = dialog.plot_widget.getViewBox()
    y_range = view_box.viewRange()[1]
    assert y_range[1] > y_range[0], "Auto Y-range produced inverted bounds"
    assert abs(y_range[1] - y_range[0]) < 100000.0, f"Auto Y-range produced unscaled infinite bounds: {y_range}"
    dialog.close()


def test_depth_plot_no_spurious_file_dialog(qapp, monkeypatch):
    """Ensure opening DepthPlotDialog does not pop up QFileDialog."""
    file_dialog_opened = False

    def mock_get_open_filename(*args, **kwargs):
        nonlocal file_dialog_opened
        file_dialog_opened = True
        return ("", "")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", mock_get_open_filename)

    viewer = ImageViewer()
    dialog = DepthPlotDialog(image_viewer=viewer)

    assert not file_dialog_opened, "DepthPlotDialog popped up a QFileDialog during initialization!"
    dialog.close()


def test_depth_plot_wavelength_x_axis_and_csv_export(loaded_viewer):
    """Verify that primary X-axis data is set to physical wavelengths for CSV export."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)
    dialog.update_plot()

    x_data, y_data = dialog.plot_data.getData()
    assert x_data is not None and len(x_data) > 0, "No X data returned from plot_data"
    
    # Check that x_data matches physical wavelengths rather than 0-indexed integers
    # For OSIRIS Kn5 cube, wavelengths are around ~2.2 µm
    assert np.mean(x_data) > 1.0, f"Expected physical wavelength X-axis (> 1.0 µm), got mean: {np.mean(x_data)}"
    
    # Bottom label check
    bottom_label = dialog.plot_widget.getAxis('bottom').labelText
    assert "Wavelength" in bottom_label, f"Expected 'Wavelength' in bottom axis label, got '{bottom_label}'"
    
    # Top axis check
    assert dialog.top_axis.wavelengths is not None, "Top axis wavelengths not set"
    dialog.close()


def test_depth_plot_export_button(loaded_viewer):
    """Verify that Export... button exists on top layout and triggers export dialog."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)
    assert hasattr(dialog, "btn_export"), "DepthPlotDialog missing btn_export button"
    assert dialog.btn_export.text() == "Export...", "Export button text mismatch"

    dialog.btn_export.click()
    scene = dialog.plot_widget.scene()
    assert scene.exportDialog is not None, "Export dialog was not created on scene"
    assert scene.exportDialog.isVisible(), "Export dialog is not visible after clicking Export... button"
    dialog.close()
