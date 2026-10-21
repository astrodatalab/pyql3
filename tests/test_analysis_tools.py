import numpy as np
import pytest
from pyql3.gui.tools.cuts import CutPlotDialog
from pyql3.gui.tools.fitting import GaussianFitDialog
from pyql3.gui.tools.statistics import StatisticsDialog
from pyql3.gui.tools.photometry import PhotometryDialog
from pyql3.gui.tools.strehl import StrehlDialog
from pyql3.gui.tools.arithmetic import ArithmeticDialog


def test_cuts_tool(loaded_viewer):
    """Test horizontal, vertical, and diagonal profile cuts."""
    for orientation in ['horizontal', 'vertical', 'diagonal']:
        dialog = CutPlotDialog(orientation, image_viewer=loaded_viewer)
        dialog.update_plot()
        assert dialog.plot_curve is not None, f"Plot curve None for {orientation} cut"
        dialog.close()


def test_gaussian_fit_tool(loaded_viewer):
    """Test 2D Gaussian fitting tool centroid calculation and FWHM outputs."""
    dialog = GaussianFitDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    dialog.update_fit()
    assert len(dialog.value_labels) > 0, "GaussianFitDialog missing value labels"
    dialog.close()


def test_statistics_tool(loaded_viewer):
    """Test region statistics calculations (mean, std, min, max, sum)."""
    dialog = StatisticsDialog(image_viewer=loaded_viewer)
    dialog.update_stats()
    assert dialog.value_labels["Mean"].text() != "N/A", "Statistics Mean value label was not updated"
    assert dialog.value_labels["Max"].text() != "N/A", "Statistics Max value label was not updated"
    dialog.close()


def test_photometry_tool(loaded_viewer):
    """Test circular and rectangular aperture photometry integration."""
    dialog = PhotometryDialog(image_viewer=loaded_viewer)
    dialog.update_photometry()
    assert dialog.lbl_flux.text() != "N/A", "Photometry flux label was not updated"
    assert dialog.lbl_total.text() != "N/A", "Photometry total label was not updated"
    dialog.close()


def test_strehl_tool(loaded_viewer):
    """Test Strehl ratio calculation tool."""
    dialog = StrehlDialog(image_viewer=loaded_viewer)
    dialog.update_strehl()
    assert dialog.lbl_strehl.text() != "N/A", "Strehl ratio label was not updated"
    dialog.close()





def _viewer_with_one_bright_pixel(qapp, shape=(31, 21), pixel=(12, 9), counts=100.0):
    """A 2-D image that is zero except for one pixel, at display index `pixel` (x, y).

    With no header a 2-D array is read as numpy (y, x), so the array is built that way and
    the result is checked against `display_data`, which is indexed `[x, y]`.
    """
    from pyql3.gui.viewers.image_viewer import ImageViewer
    data = np.zeros(shape[::-1])
    data[pixel[1], pixel[0]] = counts
    viewer = ImageViewer()
    viewer.set_data(data, None)
    assert viewer.display_data.shape == shape
    assert viewer.display_data[pixel] == counts
    return viewer


def _draw_circle_on(roi, item_center, radius):
    """Put a CircleROI centred on an ImageItem coordinate."""
    roi.setSize([2 * radius, 2 * radius])
    roi.setPos([item_center[0] - radius, item_center[1] - radius])


def test_photometry_circle_drawn_on_a_pixel_measures_that_pixel(qapp):
    """The aperture is where the circle is drawn (BUGS.md R2): the pixel at index (12, 9)
    is drawn over [12, 13) x [9, 10), so its centre is the item coordinate (12.5, 9.5)."""
    viewer = _viewer_with_one_bright_pixel(qapp)
    dialog = PhotometryDialog(image_viewer=viewer)
    try:
        dialog.spin_aperture.setValue(1.0)
        _draw_circle_on(dialog.roi, (12.5, 9.5), 1.0)
        dialog.update_photometry()
        assert float(dialog.lbl_flux.text().replace(",", "")) == pytest.approx(100.0, abs=1e-3)
    finally:
        dialog.close()


