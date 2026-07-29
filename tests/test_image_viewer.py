import pytest
import numpy as np
import pyqtgraph as pg
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


SCALE_MODES = ["Linear", "Negative", "HistEq", "Logarithmic", "Sqrt", "AsinH"]


@pytest.mark.parametrize("dtype", ["int16", "int32", "uint16", "float32", "float64"])
@pytest.mark.parametrize("scale", SCALE_MODES)
def test_scaling_modes_preserve_contrast_for_all_dtypes(qapp, dtype, scale):
    """B3: every scale mode must render varying values, including for integer FITS data.

    HistEq built its output with np.zeros_like(), so the normalized [0, 1) values
    truncated to 0 for integer dtypes and the image went uniformly flat. Negative
    additionally wrapped for unsigned data.
    """
    viewer = ImageViewer()
    data = (np.arange(64 * 32) % 500).reshape(64, 32).astype(dtype)

    viewer.set_data(data)
    viewer.combo_scale.setCurrentText(scale)
    viewer.update_image_display()

    render = viewer.imv.getImageItem().image
    assert np.issubdtype(render.dtype, np.floating), \
        f"{scale}/{dtype} rendered as {render.dtype}, which truncates scaled values"
    assert np.nanmax(render) > np.nanmin(render), \
        f"{scale}/{dtype} rendered a flat image (min == max == {np.nanmin(render)})"


def test_negative_scale_does_not_wrap_for_unsigned(qapp):
    """B3: -display_data wraps around for unsigned integers, inverting the contrast."""
    viewer = ImageViewer()
    data = np.arange(64 * 32, dtype=np.uint16).reshape(64, 32) % 500
    viewer.set_data(data)
    viewer.combo_scale.setCurrentText("Negative")
    viewer.update_image_display()

    render = viewer.imv.getImageItem().image
    assert np.nanmin(render) < 0, \
        f"Negative of unsigned data should be <= 0, got min {np.nanmin(render)} (wrapped)"


def test_histeq_handles_nans_without_dividing_by_zero(qapp):
    """B3: HistEq maps only the valid pixels; NaNs stay 0 and must not poison the rest."""
    data = ((np.arange(64 * 32) % 500).reshape(64, 32)).astype(np.float32)
    data[:8, :] = np.nan

    viewer = ImageViewer()
    viewer.set_data(data)
    viewer.combo_scale.setCurrentText("HistEq")
    with np.errstate(divide="raise", invalid="raise"):
        viewer.update_image_display()

    # set_data transposes into pyqtgraph's (X, Y) order, so locate the NaNs via display_data
    nan_mask = np.isnan(viewer.display_data)
    assert nan_mask.sum() == 8 * 32

    render = viewer.imv.getImageItem().image
    assert np.all(render[nan_mask] == 0), "NaN pixels should render as 0"
    finite = render[~nan_mask]
    assert np.all(np.isfinite(finite)), "Valid pixels should not become NaN"
    assert 0.0 < finite.max() <= 1.0, f"HistEq output out of range: max {finite.max()}"


def _make_cube(nz=10, nx=12, ny=14):
    np.random.seed(0)
    return np.random.rand(nz, nx, ny).astype(np.float32)


def test_timeline_drag_syncs_slider_labels_and_readout(qapp):
    """B4: dragging the pyqtgraph timeline must write back to the Z Slice slider.

    Previously only update_slice_info was connected, and it read the slider rather than
    the displayed index, so the slider, the labels, the Value: readout and every analysis
    tool reported a different slice than the one on screen.
    """
    viewer = ImageViewer()
    viewer.set_data(_make_cube())

    viewer.imv.setCurrentIndex(7)  # equivalent to dragging the timeline handle

    assert viewer.slider_slice.value() == 7
    assert viewer.lbl_slice_val.text() == "7"
    assert "Slice: 7" in viewer.lbl_slice_info.text()
    assert viewer.current_z() == 7
    assert viewer.imv.currentIndex == 7


