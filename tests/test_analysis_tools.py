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


def _photometry_at(dialog, item_center, r=3.0):
    """Centre the aperture on an ImageItem coordinate and return the three labels' text."""
    _draw_circle_on(dialog.roi, item_center, r)
    dialog.update_photometry()
    return dialog.lbl_flux.text(), dialog.lbl_sky.text(), dialog.lbl_total.text()


def test_photometry_on_flat_sky_is_zero_everywhere(qapp):
    """BUGS.md R18: on a flat sky of 10 the subtracted flux is 0 wherever the aperture is.
    Off the image the annulus read zeros and the sky was scaled by the full aperture area,
    and one NaN pixel made the raw flux NaN."""
    sky = np.full((60, 80), 10.0)
    viewer = _viewer_showing(qapp, sky)
    dialog = PhotometryDialog(None, viewer)
    try:
        dialog.spin_aperture.setValue(3.0)
        dialog.spin_inner.setValue(6.0)
        dialog.spin_outer.setValue(15.0)
        for where in ((40.0, 30.0), (1.0, 30.0), (0.5, 0.5)):
            raw, bkg, subtracted = (float(t) for t in _photometry_at(dialog, where))
            assert subtracted == pytest.approx(0.0, abs=1e-6), (where, raw, bkg)
            assert raw > 0 and bkg == pytest.approx(raw, abs=1e-6), (where, raw, bkg)

        with_nan = sky.copy()
        with_nan[30, 40] = np.nan          # display (40, 30), inside the aperture
        viewer.set_data(with_nan, None)
        raw, bkg, subtracted = (float(t) for t in _photometry_at(dialog, (40.0, 30.0)))
        assert np.isfinite(raw) and np.isfinite(bkg)
        assert subtracted == pytest.approx(0.0, abs=1e-6), (raw, bkg)
    finally:
        dialog.close()


def test_an_inverted_annulus_shows_a_message(qapp):
    """An inner sky radius at or above the outer one used to raise out of the slot, and the
    labels kept the previous numbers as if they were current (BUGS.md R18)."""
    viewer = _viewer_showing(qapp, np.full((60, 80), 10.0))
    dialog = PhotometryDialog(None, viewer)
    try:
        dialog.spin_aperture.setValue(3.0)
        dialog.spin_inner.setValue(6.0)
        dialog.spin_outer.setValue(15.0)
        _photometry_at(dialog, (40.0, 30.0))
        dialog.spin_inner.setValue(25.0)
        shown = (dialog.lbl_flux.text(), dialog.lbl_sky.text(), dialog.lbl_total.text())
        assert any("inner sky radius must be smaller than outer" in t for t in shown), shown
        for text in shown:
            with pytest.raises(ValueError):
                float(text)

        dialog.spin_inner.setValue(15.0)   # equal is inverted too
        assert any("inner sky radius must be smaller than outer" in t for t in
                   (dialog.lbl_flux.text(), dialog.lbl_sky.text(), dialog.lbl_total.text()))

        dialog.spin_inner.setValue(6.0)
        assert float(dialog.lbl_total.text()) == pytest.approx(0.0, abs=1e-6)
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


def _arithmetic_on(qapp, path, monkeypatch):
    """A window showing `path` and an Arithmetic dialog on it; any message box fails the test."""
    from PySide6.QtWidgets import QMessageBox
    from pyql3.gui.main_window import MainWindow

    def no_dialog(*args, **kwargs):
        raise AssertionError(f"unexpected message box: {args[1:]}")
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(no_dialog))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(no_dialog))
    win = MainWindow()
    win.load_fits(str(path))
    return win, ArithmeticDialog(win, win.image_viewer)


def test_arithmetic_keeps_precision_and_range(qapp, tmp_path, monkeypatch):
    """BUGS.md R23 / M4: the Number operand kept two decimals, Active + Active added in the
    file's integer type, and the result header still described the integer encoding."""
    from astropy.io import fits
    path = tmp_path / "raw.fits"
    fits.PrimaryHDU(np.full((4, 5), 40000, dtype=np.uint16)).writeto(path)  # BITPIX 16, BZERO 32768
    win, dialog = _arithmetic_on(qapp, path, monkeypatch)

    for spin in (dialog.spin1, dialog.spin2):
        spin.setValue(0.0012)
        assert spin.value() == 0.0012
    dialog.rb_div.setChecked(True)
    dialog.calculate()
    assert dialog.result_win.image_viewer.raw_data[0, 0] == pytest.approx(40000 / 0.0012)

    dialog.rb2_active.setChecked(True)
    dialog.rb_add.setChecked(True)
    dialog.calculate()
    result = dialog.result_win.image_viewer.raw_data
    assert result[0, 0] == 80000, result.dtype

    for header in (dialog.result_win.fits_reader.get_header(),
                   dialog.result_win.fits_reader.hdul[0].header):
        assert "BZERO" not in header and "BSCALE" not in header
        assert header["BITPIX"] == -64
    dialog.close()
    win.close()


