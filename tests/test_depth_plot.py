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