def test_slider_still_drives_the_timeline(qapp):
    """B4: the sync must not break the slider -> ImageView direction, or recurse."""
    viewer = ImageViewer()
    viewer.set_data(_make_cube())

    for value in (3, 9, 0, 5):
        viewer.slider_slice.setValue(value)
        assert viewer.imv.currentIndex == value
        assert viewer.current_z() == value
        assert f"Slice: {value}" in viewer.lbl_slice_info.text()


def test_reload_preserves_displayed_slice(qapp):
    """B4 (reverse direction): setImage() resets the ImageView to 0 and emits
    sigTimeChanged, so a reload or poller auto-load showed slice 0 while the slider,
    labels and tools still reported the old index."""
    viewer = ImageViewer()
    cube = _make_cube()
    viewer.set_data(cube)
    viewer.slider_slice.setValue(6)

    viewer.set_data(cube + 1.0)  # a new frame for the same target

    assert viewer.slider_slice.value() == 6
    assert viewer.imv.currentIndex == 6, "display dropped to slice 0 behind the slider"
    assert "Slice: 6" in viewer.lbl_slice_info.text()


def test_slice_index_clamped_when_cube_shrinks(qapp):
    """A shorter cube must leave the slider and the ImageView agreeing."""
    viewer = ImageViewer()
    viewer.set_data(_make_cube(nz=10))
    viewer.slider_slice.setValue(viewer.slider_slice.maximum())

    viewer.set_data(_make_cube(nz=4))

    nz = viewer.transposed_data.shape[0]
    assert viewer.slider_slice.value() <= nz - 1
    assert viewer.imv.currentIndex == viewer.slider_slice.value()
    assert viewer.current_z() == viewer.slider_slice.value()


def test_hover_readout_reports_the_displayed_slice(qapp):
    """B4: the Value: readout took z from the slider, so a timeline drag reported the
    pixel value of the previously displayed slice."""
    viewer = ImageViewer()
    cube = _make_cube()
    viewer.set_data(cube)
    viewer.imv.setCurrentIndex(7)

    z = viewer.current_z()
    x, y = 3, 4
    expected = viewer.display_data[z, x, y]

    scene_pos = viewer.imv.getView().mapViewToScene(pg.Point(x + 0.5, y + 0.5))
    viewer.mouse_moved([scene_pos])

    assert viewer.lbl_val.text().startswith("Value:")
    assert f"{expected:.5g}" in viewer.lbl_val.text(), \
        f"readout {viewer.lbl_val.text()!r} does not match slice {z} value {expected:.5g}"


def test_current_z_tracks_boxcar_and_collapse_range(qapp):
    """B4: imv.currentIndex goes stale once a boxcar/range is drawn via bypass_imv, so
    tools that index the cube with it disagree with the display."""
    viewer = ImageViewer()
    viewer.set_data(_make_cube(nz=20))
    viewer.slider_slice.setValue(2)
    assert viewer.current_z() == 2

    # Boxcar: centre of the averaged window
    viewer.txt_boxcar.setText("5")
    viewer.slider_slice.setValue(9)
    assert viewer.current_z() == 9
    viewer.txt_boxcar.setText("1")

    # Collapse range: middle of the range
    viewer.txt_zmin.setText("4")
    viewer.txt_zmax.setText("8")
    viewer.radio_range.setChecked(True)
    viewer.z_mode_changed()
    assert viewer.current_z() == 6

    # Reversed / out-of-range entries must not produce an out-of-bounds index
    viewer.txt_zmin.setText("18")
    viewer.txt_zmax.setText("3")
    assert 0 <= viewer.current_z() < viewer.transposed_data.shape[0]


def test_timeline_sync_ignored_in_collapse_range_mode(qapp):
    """The timeline is hidden while a range is collapsed; a stray sigTimeChanged must not
    move the slider and silently switch the display out of range mode."""
    viewer = ImageViewer()
    viewer.set_data(_make_cube(nz=20))
    viewer.txt_zmin.setText("2")
    viewer.txt_zmax.setText("5")
    viewer.radio_range.setChecked(True)
    viewer.z_mode_changed()

    before = viewer.slider_slice.value()
    viewer.imv.sigTimeChanged.emit(19, 19.0)

    assert viewer.slider_slice.value() == before
    assert "Collapsed: 2-5" in viewer.lbl_slice_info.text()