def test_arithmetic_result_title_is_the_expression(qapp, tmp_path, monkeypatch):
    """BUGS.md R69: `os.path.basename` of an expression kept only the text after its `/`, so
    a quotient of two files was titled as if the window held the second file."""
    from astropy.io import fits
    paths = []
    for name in ("a001.fits", "a002.fits"):
        paths.append(tmp_path / name)
        fits.PrimaryHDU(np.full((4, 5), 3.0, dtype=np.float32)).writeto(paths[-1])
    win, dialog = _arithmetic_on(qapp, paths[0], monkeypatch)

    dialog.rb1_file.setChecked(True)
    dialog.txt1_file.setText(str(paths[0]))
    dialog.rb2_file.setChecked(True)
    dialog.txt2_file.setText(str(paths[1]))
    dialog.rb_div.setChecked(True)
    dialog.calculate()
    assert dialog.result_win.windowTitle() == "QuickLook 3 - (a001.fits / a002.fits)"

    dialog.rb1_active.setChecked(True)
    dialog.rb2_num.setChecked(True)
    dialog.spin2.setValue(2.0)
    dialog.calculate()
    assert dialog.result_win.windowTitle() == "QuickLook 3 - (ActiveImage / 2.0)"
    dialog.close()
    win.close()


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


def test_fit_models_agree_on_the_angle(qapp):
    """BUGS.md R72: the Gaussian's cross term had the opposite sign to the Lorentzian and
    Moffat rotations, so for one source the Gaussian reported a mirrored angle (-120 against
    +120 and -60). The angle is counter-clockwise from +X, and FWHM X is the width along it."""
    yy, xx = np.mgrid[0:60, 0:80]
    theta = np.radians(30.0)
    dx, dy = xx - 40.0, yy - 30.0
    along = dx * np.cos(theta) + dy * np.sin(theta)
    across = -dx * np.sin(theta) + dy * np.cos(theta)
    img = 1000.0 * np.exp(-(along / 4.0) ** 2 / 2 - (across / 2.0) ** 2 / 2) + 10.0
    dialog = GaussianFitDialog(None, _viewer_showing(qapp, img))
    try:
        dialog.roi.setPos([25.0, 15.0])
        dialog.roi.setSize([30, 30])
        major = {}
        for model in ("Gaussian", "Lorentzian", "Moffat"):
            dialog.combo_type.setCurrentText(model)
            dialog.update_fit()
            angle = float(dialog.value_labels["Angle (deg)"].text())
            fwhm_x = float(dialog.value_labels["FWHM X"].text())
            fwhm_y = float(dialog.value_labels["FWHM Y"].text())
            # The major axis is along the angle when FWHM X is the larger width.
            major[model] = angle if fwhm_x >= fwhm_y else angle + 90.0

        def apart(a, b):
            return abs((a - b + 90.0) % 180.0 - 90.0)

        for model, direction in major.items():
            assert apart(direction, major["Gaussian"]) < 1.0, major
            assert apart(direction, 30.0) < 1.0, major
    finally:
        dialog.close()


def test_peak_fit_surface_has_the_shape_of_the_data(qapp):
    """Phase 7 review m6: the model surface was reshaped (ny, nx) from an (nx, ny) grid, so
    the Display Peak Fit view of a non-square box showed it scrambled."""
    dialog = GaussianFitDialog(None, _viewer_showing(qapp, _round_star(40.0)))
    try:
        dialog.roi.setPos([30.0, 22.0])
        dialog.roi.setSize([20, 16])
        dialog.update_fit()
        raw, fit = dialog.last_raw_data, dialog.last_fit_data
        assert raw.shape == (20, 16)
        assert fit.shape == raw.shape
        assert np.abs(fit - raw).max() < 0.01 * raw.max()
    finally:
        dialog.close()