def test_strehl_aperture_is_centred_on_the_drawn_circle(qapp, monkeypatch):
    """`calculate_strehl` takes a numpy index; the circle's centre is an ImageItem coordinate."""
    from pyql3.gui.tools import strehl as strehl_module
    viewer = _viewer_with_one_bright_pixel(qapp)
    dialog = StrehlDialog(image_viewer=viewer)
    try:
        calls = []
        monkeypatch.setattr(strehl_module, "calculate_strehl",
                            lambda data, center, **kw: calls.append(center))
        _draw_circle_on(dialog.roi, (12.5, 9.5), 10.0)
        calls.clear()
        dialog.update_strehl()
        assert calls, "calculate_strehl was not called"
        assert calls[-1] == pytest.approx((12.0, 9.0))
    finally:
        dialog.close()


def test_arithmetic_tool(loaded_viewer):
    """Test datacube arithmetic dialog initialization with open viewers."""
    dialog = ArithmeticDialog(image_viewer=loaded_viewer)
    dialog.close()


# --------------------------------------------------- whole-pixel sampling (BUGS.md R9)

def _flat_image_with_a_hot_pixel():
    """100 everywhere, and 1100 at display (40, 30)."""
    img = np.full((60, 80), 100.0)
    img[30, 40] = 1100.0
    return img


def _viewer_showing(qapp, img):
    from pyql3.gui.viewers.image_viewer import ImageViewer
    viewer = ImageViewer()
    viewer.set_data(img, None)
    return viewer


def _stats_at(dialog, pos, size=(10, 10)):
    dialog.roi.setPos(list(pos))
    dialog.roi.setSize(list(size))
    dialog.update_stats()
    return {k: dialog.value_labels[k].text() for k in ("Min", "Max", "Mean", "Total Pixels")}


def test_statistics_reads_whole_pixels(qapp):
    """A box at a fractional position used to read linear interpolations between pixels,
    and the part of it off the image read as zeros."""
    dialog = StatisticsDialog(None, _viewer_showing(qapp, _flat_image_with_a_hot_pixel()))

    on_edges = _stats_at(dialog, (35.0, 25.0))
    assert float(on_edges["Max"]) == 1100.0 and on_edges["Total Pixels"] == "100"

    # Half a pixel over, the hot pixel is still inside the box and still 1100.
    shifted = _stats_at(dialog, (35.5, 25.5))
    assert float(shifted["Max"]) == 1100.0, shifted
    assert float(shifted["Mean"]) == pytest.approx(110.0)
    assert shifted["Total Pixels"] == "100"

    # Half off the left edge: only the 50 pixels on the image are measured.
    half_off = _stats_at(dialog, (-5.0, 25.0))
    assert float(half_off["Min"]) == 100.0, half_off
    assert float(half_off["Mean"]) == pytest.approx(100.0)
    assert half_off["Total Pixels"] == "50"
    dialog.close()


def test_statistics_says_when_the_box_misses_the_image(qapp):
    dialog = StatisticsDialog(None, _viewer_showing(qapp, _flat_image_with_a_hot_pixel()))
    _stats_at(dialog, (35.0, 25.0))
    shown = _stats_at(dialog, (-50.0, 25.0))
    assert all(v == "N/A" for v in shown.values()), shown
    assert "does not overlap the image" in dialog.lbl_status.text()
    _stats_at(dialog, (35.0, 25.0))
    assert dialog.lbl_status.text() == ""
    dialog.close()


def test_statistics_reports_the_collapsed_plane(qapp):
    """In Z Range mode the screen shows a plane that is in no single channel of the cube,
    and the statistics are of that plane, in the displayed units."""
    from pyql3.gui.viewers.image_viewer import ImageViewer
    cube = np.random.default_rng(1).normal(100, 10, (20, 30, 30)).astype(np.float32)
    viewer = ImageViewer()
    viewer.set_data(cube)
    viewer._itime_coadds = 4.0
    viewer.disp_as_dn = True
    viewer.txt_zmin.setText("4")
    viewer.txt_zmax.setText("6")
    viewer.radio_range.setChecked(True)

    dialog = StatisticsDialog(None, viewer)
    dialog.roi.setPos([5.3, 8.4])      # off the pixel grid, as a mouse drag leaves it
    dialog.roi.setSize([10, 6])
    dialog.update_stats()

    expected = viewer.current_plane()[5:15, 8:14] * viewer.data_multiplier
    assert float(dialog.value_labels["Mean"].text()) == pytest.approx(expected.mean(), rel=1e-5)
    assert dialog.value_labels["Total Pixels"].text() == "60"
    dialog.close()


