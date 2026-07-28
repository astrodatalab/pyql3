import pytest
import numpy as np
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.core.fits_reader import FitsReader


def test_image_viewer_data_state(loaded_viewer):
    """CRITICAL RULE TEST: Ensure raw_data preserves original FITS array shape while transposed_data is formatted for PyQtGraph rendering."""
    assert loaded_viewer.raw_data is not None, "ImageViewer raw_data is None after loading FITS"
    assert loaded_viewer.transposed_data is not None, "ImageViewer transposed_data is None after loading FITS"

    # raw_data retains original un-transposed shape: (WAVE, DEC, RA)
    assert loaded_viewer.raw_data.ndim == 3
    assert loaded_viewer.transposed_data.ndim == 3
    assert loaded_viewer.raw_data.shape == (50, 40, 40), f"raw_data shape mismatch: {loaded_viewer.raw_data.shape}"


def test_image_viewer_rotation_and_flip(loaded_viewer):
    """Test image viewer rotation angle and flip transformations."""
    loaded_viewer.rot_angle = 90
    assert loaded_viewer.rot_angle == 90
    loaded_viewer.rot_angle = 180
    assert loaded_viewer.rot_angle == 180
    loaded_viewer.rot_angle = 0
    assert loaded_viewer.rot_angle == 0

    loaded_viewer.flip = True
    assert loaded_viewer.flip is True
    loaded_viewer.flip = False
    assert loaded_viewer.flip is False


def test_image_viewer_scaling_and_units(loaded_viewer):
    """Test log vs linear display scaling and DN/s vs Total DN unit toggling."""
    loaded_viewer.exptime = 10.0
    loaded_viewer.disp_as_dn = True
    mult = loaded_viewer.data_multiplier
    assert mult == 10.0, f"Expected multiplier 10.0 for EXPTIME=10 with Total DN, got {mult}"

    loaded_viewer.disp_as_dn = False
    mult_sec = loaded_viewer.data_multiplier
    assert mult_sec == 1.0, f"Expected multiplier 1.0 for DN/s, got {mult_sec}"

    loaded_viewer.use_log_scale = True
    assert loaded_viewer.use_log_scale is True
    loaded_viewer.use_log_scale = False
    assert loaded_viewer.use_log_scale is False


def test_image_viewer_colormap_fallback(qapp):
    """Test colormap setting with native pyqtgraph, matplotlib, cmcrameri, and invalid fallbacks."""
    viewer = ImageViewer()

    colormaps_to_test = [
        "viridis",
        "plasma",
        "grey",
        "coolwarm",
        "cmc.oslo",
        "cmc.hawaii",
        "cmc.batlow",
        "non_existent_colormap_12345" # Should trigger fallback to viridis without crash
    ]

    for cmap in colormaps_to_test:
        try:
            viewer.set_colormap(cmap)
            viewer.set_colormap(cmap, invert=True)
        except Exception as e:
            pytest.fail(f"ImageViewer.set_colormap('{cmap}') crashed with error: {e}")