def test_display_peak_fit_shows_data_model_and_residual(qapp):
    """Display Peak Fit is three flat images, so it needs no OpenGL: the box's pixels, the
    fitted model and data minus model, each placed at the box's own pixel coordinates."""
    dialog = GaussianFitDialog(None, _viewer_showing(qapp, _round_star(40.0)))
    try:
        dialog.roi.setPos([30.0, 22.0])
        dialog.roi.setSize([20, 16])
        dialog.update_fit()
        dialog.display_peak_fit()
        display = dialog.disp_dialog
        raw, fit = dialog.last_raw_data, dialog.last_fit_data

        images = display.images
        assert set(images) == {"data", "model", "residual"}
        np.testing.assert_array_equal(images["data"].image, raw)
        np.testing.assert_array_equal(images["model"].image, fit)
        np.testing.assert_allclose(images["residual"].image, raw - fit)
        for item in images.values():
            rect = item.mapRectToParent(item.boundingRect())
            assert (rect.x(), rect.y(), rect.width(), rect.height()) == (30, 22, 20, 16)

        # Data and model on one scale, so they compare by eye; the residual centred on zero
        assert images["data"].getLevels() == pytest.approx(images["model"].getLevels())
        low, high = images["residual"].getLevels()
        assert low == pytest.approx(-high) and high > 0
    finally:
        dialog.close()


def test_no_module_imports_opengl():
    """OpenGL is not available on every machine, CI's Linux runner included (Qt's offscreen
    platform reports OpenGL 2.0, below pyqtgraph.opengl's 2.1), so nothing may need it."""
    import os
    import subprocess
    import sys

    script = ("import sys, pkgutil, importlib, pyql3\n"
              "for m in pkgutil.walk_packages(pyql3.__path__, 'pyql3.'):\n"
              "    importlib.import_module(m.name)\n"
              "print('pyqtgraph.opengl' in sys.modules, 'OpenGL' in sys.modules)\n")
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         check=True, env=environment)
    assert out.stdout.split()[-2:] == ["False", "False"], out.stdout + out.stderr


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


# ----------------------------------- tools follow the viewer's signals (BUGS.md R14, R87)

#: Channel k of `_scaled_star_cube` is `CHANNEL_SCALE[k]` times one image of a star. No channel
#: is zero, so the Peak Fit has a peak to fit on every plane, and every reading a tool makes is
#: the channel-0 reading times the scale of the plane on screen.
CHANNEL_SCALE = np.array([10., 50., 3., 80., 1., 90., 7., 20., 60., 5.])


def _scaled_star_cube(shape=(40, 30)):
    """numpy (z, y, x) with no header, which is read as FITS axes x, y, z: channel k is
    `CHANNEL_SCALE[k] * (1 + a star at the centre of the image)`. `shape` is (x, y)."""
    xx, yy = np.mgrid[0:shape[0], 0:shape[1]]
    star = 1.0 + 100.0 * np.exp(-((xx - shape[0] / 2) ** 2 + (yy - shape[1] / 2) ** 2)
                                / (2 * 1.5 ** 2))
    return CHANNEL_SCALE[:, None, None] * star.T[None, :, :]


def _readings(stats, phot, fit, cut, strehl_planes):
    """One number from each tool, each proportional to the plane it measured."""
    return {
        "Statistics": float(stats.value_labels["Mean"].text()),
        "Photometry": float(phot.lbl_flux.text()),
        "Peak Fit": float(fit.value_labels["Max Pixel Value"].text()),
        "Strehl": float(np.nanmax(strehl_planes[-1])),
        "Horizontal Cut": float(np.nanmax(cut.plot_curve.getData()[1])),
    }