@pytest.fixture
def channel_cube(qapp):
    """Viewer on a cube whose wavelength channel k holds the constant value k.

    Any reported number then names exactly which channel(s) were used. Uses a real
    OSIRIS-ordered WCS, so numpy order is (RA, DEC, WAVE) and wavelength is axis 2.
    """
    from astropy.wcs import WCS

    nra, ndec, nz = 12, 16, 20
    data = np.zeros((nra, ndec, nz), dtype=np.float32)
    for k in range(nz):
        data[:, :, k] = k

    w = WCS(naxis=3)
    w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']
    w.wcs.crval = [2.2, 34.0, -118.0]
    w.wcs.cdelt = [0.0005, 0.0001, 0.0001]
    w.wcs.crpix = [1, 8, 6]
    w.wcs.cunit = ['um', 'deg', 'deg']
    header = w.to_header()
    header['EXPTIME'] = 1.0
    header['ITIME'] = 1.0

    viewer = ImageViewer()
    viewer.set_data(data, header)
    assert viewer.transposed_data.shape[0] == nz, "wavelength should be the viewer's z axis"
    return viewer


# --- B12: collapse-range clamping -------------------------------------------------

@pytest.mark.parametrize("typed,expected", [
    (("10", "3"), (3, 10)),      # reversed
    (("99", "3"), (3, 19)),      # past the end
    (("-5", "4"), (0, 4)),       # before the start
    (("7", "7"), (7, 7)),        # single channel
    (("0", "19"), (0, 19)),      # whole cube
])
def test_collapse_range_is_clamped_and_written_back(channel_cube, typed, expected):
    """B12: a reversed or out-of-range entry sliced an empty subcube, so nanmedian
    returned an all-NaN plane: blank display, console warning, and a label that still
    read e.g. 'Collapsed: 10-3'."""
    v = channel_cube
    v.combo_collapse.setCurrentText("Mean")
    v.radio_range.setChecked(True)
    v.txt_zmin.setText(typed[0])
    v.txt_zmax.setText(typed[1])
    v.apply_z_range()

    zmin, zmax = expected
    # the boxes are corrected so the UI shows the range actually used
    assert (v.txt_zmin.text(), v.txt_zmax.text()) == (str(zmin), str(zmax))
    assert f"Collapsed: {zmin}-{zmax}" in v.lbl_slice_info.text()

    render = v.imv.getImageItem().image
    assert np.all(np.isfinite(render)), "collapse produced an all-NaN plane"
    assert np.isclose(np.nanmean(render), np.mean(np.arange(zmin, zmax + 1)))


def test_collapse_range_emits_no_empty_slice_warning(channel_cube):
    """B12: the empty subcube used to raise 'Mean of empty slice' on the console."""
    v = channel_cube
    v.combo_collapse.setCurrentText("Median")
    v.radio_range.setChecked(True)
    v.txt_zmin.setText("15")
    v.txt_zmax.setText("2")

    import warnings as _warnings
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        v.apply_z_range()
    runtime = [str(x.message) for x in caught if issubclass(x.category, RuntimeWarning)]
    assert runtime == [], f"unexpected RuntimeWarnings: {runtime}"


def test_unparsable_collapse_range_is_reported_not_applied(channel_cube):
    """Garbage in the box should say so, and must not rewrite what the user typed."""
    v = channel_cube
    v.radio_range.setChecked(True)
    v.txt_zmin.setText("4")
    v.txt_zmax.setText("8")
    v.apply_z_range()
    good = v.imv.getImageItem().image.copy()

    v.txt_zmin.setText("abc")
    v.apply_z_range()

    assert "Invalid Range" in v.lbl_slice_info.text()
    assert v.txt_zmin.text() == "abc", "the user's text should not be silently rewritten"
    assert np.allclose(v.imv.getImageItem().image, good), "display should be left alone"