def _round_star(x0, y0=30.0, fwhm=2.5, sky=10.0, shape=(60, 80)):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    sigma = fwhm / 2.3548
    return 1000.0 * np.exp(-((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * sigma ** 2)) + sky


def test_peak_fit_fwhm_does_not_depend_on_box_phase(qapp):
    dialog = GaussianFitDialog(None, _viewer_showing(qapp, _round_star(40.0)))

    def fit_at(pos, size=(20, 20)):
        dialog.roi.setPos(list(pos))
        dialog.roi.setSize(list(size))
        dialog.update_fit()
        return {k: float(dialog.value_labels[k].text()) for k in
                ("FWHM X", "FWHM Y", "Center X", "Center Y", "Background")}

    whole = fit_at((30.0, 20.0))
    half = fit_at((30.5, 20.5))
    assert whole["FWHM X"] == pytest.approx(2.5, rel=0.02)
    assert half["FWHM X"] == pytest.approx(whole["FWHM X"], rel=0.01)
    assert half["FWHM Y"] == pytest.approx(whole["FWHM Y"], rel=0.01)
    # The centre is a numpy index: the star is at index (40, 30) whichever way the box sits.
    for fit in (whole, half):
        assert fit["Center X"] == pytest.approx(40.0, abs=0.05)
        assert fit["Center Y"] == pytest.approx(30.0, abs=0.05)
    dialog.close()


def test_peak_fit_background_ignores_the_part_of_the_box_off_the_image(qapp):
    """Off-image samples used to be zeros, which pulled the fitted background to 6.2."""
    dialog = GaussianFitDialog(None, _viewer_showing(qapp, _round_star(3.0)))
    dialog.roi.setPos([-7.0, 20.0])
    dialog.roi.setSize([20, 20])
    dialog.update_fit()
    assert float(dialog.value_labels["Background"].text()) == pytest.approx(10.0, rel=0.02)
    assert float(dialog.value_labels["Center X"].text()) == pytest.approx(3.0, abs=0.05)
    dialog.close()


def test_peak_fit_says_when_the_box_misses_the_image(qapp):
    dialog = GaussianFitDialog(None, _viewer_showing(qapp, _round_star(40.0)))
    dialog.roi.setPos([200.0, 20.0])
    dialog.roi.setSize([20, 20])
    dialog.update_fit()
    assert dialog.value_labels["FWHM X"].text() == "N/A"
    assert "does not overlap the image" in dialog.lbl_status.text()
    dialog.close()


def test_peak_fit_box_is_drawn_over_the_pixels_it_measures(qapp):
    """`set_center` takes a numpy index, so the box is centred on that pixel's centre in
    ImageItem coordinates, `index + 0.5`, and the measured pixels are the ones around it."""
    dialog = GaussianFitDialog(None, _viewer_showing(qapp, _round_star(40.0)))
    dialog.set_center((40, 30))
    centre = (dialog.roi.pos().x() + dialog.roi.size().x() / 2,
              dialog.roi.pos().y() + dialog.roi.size().y() / 2)
    assert centre == pytest.approx((40.5, 30.5))
    pixels, (xs, ys) = dialog.roi_plane_pixels()
    assert (xs, ys) == (slice(38, 43), slice(28, 33))
    dialog.close()


def test_peak_fit_with_too_few_pixels_on_the_image_clears_the_old_fit(qapp):
    """A 5x5 box dragged so one column is left on the image has 5 pixels, too few to fit.
    The numbers from the previous box must not stay on screen without a message."""
    dialog = GaussianFitDialog(None, _viewer_showing(qapp, _round_star(40.0)))
    dialog.roi.setPos([37.0, 28.0])
    dialog.roi.setSize([5, 5])
    dialog.update_fit()
    assert float(dialog.value_labels["FWHM X"].text()) > 0
    assert dialog.lbl_status.text() == ""

    dialog.roi.setPos([-4.0, 28.0])         # one column, 5 pixels, left on the image
    dialog.update_fit()
    assert all(dialog.value_labels[k].text() == "N/A"
               for k in ("Min Pixel Value", "Max Pixel Value", *dialog.FIT_KEYS))
    assert "at least 9" in dialog.lbl_status.text()
    assert dialog.last_fit_data is None

    dialog.roi.setPos([37.0, 28.0])
    dialog.update_fit()
    assert dialog.lbl_status.text() == ""
    assert float(dialog.value_labels["FWHM X"].text()) > 0
    dialog.close()