def test_tools_follow_the_displayed_plane(qapp, monkeypatch):
    """Statistics, Photometry, Peak Fit and Strehl recomputed only when their ROI moved, and
    the cuts followed only a single-slice slider step: a slider step, a Boxcar plane and a
    reload all left numbers on screen for a plane that was no longer displayed (BUGS.md R14)."""
    from pyql3.gui.tools import strehl as strehl_module
    from pyql3.gui.viewers.image_viewer import ImageViewer
    strehl_planes = []
    monkeypatch.setattr(strehl_module, "calculate_strehl",
                        lambda data, center, **kw: strehl_planes.append(np.array(data)))
    viewer = ImageViewer()
    viewer.set_data(_scaled_star_cube())
    stats, phot = StatisticsDialog(None, viewer), PhotometryDialog(None, viewer)
    fit, strehl = GaussianFitDialog(None, viewer), StrehlDialog(None, viewer)
    cut = CutPlotDialog("horizontal", None, viewer)
    tools = (stats, phot, fit, strehl, cut)
    for tool in tools:
        tool.show()
    try:
        at_channel_0 = _readings(stats, phot, fit, cut, strehl_planes)

        def scaled(by):
            return {name: pytest.approx(value * by, rel=1e-4)
                    for name, value in at_channel_0.items()}

        viewer.slider_slice.setValue(3)
        assert _readings(stats, phot, fit, cut, strehl_planes) == scaled(80 / 10)

        viewer.txt_boxcar.setText("3")
        viewer.btn_apply_slice.click()
        viewer.slider_slice.setValue(6)         # median of channels 5-7: 90, 7, 20
        assert _readings(stats, phot, fit, cut, strehl_planes) == scaled(20 / 10)

        viewer.txt_boxcar.setText("1")
        viewer.btn_apply_slice.click()
        viewer.slider_slice.setValue(3)
        viewer.set_data(_scaled_star_cube() * 100)
        assert _readings(stats, phot, fit, cut, strehl_planes) == scaled(8000 / 10)
    finally:
        for tool in tools:
            tool.close()


def test_a_slider_step_does_not_recompute_overlay_tools(qapp, monkeypatch):
    """A slider step changes the plane and nothing else. The catalog's markers and the Depth
    Plot's spectrum do not depend on the plane, so neither may be redrawn ten times for ten
    steps."""
    from pyql3.gui.tools.depth_plot import DepthPlotDialog
    from pyql3.gui.tools.plot_catalog import PlotCatalogDialog
    from pyql3.gui.viewers.image_viewer import ImageViewer
    calls = []
    for cls in (PlotCatalogDialog, DepthPlotDialog):
        original = cls.update_plot

        def spy(self, *args, _original=original, _name=cls.__name__, **kwargs):
            calls.append(_name)
            return _original(self, *args, **kwargs)
        monkeypatch.setattr(cls, "update_plot", spy)

    viewer = ImageViewer()
    viewer.set_data(_scaled_star_cube())
    catalog, depth = PlotCatalogDialog(None, viewer), DepthPlotDialog(None, viewer)
    try:
        catalog.show()
        depth.show()
        assert depth.combo_type.currentText() == "Depth Plot"
        calls.clear()

        for z in range(1, 11):
            viewer.slider_slice.setValue(z % 10)

        assert calls == []
    finally:
        catalog.close()
        depth.close()


#: The method each measuring tool recomputes through, by class.
RECOMPUTES = (
    (StatisticsDialog, "update_stats"),
    (PhotometryDialog, "update_photometry"),
    (GaussianFitDialog, "update_fit"),
    (StrehlDialog, "update_strehl"),
    (CutPlotDialog, "update_plot"),
)


def _flip(win):
    win.image_viewer.flip = True                # what Rotate Image > Flip Horizontal does
    win.image_viewer.refresh_display()


def _to_total_dn(win):
    win.set_display_unit(True)


def _reload(win):
    win.image_viewer.set_data(win.image_viewer.raw_data * 2)