def test_clamp_z_range_helper_is_the_single_definition(channel_cube):
    """update_slice_info used to repeat the half-clamped arithmetic independently."""
    v = channel_cube
    assert v.clamp_z_range(10, 3) == (3, 10)
    assert v.clamp_z_range(-4, 999) == (0, 19)
    assert v.clamp_z_range(5, 5) == (5, 5)

    v.radio_range.setChecked(True)
    v.txt_zmin.setText("12")
    v.txt_zmax.setText("2")
    v.update_slice_info()
    assert "Collapsed: 2-12" in v.lbl_slice_info.text()


# --- B16: all-invalid planes ------------------------------------------------------

@pytest.mark.parametrize("scale", SCALE_MODES)
def test_all_nan_image_does_not_raise(qapp, scale):
    """B16: pyqtgraph derives its autorange from nanmin/nanmax and raised
    'Cannot set range [nan, nan]' for a plane with no finite pixels."""
    viewer = ImageViewer()
    viewer.combo_scale.setCurrentText(scale)
    viewer.set_data(np.full((32, 16), np.nan, dtype=np.float32))
    viewer.update_image_display()

    render = viewer.imv.getImageItem().image
    assert np.all(np.isfinite(render)), "an all-invalid plane must not reach pyqtgraph"
    assert viewer._plane_all_invalid is True
    assert "no valid data" in viewer.lbl_slice_info.text()


def test_all_nan_cube_does_not_raise(qapp):
    """Same for a cube where every channel is dead."""
    viewer = ImageViewer()
    viewer.set_data(np.full((10, 12, 14), np.nan, dtype=np.float32))
    assert np.all(np.isfinite(viewer.imv.getImageItem().image))
    assert "no valid data" in viewer.lbl_slice_info.text()


def test_dead_channel_is_flagged_only_while_displayed(qapp):
    """The marker has to track slice changes: paging onto a dead channel does not
    redisplay the image, it only moves the ImageView index."""
    np.random.seed(0)
    cube = np.random.rand(10, 12, 14).astype(np.float32)
    cube[:, :, 5] = np.nan          # wavelength channel 5 is dead

    viewer = ImageViewer()
    viewer.set_data(cube)

    viewer.slider_slice.setValue(4)
    assert "no valid data" not in viewer.lbl_slice_info.text()
    viewer.slider_slice.setValue(5)
    assert "no valid data" in viewer.lbl_slice_info.text()
    viewer.slider_slice.setValue(6)
    assert "no valid data" not in viewer.lbl_slice_info.text()

    # and via a timeline drag
    viewer.imv.setCurrentIndex(5)
    assert "no valid data" in viewer.lbl_slice_info.text()
    viewer.imv.setCurrentIndex(6)
    assert "no valid data" not in viewer.lbl_slice_info.text()


def test_non_finite_manual_levels_do_not_blank_the_display(qapp):
    """float('nan') parses happily from the Min/Max boxes and poisoned every scale mode."""
    np.random.seed(0)
    viewer = ImageViewer()
    viewer.set_data(np.random.rand(32, 16).astype(np.float32))

    viewer.txt_min.setText("nan")
    viewer.txt_max.setText("inf")
    viewer.update_image_display(use_manual_levels=True)

    render = viewer.imv.getImageItem().image
    assert np.all(np.isfinite(render))
    assert viewer._plane_all_invalid is False


# --- B17: cuts must follow the displayed plane ------------------------------------

@pytest.mark.parametrize("method,expected", [
    ("Sum", 30.0),        # 4+5+6+7+8
    ("Mean", 6.0),
    ("Median", 6.0),
])
def test_depth_plot_cuts_use_the_collapsed_plane(channel_cube, method, expected):
    """B17: the cut branches indexed a single channel of the always-3D cube, so with a
    collapse displayed they plotted data that is not on screen. Sum is the case that
    exposes it -- for Mean/Median of a linear cube the midpoint channel coincides."""
    from pyql3.gui.tools.depth_plot import DepthPlotDialog

    v = channel_cube
    v.slider_slice.setValue(3)                 # leave the ImageView index at 3
    v.txt_zmin.setText("4")
    v.txt_zmax.setText("8")
    v.combo_collapse.setCurrentText(method)
    v.radio_range.setChecked(True)
    v.z_mode_changed()

    on_screen = float(np.nanmean(v.imv.getImageItem().image))
    assert np.isclose(on_screen, expected)

    dp = DepthPlotDialog(None, v)
    try:
        dp.roi.setPos(2, 2)
        dp.roi.setSize([6, 6])
        for cut in ("Horizontal Cut", "Vertical Cut"):
            dp.combo_type.setCurrentText(cut)
            dp.update_plot()
            _, ydata = dp.plot_data.getData()
            assert ydata is not None and len(ydata) > 0
            assert np.allclose(ydata, expected), \
                f"{cut} plotted {np.unique(ydata)}, screen shows {expected}"
    finally:
        dp.close()


def test_depth_plot_cuts_follow_boxcar(channel_cube):
    """Boxcar renders a median over a window; the cuts must match that, not one channel."""
    from pyql3.gui.tools.depth_plot import DepthPlotDialog

    v = channel_cube
    v.txt_boxcar.setText("5")
    v.slider_slice.setValue(9)                 # median of channels 7..11 == 9
    assert np.isclose(float(np.nanmean(v.imv.getImageItem().image)), 9.0)

    dp = DepthPlotDialog(None, v)
    try:
        dp.roi.setPos(2, 2)
        dp.roi.setSize([6, 6])
        dp.combo_type.setCurrentText("Horizontal Cut")
        dp.update_plot()
        _, ydata = dp.plot_data.getData()
        assert np.allclose(ydata, 9.0), f"boxcar cut plotted {np.unique(ydata)}, expected 9.0"
    finally:
        dp.close()


def test_depth_plot_cuts_still_follow_a_single_slice(channel_cube):
    """Plain Z Slice mode must be unchanged: the cut is that channel."""
    from pyql3.gui.tools.depth_plot import DepthPlotDialog

    v = channel_cube
    v.txt_boxcar.setText("1")
    v.slider_slice.setValue(13)

    dp = DepthPlotDialog(None, v)
    try:
        dp.roi.setPos(2, 2)
        dp.roi.setSize([6, 6])
        dp.combo_type.setCurrentText("Vertical Cut")
        dp.update_plot()
        _, ydata = dp.plot_data.getData()
        assert np.allclose(ydata, 13.0)
    finally:
        dp.close()


def test_current_plane_matches_display_data_without_the_dn_multiplier(channel_cube):
    """current_plane() feeds callers that apply data_multiplier themselves, so it must
    return unscaled values in display orientation."""
    v = channel_cube
    v.slider_slice.setValue(7)

    plane = v.current_plane()
    assert plane.ndim == 2
    assert np.allclose(plane, 7.0)
    assert plane.shape == v.display_data.shape[1:]

    v._itime_coadds = 10.0
    v.disp_as_dn = True
    v.refresh_display()
    assert np.allclose(v.current_plane(), 7.0), "current_plane must not include the multiplier"
    assert np.allclose(v.display_data[v.current_z()], 70.0), "display_data should include it"


@pytest.mark.parametrize("rot,flip", [(0, False), (90, False), (180, True), (270, True)])
def test_current_plane_orientation_matches_display(channel_cube, rot, flip):
    """current_plane() and display_data must agree pixel for pixel under rotation/flip,
    since tools index it with display coordinates."""
    v = channel_cube
    # break the per-channel symmetry so orientation errors cannot hide
    v.raw_data[0, 0, :] = 99.0
    v.rot_angle = rot
    v.flip = flip
    v.apply_axis_mapping()
    v.slider_slice.setValue(5)

    assert np.allclose(v.current_plane(), v.display_data[v.current_z()])