def _type_then_click(win, field, text, button):
    """Type into `field` with the cursor in it, then click `button`. Focus leaving the field
    sends its `editingFinished`, and the click then sends the button's `clicked`."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    win.image_viewer.tabs.setCurrentWidget(win.image_viewer.tab_advanced)
    # The tool opened last is the active window. `qWaitForWindowActive` does not wait for the
    # main window to take over from it, because a window counts as active while one of its
    # child windows is.
    win.activateWindow()
    for _ in range(100):
        if QApplication.activeWindow() is win:
            break
        QTest.qWait(10)
    if QApplication.activeWindow() is not win:
        # Focus follows the active window, and a CI window manager (native macOS runners) may
        # refuse to activate one; typing would then go nowhere, which says nothing about the tool.
        pytest.skip("the window system did not activate the main window")
    field.setFocus()
    field.selectAll()
    QTest.keyClicks(field, text)
    assert field.hasFocus()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)


def _z_range_mode(win):
    win.image_viewer.radio_range.setChecked(True)


def _apply_after_editing_z_min(win):
    v = win.image_viewer
    _type_then_click(win, v.txt_zmin, "4", v.btn_apply_range)


def _apply_after_editing_boxcar(win):
    v = win.image_viewer
    _type_then_click(win, v.txt_boxcar, "3", v.btn_apply_slice)


@pytest.mark.parametrize("setup,change", [
    pytest.param(None, _flip, id="flip"),
    pytest.param(None, _to_total_dn, id="unit-change"),
    pytest.param(None, _reload, id="set_data"),
    pytest.param(_z_range_mode, _apply_after_editing_z_min, id="apply-after-editing-z-min"),
    pytest.param(None, _apply_after_editing_boxcar, id="apply-after-editing-boxcar"),
])
def test_each_change_recomputes_a_tool_once(qapp, monkeypatch, setup, change):
    """A flip or a unit change emits `plane_changed` and then `display_changed`; a tool that
    recomputed on both, or that `update_tools_for_unit` also called, did the work twice. With
    the cursor in Z Min or Boxcar, a click on Apply ran the apply twice: once for the field's
    `editingFinished` as focus left it, once for the button."""
    from pyql3.gui.main_window import MainWindow
    calls = []
    for cls, name in RECOMPUTES:
        original = getattr(cls, name)

        def spy(self, *args, _original=original, _name=cls.__name__, **kwargs):
            calls.append(_name)
            return _original(self, *args, **kwargs)
        monkeypatch.setattr(cls, name, spy)

    from astropy.io import fits
    win = MainWindow()
    # A declared count rate with an integration time, or Total DN is refused (R83)
    win.image_viewer.set_data(_scaled_star_cube(), fits.Header({'BUNIT': 'DN/s', 'ITIME': 4.0}))
    win.show()
    for open_tool in (win.open_statistics, win.open_photometry, win.open_gaussian_fit,
                      win.open_strehl_tool, win.open_horizontal_cut):
        open_tool()
    try:
        if setup is not None:
            setup(win)
        qapp.processEvents()
        calls.clear()

        change(win)
        qapp.processEvents()

        assert sorted(calls) == sorted(cls.__name__ for cls, _ in RECOMPUTES)
    finally:
        win.close()


def test_contour_updates_retire_their_old_items(qapp):
    """A Contour update that changes the number of levels took the old `IsocurveItem`s off the
    image and dropped them before building the new ones, so the collector could free one inside
    the construction of its replacement (`BUGS.md` M18, R87). They are retired, and released
    once the event loop is back or when the dialog is torn down."""
    import shiboken6
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from pyql3.gui.tools.advanced_plots import ContourDialog
    from pyql3.gui.viewers.image_viewer import ImageViewer
    viewer = ImageViewer()
    viewer.set_data(_scaled_star_cube())
    dialog = ContourDialog(None, viewer)
    dialog.show()
    old = list(dialog.iso_items)
    assert len(old) == 10

    dialog.spin_levels.setValue(5)

    assert len(dialog.iso_items) == 5 and not any(item in old for item in dialog.iso_items)
    assert all(any(item is retired for retired in dialog._retired_rois) for item in old)
    assert all(shiboken6.isValid(item) for item in old), "released before the event loop ran"

    qapp.processEvents()
    assert not any(shiboken6.isValid(item) for item in old)

    shown = list(dialog.iso_items)
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    assert not any(shiboken6.isValid(item) for item in shown)


def test_a_plane_change_reuses_the_contours(qapp):
    """With the number of levels unchanged, a new plane re-levels the same `IsocurveItem`s.
    Retiring them on every slider step cost a `gc.collect()` per step: 35-38 ms in a session
    holding a 66k-row catalog."""
    from pyql3.gui.tools.advanced_plots import ContourDialog
    from pyql3.gui.viewers.image_viewer import ImageViewer
    viewer = ImageViewer()
    viewer.set_data(_scaled_star_cube())
    dialog = ContourDialog(None, viewer)
    dialog.show()
    try:
        old = list(dialog.iso_items)
        old_top = max(item.level for item in old)
        qapp.processEvents()

        viewer.slider_slice.setValue(3)

        assert len(dialog.iso_items) == len(old)
        assert all(new is was for new, was in zip(dialog.iso_items, old, strict=True))
        assert dialog._retired_rois == []
        assert max(item.level for item in dialog.iso_items) == pytest.approx(old_top * 8)
        assert np.nanmax(dialog.iso_items[0].data) == pytest.approx(
            np.nanmax(viewer.current_plane()))
    finally:
        dialog.close()


def _z_range_apply(viewer):
    viewer.txt_zmin.setText("2")
    viewer.txt_zmax.setText("7")
    viewer.on_z_range_entered()


def _flip_viewer(viewer):
    viewer.flip = not viewer.flip
    viewer.refresh_display()


def _polled_set_data(viewer):
    viewer.set_data(_scaled_star_cube() * 3)


def _boxcar_step(viewer):
    viewer.slider_slice.setValue(viewer.slider_slice.value() + 1)


def _switch_to_z_range(viewer):
    viewer.radio_range.setChecked(True)
    _z_range_apply(viewer)


def _switch_to_boxcar(viewer):
    viewer.txt_boxcar.setText("3")
    viewer.on_boxcar_entered()


@pytest.mark.parametrize("setup,change", [
    pytest.param(None, _z_range_apply, id="z-range-apply"),
    pytest.param(_switch_to_z_range, _flip_viewer, id="flip-in-z-range"),
    pytest.param(_switch_to_z_range, _polled_set_data, id="set_data-in-z-range"),
    pytest.param(_switch_to_boxcar, _boxcar_step, id="boxcar-step"),
    pytest.param(_switch_to_boxcar, _flip_viewer, id="flip-in-boxcar"),
])
def test_a_change_collapses_the_cube_once(qapp, monkeypatch, setup, change):
    """With several tools open in Z Range or Boxcar mode, every tool's `current_plane()` call
    collapsed the cube again: seven collapses of 465 channels for one Z Range apply, 586 ms
    against 62 ms with no tools open. The plane the viewer drew is reused, and it is still the
    plane on screen after the change."""
    from pyql3.gui.viewers.image_viewer import ImageViewer
    collapses = []
    original = ImageViewer.collapse_plane

    def spy(self, *args, **kwargs):
        collapses.append(args)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ImageViewer, "collapse_plane", spy)

    viewer = ImageViewer()
    viewer.set_data(_scaled_star_cube())
    if change is _z_range_apply:
        viewer.radio_range.setChecked(True)
    stats, phot = StatisticsDialog(None, viewer), PhotometryDialog(None, viewer)
    stats.show()
    phot.show()
    try:
        if setup is not None:
            setup(viewer)
        collapses.clear()

        change(viewer)

        assert len(collapses) == 1, collapses
        assert viewer.display_data.ndim == 2
        assert np.allclose(viewer.current_plane(), viewer.display_data)
        assert len(collapses) == 1, "current_plane() collapsed the cube again"
    finally:
        stats.close()
        phot.close()


def test_current_plane_is_not_served_from_an_old_collapse(qapp):
    """The reused plane is keyed on what produced it: new data, another range or another
    collapse method each give a new plane."""
    from pyql3.gui.viewers.image_viewer import ImageViewer
    viewer = ImageViewer()
    viewer.set_data(_scaled_star_cube())
    viewer.radio_range.setChecked(True)
    _z_range_apply(viewer)                       # channels 2-7: 3, 80, 1, 90, 7, 20
    first = viewer.current_plane().copy()

    viewer.set_data(_scaled_star_cube() * 2)
    assert np.allclose(viewer.current_plane(), first * 2)

    viewer.combo_collapse.setCurrentText("Sum")
    assert np.allclose(viewer.current_plane(), first * 2 * (201 / 13.5))

    viewer.txt_zmax.setText("3")                 # typed, not applied: the fields still rule
    assert np.allclose(viewer.current_plane(), first * 2 * (83 / 13.5))


def test_strehl_profile_has_no_data_unit(qapp, loaded_viewer):
    """Both radial profiles are divided by their peak, and the axis was labelled
    "Intensity (DN/s)" — and only after a computation that succeeded (R83)."""
    loaded_viewer.disp_as_dn = True
    loaded_viewer.refresh_display()
    dialog = StrehlDialog(None, loaded_viewer)
    try:
        assert dialog.plot_widget.getAxis('left').labelText == "Intensity / Peak"
        dialog.update_strehl()
        assert dialog.plot_widget.getAxis('left').labelText == "Intensity / Peak"
    finally:
        dialog.close()
